from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from sqlalchemy.engine import URL

from .timeutils import DEFAULT_TIMEZONE, get_timezone


def _app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = _app_base_dir()
DEFAULT_EXPORT_DIR = BASE_DIR / "data" / "raw_data"

class ConfigError(ValueError):
    pass

@dataclass(frozen = True)
class DatabaseSettings:
    host: str = "localhost"
    port: int = 5432
    name: str = "callfrow_db"
    user: str = "callflow"
    password: str = field(default = "", repr = False) # Hiding Password
    connect_timeout: int = 5
    
    def __post_init__(self) -> None:
        if not(1 <= int(self.port) <= 65535):
            raise ConfigError(f"DB_HOST Không hợp lệ: {self.port}")
        if int(self.connect_timeout) < 1:
            raise ConfigError("DB_CONNECT_TIMEOUT phải >= 1")
        if not self.name:
            raise ConfigError("DB_NAME trống")
    
    def sqlalchemy_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg", username=self.user, password=self.password or None,
            host=self.host, port=int(self.port), database=self.name
        )
        
    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "DatabaseSettings":
        env = os.environ if env is None else env
        try:
            port = int(env.get("DB_PORT", "5432"))
            timeout = int(env.get("DB_CONNECT_TIMEOUT", "5"))
        except ValueError as exc:
            raise ConfigError(f"DB_PORT / DB_CONNECT_TIMEOUT phải là số nguyên: {exc}") from exc
        return cls(
            host=env.get("DB_HOST", "localhost"),
            port=port,
            name=env.get("DB_NAME", "callflow_db"),
            user=env.get("DB_USER", "callflow"),
            password=env.get("DB_PASSWORD", ""),
            connect_timeout=timeout,
        )
        
@dataclass(frozen=True)
class AppSettings:
    database: DatabaseSettings
    export_dir: Path = DEFAULT_EXPORT_DIR
    timezone: str = DEFAULT_TIMEZONE
    
    def __post_init__(self) -> None:
        try:
            get_timezone(self.timezone)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        
def load_settings(env: Optional[Mapping[str, str]] = None, *, use_dotenv: bool = True) -> AppSettings:
    if env is None and use_dotenv:
        try:
            from dotenv import load_dotenv
            load_dotenv(BASE_DIR / ".env", override = False)
        except ImportError:
            pass
    source = os.environ if env is None else env
    export_dir = Path(source.get("CALLFLOW_EXPORT_DIR") or DEFAULT_EXPORT_DIR)
    if not export_dir.is_absolute():
        export_dir = BASE_DIR / export_dir
    return AppSettings(
        database=DatabaseSettings.from_env(source),
        export_dir=export_dir,
        timezone=source.get("CALLFLOW_TIMEZONE", DEFAULT_TIMEZONE)
    )