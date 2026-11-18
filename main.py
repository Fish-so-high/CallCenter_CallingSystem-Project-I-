from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from datetime import datetime

from config.runtime import RuntimeConfig
from config.settings import BASE_DIR, ConfigError, load_settings
from config.timeutils import get_timezone
from core.employee_manager import load_employees
from services import AutomationService, PersistenceService, SimulationService
from services.persistence import build_backend

LOG_DIR = BASE_DIR / "logs"
EMPLOYEES_FILE = BASE_DIR / "data" / "employees.txt"
INPUT_TESTCASE = BASE_DIR / "data" / "input_testcase.txt"
SCHEDULER_STATE = BASE_DIR / "data" / "scheduler_state.json"


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    handlers = []
    fh = logging.handlers.RotatingFileHandler(LOG_DIR / "callcenter.log", maxBytes=5_000_000, backupCount=5,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    handlers.append(fh)
    if sys.stderr is not None:                
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        handlers.append(console)
    logging.basicConfig(level=logging.INFO, handlers=handlers)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def selftest(settings, tz) -> int:
    from database.migrate import discover
    log = logging.getLogger("callcenter.selftest")
    problems = []
    try:
        migrations = discover()
        if not migrations:
            problems.append("không tìm thấy file migration (*.sql)")
        log.info("migration: %s", [m.version for m in migrations])
    except Exception as exc:
        problems.append(f"migration: {exc}")
    log.info("múi giờ %s, bây giờ: %s", settings.timezone, datetime.now(tz).isoformat(timespec="seconds"))
    try:
        import psycopg  # noqa: F401
        import sqlalchemy.dialects.postgresql.psycopg  # noqa: F401
    except Exception as exc:
        problems.append(f"thư viện PostgreSQL: {exc}")
    from database import Database
    db = Database.from_settings(settings.database)
    try:
        log.info("kết nối PostgreSQL: %s", "OK" if db.check_connection() else "KHÔNG kết nối được (không tính là lỗi đóng gói)")
    finally:
        db.close()
    log.info("thư mục dữ liệu: %s", BASE_DIR)
    for p in problems:
        log.error("SELFTEST LỖI: %s", p)
    log.info("SELFTEST %s", "THẤT BẠI" if problems else "OK")
    return 1 if problems else 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "export":                     
        setup_logging()
        from storage.export_cli import main as export_main
        code = export_main(argv[1:])
        logging.getLogger("callcenter").info("export %s -> mã thoát %d", " ".join(argv[1:]), code)
        return code
    ap = argparse.ArgumentParser(prog="CallCenter")
    ap.add_argument("--selftest", action="store_true", help="Tự kiểm tra rồi thoát")
    args = ap.parse_args(argv)

    setup_logging()
    log = logging.getLogger("callcenter")
    try:
        settings = load_settings()
        tz = get_timezone(settings.timezone)
    except (ConfigError, ValueError) as exc:
        log.error("Cấu hình không hợp lệ: %s", exc)
        if not args.selftest:
            from PySide6.QtWidgets import QApplication, QMessageBox
            _app = QApplication(sys.argv)
            QMessageBox.critical(None, "CallCenter", f"Cấu hình không hợp lệ:\n{exc}")
        return 2
    if args.selftest:
        return selftest(settings, tz)

    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    from gui.controllers import AppController
    from gui.gc_guard import GuiGarbageCollector
    from gui.main_window import MainWindow
    from gui.theme import stylesheet
    app.setStyleSheet(stylesheet())
    gc_guard = GuiGarbageCollector(app)      

    loaded = load_employees(EMPLOYEES_FILE)
    config = RuntimeConfig.load()
    local_naive = lambda: datetime.now(tz).replace(tzinfo=None)
    service: SimulationService
    persistence = PersistenceService(lambda: build_backend(settings.database, tz), lambda: service.employees())
    service = SimulationService(config, loaded.employees, persistence, EMPLOYEES_FILE, clock=local_naive,
                                generator_record_path=INPUT_TESTCASE)
    automation = AutomationService(persistence, service, tz, SCHEDULER_STATE)
    controller = AppController(service, automation)
    window = MainWindow(controller)

    persistence.start()
    service.start_loop()
    automation.start()
    window.show()
    if loaded.template_created or not loaded.employees:
        controller.notice.emit("warning", "Chưa có nhân viên nào. Hãy khai báo trong data/employees.txt hoặc mục “Danh sách nhân viên”.")
    for err in loaded.errors[:3]:
        controller.notice.emit("warning", f"employees.txt: {err}")
    log.info("CallCenter đã khởi động (dữ liệu tại %s)", BASE_DIR)
    code = app.exec()
    gc_guard.stop()
    log.info("CallCenter đã dừng")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
