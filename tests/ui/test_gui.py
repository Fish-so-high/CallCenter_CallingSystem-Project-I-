import threading
import time
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QThread, Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

from config.runtime import RuntimeConfig
from core.models import CallResult, CallStatus, CallTag, Employee, EmployeeStatus as S
from gui.controllers import AppController
from gui.dialogs.employee_dialog import EmployeeDialog, EmployeeForm
from gui.dialogs.export_dialog import ExportDialog
from gui.dialogs.settings_dialog import SettingsDialog
from gui.gc_guard import GuiGarbageCollector
from gui.main_window import MainWindow
from gui.theme import stylesheet
from gui.views.calling_queue_view import CallingQueueModel
from gui.views.history_view import HEADERS, HistoryModel
from core.models import WaitingCallView
from services import AutomationService, PersistenceService, SimulationService
from tests.conftest import VN
from tests.conftest import make_result
from tests.helpers import FakeBackend, wait_until


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(stylesheet())
    guard = GuiGarbageCollector(app)       
    yield app
    guard.stop()


class Collector(QObject):

    def __init__(self):
        super().__init__()
        self.snapshots, self.results, self.notices, self.states, self.exports = [], [], [], [], []
        self.thread_ids = set()

    def _mark(self):
        self.thread_ids.add(threading.get_ident())

    @Slot(object)
    def on_snapshot(self, s): self._mark(); self.snapshots.append(s)

    @Slot(object)
    def on_results(self, r): self._mark(); self.results.extend(r)

    @Slot(str, str)
    def on_notice(self, k, m): self._mark(); self.notices.append((k, m))

    @Slot(bool, bool)
    def on_state(self, r, g): self._mark(); self.states.append((r, g))

    @Slot(object, object)
    def on_export(self, res, err): self._mark(); self.exports.append((res, err))


@pytest.fixture
def make_gui(qapp, tmp_path):
    made = []

    def pump():
        qapp.processEvents()

    def _make(n=5, backend=None, factory=None, settle=True, automation=False, schedule_thread=False, **cfg_kw):
        base = dict(min_interval=0.1, max_interval=0.1, max_call=3, seed=1, export_dir=str(tmp_path / "out"))
        base.update(cfg_kw)
        be = backend or FakeBackend()
        emps = [Employee(f"NV{i:03d}", f"Nhân viên {i}") for i in range(1, n + 1)]
        holder = {}
        persistence = PersistenceService(factory or (lambda: be), lambda: holder["svc"].employees(),
                                         retry_interval=0.05)
        svc = SimulationService(RuntimeConfig(**base), emps, persistence, tmp_path / "employees.txt",
                                config_path=tmp_path / "settings.json", generator_record_path=tmp_path / "rec.txt")
        holder["svc"] = svc
        clock = {"now": datetime.now(VN)}
        auto = None
        if automation:
            auto = AutomationService(persistence, svc, VN, tmp_path / "state.json", clock=lambda: clock["now"],
                                     poll_interval=0.02, retry_delay=0.2, max_retries=5, startup_grace=0)
        ctl = AppController(svc, auto)
        win = MainWindow(ctl)
        win.resize(1360, 820)
        col = Collector()
        ctl.snapshot_ready.connect(col.on_snapshot); ctl.results_ready.connect(col.on_results)
        ctl.notice.connect(col.on_notice); ctl.state_changed.connect(col.on_state)
        ctl.export_finished.connect(col.on_export)
        persistence.start(); svc.start_loop(); win.show()
        if auto is not None and schedule_thread:
            auto.start()
        made.append(win)
        if settle:                                  
            wait_until(lambda: persistence.status().connected, 2.0, pump=pump)
            wait_until(lambda: len(win.toasts.toasts) >= 1, 1.0, pump=pump)
            for t in list(win.toasts.toasts):
                win.toasts.dismiss(t)
            col.notices.clear()
        return SimpleNamespace(win=win, ctl=ctl, svc=svc, be=be, col=col, persistence=persistence, pump=pump,
                               auto=auto, clock=clock,
                               wait=lambda cond, t=6.0: wait_until(cond, t, pump=pump), tmp=tmp_path)

    yield _make
    for w in made:
        w.close()
        w.deleteLater()
    qapp.processEvents()
    import gc; gc.collect()


def toast_texts(g):
    return [t.text() for t in g.win.toasts.toasts]


def has_toast(g, kind=None, contains=None):
    return any((kind is None or t.kind == kind) and (contains is None or contains in t.text())
               for t in g.win.toasts.toasts)


def history_tags(g):
    m = g.win.history_view.model
    return [m.data(m.index(r, 5)) for r in range(m.rowCount())]


def all_break(g):
    for e in g.svc.employees():
        assert g.ctl.employee_action(e.employee_id, "break").ok


# ========================================== Layer - Views
def test_initial_layout_matches_spec(make_gui):
    g = make_gui()
    w = g.win
    assert w.service_view.counter.text() == "Số nhân viên hoạt động: 5 / 5"
    assert w.calling_view.counter.text() == "Số cuộc gọi: 0"
    assert set(w.service_view.cards) == {f"NV{i:03d}" for i in range(1, 6)}
    assert all(c.state == "available" for c in w.service_view.cards.values())
    assert [w.history_view.model.headerData(i, Qt.Orientation.Horizontal) for i in range(6)] == list(HEADERS)
    assert HEADERS == ("Số điện thoại", "Nhân viên trả lời", "Thời gian nhận cuộc gọi",
                       "Thời gian cuộc gọi kết thúc", "Tổng thời gian xử lý", "Tag")
    sb = w.sidebar
    assert "Khởi chạy" in sb.run_button.text()
    assert sb.generator_button.isChecked()
    for b in (sb.export_button, sb.employees_button, sb.data_button, sb.settings_button):
        assert b.isEnabled()
    sg, cg = w.service_view.geometry(), w.calling_view.geometry()
    assert abs(sg.width() - cg.width()) <= 2 and sg.top() == cg.top()
    assert w.history_view.mapTo(w, w.history_view.rect().topLeft()).y() > cg.bottom() - 1


def test_available_cards_are_green_and_show_queue_position(make_gui):
    g = make_gui()
    cards = g.win.service_view.cards
    img = cards["NV001"].grab().toImage()
    px = img.pixelColor(img.width() - 6, img.height() - 8)
    assert (px.red(), px.green(), px.blue()) == (0x2f, 0xb3, 0x6a)
    assert "#1" in cards["NV001"].detail_label.text() and "#5" in cards["NV005"].detail_label.text()


# ============ Run
def test_run_updates_all_views_consistently(make_gui):
    g = make_gui(n=40, min_interval=0.15, max_interval=0.15, max_call=2)
    g.win.sidebar.run_button.click()
    assert g.ctl.running and g.wait(lambda: "Tạm dừng" in g.win.sidebar.run_button.text(), 2)
    assert g.wait(lambda: g.win.history_view.model.rowCount() >= 12)
    g.svc.set_generator_enabled(False)
    assert g.wait(lambda: not g.svc.snapshot().waiting and not g.svc.engine.processor.active_calls(), 10)
    g.wait(lambda: False, 0.4)
    last = g.col.snapshots[-1]
    w = g.win
    assert w.calling_view.counter.text() == f"Số cuộc gọi: {len(last.waiting)}"
    assert w.service_view.counter.text() == f"Số nhân viên hoạt động: {last.working_count} / {last.total_employees}"
    for v in last.employees:
        assert w.service_view.cards[v.employee_id].state == v.status.value.lower()
    total = last.stats["total"]
    assert w.history_view.model.total == total == w.history_view.model.rowCount()
    assert sum(int(lbl.text().replace(",", "")) for lbl in w.sidebar._stat_labels.values()) == total
    assert "kết nối" in w.sidebar.db_chip.text()


def test_busy_cards_are_red_with_phone_and_idle_cards_have_no_waiting_calls(make_gui):
    g = make_gui(min_interval=0.05, max_interval=0.05, max_call=6)
    g.win.sidebar.run_button.click()
    seen_busy = False
    for _ in range(60):
        g.wait(lambda: False, 0.05)
        busy = [c for c in g.win.service_view.cards.values() if c.state == "busy"]
        if busy:
            seen_busy = True
            assert all(len(c.detail_label.text()) == 10 and c.detail_label.text().isdigit() for c in busy)
            img = busy[0].grab().toImage(); px = img.pixelColor(img.width() - 6, img.height() - 8)
            assert (px.red(), px.green(), px.blue()) == (0xe5, 0x48, 0x4d)
            break
    assert seen_busy


def test_snapshot_invariant_holds_for_every_published_snapshot(make_gui):
    g = make_gui(min_interval=0.03, max_interval=0.03, max_call=8)
    g.win.sidebar.run_button.click()
    g.wait(lambda: len(g.col.snapshots) >= 25, 8)
    assert len(g.col.snapshots) >= 25
    for s in g.col.snapshots:
        assert not (s.waiting and s.service_queue)               
        phones = [e.current_phone for e in s.employees if e.current_phone]
        assert len(phones) == len(set(phones))                      
        assert {e.employee_id for e in s.employees if e.queue_position} == set(s.service_queue)
        busy = {e.employee_id for e in s.employees if e.status is S.BUSY}
        assert busy.isdisjoint(s.service_queue)


def test_pause_and_resume_buttons(make_gui):
    g = make_gui()
    btn = g.win.sidebar.run_button
    btn.click(); assert g.wait(lambda: g.win.history_view.model.rowCount() >= 3)
    btn.click(); g.pump()
    assert "Khởi chạy" in btn.text() and not g.ctl.running and "tạm dừng" in g.win.sidebar.state_label.text()
    g.wait(lambda: False, 0.5); n = g.win.history_view.model.total; g.wait(lambda: False, 0.6)
    assert g.win.history_view.model.total == n
    btn.click(); assert g.wait(lambda: g.win.history_view.model.total > n + 2)
    assert any("khởi chạy" in m.lower() or "tạm dừng" in m.lower() for _, m in g.col.notices + [("", t) for t in toast_texts(g)])


def test_generator_toggle_button_and_sync(make_gui):
    g = make_gui()
    g.win.sidebar.generator_button.click(); g.pump()
    assert not g.ctl.generator_enabled and not g.win.sidebar.generator_button.isChecked()
    g.ctl.set_generator_enabled(True); g.wait(lambda: g.win.sidebar.generator_button.isChecked(), 2)
    assert g.win.sidebar.generator_button.isChecked()


# =========================== Thread
def test_signals_are_delivered_on_gui_thread_but_produced_on_workers(make_gui):
    g = make_gui(min_interval=0.03, max_interval=0.03)
    worker_threads = set()
    g.svc.on_result.append(lambda r: worker_threads.add(threading.get_ident()))
    g.svc.on_snapshot.append(lambda s: worker_threads.add(threading.get_ident()))
    g.win.sidebar.run_button.click()
    g.wait(lambda: len(g.col.results) >= 10 and len(g.col.snapshots) >= 5)
    g.ctl.notice.emit("info", "x")
    g.persistence.on_event[0]("success", "y") if g.persistence.on_event else None
    g.wait(lambda: len(g.col.notices) >= 2, 2)
    main = threading.get_ident()
    assert worker_threads and main not in worker_threads           
    assert g.col.thread_ids == {main}                               
    assert QThread.currentThread() is QApplication.instance().thread()


def test_results_are_batched_not_one_signal_per_call(make_gui):
    g = make_gui(min_interval=0.02, max_interval=0.02, max_call=10)
    batches = []
    g.ctl.results_ready.connect(lambda b: batches.append(len(b)))
    g.win.sidebar.run_button.click()
    g.wait(lambda: sum(batches) >= 40, 8)
    assert max(batches) > 1 and len(batches) < sum(batches)


# ======================= Employee 
def test_context_menu_actions_change_card_state(make_gui):
    g = make_gui()
    sv = g.win.service_view
    sv.cards["NV002"].action_requested.emit("NV002", "break")
    assert g.wait(lambda: sv.cards["NV002"].state == "break", 3)
    assert g.win.service_view.counter.text().endswith("4 / 5") or g.wait(lambda: sv.counter.text().endswith("4 / 5"), 2)
    assert "Tạm nghỉ" in sv.cards["NV002"].status_label.text()
    sv.cards["NV002"].action_requested.emit("NV002", "available")
    assert g.wait(lambda: sv.cards["NV002"].state == "available", 3)
    sv.cards["NV003"].action_requested.emit("NV003", "offline")
    assert g.wait(lambda: sv.cards["NV003"].state == "offline", 3)
    assert sv.counter.text().endswith("/ 5") and "4 / 5" in sv.counter.text()
    assert any("quay lại" in t or "ngừng" in t for t in toast_texts(g))


def test_invalid_action_shows_warning_toast_and_does_not_crash(make_gui):
    g = make_gui()
    g.win.service_view.cards["NV001"].action_requested.emit("NV001", "available")  
    assert g.wait(lambda: has_toast(g, "warning"), 2)
    for t in list(g.win.toasts.toasts):
        g.win.toasts.dismiss(t)
    g.win._on_employee_action("KHONG_CO", "break")
    assert g.wait(lambda: has_toast(g, "warning", "không tồn tại"), 2)


def test_busy_employee_break_is_deferred_and_shown(make_gui):
    g = make_gui(n=1, min_interval=0.02, max_interval=0.02, max_call=5)
    g.win.sidebar.run_button.click()
    assert g.wait(lambda: g.win.service_view.cards["NV001"].state == "busy", 5)
    assert g.ctl.employee_action("NV001", "break").ok
    assert g.wait(lambda: g.win.service_view.cards["NV001"].state == "break", 6)   


def test_waiting_calls_listed_and_abandon_removes_and_records(make_gui):
    g = make_gui(min_interval=0.03, max_interval=0.03)
    all_break(g)
    g.win.sidebar.run_button.click()
    assert g.wait(lambda: g.win.calling_view.model.rowCount() >= 4)
    assert g.win.calling_view.counter.text() == f"Số cuộc gọi: {g.win.calling_view.model.rowCount()}"
    assert g.win.service_view._idle_empty.isVisibleTo(g.win.service_view)
    cid = g.col.snapshots[-1].waiting[0].call_id
    phone = g.col.snapshots[-1].waiting[0].phone_number
    g.win.calling_view.abandon_requested.emit(cid)
    assert g.wait(lambda: "Abandoned" in history_tags(g), 3)
    assert cid not in [w.call_id for w in g.svc.snapshot().waiting]
    m = g.win.history_view.model
    row = [r for r in range(m.rowCount()) if m.data(m.index(r, 5)) == "Abandoned"][0]
    assert m.data(m.index(row, 0)) == phone and m.data(m.index(row, 1)) == "—"


def test_queue_full_rejects_and_shows_rejected_rows(make_gui):
    g = make_gui(min_interval=0.02, max_interval=0.02, max_call=8, max_queue_size=4)
    all_break(g); g.win.sidebar.run_button.click()
    assert g.wait(lambda: "Rejected" in history_tags(g), 5)
    assert g.wait(lambda: g.win.calling_view.counter.text() == "Số cuộc gọi: 4", 3)
    assert g.wait(lambda: int(g.win.sidebar._stat_labels["Rejected"].text().replace(",", "")) > 0, 3)
    assert int(g.win.sidebar._stat_labels["Rejected"].text().replace(",", "")) > 0


# ====================== Toast
def test_toast_stacks_in_bottom_right_and_limits_visible(make_gui):
    g = make_gui(); tm = g.win.toasts
    for i in range(6):
        tm.show(f"Thông báo {i}", "info", 5000)
    assert len(tm.toasts) == 4 and [t.text() for t in tm.toasts] == [f"Thông báo {i}" for i in range(2, 6)]
    W, H = g.win.width(), g.win.height()
    for t in tm.toasts:
        r = t.geometry()
        assert r.right() <= W - 15 and r.bottom() <= H - 15 and r.left() >= 0 and r.top() >= 0
    ys = sorted(t.geometry().top() for t in tm.toasts)
    rects = [t.geometry() for t in sorted(tm.toasts, key=lambda t: t.geometry().top())]
    assert all(a.bottom() < b.top() for a, b in zip(rects, rects[1:]))        
    g.win.resize(1500, 900); g.pump()
    assert tm.toasts[-1].geometry().right() <= 1500 - 15 and tm.toasts[-1].geometry().bottom() <= 900 - 15


def test_long_toast_shows_all_its_text(make_gui):
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtGui import QFontMetrics
    g = make_gui(); tm = g.win.toasts
    msg = "PostgreSQL không khả dụng: connection failed: connection to server at 127.0.0.1, port 1 failed: Connection refused"
    t = tm.show(msg, "error", 5000)
    need = QFontMetrics(t.font()).boundingRect(0, 0, t.width() - 28, 10_000, int(_Qt.TextFlag.TextWordWrap), msg).height()
    assert t.height() >= need + 20
    short = tm.show("Ngắn", "info", 5000)
    assert short.height() < t.height()


def test_history_tag_column_fits_longest_bold_tag(make_gui):
    from PySide6.QtGui import QFont, QFontMetrics
    g = make_gui()
    g.win.history_view.add_results([make_result(CallTag.ABANDONED)])
    g.pump()
    bold = QFont(g.win.history_view.table.font()); bold.setBold(True)
    assert g.win.history_view.table.horizontalHeader().sectionSize(5) >= QFontMetrics(bold).horizontalAdvance("Abandoned") + 12


def test_toast_click_and_auto_dismiss_and_error_lasts_longer(make_gui):
    g = make_gui(); tm = g.win.toasts
    a = tm.show("tự tắt", "success", 120)
    b = tm.show("bấm để đóng", "info", 5000)
    e = tm.show("lỗi", "error", 120)
    assert g.wait(lambda: a not in tm.toasts, 2)
    assert e in tm.toasts                                                    
    b.on_close(b); g.pump()
    assert b not in tm.toasts


def test_notice_signal_uses_correct_argument_order(make_gui):
    g = make_gui()
    g.ctl.notice.emit("error", "Lỗi cụ thể XYZ")
    assert g.wait(lambda: has_toast(g, "error", "Lỗi cụ thể XYZ"), 2)
    t = [t for t in g.win.toasts.toasts if t.text() == "Lỗi cụ thể XYZ"][0]
    assert t.kind == "error" and "#c93a3f" in t.styleSheet()


# ============== Export csv
def test_export_confirm_then_creates_csv(make_gui, monkeypatch):
    g = make_gui(n=40, min_interval=0.1, max_interval=0.1, max_call=2)
    monkeypatch.setattr(ExportDialog, "exec", lambda self: 1)
    g.win.sidebar.run_button.click()
    assert g.wait(lambda: len(g.be.saved) >= 10)
    g.win.sidebar.export_button.click()
    assert g.wait(lambda: g.col.exports, 8)
    res, err = g.col.exports[0]
    assert err is None and res.written and res.path.exists() and res.path.parent == g.tmp / "out"
    lines = res.path.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].split(",")[0] == "Số điện thoại" and len(lines) - 1 == res.rows >= 10
    assert any("Đã xuất" in t for t in toast_texts(g))
    assert res.path.name == f"calls-{datetime.now():%d-%m-%Y}.csv"


def test_export_cancel_does_nothing(make_gui, monkeypatch):
    g = make_gui()
    monkeypatch.setattr(ExportDialog, "exec", lambda self: 0)
    g.win.sidebar.export_button.click(); g.wait(lambda: False, 0.4)
    assert g.col.exports == [] and not (g.tmp / "out").exists()


def test_export_dialog_preview_follows_selected_date(qapp, tmp_path):
    d = ExportDialog(tmp_path, date(2026, 10, 29))
    assert d.selected_day() == date(2026, 10, 29)
    assert d.target.text().endswith("calls-29-10-2026.csv")


def test_export_reports_error_when_database_down(make_gui, monkeypatch):
    def down():
        raise ConnectionError("refused")
    g = make_gui(factory=down, settle=False)
    monkeypatch.setattr(ExportDialog, "exec", lambda self: 1)
    g.win.sidebar.export_button.click()
    assert g.wait(lambda: g.col.exports, 5)
    assert g.col.exports[0][0] is None and "PostgreSQL" in g.col.exports[0][1]
    assert g.wait(lambda: any(t.kind == "error" and "Xuất CSV thất bại" in t.text() for t in g.win.toasts.toasts), 2)


def test_open_data_folder(make_gui, monkeypatch):
    g = make_gui(); opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: (opened.append(url.toLocalFile()), True)[1])
    g.win.sidebar.data_button.click(); g.pump()
    assert opened == [str(g.tmp / "out")] and (g.tmp / "out").is_dir()
    assert has_toast(g, "info", "Đã mở thư mục")
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: False)
    g.win.sidebar.data_button.click(); g.pump()
    assert has_toast(g, "error", "Không mở được")


# =========== Lost connect SQL
def test_database_outage_is_visible_and_simulation_continues(make_gui):
    g = make_gui(n=20, min_interval=0.05, max_interval=0.05, max_call=3)
    g.win.sidebar.run_button.click()
    assert g.wait(lambda: len(g.be.saved) >= 5 and "kết nối" in g.win.sidebar.db_chip.text())
    g.be.alive = False
    assert g.wait(lambda: "mất kết nối" in g.win.sidebar.db_chip.text(), 5)
    assert g.win.sidebar.db_chip.property("ok") is False
    before = g.win.history_view.model.total
    assert g.wait(lambda: g.win.history_view.model.total > before + 5 and "chờ ghi" in g.win.sidebar.db_chip.text(), 6)
    assert any(t.kind == "error" for t in g.win.toasts.toasts)        
    g.be.alive = True
    assert g.wait(lambda: "mất kết nối" not in g.win.sidebar.db_chip.text(), 8)
    assert g.wait(lambda: g.persistence.status().pending < 5 and "chờ ghi" not in g.win.sidebar.db_chip.text()
                  or g.persistence.status().pending == 0, 8)
    assert len({r.call_id for r in g.be.saved}) == len(g.be.saved)       


def test_database_down_at_startup_shows_error_but_app_works(make_gui):
    def down():
        raise ConnectionError("connection refused")
    g = make_gui(factory=down, settle=False)
    assert g.wait(lambda: "mất kết nối" in g.win.sidebar.db_chip.text(), 4)
    assert g.wait(lambda: any("PostgreSQL không khả dụng" in t for t in toast_texts(g)), 3)
    g.win.sidebar.run_button.click()
    assert g.wait(lambda: g.win.history_view.model.rowCount() >= 5)      
    assert g.wait(lambda: "chờ ghi" in g.win.sidebar.db_chip.text(), 3)


# ======================= Setting dialog
def test_settings_dialog_shows_unconfirmed_defaults_warning(qapp):
    d = SettingsDialog(RuntimeConfig(), 5)
    texts = [w.text() for w in d.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)]
    assert any("chưa được xác nhận" in t for t in texts)
    assert d.max_call.value() == 20 and d.threshold.value() == 50 and d.max_queue.value() == 100


def test_settings_invalid_values_keep_dialog_open_and_config_unchanged(make_gui):
    g = make_gui()
    d = SettingsDialog(g.ctl.config, 5, g.win); d.apply_callback = g.ctl.apply_config
    d.min_iv.setValue(5); d.max_iv.setValue(1); d.max_queue.setValue(10)
    d._accept()
    assert d.result() == 0 and "tối thiểu" in d.error.text()
    assert g.ctl.config.min_interval == 0.1 and g.svc.engine.queues.calling.capacity == 100
    d.use_seed.setChecked(False); d.min_iv.setValue(0.5); d.max_iv.setValue(1); d.mode.setCurrentIndex(1)
    d.replay.setText("khong_ton_tai.txt"); d._accept()
    assert d.result() == 0 and "file phát lại" in d.error.text()


def test_settings_apply_live_and_persist(make_gui, monkeypatch):
    g = make_gui()

    def fake_exec(self):
        self.max_queue.setValue(9); self.max_call.setValue(4); self.break_policy.setCurrentIndex(1)
        self.w_success.setValue(1); self.w_miss.setValue(0); self.w_break.setValue(0)
        self.use_seed.setChecked(True); self.seed.setValue(31)
        self._accept()
        return self.result()
    monkeypatch.setattr(SettingsDialog, "exec", fake_exec)
    g.win.sidebar.settings_button.click(); g.pump()
    assert g.svc.engine.queues.calling.capacity == 9 and g.svc.engine.config.break_release_policy.value == "to_break"
    assert g.ctl.config.seed == 31 and g.svc._gen_cfg.max_call == 4
    assert RuntimeConfig.load(g.tmp / "settings.json").max_queue_size == 9
    assert has_toast(g, "success", "cài đặt")

    all_events = []
    g.svc.on_result.append(lambda r: all_events.append(r.tag))
    g.win.sidebar.run_button.click()
    g.wait(lambda: len(all_events) >= 10)
    assert {t for t in all_events if t is not CallTag.REJECTED} == {CallTag.SUCCESS}


# =========================== Employee Dialog
def test_employee_dialog_lists_and_edits(make_gui, monkeypatch):
    g = make_gui()
    notes = []
    d = EmployeeDialog(g.ctl, lambda k, m: notes.append((k, m)), g.win)
    assert d.table.rowCount() == 5 and d.table.item(0, 0).text() == "NV001" and d.table.item(0, 2).text() == "Rảnh"
    d.break_btn.click()
    assert notes[-1][0] == "warning"                                  
    d.table.selectRow(1); d.break_btn.click()
    assert g.wait(lambda: d.table.item(1, 2).text() == "Tạm nghỉ", 3) and notes[-1][0] == "success"
    d.back_btn.click()
    assert g.wait(lambda: d.table.item(1, 2).text() == "Rảnh", 3)
    d.table.selectRow(2); d.off_btn.click()
    assert g.wait(lambda: d.table.item(2, 2).text() == "Offline", 3)
    # mở file employees.txt
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: True)
    g.svc._save_roster(); d.open_btn.click(); assert notes[-1][0] == "info"
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: False)
    d.open_btn.click(); assert notes[-1][0] == "error"
    d.accept()
    g.svc.set_break("NV004"); g.wait(lambda: False, 0.3)              


def test_employee_form_add_edit_validation(make_gui):
    g = make_gui()
    f = EmployeeForm(g.ctl, parent=g.win)
    f.id_edit.setText("NV001"); f.name_edit.setText("Trùng"); f._submit()
    assert f.result() == 0 and "đã tồn tại" in f.error.text()
    f.id_edit.setText("co khoang"); f._submit(); assert f.result() == 0 and "không hợp lệ" in f.error.text()
    f.id_edit.setText("NV100"); f.name_edit.setText("Trần Văn Mới"); f._submit()
    assert f.result() == 1 and "NV100" in f.message
    assert g.wait(lambda: "NV100" in g.win.service_view.cards, 3)
    assert g.win.service_view.counter.text().endswith("/ 6")
    e = EmployeeForm(g.ctl, "NV100", "Tên sửa", g.win)
    assert not e.id_edit.isEnabled(); e.name_edit.setText("Tên đã sửa"); e._submit()
    assert e.result() == 1 and {x.employee_id: x.name for x in g.ctl.employees()}["NV100"] == "Tên đã sửa"


# =========================== Model & Responsive
def test_history_model_caps_rows_keeps_newest_first_and_is_fast(qapp):
    m = HistoryModel()
    rs = [make_result(CallTag.SUCCESS, "E1", ended=datetime(2026, 10, 29, 10, 0, i % 60), duration=1) for i in range(6000)]
    t = time.perf_counter()
    m.add_results(rs[:1000]); m.add_results(rs[1000:])
    assert time.perf_counter() - t < 1.0
    assert m.rowCount() == 5000 and m.total == 6000
    assert m.data(m.index(0, 0)) == rs[-1].phone_number              
    assert m.data(m.index(0, 5)) == "Success" and m.data(m.index(0, 4)) == "1.00 s"
    assert m.columnCount() == 6


def test_calling_model_does_not_reset_when_unchanged(qapp):
    m = CallingQueueModel(); now = datetime.now()
    items = [WaitingCallView(f"id{i}", f"09{i:08d}", now) for i in range(3)]
    assert m.set_items(items) is True and m.rowCount() == 3
    assert m.set_items(list(items)) is False
    assert m.set_items(items[1:]) is True and m.rowCount() == 2
    assert m.data(m.index(0, 0)).strip().startswith("1.")


@pytest.mark.parametrize("size", [(1100, 700), (1366, 768), (1920, 1080), (900, 600)])
def test_window_resizes_without_clipping_core_widgets(make_gui, size):
    g = make_gui(n=20)
    g.win.resize(*size); g.pump(); g.wait(lambda: False, 0.2)
    w = g.win
    assert w.width() >= 1100 and w.height() >= 700                     
    for v in (w.service_view, w.calling_view, w.history_view, w.sidebar):
        assert v.isVisible() and v.width() > 100 and v.height() > 100
    assert not w.grab().isNull()
  
    ys = {c.y() for c in w.service_view.cards.values()}
    assert len(ys) >= 1


def test_closing_window_stops_background_threads(make_gui):
    g = make_gui(); g.win.sidebar.run_button.click()
    g.wait(lambda: g.svc.engine.stats["total"] >= 3)
    gen = g.svc._generator
    g.win.close(); g.pump()
    assert g.wait(lambda: not g.svc._thread.is_alive() and not gen.is_alive(), 3)
    assert not g.persistence._thread.is_alive()
    assert not g.ctl._flush_timer.isActive()


# ====================== Automatic Scheduler
def _today():
    return datetime.now().date()


def test_sidebar_shows_schedule_summary_and_tooltip(make_gui):
    g = make_gui(automation=True)
    assert g.win.sidebar.schedule_label.text() == "Tự động: xuất 19:00 · phân kỳ 00:00"
    tip = g.win.sidebar.schedule_label.toolTip()
    assert "đang mở" in tip and "Xuất CSV" in tip and "Phân kỳ ngày" in tip


def _at(day, h=0, m=0, s=0):
    return datetime(day.year, day.month, day.day, h, m, s, tzinfo=VN)


def test_midnight_rollover_clears_table_and_stats_and_drops_late_old_day_results(make_gui):
    from datetime import timedelta
    g = make_gui(n=40, automation=True, min_interval=0.15, max_interval=0.15, max_call=2)
    today, yesterday = _today(), _today() - timedelta(days=1)
    g.auto.scheduler.state.set("day-rollover", yesterday - timedelta(days=1))
    g.auto.scheduler.state.set("daily-export", today)
    g.win.sidebar.run_button.click()
    assert g.wait(lambda: g.win.history_view.model.rowCount() >= 6)
    g.svc.set_generator_enabled(False)

    assert g.wait(lambda: not g.svc.snapshot().waiting and not g.svc.engine.processor.active_calls(), 10)
    g.wait(lambda: len(g.col.results) == g.win.history_view.model.total, 3)
    g.wait(lambda: False, 0.3)
    assert g.win.sidebar.schedule_label.text().startswith("Tự động")
    g.auto.scheduler.tick(_at(today, 0, 0, 1))                      
    assert g.wait(lambda: g.win.history_view.model.rowCount() == 0, 3)
    assert g.win.history_view.counter.text() == "0 cuộc gọi"
    assert g.wait(lambda: all(l.text() == "0" for l in g.win.sidebar._stat_labels.values()), 3)
    assert g.wait(lambda: has_toast(g, "info", "Đã sang ngày mới"), 3)

    g.ctl._result_in.emit(make_result(CallTag.SUCCESS, "NV001", ended=datetime.combine(yesterday, datetime.min.time()).replace(hour=23, minute=59, second=59)))
    g.ctl._result_in.emit(make_result(CallTag.SUCCESS, "NV001", ended=datetime.now()))
    assert g.wait(lambda: g.win.history_view.model.rowCount() == 1, 3)
    g.wait(lambda: False, 0.3)
    assert g.win.history_view.model.rowCount() == 1


def test_scheduler_thread_notifies_gui_on_gui_thread_and_exports_csv(make_gui):
    from datetime import timedelta
    g = make_gui(automation=True, schedule_thread=True)
    day = _today()
    g.auto.scheduler.state.set("daily-export", day - timedelta(days=1))
    g.auto.scheduler.state.set("day-rollover", day - timedelta(days=1))
    for i in range(3):
        g.be.calls.save(make_result(CallTag.SUCCESS, "NV001", ended=datetime.combine(day, datetime.min.time()).replace(hour=10, minute=0, second=i)))
    g.clock["now"] = _at(day, 19, 0, 1)                             
    assert g.wait(lambda: has_toast(g, "success", "[Tự động]"), 5)
    csv = g.tmp / "out" / f"calls-{day:%d-%m-%Y}.csv"
    assert csv.exists() and len(csv.read_text(encoding="utf-8-sig").splitlines()) == 4
    assert g.wait(lambda: "gần nhất" in g.win.sidebar.schedule_label.toolTip(), 3)
    assert g.col.thread_ids == {threading.get_ident()}               
    n = len([t for t in g.win.toasts.toasts if "[Tự động]" in t.text()])
    g.clock["now"] = _at(day, 19, 30)
    g.wait(lambda: False, 0.4)
    assert len([t for t in g.win.toasts.toasts if "[Tự động]" in t.text()]) == n     


def test_failure_toast_is_shown_once_when_database_is_down(make_gui):
    from datetime import timedelta
    g = make_gui(automation=True, schedule_thread=True)
    day = _today()
    g.auto.scheduler.state.set("daily-export", day - timedelta(days=1))
    g.auto.scheduler.state.set("day-rollover", day - timedelta(days=1))
    g.be.calls.save(make_result(CallTag.SUCCESS, "NV001", ended=datetime.combine(day, datetime.min.time()).replace(hour=9)))
    g.be.alive = False
    g.clock["now"] = _at(day, 19, 0, 1)
    assert g.wait(lambda: has_toast(g, "error", "Sẽ thử lại"), 5)
    for _ in range(3):                                              
        g.clock["now"] += timedelta(seconds=0.3)
        g.wait(lambda: False, 0.1)
    attempts = max(a for a, _ in g.auto.scheduler._attempts.values())
    assert attempts >= 3                                            
    assert len([t for t in g.win.toasts.toasts if "Sẽ thử lại" in t.text()]) == 1     
    g.be.alive = True
    csv = g.tmp / "out" / f"calls-{day:%d-%m-%Y}.csv"
    for _ in range(40):
        g.clock["now"] += timedelta(seconds=0.3)
        if g.wait(lambda: csv.exists(), 0.15):
            break
    assert csv.exists()


def test_settings_schedule_tab_applies_live_and_updates_sidebar(make_gui, monkeypatch):
    from PySide6.QtCore import QTime
    g = make_gui(automation=True)

    def exec_off(self):
        self.auto_enabled.setChecked(False); self._accept(); return self.result()
    monkeypatch.setattr(SettingsDialog, "exec", exec_off)
    g.win.sidebar.settings_button.click(); g.pump()
    assert g.win.sidebar.schedule_label.text() == "Lịch tự động: đang tắt"
    assert RuntimeConfig.load(g.tmp / "settings.json").auto_schedule_enabled is False

    def exec_on(self):
        self.auto_enabled.setChecked(True); self.export_time.setTime(QTime(8, 30)); self._accept(); return self.result()
    monkeypatch.setattr(SettingsDialog, "exec", exec_on)
    g.win.sidebar.settings_button.click(); g.pump()
    assert g.win.sidebar.schedule_label.text() == "Tự động: xuất 08:30 · phân kỳ 00:00"
    d = SettingsDialog(g.ctl.config, 5)
    assert d.export_time.time().toString("HH:mm") == "08:30" and d.auto_enabled.isChecked()
    texts = " ".join(w.text() for w in d.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel))
    assert "00:00" in texts and "ứng dụng đang mở" in texts


def test_closing_window_stops_scheduler_thread(make_gui):
    g = make_gui(automation=True, schedule_thread=True)
    assert any(t.name == "callflow-scheduler" for t in threading.enumerate())
    g.win.close(); g.pump()
    assert g.wait(lambda: not any(t.name == "callflow-scheduler" for t in threading.enumerate()), 3)
