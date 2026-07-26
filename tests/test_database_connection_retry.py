import sqlite3

import pytest

from app.database import _connect_with_retry


def test_connect_succeeds_immediately_when_unlocked(tmp_path, monkeypatch):
    import app.database as database_module

    monkeypatch.setattr(database_module.settings, "db_path", tmp_path / "app.db")

    conn = _connect_with_retry(attempts=3, delay_s=0)
    conn.execute("SELECT 1")
    conn.close()


def test_connect_retries_then_succeeds_after_transient_lock_on_connect(tmp_path, monkeypatch):
    """The file-open race can, in principle, also surface directly from connect()."""
    import app.database as database_module

    monkeypatch.setattr(database_module.settings, "db_path", tmp_path / "app.db")

    calls = {"count": 0}
    real_connect = sqlite3.connect

    def flaky_connect(path, check_same_thread=False):
        calls["count"] += 1
        if calls["count"] < 3:
            raise sqlite3.OperationalError("unable to open database file")
        return real_connect(path, check_same_thread=check_same_thread)

    monkeypatch.setattr(sqlite3, "connect", flaky_connect)

    conn = _connect_with_retry(attempts=5, delay_s=0)

    assert calls["count"] == 3
    conn.execute("SELECT 1")
    conn.close()


class _FlakyExecuteConnection:
    """Wraps a real sqlite3.Connection, injecting failures into .execute() only.
    sqlite3.Connection is a C type and its methods can't be monkeypatched directly
    (they're immutable), so composition stands in for the real object here."""

    def __init__(self, real_conn, calls, fail_until):
        self._real = real_conn
        self._calls = calls
        self._fail_until = fail_until

    def execute(self, *args, **kwargs):
        self._calls["count"] += 1
        if self._calls["count"] < self._fail_until:
            raise sqlite3.OperationalError("unable to open database file")
        return self._real.execute(*args, **kwargs)

    def close(self):
        self._real.close()


def test_connect_retries_then_succeeds_after_transient_lock_on_first_statement(tmp_path, monkeypatch):
    """The real-world failure mode: sqlite3.connect() itself succeeds (it opens the
    file lazily), and the race instead surfaces on the first statement executed
    against the connection - which is exactly what _connect_with_retry now runs
    (a PRAGMA) before handing the connection back, so the retry loop must catch
    failures there too, not just around connect()."""
    import app.database as database_module

    monkeypatch.setattr(database_module.settings, "db_path", tmp_path / "app.db")

    calls = {"count": 0}
    real_connect = sqlite3.connect

    def fake_connect(path, check_same_thread=False):
        real_conn = real_connect(path, check_same_thread=check_same_thread)
        return _FlakyExecuteConnection(real_conn, calls, fail_until=3)

    monkeypatch.setattr(sqlite3, "connect", fake_connect)

    conn = _connect_with_retry(attempts=5, delay_s=0)

    assert calls["count"] == 3
    conn.execute("SELECT 1")
    conn.close()


def test_connect_gives_up_and_raises_after_exhausting_attempts(tmp_path, monkeypatch):
    import app.database as database_module

    monkeypatch.setattr(database_module.settings, "db_path", tmp_path / "app.db")

    calls = {"count": 0}
    real_connect = sqlite3.connect

    def fake_connect(path, check_same_thread=False):
        real_conn = real_connect(path, check_same_thread=check_same_thread)
        return _FlakyExecuteConnection(real_conn, calls, fail_until=float("inf"))

    monkeypatch.setattr(sqlite3, "connect", fake_connect)

    with pytest.raises(sqlite3.OperationalError):
        _connect_with_retry(attempts=3, delay_s=0)
