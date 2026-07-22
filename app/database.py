import logging
import sqlite3
import time
from contextlib import contextmanager

from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

log = logging.getLogger(__name__)

from app.config import settings

settings.ensure_dirs()


def _connect_with_retry(attempts: int = 8, delay_s: float = 0.25) -> sqlite3.Connection:
    """Docker Desktop's Windows bind-mount occasionally makes sqlite fail with "unable
    to open database file" for no real reason - a transient file-access race, not a
    genuine problem with the DB file itself (the very next attempt, moments later,
    succeeds). Seen in practice specifically when several scheduler jobs fire on the
    same tick and each opens its own connection at once.

    sqlite3.connect() itself does NOT touch the file - sqlite opens it lazily on the
    first real statement. So retrying only around connect() (the original version of
    this function) never actually caught the race: connect() always "succeeded", and
    the OperationalError instead surfaced later and uncaught, from inside SQLAlchemy's
    "connect" event listener (_set_sqlite_pragma's `PRAGMA journal_mode=WAL`), by which
    point it was no longer inside this retry loop. Executing a real statement here -
    the same PRAGMA _set_sqlite_pragma will otherwise be the first to run - forces the
    lazy open (and the race) to happen inside the loop, where a failure discards the
    connection and retries with a fresh one, matching the retry/backoff pattern already
    used for the analogous Windows file-lock race in downloader.py's
    _replace_with_retry()."""
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(attempts):
        conn = None
        try:
            conn = sqlite3.connect(str(settings.db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            if conn is not None:
                conn.close()
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(delay_s)
            continue
        return conn
    raise last_exc


engine = create_engine("sqlite://", creator=_connect_with_retry)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _sql_default_literal(column) -> str | None:
    """Renders a column's Python-level scalar default as a SQL literal, so ALTER TABLE
    ADD COLUMN backfills existing rows correctly instead of leaving them NULL (SQLite
    always permits NULL on an ALTER-added column regardless of the model's intent)."""
    default = getattr(column, "default", None)
    if default is None or not getattr(default, "is_scalar", False):
        return None
    value = default.arg
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _migrate_missing_columns() -> None:
    """Lightweight additive migration: add columns that exist on the model but not
    yet on the live table. New tables are handled separately by create_all()."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table in SQLModel.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue
            col_type = column.type.compile(dialect=engine.dialect)
            default_literal = _sql_default_literal(column)
            default_clause = f" DEFAULT {default_literal}" if default_literal is not None else ""
            log.info("Migrating table %s: adding column %s %s%s", table.name, column.name, col_type, default_clause)
            with engine.begin() as conn:
                conn.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}{default_clause}')
                )


def _backfill_null_defaults() -> None:
    """Older ALTER-added columns (from before this migration included a DEFAULT clause)
    can still hold NULL in rows that predate them, even though the model declares them
    non-nullable with a concrete default (e.g. is_correction, is_duplicate_match). Any
    row left with NULL there is invisible to `== False`/`== True` filters elsewhere in
    the app, so self-heal it on every startup rather than requiring a manual fix."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table in SQLModel.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        for column in table.columns:
            if column.nullable:
                continue
            default_literal = _sql_default_literal(column)
            if default_literal is None:
                continue
            with engine.begin() as conn:
                result = conn.execute(
                    text(
                        f'UPDATE "{table.name}" SET "{column.name}" = {default_literal} '
                        f'WHERE "{column.name}" IS NULL'
                    )
                )
                if result.rowcount:
                    log.info(
                        "Backfilled %d row(s) with NULL %s.%s -> %s",
                        result.rowcount,
                        table.name,
                        column.name,
                        default_literal,
                    )


def init_db() -> None:
    import app.models  # noqa: F401  ensure models are registered

    _migrate_missing_columns()
    SQLModel.metadata.create_all(engine)
    _backfill_null_defaults()


def get_session() -> Session:
    """FastAPI dependency: yields a session per-request."""
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Session:
    """Context manager for use outside request scope (scheduler jobs, services)."""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
