from pathlib import Path

import pytest

from config import ConfigError, DatabaseSettings, load_settings
from config.settings import BASE_DIR, DEFAULT_EXPORT_DIR


def test_defaults_contain_no_secret():
    s = DatabaseSettings()
    assert (s.host, s.port, s.name) == ("localhost", 5432, "callflow_db")
    assert s.password == ""


def test_from_env_reads_values():
    s = DatabaseSettings.from_env({"DB_HOST": "db", "DB_PORT": "6543", "DB_NAME": "x", "DB_USER": "u",
                                   "DB_PASSWORD": "p", "DB_CONNECT_TIMEOUT": "9"})
    assert (s.host, s.port, s.name, s.user, s.password, s.connect_timeout) == ("db", 6543, "x", "u", "p", 9)


@pytest.mark.parametrize("env", [{"DB_PORT": "abc"}, {"DB_PORT": "0"}, {"DB_PORT": "70000"},
                                 {"DB_CONNECT_TIMEOUT": "0"}, {"DB_NAME": ""}])
def test_invalid_env_raises(env):
    with pytest.raises(ConfigError):
        DatabaseSettings.from_env(env)


def test_password_never_shown_in_repr_or_url_string():
    s = DatabaseSettings(password="s3cret!")
    assert "s3cret" not in repr(s)
    assert "s3cret" not in str(s.sqlalchemy_url())
    assert "s3cret!" in s.sqlalchemy_url().render_as_string(hide_password=False).replace("%21", "!")


def test_url_uses_psycopg_driver():
    assert DatabaseSettings().sqlalchemy_url().drivername == "postgresql+psycopg"


def test_load_settings_defaults_and_relative_export_dir():
    st = load_settings({}, use_dotenv=False)
    assert st.export_dir == DEFAULT_EXPORT_DIR and st.timezone == "Asia/Ho_Chi_Minh"
    st = load_settings({"CALLFLOW_EXPORT_DIR": "out/csv"}, use_dotenv=False)
    assert st.export_dir == BASE_DIR / "out" / "csv"
    # st = load_settings({"CALLFLOW_EXPORT_DIR": str(Path("/tmp/abs"))}, use_dotenv=False)
    # assert st.export_dir == Path("/tmp/abs")


def test_invalid_timezone_raises():
    with pytest.raises(ConfigError):
        load_settings({"CALLFLOW_TIMEZONE": "Mars/Olympus"}, use_dotenv=False)


def test_env_example_has_no_real_password():
    text = (BASE_DIR / ".env.example").read_text(encoding="utf-8")
    assert "DB_PASSWORD=\n" in text or text.rstrip().endswith("DB_PASSWORD=")
