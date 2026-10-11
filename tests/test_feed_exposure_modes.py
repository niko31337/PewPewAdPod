from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.models import Episode, EpisodeStatus, Feed
from app.routers import feeds as feeds_router
from app.routers import public_feed


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(feeds_router.router)
    app.include_router(public_feed.router)

    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    return TestClient(app), engine


def _seed_master_entries(session):
    preferred = Feed(title="Preferred", original_rss_url="https://example.test/preferred.xml", preferred=True)
    normal = Feed(title="Normal", original_rss_url="https://example.test/normal.xml", preferred=False)
    session.add_all([preferred, normal])
    session.commit()
    session.refresh(preferred)
    session.refresh(normal)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session.add_all([
        Episode(feed_id=normal.id, guid="normal", title="Normal newer", original_audio_url="https://x/normal.mp3",
                status=EpisodeStatus.PUBLISHED, processed_audio_path="/data/normal.mp3", processed_size_bytes=10, pubdate=base + timedelta(days=10)),
        Episode(feed_id=preferred.id, guid="preferred", title="Preferred older", original_audio_url="https://x/preferred.mp3",
                status=EpisodeStatus.PUBLISHED, processed_audio_path="/data/preferred.mp3", processed_size_bytes=10, pubdate=base),
    ])
    session.commit()
    return preferred, normal


def test_master_feed_preferred_first_when_mode_selected():
    client, engine = _client()
    with Session(engine) as session:
        preferred, normal = _seed_master_entries(session)
        from app.models import AppConfig
        session.add(AppConfig(id=1, feed_exposure_mode="master_preferred"))
        session.commit()

    response = client.get("/feed/master.xml")
    assert response.status_code == 200
    assert response.text.index("Preferred older") < response.text.index("Normal newer")


def test_toggle_preferred_persists():
    client, engine = _client()
    with Session(engine) as session:
        feed = Feed(title="Feed", original_rss_url="https://example.test/feed.xml")
        session.add(feed)
        session.commit()
        session.refresh(feed)
        feed_id = feed.id

    response = client.post(f"/feeds/{feed_id}/toggle-preferred")
    assert response.status_code == 303

    with Session(engine) as session:
        feed = session.get(Feed, feed_id)
        assert feed.preferred is True


def test_bulk_action_updates_multiple_feeds():
    client, engine = _client()
    with Session(engine) as session:
        feeds = [
            Feed(title="One", original_rss_url="https://example.test/one.xml", auto_cut=False),
            Feed(title="Two", original_rss_url="https://example.test/two.xml", auto_cut=True),
        ]
        session.add_all(feeds)
        session.commit()
        ids = [feed.id for feed in feeds]

    response = client.post("/feeds/bulk-action", data={"action": "set_preferred", "feed_ids": [str(i) for i in ids]})
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    with Session(engine) as session:
        for feed_id in ids:
            assert session.get(Feed, feed_id).preferred is True
