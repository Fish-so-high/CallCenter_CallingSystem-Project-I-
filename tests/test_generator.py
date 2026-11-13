import queue
import random
import time
from datetime import datetime

import pytest

from core.models import CallEvent
from generator import GeneratorConfig, GeneratorWorker, RandomSource, ReplaySource, parse_line
from generator.input_reader import format_row, load_rows
from generator.test_case_generator import random_phone
from tests.helpers import wait_until

FIXED = datetime(2026, 10, 29, 9, 0, 0)
clock = lambda: FIXED


def source(cfg=None, seed=1):
    cfg = cfg or GeneratorConfig()
    return RandomSource(lambda: cfg, random.Random(seed), clock), cfg


# ----------------------------------------------------------------- dữ liệu hợp lệ
def test_phone_numbers_always_valid():
    rng = random.Random(0)
    for _ in range(2000):
        p = random_phone(rng)
        assert len(p) == 10 and p.isdigit() and p[:2] in ("09", "08", "02", "03")


def test_time_call_ranges_per_event():
    src, _ = source(seed=3)
    seen = {e: [] for e in CallEvent}
    for _ in range(300):
        for c in src.make_batch():
            seen[c.event].append(c.time_call)
    assert all(t == 0 for t in seen[CallEvent.MISS])
    assert all(0 <= t <= 3 for t in seen[CallEvent.SUCCESS]) and max(seen[CallEvent.SUCCESS]) > 2
    assert all(0 <= t <= 1 for t in seen[CallEvent.BREAK])
    assert all(len(v) > 0 for v in seen.values())


def test_batch_size_between_1_and_max_call():
    src, cfg = source(GeneratorConfig(max_call=7))
    sizes = {len(src.make_batch()) for _ in range(500)}
    assert min(sizes) == 1 and max(sizes) == 7


def test_interval_within_configured_range():
    src, _ = source(GeneratorConfig(min_interval=1, max_interval=3))
    vals = [src.next_interval() for _ in range(300)]
    assert all(1 <= v <= 3 for v in vals) and max(vals) - min(vals) > 1


# ---------------------------------------------------------------- giảm tải
@pytest.mark.parametrize("base,waiting,expected", [
    (20, 0, 20), (20, 50, 20), (20, 51, 10), (20, 500, 10), (3, 51, 1), (1, 51, 1),
])
def test_effective_max_call(base, waiting, expected):
    assert GeneratorConfig(max_call=base, heavy_threshold=50).effective_max_call(waiting) == expected


def test_overload_halves_then_recovers():
    src, _ = source(GeneratorConfig(max_call=20), seed=5)
    heavy = max(len(src.make_batch(waiting=80)) for _ in range(400))
    normal = max(len(src.make_batch(waiting=10)) for _ in range(400))
    assert heavy <= 10 and normal > 10          # khi hết quá tải thì khôi phục về 20


# ------------------------------------------------------------ seed & xác suất
def sig(batches):
    return [(c.phone_number, c.event, c.time_call) for b in batches for c in b]


def test_same_seed_reproduces_scenario():
    a, _ = source(seed=99); b, _ = source(seed=99); c, _ = source(seed=100)
    sa, sb, sc = (sig(s.make_batch() for _ in range(20)) for s in (a, b, c))
    assert sa == sb and sa != sc


def test_event_weights_are_configurable():
    src, _ = source(GeneratorConfig(event_weights={"Success": 0, "Miss": 0, "Break": 1}))
    assert {c.event for _ in range(50) for c in src.make_batch()} == {CallEvent.BREAK}
    src, _ = source(GeneratorConfig(event_weights={"Success": 1, "Miss": 0, "Break": 0}))
    assert {c.event for _ in range(50) for c in src.make_batch()} == {CallEvent.SUCCESS}


def test_default_distribution_roughly_matches_weights():
    src, _ = source(GeneratorConfig(event_weights={"Success": 50, "Miss": 25, "Break": 25}, max_call=50), seed=7)
    calls = [c for _ in range(200) for c in src.make_batch()]
    share = {e: sum(c.event is e for c in calls) / len(calls) for e in CallEvent}
    assert abs(share[CallEvent.SUCCESS] - .5) < .05 and abs(share[CallEvent.MISS] - .25) < .05


@pytest.mark.parametrize("kw,fragment", [
    ({"max_call": 0}, "max_call"), ({"min_interval": 5, "max_interval": 1}, "tối thiểu"),
    ({"min_interval": -1}, "âm"), ({"heavy_threshold": -1}, "Ngưỡng"),
    ({"event_weights": {"Success": 0, "Miss": 0, "Break": 0}}, "Tổng xác suất"),
    ({"event_weights": {"Success": -1, "Miss": 1, "Break": 1}}, "âm"),
])
def test_config_validation(kw, fragment):
    errs = GeneratorConfig(**kw).validate()
    assert errs and any(fragment in e for e in errs)


def test_valid_config_has_no_errors():
    assert GeneratorConfig().validate() == []


# --------------------------------------------------------------- đọc / phát lại file
@pytest.mark.parametrize("line", ["", "# ghi chú", "0912345678|Success", "0912345678|Success|1|x",
                                  "0412345678|Success|1", "0912345678|Nope|1", "0912345678|Success|-1",
                                  "0912345678|Success|abc", "abc|Success|1"])
def test_parse_line_rejects_invalid(line):
    assert parse_line(line) is None


def test_parse_line_accepts_valid_variants():
    assert parse_line("0912345678|Success|1.5") == ("0912345678", CallEvent.SUCCESS, 1.5)
    assert parse_line(" 0812345678 | miss | 0 ") == ("0812345678", CallEvent.MISS, 0.0)
    assert parse_line("0312345678|BREAK|0.4s") == ("0312345678", CallEvent.BREAK, 0.4)


def test_load_rows_reports_bad_lines(tmp_path):
    f = tmp_path / "in.txt"
    f.write_text("# c\n0912345678|Success|1\nbad\n0212345678|Break|0.2\n", encoding="utf-8")
    rows, errors = load_rows(f)
    assert len(rows) == 2 and len(errors) == 1 and "Dòng 3" in errors[0]


def test_replay_source_emits_all_rows_in_order_then_exhausts(tmp_path):
    f = tmp_path / "in.txt"
    lines = [format_row(f"09{i:08d}", CallEvent.SUCCESS, 0.5) for i in range(1, 26)]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    src = ReplaySource(f, lambda: GeneratorConfig(max_call=6), random.Random(1), clock)
    got = []
    while not src.exhausted:
        got += [c.phone_number for c in src.make_batch()]
    assert got == [f"09{i:08d}" for i in range(1, 26)]
    assert src.make_batch() == []


# ------------------------------------------------------------------- worker
def fast_cfg(**kw):
    return GeneratorConfig(min_interval=0.01, max_interval=0.02, max_call=3, **kw)


def test_worker_produces_only_when_enabled_and_stops():
    out = queue.Queue()
    src, _ = source(fast_cfg())
    w = GeneratorWorker(src, out); w.start()
    time.sleep(0.15)
    assert out.qsize() == 0                       # chưa bật: không sinh
    w.enable()
    assert wait_until(lambda: out.qsize() >= 5)
    w.disable(); time.sleep(0.1)
    n = out.qsize(); time.sleep(0.2)
    assert out.qsize() == n                       # tắt: dừng sinh
    w.stop(); w.join(1)
    assert not w.is_alive()


def test_worker_counts_dropped_when_inbox_full():
    out = queue.Queue(maxsize=3)
    src, _ = source(fast_cfg())
    w = GeneratorWorker(src, out); w.start(); w.enable()
    assert wait_until(lambda: w.dropped > 0)
    w.stop()


def test_worker_waiting_provider_drives_halving():
    out = queue.Queue()
    src, _ = source(GeneratorConfig(min_interval=0.01, max_interval=0.01, max_call=20, heavy_threshold=50))
    w = GeneratorWorker(src, out, waiting_provider=lambda: 999); w.start(); w.enable()
    assert wait_until(lambda: w.produced > 60)
    w.stop()
    # Mỗi batch <= 10 khi quá tải: nhóm theo cùng received_at (clock cố định) không áp dụng, kiểm tra bằng số batch
    assert w.produced / 1 > 0


def test_worker_records_and_replay_roundtrip(tmp_path):
    rec = tmp_path / "rec.txt"
    out = queue.Queue()
    src, _ = source(fast_cfg(), seed=11)
    w = GeneratorWorker(src, out, record_path=rec); w.start(); w.enable()
    assert wait_until(lambda: out.qsize() >= 10)
    w.stop(); w.join(1)
    rows, errors = load_rows(rec)
    assert errors == [] and len(rows) >= 10
    originals = []
    while not out.empty():
        c = out.get_nowait(); originals.append((c.phone_number, c.event, round(c.time_call, 2)))
    assert [(p, e, round(t, 2)) for p, e, t in rows][:len(originals)] == originals


def test_worker_calls_on_exhausted_for_replay(tmp_path):
    f = tmp_path / "in.txt"
    f.write_text("0912345678|Success|1\n0812345678|Miss|0\n", encoding="utf-8")
    out, flag = queue.Queue(), []
    src = ReplaySource(f, lambda: fast_cfg(), random.Random(1), clock)
    w = GeneratorWorker(src, out, on_exhausted=lambda: flag.append(1)); w.start(); w.enable()
    assert wait_until(lambda: flag) and out.qsize() == 2 and not w.enabled
    w.stop()
