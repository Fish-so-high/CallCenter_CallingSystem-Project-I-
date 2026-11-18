import csv
from datetime import date, datetime, timedelta

import pytest

from config.runtime import RuntimeConfig
from core.models import CallEvent, Employee, IncomingCall
from services import AutomationService, PersistenceService, SimulationService
from storage.scheduler import TaskStatus
from tests.conftest import VN
from tests.helpers import FakeBackend, wait_until

D = date(2026, 10, 29)
NEXT = date(2026, 10, 30)


def at(day, h=0, m=0, s=0):
    return datetime(day.year, day.month, day.day, h, m, s, tzinfo=VN)


def naive(dt):
    return dt.replace(tzinfo=None)


class Env:
    def __init__(self, tmp_path, state=None, **cfg):
        self.be = FakeBackend()
        self.tmp = tmp_path
        self.holder = {}
        self.persistence = PersistenceService(lambda: self.be, lambda: self.holder["svc"].employees(), retry_interval=0.05)
        base = dict(export_dir=str(tmp_path / "out"))
        base.update(cfg)
        self.svc = SimulationService(RuntimeConfig(**base), [Employee("E1", "a"), Employee("E2", "b")],
                                     self.persistence, None, config_path=None)
        self.holder["svc"] = self.svc
        self.auto = AutomationService(self.persistence, self.svc, VN, state or tmp_path / "state.json",
                                      retry_delay=60, max_retries=5, startup_grace=0)
        for name in ("daily-export", "day-rollover"):          
            if self.auto.scheduler.state.get(name) is None:
                self.auto.scheduler.state.set(name, D - timedelta(days=1))
        self.notices, self.new_days = [], []
        self.auto.on_notice.append(lambda k, m: self.notices.append((k, m)))
        self.auto.on_new_day.append(self.new_days.append)
        self.persistence.start()

    def call(self, when, event=CallEvent.SUCCESS, dur=1.0):
        """Một cuộc gọi bắt đầu lúc `when` (giờ VN) và kết thúc sau `dur` giây."""
        n = naive(when)
        self.svc.engine.submit(IncomingCall(f"09{self.counter():08d}", event, dur, n), n)
        self.svc.engine.tick(n + timedelta(seconds=dur))

    _n = 0

    def counter(self):
        Env._n += 1
        return Env._n

    def csv_rows(self, day=D):
        p = self.tmp / "out" / f"calls-{day:%d-%m-%Y}.csv"
        with open(p, encoding="utf-8-sig", newline="") as f:
            return list(csv.reader(f))

    def stop(self):
        self.persistence.stop(timeout=2)


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.stop()


def test_full_day_cycle_export_at_1900_and_rollover_at_midnight(env):
    for i in range(6):
        env.call(at(D, 9, i))
    env.persistence.wait_idle(3)
    assert env.auto.scheduler.tick(at(D, 18, 59, 59)) == []
    res = env.auto.scheduler.tick(at(D, 19, 0, 0))
    assert [r.task for r in res] == ["daily-export"] and res[0].status is TaskStatus.OK
    assert len(env.csv_rows()) == 7                            
    assert ("success", "[Tự động] Xuất trong ngày: đã xuất 6 dòng ra calls-29-10-2026.csv (ngày 29/10/2026)") in env.notices

    for i in range(4):                                            
        env.call(at(D, 22, i))
    env.persistence.wait_idle(3)
    assert env.auto.scheduler.tick(at(D, 23, 59, 59)) == []
    stats_before = env.svc.engine.stats["total"]
    assert stats_before == 10

    res = env.auto.scheduler.tick(at(NEXT, 0, 0, 0))
    assert [r.task for r in res] == ["day-rollover"] and res[0].day == D
    assert env.new_days == [D]                                  
    assert env.svc.engine.stats["total"] == 0                     
    assert len(env.csv_rows()) == 11                             
    assert len(env.be.saved) == 10                                


def test_calls_after_midnight_belong_to_new_day_and_not_to_old_file(env):
    env.call(at(D, 23, 59, 58), dur=1.0)                         
    env.call(at(NEXT, 0, 0, 5), dur=1.0)                          
    env.persistence.wait_idle(3)
    env.auto.scheduler.tick(at(NEXT, 0, 0, 10))
    assert len(env.csv_rows(D)) == 2
    env.auto.scheduler.tick(at(NEXT, 19, 0, 0))
    assert len(env.csv_rows(NEXT)) == 2


def test_each_task_runs_once_across_many_days_with_long_running_app(env):
    t = at(D, 8, 0)
    for day_i in range(5):
        day = D + timedelta(days=day_i)
        env.call(at(day, 10, 0)); env.persistence.wait_idle(3)
        t = at(day, 8, 0)
        for minute in range(0, 24 * 60, 5):
            env.auto.scheduler.tick(t + timedelta(minutes=minute))
    env.auto.scheduler.tick(at(D + timedelta(days=5), 0, 1))
    assert env.new_days == [D + timedelta(days=i) for i in range(5)]
    assert len(list((env.tmp / "out").glob("calls-*.csv"))) == 5


def test_postgres_down_at_1900_retries_then_succeeds_with_single_error_toast(env):
    env.call(at(D, 10, 0)); env.persistence.wait_idle(3)
    env.be.alive = False
    r1 = env.auto.scheduler.tick(at(D, 19, 0, 0))
    assert r1[0].status is TaskStatus.FAILED and not r1[0].final
    assert not (env.tmp / "out").exists() or not list((env.tmp / "out").iterdir())
    env.auto.scheduler.tick(at(D, 19, 1, 0))                     
    env.be.alive = True
    assert wait_until(lambda: env.persistence.status().connected, 3)
    r3 = env.auto.scheduler.tick(at(D, 19, 2, 0))
    assert r3[0].status is TaskStatus.OK and len(env.csv_rows()) == 2
    errors = [m for k, m in env.notices if k == "error"]
    assert len(errors) == 1 and "Sẽ thử lại sau 60 giây" in errors[0]   


def test_postgres_down_at_midnight_still_opens_new_day_and_exports_when_back(env):
    env.call(at(D, 10, 0)); env.persistence.wait_idle(3)
    env.auto.scheduler.tick(at(D, 19, 0))
    env.be.alive = False
    env.auto.scheduler.tick(at(NEXT, 0, 0, 1))
    assert env.new_days == [D]                                    
    env.be.alive = True
    assert wait_until(lambda: env.persistence.status().connected, 3)
    env.auto.scheduler.tick(at(NEXT, 0, 1, 5))
    assert env.new_days == [D]                                    
    assert env.auto.scheduler.state.get("day-rollover") == D


def test_app_was_closed_at_1900_runs_catch_up_on_next_start(tmp_path):
    e = Env(tmp_path)
    try:
        e.call(at(D, 10, 0)); e.persistence.wait_idle(3)
        res = e.auto.scheduler.tick(at(D, 21, 30))               
        assert len(res) == 1 and res[0].catch_up
        assert any("[Tự động, bù]" in m for _, m in e.notices)
        assert e.new_days == []                                  
    finally:
        e.stop()


def test_restart_with_same_state_file_does_not_export_again(tmp_path):
    e1 = Env(tmp_path)
    e1.call(at(D, 10, 0)); e1.persistence.wait_idle(3)
    e1.auto.scheduler.tick(at(D, 19, 0))
    first = (tmp_path / "out" / "calls-29-10-2026.csv").stat().st_mtime_ns
    e1.stop()
    e2 = Env(tmp_path, state=tmp_path / "state.json")
    try:
        assert e2.auto.scheduler.tick(at(D, 20, 0)) == []
        assert (tmp_path / "out" / "calls-29-10-2026.csv").stat().st_mtime_ns == first
    finally:
        e2.stop()


def test_disabled_in_settings_does_nothing(tmp_path):
    e = Env(tmp_path, auto_schedule_enabled=False)
    try:
        e.call(at(D, 10, 0)); e.persistence.wait_idle(3)
        assert e.auto.scheduler.tick(at(D, 19, 5)) == [] and e.auto.scheduler.tick(at(NEXT, 0, 5)) == []
        assert not (tmp_path / "out").exists() and e.new_days == []
        assert "tắt" in e.auto.summary()
    finally:
        e.stop()


def test_export_time_follows_settings_live(tmp_path):
    e = Env(tmp_path, auto_export_time="08:30")
    try:
        e.call(at(D, 8, 0)); e.persistence.wait_idle(3)
        assert e.auto.scheduler.tick(at(D, 8, 29)) == []
        assert len(e.auto.scheduler.tick(at(D, 8, 30))) == 1
        assert "08:30" in e.auto.summary()
    finally:
        e.stop()


def test_empty_day_creates_no_file_and_reports_nothing_noisy(env):
    env.auto.scheduler.tick(at(D, 19, 0))
    env.auto.scheduler.tick(at(NEXT, 0, 0, 1))
    assert not (env.tmp / "out").exists()
    assert [k for k, _ in env.notices if k in ("success", "error")] == []
    assert env.new_days == [D]


def test_details_text_lists_next_runs_and_open_app_warning(env):
    env.auto.scheduler.tick(at(D, 19, 0))
    text = env.auto.details()
    assert "ứng dụng phải đang mở" in text.lower() and "Xuất CSV" in text and "Phân kỳ ngày" in text


def test_startup_waits_for_postgres_before_running_schedule(tmp_path):
    clock = {"m": 0.0}
    e = Env(tmp_path)
    e.persistence.stop(timeout=1)                                   
    be = FakeBackend()
    holder = {}
    p2 = PersistenceService(lambda: (_ for _ in ()).throw(ConnectionError("chưa lên")), lambda: [], retry_interval=0.05)
    auto = AutomationService(p2, e.svc, VN, tmp_path / "s2.json", startup_grace=15, monotonic=lambda: clock["m"])
    notices = []
    auto.on_notice.append(lambda k, m: notices.append((k, m)))
    auto.scheduler.state.set("daily-export", D - timedelta(days=1)); auto.scheduler.state.set("day-rollover", D - timedelta(days=1))
    p2.start()
    assert auto.scheduler.tick(at(D, 20, 0)) == [] and notices == []       
    clock["m"] = 14.9
    assert auto.scheduler.tick(at(D, 20, 0)) == []
    clock["m"] = 15.0                                                       
    res = auto.scheduler.tick(at(D, 20, 0, 5))
    assert res and all(r.status is TaskStatus.FAILED for r in res)
    assert any(k == "error" for k, _ in notices)                            
    p2.stop(timeout=1)


def test_schedule_runs_as_soon_as_postgres_connects_even_within_grace(tmp_path):
    e = Env(tmp_path)
    try:
        e.call(at(D, 10, 0)); e.persistence.wait_idle(3)
        e.auto.startup_grace = 3600
        e.auto._t0 = e.auto._mono()
        assert wait_until(lambda: e.persistence.status().connected, 3)
        res = e.auto.scheduler.tick(at(D, 19, 1))
        assert res and res[0].status is TaskStatus.OK
    finally:
        e.stop()
