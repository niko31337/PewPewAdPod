from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.models import Episode, EpisodeStatus, Feed
from app.routers import queue


def _make_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    app = FastAPI()
    app.include_router(queue.router)

    def _override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    return TestClient(app), engine


def _make_feed(session, title="Feed"):
    feed = Feed(title=title, original_rss_url=f"https://example.test/{title}.xml")
    session.add(feed)
    session.commit()
    session.refresh(feed)
    return feed


def test_queue_page_lists_queued_episodes_in_processing_order():
    client, engine = _make_client()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with Session(engine) as session:
        feed = _make_feed(session)
        newer = Episode(
            feed_id=feed.id, guid="g1", title="Newer", original_audio_url="https://example.test/1.mp3",
            status=EpisodeStatus.NEW, created_at=base + timedelta(minutes=5),
        )
        older = Episode(
            feed_id=feed.id, guid="g2", title="Older", original_audio_url="https://example.test/2.mp3",
            status=EpisodeStatus.NEW, created_at=base,
        )
        not_queued = Episode(
            feed_id=feed.id, guid="g3", title="Already published", original_audio_url="https://example.test/3.mp3",
            status=EpisodeStatus.PUBLISHED, created_at=base,
        )
        session.add_all([newer, older, not_queued])
        session.commit()

    response = client.get("/queue")

    assert response.status_code == 200
    body = response.text
    assert "Older" in body and "Newer" in body
    assert "Already published" not in body
    # process_queue_job claims the oldest created_at first - the listing must match
    assert body.index("Older") < body.index("Newer")


def test_skip_episode_removes_it_from_the_queue():
    client, engine = _make_client()
    with Session(engine) as session:
        feed = _make_feed(session)
        episode = Episode(
            feed_id=feed.id, guid="g1", title="Old backlog episode", original_audio_url="https://example.test/1.mp3",
            status=EpisodeStatus.NEW,
        )
        session.add(episode)
        session.commit()
        session.refresh(episode)
        episode_id = episode.id

    response = client.post(f"/episodes/{episode_id}/skip", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/queue"
    with Session(engine) as session:
        episode = session.get(Episode, episode_id)
        assert episode.status == EpisodeStatus.SKIPPED

    listing = client.get("/queue")
    assert "Old backlog episode" not in listing.text


def test_skip_unknown_episode_redirects_without_error():
    client, _ = _make_client()

    response = client.post("/episodes/999999/skip", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/queue"
