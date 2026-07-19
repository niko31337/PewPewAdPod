from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.database import _backfill_null_defaults, _migrate_missing_columns


def test_alter_added_column_backfills_default_instead_of_leaving_null(monkeypatch, tmp_path):
    import app.database as database_module
    import app.models  # noqa: F401 - ensure SQLModel.metadata knows about AdSegment etc.

    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(database_module, "engine", engine)

    # Simulate an "old" schema: create the adsegment table without the newer
    # is_correction/is_duplicate_match columns, then insert a row the way old code would
    # have - this mirrors a real pre-migration database.
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE adsegment (
                    id INTEGER PRIMARY KEY,
                    episode_id INTEGER NOT NULL,
                    start_ms INTEGER NOT NULL,
                    end_ms INTEGER NOT NULL,
                    confidence FLOAT NOT NULL,
                    matched_keywords VARCHAR NOT NULL,
                    matched_jingles VARCHAR NOT NULL,
                    transcript_snippet VARCHAR NOT NULL,
                    source VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO adsegment (id, episode_id, start_ms, end_ms, confidence, matched_keywords, "
                "matched_jingles, transcript_snippet, source, status, created_at) "
                "VALUES (1, 1, 0, 1000, 0.9, '[]', '[]', '', 'keyword', 'pending', '2026-01-01 00:00:00')"
            )
        )

    _migrate_missing_columns()
    _backfill_null_defaults()

    with Session(engine) as session:
        row = session.exec(text("SELECT is_correction, is_duplicate_match FROM adsegment WHERE id = 1")).first()
        # must be backfilled to the model's real default (False), not left as NULL -
        # NULL would make `.where(AdSegment.is_correction == False)` silently exclude
        # this row everywhere in the app (review page, stale-segment cleanup, ...).
        assert row[0] == 0
        assert row[1] == 0
