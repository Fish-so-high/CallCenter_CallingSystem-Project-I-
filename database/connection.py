from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Optional, TypeVar

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from config.settings import DatabaseSettings

log = logging.getLogger(__name__)
T = TypeVar("T")

_NON_RETRYABLE_PREFIXES = ("28", "3D", "42")


class DatabaseUnavailableError(RuntimeError):
    """PostgreSQL không kết nối được hoặc mất kết nối sau khi đã thử lại."""


def _sqlstate(exc: BaseException) -> Optional[str]:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


def _is_retryable(exc: BaseException) -> bool:
    state = _sqlstate(exc)
    return not (state and state.startswith(_NON_RETRYABLE_PREFIXES))


def describe_error(exc: BaseException) -> str:
    orig = getattr(exc, "orig", exc)
    return str(orig).strip().splitlines()[0] if str(orig).strip() else exc.__class__.__name__


class Database:
    def __init__(self, engine: Engine, *, retry_attempts: int = 3, retry_delay: float = 0.5,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self.retry_attempts = max(1, retry_attempts)
        self.retry_delay = retry_delay
        self._sleep = sleep

    # ------------------------------------------------------------------ tạo
    @classmethod
    def from_settings(cls, settings: DatabaseSettings, **kwargs) -> "Database":
        return cls(create_db_engine(settings.sqlalchemy_url(), connect_timeout=settings.connect_timeout), **kwargs)

    # ------------------------------------------------------------- phiên làm việc
    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        session = self._sessions()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def run(self, operation: Callable[[Session], T], *, attempts: Optional[int] = None) -> T:
        attempts = attempts or self.retry_attempts
        for attempt in range(1, attempts + 1):
            try:
                with self.session_scope() as session:
                    return operation(session)
            except (OperationalError, InterfaceError) as exc:
                retryable = _is_retryable(exc)
                log.warning("Lỗi CSDL (lần %d/%d)%s: %s", attempt, attempts,
                            "" if retryable else " - không thử lại", describe_error(exc))
                if not retryable or attempt == attempts:
                    raise DatabaseUnavailableError(
                        f"PostgreSQL không khả dụng: {describe_error(exc)}") from exc
                self._sleep(self.retry_delay * attempt)
        raise AssertionError("unreachable")  # pragma: no cover

    # --------------------------------------------------------------- tiện ích
    def check_connection(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:
            log.warning("Kiểm tra kết nối PostgreSQL thất bại: %s", describe_error(exc))
            return False

    def close(self) -> None:
        self.engine.dispose()


def create_db_engine(url: URL, *, connect_timeout: int = 5, **kwargs) -> Engine:
    return create_engine(
        url, pool_pre_ping=True, pool_size=5, max_overflow=5,
        connect_args={"connect_timeout": connect_timeout}, **kwargs,
    )
