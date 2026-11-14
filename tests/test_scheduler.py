import json
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone

import pytest

from storage.scheduler import (
    DailyScheduler, JobOutcome, RunContext, ScheduledTask, SchedulerState, TaskResult, TaskStatus,
)
from tests.conftest import VN
from tests.helpers import wait_until

D = date(2026, 10, 29)


def at(day, h=0, m=0, s=0):
    return datetime(day.year, day.month, day.day, h, m, s, tzinfo=VN)


class Jobs:
    """Ghi lại từng lần chạy; có thể cấu hình để lỗi / bỏ qua theo kịch bản."""

    def __init__(self, fail_times=0, status=TaskStatus.OK):
        self.calls = []
        self.fail_times = fail_times
        self.status = status

    def __call__(self, ctx: RunContext) -> JobOutcome:
        self.calls.append(ctx)
        if len(self.calls) <= self.fail_times:
            raise RuntimeError("PostgreSQL chưa sẵn sàng")
        return JobOutcome(self.status, f"xong {ctx.day}", None, 5)

    @property
    def days(self):
        return [c.day for c in self.calls]


def make(job=None, state=None, **kw):
    job = job or Jobs()
    export = ScheduledTask("daily-export", lambda: dtime(19, 0), 0, job)
    sch = DailyScheduler([export], VN, state or SchedulerState(), **kw)
    return sch, job


def make_two(**kw):
    ex, ro = Jobs(), Jobs()
    tasks = [ScheduledTask("daily-export", lambda: dtime(19, 0), 0, ex),
             ScheduledTask("day-rollover", lambda: dtime(0, 0), 1, ro)]
    return DailyScheduler(tasks, VN, SchedulerState(), **kw), ex, ro


# ------------------------------------------------------------------ đúng giờ, đúng một lần
def test_export_runs_at_1900_exactly_once_per_day():
    sch, job = make()
    sch.state.set("daily-export", D - timedelta(days=1))
    assert sch.tick(at(D, 18, 59, 59)) == [] and job.calls == []
    res = sch.tick(at(D, 19, 0, 0))
    assert len(res) == 1 and res[0].status is TaskStatus.OK and job.days == [D]
    for minute in range(1, 300, 7):                       # nhiều lần tick sau đó trong cùng ngày
        sch.tick(at(D, 19, 0) + timedelta(minutes=minute))
    assert job.days == [D]


def test_rollover_for_day_d_runs_at_midnight_of_next_day_with_day_d():
    sch, ex, ro = make_two()
    sch.state.set("day-rollover", D - timedelta(days=1)); sch.state.set("daily-export", D)
    sch.tick(at(D, 23, 59, 59)); assert ro.calls == []
    sch.tick(at(D + timedelta(days=1), 0, 0, 0))
    assert ro.days == [D] and ro.calls[0].due_at == at(D + timedelta(days=1))
    assert not ro.calls[0].catch_up


def test_two_tasks_are_independent():
    sch, ex, ro = make_two(retry_delay=60)
    ex.fail_times = 99
    sch.state.set("daily-export", D - timedelta(days=1)); sch.state.set("day-rollover", D - timedelta(days=1))
    sch.tick(at(D, 19, 0))                                 # export lỗi
    sch.tick(at(D + timedelta(days=1), 0, 0, 1))           # rollover vẫn chạy bình thường
    assert ro.days == [D] and len(ex.calls) >= 1
    assert sch.state.get("day-rollover") == D and sch.state.get("daily-export") == D - timedelta(days=1)


def test_long_run_exactly_one_run_per_day_no_duplicates():
    sch, ex, ro = make_two()
    t = at(date(2026, 1, 1), 0, 0, 0)
    sch.tick(t)
    end = t + timedelta(days=90)
    while t < end:
        t += timedelta(minutes=5)
        sch.tick(t)
    # 90 ngày liên tiếp: mỗi ngày đúng 1 lần, không trùng, không sót
    assert len(ex.days) == len(set(ex.days)) and len(ro.days) == len(set(ro.days))
    assert ex.days[-90:] == [date(2026, 1, 1) + timedelta(days=i) for i in range(90)][:len(ex.days[-90:])]
    assert len(ex.days) >= 89 and len(ro.days) >= 89
    assert ex.days == sorted(ex.days) and ro.days == sorted(ro.days)


def test_converts_any_timezone_to_configured_one():
    sch, job = make()
    sch.state.set("daily-export", D - timedelta(days=1))
    assert sch.tick(datetime(2026, 10, 29, 11, 59, tzinfo=timezone.utc)) == []        # 18:59 giờ VN
    assert len(sch.tick(datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc))) == 1     # 19:00 giờ VN


def test_task_time_is_read_each_tick_so_settings_apply_live():
    box = {"t": dtime(19, 0)}
    job = Jobs()
    sch = DailyScheduler([ScheduledTask("daily-export", lambda: box["t"], 0, job)], VN, SchedulerState())
    sch.state.set("daily-export", D - timedelta(days=1))
    sch.tick(at(D, 8, 0)); assert job.calls == []
    box["t"] = dtime(8, 30)
    sch.tick(at(D, 8, 29)); assert job.calls == []
    sch.tick(at(D, 8, 30)); assert job.days == [D]


# --------------------------------------------------------------- khởi động lại / trạng thái
def test_restart_does_not_rerun_finished_day(tmp_path):
    path = tmp_path / "state.json"
    sch, job = make(state=SchedulerState(path)); sch.state.set("daily-export", D - timedelta(days=1))
    sch.tick(at(D, 19, 5)); assert job.days == [D]
    sch2, job2 = make(state=SchedulerState(path))               # "khởi động lại ứng dụng"
    sch2.tick(at(D, 20, 0)); sch2.tick(at(D, 23, 0))
    assert job2.calls == []
    sch2.tick(at(D + timedelta(days=1), 19, 0)); assert job2.days == [D + timedelta(days=1)]
    assert json.loads(path.read_text())["last_done_day"]["daily-export"] == (D + timedelta(days=1)).isoformat()


def test_corrupt_or_unwritable_state_does_not_crash(tmp_path):
    bad = tmp_path / "state.json"; bad.write_text("{{{ không phải json", encoding="utf-8")
    st = SchedulerState(bad)
    assert st.get("daily-export") is None
    st.set("daily-export", D)                                   # ghi đè được
    assert SchedulerState(bad).get("daily-export") == D
    blocked = tmp_path / "file"; blocked.write_text("x")
    st2 = SchedulerState(blocked / "sub" / "state.json")        # không ghi được (cha là file)
    st2.set("daily-export", D)
    assert st2.get("daily-export") == D                         # vẫn nhớ trong bộ nhớ -> không chạy lặp trong phiên


def test_first_ever_run_only_runs_latest_due_day_no_backfill():
    sch, ex, ro = make_two()
    sch.tick(at(D, 20, 0))
    assert ex.days == [D] and ro.days == [D - timedelta(days=1)]


def test_clock_moved_backwards_does_not_rerun():
    sch, job = make(); sch.state.set("daily-export", D - timedelta(days=1))
    sch.tick(at(D, 19, 0)); assert job.days == [D]
    sch.tick(at(D, 12, 0)); sch.tick(at(D, 19, 30)); sch.tick(at(D, 18, 0)); sch.tick(at(D, 19, 1))
    assert job.days == [D]


# ---------------------------------------------------------------------- chạy bù
def test_catch_up_runs_missed_days_in_order_and_marks_late_runs():
    sch, job = make(); sch.state.set("daily-export", D - timedelta(days=3))
    res = sch.tick(at(D, 19, 0, 30))
    assert job.days == [D - timedelta(days=2), D - timedelta(days=1), D]
    assert [c.catch_up for c in job.calls] == [True, True, False]
    assert [r.day for r in res] == job.days


def test_catch_up_is_bounded():
    sch, job = make(max_catch_up_days=7); sch.state.set("daily-export", D - timedelta(days=60))
    sch.tick(at(D, 19, 0, 30))
    assert len(job.calls) == 7 and job.days[-1] == D and job.days[0] == D - timedelta(days=6)


def test_app_reopened_late_runs_missed_export_flagged_as_catch_up():
    sch, job = make(); sch.state.set("daily-export", D - timedelta(days=1))
    sch.tick(at(D, 21, 40))                                     # mở app lúc 21:40, đã trễ 2h40
    assert job.days == [D] and job.calls[0].catch_up is True


# ------------------------------------------------------------------- lỗi và thử lại
def test_failure_is_retried_after_delay_then_succeeds():
    sch, job = make(Jobs(fail_times=2), retry_delay=60); sch.state.set("daily-export", D - timedelta(days=1))
    r1 = sch.tick(at(D, 19, 0))
    assert r1[0].status is TaskStatus.FAILED and not r1[0].final and r1[0].attempt == 1
    assert sch.tick(at(D, 19, 0, 30)) == [] and len(job.calls) == 1            # chưa đến hạn thử lại
    r2 = sch.tick(at(D, 19, 1, 0)); assert r2[0].attempt == 2 and not r2[0].final
    r3 = sch.tick(at(D, 19, 2, 0)); assert r3[0].status is TaskStatus.OK and r3[0].attempt == 3 and r3[0].final
    assert sch.state.get("daily-export") == D
    assert sch.tick(at(D, 19, 5)) == [] and len(job.calls) == 3


def test_failed_day_blocks_later_days_until_resolved_keeping_order():
    job = Jobs(fail_times=1)
    sch, _ = make(job, retry_delay=60); sch.state.set("daily-export", D - timedelta(days=2))
    sch.tick(at(D, 19, 0, 30))                                  # ngày D-1 lỗi -> ngày D chờ
    assert job.days == [D - timedelta(days=1)]
    sch.tick(at(D, 19, 1, 40))
    assert job.days == [D - timedelta(days=1), D - timedelta(days=1), D]


def test_gives_up_after_max_retries_and_does_not_block_next_day():
    job = Jobs(fail_times=999)
    sch, _ = make(job, retry_delay=10, max_retries=3); sch.state.set("daily-export", D - timedelta(days=1))
    results = []
    for k in range(6):
        results += sch.tick(at(D, 19, 0) + timedelta(seconds=10 * k))
    assert [r.final for r in results] == [False, False, True] and len(job.calls) == 3
    assert "bỏ qua" in results[-1].message and sch.state.get("daily-export") == D
    job.fail_times = 0
    res = sch.tick(at(D + timedelta(days=1), 19, 0)); assert res[0].status is TaskStatus.OK
    assert job.days[-1] == D + timedelta(days=1)


def test_skipped_counts_as_done_and_is_not_retried():
    sch, job = make(Jobs(status=TaskStatus.SKIPPED)); sch.state.set("daily-export", D - timedelta(days=1))
    assert sch.tick(at(D, 19, 0))[0].status is TaskStatus.SKIPPED
    sch.tick(at(D, 19, 5)); assert len(job.calls) == 1


def test_disabled_scheduler_runs_nothing_and_resumes_later():
    box = {"on": False}
    sch, job = make(enabled=lambda: box["on"]); sch.state.set("daily-export", D - timedelta(days=1))
    assert sch.tick(at(D, 19, 30)) == [] and job.calls == []
    box["on"] = True
    sch.tick(at(D, 20, 0)); assert job.days == [D]


def test_result_handler_error_is_isolated():
    sch, job = make(); sch.state.set("daily-export", D - timedelta(days=1))
    seen = []
    sch.on_result.append(lambda r: 1 / 0)
    sch.on_result.append(seen.append)
    sch.tick(at(D, 19, 0))
    assert len(seen) == 1 and sch.last_results["daily-export"].day == D


def test_next_due_reports_future_time():
    sch, job = make(); sch.state.set("daily-export", D)
    assert sch.next_due(sch.tasks[0], at(D, 20, 0)) == at(D + timedelta(days=1), 19, 0)
    sch2, _ = make(); sch2.state.set("daily-export", D - timedelta(days=1))
    assert sch2.next_due(sch2.tasks[0], at(D, 10, 0)) == at(D, 19, 0)


# ----------------------------------------------------------------------- luồng nền
def test_background_thread_runs_job_once_and_stops_cleanly():
    now = {"t": at(D, 18, 59, 50)}
    job = Jobs()
    sch = DailyScheduler([ScheduledTask("daily-export", lambda: dtime(19, 0), 0, job)], VN, SchedulerState(),
                         clock=lambda: now["t"], poll_interval=0.01)
    sch.state.set("daily-export", D - timedelta(days=1))
    sch.start()
    time.sleep(0.1); assert job.calls == []
    now["t"] = at(D, 19, 0, 1)
    assert wait_until(lambda: len(job.calls) == 1)
    time.sleep(0.2); assert len(job.calls) == 1                # thread chạy liên tục nhưng không lặp
    names = {t.name for t in threading.enumerate()}
    assert "callflow-scheduler" in names
    sch.stop(); assert wait_until(lambda: "callflow-scheduler" not in {t.name for t in threading.enumerate()}, 2)
