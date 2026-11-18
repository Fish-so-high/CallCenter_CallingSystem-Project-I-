import threading
import time

import pytest

from config.runtime import RuntimeConfig
from core.employee_manager import load_employees
from core.models import CallTag, Employee, EmployeeStatus as S
from services import PersistenceService, SimulationService
from storage import ExportError
from tests.helpers import FakeBackend, wait_until


@pytest.fixture
def make_svc(tmp_path):
    created = []

    def _make(n=3, persistence=None, start_loop=True, **cfg_kw):
        base = dict(min_interval=0.01, max_interval=0.02, max_call=3, seed=1, export_dir=str(tmp_path / "out"))
        base.update(cfg_kw)
        emps = [Employee(f"E{i}", f"Nhân viên {i}") for i in range(1, n + 1)]
        svc = SimulationService(RuntimeConfig(**base), emps, persistence, tmp_path / "employees.txt",
                                config_path=tmp_path / "settings.json", generator_record_path=tmp_path / "rec.txt")
        created.append(svc)
        if start_loop:
            svc.start_loop()
        return svc

    yield _make
    for s in created:
        s.shutdown()


def total(svc):
    return svc.engine.stats["total"]


def test_idle_until_started(make_svc):
    svc = make_svc()
    time.sleep(0.3)
    assert not svc.running and total(svc) == 0 and svc._generator is None


def test_start_processes_calls_and_delivers_results_on_worker_thread(make_svc):
    svc = make_svc()
    seen, threads = [], set()
    svc.on_result.append(lambda r: (seen.append(r), threads.add(threading.current_thread().name)))
    assert svc.start().ok and svc.running
    assert wait_until(lambda: len(seen) >= 15, 5)
    assert len(seen) <= total(svc)
    assert threading.main_thread().name not in threads        


def test_pause_freezes_everything_and_resume_continues(make_svc):
    svc = make_svc(); svc.start()
    assert wait_until(lambda: total(svc) >= 5)
    assert svc.pause().ok and not svc.running and svc.engine.paused
    time.sleep(0.15); frozen = total(svc); time.sleep(0.4)
    assert total(svc) == frozen
    assert not svc._generator.enabled
    svc.start()
    assert wait_until(lambda: total(svc) > frozen + 3, 5)


def test_toggle(make_svc):
    svc = make_svc()
    assert svc.toggle().ok and svc.running
    assert svc.toggle().ok and not svc.running


def test_generator_can_be_switched_off_while_engine_keeps_running(make_svc):
    svc = make_svc(n=40, min_interval=0.1, max_interval=0.1, max_call=2); svc.start()
    assert wait_until(lambda: total(svc) >= 3)
    assert svc.set_generator_enabled(False).ok and not svc.generator_enabled
    time.sleep(0.2)                                            
    assert wait_until(lambda: not svc.engine.queues.calling and svc.engine.processor.active_calls() == [], 6)
    settled = total(svc); time.sleep(0.4)
    assert total(svc) == settled and svc.running              
    svc.set_generator_enabled(True)
    assert wait_until(lambda: total(svc) > settled, 5)


def test_state_callbacks(make_svc):
    svc = make_svc(); states = []
    svc.on_state.append(lambda r, g: states.append((r, g)))
    svc.start(); svc.set_generator_enabled(False); svc.pause()
    assert states == [(True, True), (True, False), (False, False)]


# ------------------------------------------------------------------ Config
def test_invalid_config_is_rejected_and_nothing_changes(make_svc, tmp_path):
    svc = make_svc()
    old = svc.config
    res = svc.apply_config(RuntimeConfig(max_queue_size=0, min_interval=5, max_interval=1))
    assert not res.ok and "Calling_queue" in res.message
    assert svc.config is old and svc.engine.queues.calling.capacity == 100
    assert not (tmp_path / "settings.json").exists()


def test_valid_config_applies_live_and_persists(make_svc, tmp_path):
    svc = make_svc()
    res = svc.apply_config(RuntimeConfig(max_queue_size=7, break_release_policy="to_break", max_call=2,
                                         min_interval=0.01, max_interval=0.01, seed=1, export_dir=str(tmp_path / "x")))
    assert res.ok
    assert svc.engine.queues.calling.capacity == 7
    assert svc.engine.config.break_release_policy.value == "to_break"
    assert svc._gen_cfg.max_call == 2
    assert RuntimeConfig.load(tmp_path / "settings.json").max_queue_size == 7


def test_same_seed_gives_same_scenario(make_svc, tmp_path):
    def run(sub):
        d = tmp_path / sub; d.mkdir()
        svc = SimulationService(RuntimeConfig(min_interval=0.01, max_interval=0.01, max_call=4, seed=123,
                                              record_generated=True, export_dir=str(d)),
                                [Employee("E1", "a")], None, None, config_path=None,
                                generator_record_path=d / "rec.txt")
        svc.start_loop(); svc.start()
        assert wait_until(lambda: (d / "rec.txt").exists() and len((d / "rec.txt").read_text().splitlines()) >= 30, 5)
        svc.shutdown()
        return (d / "rec.txt").read_text().splitlines()[:30]
    assert run("a") == run("b")


def test_changing_seed_restarts_generator(make_svc):
    svc = make_svc(); svc.start()
    first = svc._generator
    cfg = RuntimeConfig(**{**svc.config.__dict__, "seed": 777})
    assert svc.apply_config(cfg).ok
    assert svc._generator is not first and svc._generator is not None
    assert wait_until(lambda: not first.is_alive(), 2)


# --------------------------------------------------------- Full / Break Employee
def all_on_break(svc):
    for e in list(svc.engine.employees.all()):
        assert svc.set_break(e.employee_id).ok


def test_rejected_when_queue_full(make_svc):
    svc = make_svc(max_queue_size=5)
    all_on_break(svc); svc.start()
    assert wait_until(lambda: svc.engine.stats["Rejected"] > 0, 5)
    assert len(svc.engine.queues.calling) == 5
    assert svc.engine.stats["Success"] == 0


def test_abandon_waiting(make_svc):
    svc = make_svc(); all_on_break(svc); svc.start()
    assert wait_until(lambda: len(svc.snapshot().waiting) >= 3)
    cid = svc.snapshot().waiting[1].call_id
    assert svc.abandon_waiting(cid).ok
    assert cid not in [w.call_id for w in svc.snapshot().waiting]
    assert not svc.abandon_waiting(cid).ok
    assert svc.engine.stats["Abandoned"] == 1


def test_employee_operations_report_errors_not_exceptions(make_svc):
    svc = make_svc()
    assert svc.set_break("E1").ok
    assert not svc.set_break("E1").ok                       
    assert not svc.set_break("ZZZ").ok and "không tồn tại" in svc.set_break("ZZZ").message
    assert not svc.set_available("E2").ok                     
    assert svc.set_available("E1").ok
    assert not svc.add_employee("E1", "trùng").ok
    assert not svc.add_employee("bad id", "x").ok
    assert not svc.update_employee("ZZZ", "x").ok


def test_roster_file_is_updated_and_temporary_states_are_not_persisted(make_svc, tmp_path):
    svc = make_svc()
    assert svc.add_employee("E9", "Nhân viên Chín").ok
    svc.set_break("E1"); svc.set_offline("E2")
    loaded = {e.employee_id: e for e in load_employees(tmp_path / "employees.txt").employees}
    assert set(loaded) == {"E1", "E2", "E3", "E9"} and loaded["E9"].name == "Nhân viên Chín"
    assert loaded["E1"].status is S.AVAILABLE                
    assert loaded["E2"].status is S.OFFLINE                  


def test_new_employee_receives_waiting_calls(make_svc):
    svc = make_svc(n=1); all_on_break(svc); svc.start()
    assert wait_until(lambda: len(svc.snapshot().waiting) >= 2)
    svc.add_employee("N1", "Mới")
    assert wait_until(lambda: svc.engine.stats["Success"] + svc.engine.stats["Miss"] + svc.engine.stats["Break"] > 0, 5)


# ------------------------------------------------------------ Snapshot / Loop power
def test_snapshots_are_throttled_and_callback_errors_do_not_stop_loop(make_svc):
    svc = make_svc(n=5, max_call=10)
    snaps = []
    svc.on_snapshot.append(lambda s: (_ for _ in ()).throw(RuntimeError("GUI lỗi")))
    svc.on_snapshot.append(snaps.append)
    svc.start()
    time.sleep(1.0)
    assert 3 <= len(snaps) <= 14                             
    assert total(svc) > 5                                      


# ------------------------------------------------------------------ Replay / Record
def test_replay_mode_plays_whole_file_then_turns_generator_off(make_svc, tmp_path):
    f = tmp_path / "replay.txt"
    f.write_text("".join(f"09{i:08d}|{ev}|0.05\n" for i, ev in
                         enumerate(["Success", "Miss", "Break", "Success", "Success", "Miss", "Break"], 1)),
                 encoding="utf-8")
    svc = make_svc(source_mode="replay", replay_file=str(f))
    notices, states = [], []
    svc.on_notice.append(lambda k, m: notices.append(m)); svc.on_state.append(lambda r, g: states.append(g))
    svc.start()
    assert wait_until(lambda: total(svc) == 7, 6)
    assert wait_until(lambda: not svc.generator_enabled, 2)
    assert any("phát hết" in m for m in notices) and states[-1] is False
    assert svc.engine.stats["Miss"] == 2 and svc.engine.stats["Break"] == 2 and svc.engine.stats["Success"] == 3
    svc.set_generator_enabled(True)                         
    assert wait_until(lambda: total(svc) == 14, 6)


# --------------------------------------------------------------- Save & Export CSV
def test_results_are_persisted_and_exportable(make_svc, tmp_path):
    be = FakeBackend()
    p = PersistenceService(lambda: be, lambda: svc.employees(), retry_interval=0.05)
    svc = make_svc(persistence=p, n=40, min_interval=0.1, max_interval=0.1, max_call=2); p.start(); svc.start()
    assert wait_until(lambda: total(svc) >= 20, 6)
    svc.set_generator_enabled(False)
    assert wait_until(lambda: not svc.engine.queues.calling and not svc.engine.processor.active_calls(), 8)
    assert p.wait_idle(3) and len(be.saved) == total(svc)
    res = svc.export_day().result(timeout=5)
    assert res.path.name.startswith("calls-") and res.rows == len(be.saved) and res.path.parent == tmp_path / "out"
    assert len(res.path.read_text(encoding="utf-8-sig").splitlines()) == res.rows + 1


def test_export_fails_cleanly_when_database_unavailable(make_svc):
    svc = make_svc()                                           
    with pytest.raises(ExportError, match="PostgreSQL"):
        svc.export_day().result(timeout=3)


def test_shutdown_stops_all_threads(make_svc):
    svc = make_svc(); svc.start()
    gen = svc._generator
    wait_until(lambda: total(svc) >= 1)
    svc.shutdown()
    assert not svc._thread.is_alive() and not gen.is_alive()
