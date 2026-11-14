import json

import pytest

from config.runtime import RuntimeConfig


def test_defaults_are_valid():
    assert RuntimeConfig().validate() == []


@pytest.mark.parametrize("kw,fragment", [
    ({"max_queue_size": 0}, "Calling_queue"), ({"miss_release_policy": "bogus"}, "chính sách"),
    ({"source_mode": "weird"}, "Nguồn"), ({"export_dir": "  "}, "Thư mục"),
    ({"min_interval": 3, "max_interval": 1}, "tối thiểu"), ({"max_call": 0}, "max_call"),
    ({"source_mode": "replay", "replay_file": "khong_co.txt"}, "file phát lại"),
    ({"source_mode": "replay", "replay_file": "data/employees.txt", "record_generated": True}, "ghi lại"),
])
def test_invalid(kw, fragment):
    errs = RuntimeConfig(**kw).validate()
    assert errs and any(fragment.lower() in e.lower() for e in errs), errs


def test_save_load_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    cfg = RuntimeConfig(max_queue_size=7, seed=5, event_weights={"Success": 1, "Miss": 2, "Break": 3},
                        record_generated=True)
    cfg.save(p)
    assert RuntimeConfig.load(p) == cfg
    assert [f.name for f in tmp_path.iterdir()] == ["s.json"]       # không để lại file tạm


def test_missing_corrupt_or_invalid_file_falls_back_to_defaults(tmp_path):
    assert RuntimeConfig.load(tmp_path / "none.json") == RuntimeConfig()
    bad = tmp_path / "bad.json"; bad.write_text("{không phải json", encoding="utf-8")
    assert RuntimeConfig.load(bad) == RuntimeConfig()
    inv = tmp_path / "inv.json"; inv.write_text(json.dumps({"max_queue_size": -5}), encoding="utf-8")
    assert RuntimeConfig.load(inv) == RuntimeConfig()


def test_unknown_keys_are_ignored(tmp_path):
    p = tmp_path / "s.json"; p.write_text(json.dumps({"max_call": 9, "tuong_lai": 1}), encoding="utf-8")
    assert RuntimeConfig.load(p).max_call == 9


def test_engine_and_generator_configs_follow_values():
    cfg = RuntimeConfig(max_queue_size=12, break_release_policy="to_break", max_call=4, seed=3)
    assert cfg.engine_config().max_queue_size == 12 and cfg.engine_config().break_release_policy.value == "to_break"
    assert cfg.generator_config().max_call == 4 and cfg.generator_config().seed == 3


@pytest.mark.parametrize("value,ok", [("19:00", True), ("00:00", True), ("23:59", True), ("7:05", True),
                                      ("24:00", False), ("19:60", False), ("abc", False), ("", False), ("19", False)])
def test_auto_export_time_validation(value, ok):
    errs = RuntimeConfig(auto_export_time=value).validate()
    assert (errs == []) is ok, errs


def test_schedule_defaults_follow_spec():
    c = RuntimeConfig()
    assert c.auto_schedule_enabled is True and c.auto_export_time == "19:00"
