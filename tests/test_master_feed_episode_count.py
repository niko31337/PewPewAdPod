from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.models import AppConfig, Episode, EpisodeStatus, Feed
from app.routers import public_feed


def _make_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    app = FastAPI()
    app.include_router(public_feed.router)

    def _override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    return TestClient(app), engine


def _seed_feed_with_episodes(session, n, episodes_per_podcast=None):
    if episodes_per_podcast is not None:
        session.add(AppConfig(id=1, master_feed_episodes_per_podcast=episodes_per_podcast))

    feed = Feed(title="Feed", original_rss_url="https://example.test/feed.xml")
    session.add(feed)
    session.commit()
    session.refresh(feed)

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(n):
        session.add(
            Episode(
                feed_id=feed.id,
                guid=f"g{i}",
                title=f"Episode {i}",
                original_audio_url=f"https://example.test/{i}.mp3",
                status=EpisodeStatus.PUBLISHED,
                processed_audio_path=f"/data/audio/processed/{i}.mp3",
                processed_size_bytes=100,
                pubdate=base + timedelta(days=i),  # higher i = newer
            )
        )
    session.commit()
    return feed


def test_master_feed_defaults_to_one_episode_per_podcast():
    client, engine = _make_client()
    with Session(engine) as session:
        _seed_feed_with_episodes(session, n=3)  # no AppConfig row - default applies

    response = client.get("/feed/master.xml")

    assert response.status_code == 200
    assert response.text.count("<item>") == 1
    assert "Episode 2" in response.text  # the newest by pubdate
    assert "Episode 1" not in response.text
    assert "Episode 0" not in response.text


def test_master_feed_respects_configured_episode_count():
    client, engine = _make_client()
    with Session(engine) as session:
        _seed_feed_with_episodes(session, n=5, episodes_per_podcast=3)

    response = client.get("/feed/master.xml")

    assert response.status_code == 200
    assert response.text.count("<item>") == 3
    assert "Episode 4" in response.text
    assert "Episode 3" in response.text
    assert "Episode 2" in response.text
    assert "Episode 1" not in response.text
    assert "Episode 0" not in response.text


def test_master_feed_configured_count_larger_than_available_episodes_returns_all():
    client, engine = _make_client()
    with Session(engine) as session:
        _seed_feed_with_episodes(session, n=2, episodes_per_podcast=10)

    response = client.get("/feed/master.xml")

    assert response.text.count("<item>") == 2


def test_master_feed_skips_episodes_missing_processed_audio_without_shrinking_the_budget():
    client, engine = _make_client()
    with Session(engine) as session:
        session.add(AppConfig(id=1, master_feed_episodes_per_podcast=2))
        feed = Feed(title="Feed", original_rss_url="https://example.test/feed.xml")
        session.add(feed)
        session.commit()
        session.refresh(feed)

        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # newest episode has no cached audio (evicted) - must not count against the
        # per-podcast budget and hide an older, still-playable episode.
        session.add_all(
            [
                Episode(
                    feed_id=feed.id, guid="g0", title="Oldest, has audio",
                    original_audio_url="https://example.test/0.mp3", status=EpisodeStatus.PUBLISHED,
                    processed_audio_path="/data/audio/processed/0.mp3", processed_size_bytes=100,
                    pubdate=base,
                ),
                Episode(
                    feed_id=feed.id, guid="g1", title="Middle, has audio",
                    original_audio_url="https://example.test/1.mp3", status=EpisodeStatus.PUBLISHED,
                    processed_audio_path="/data/audio/processed/1.mp3", processed_size_bytes=100,
                    pubdate=base + timedelta(days=1),
                ),
                Episode(
                    feed_id=feed.id, guid="g2", title="Newest, evicted audio",
                    original_audio_url="https://example.test/2.mp3", status=EpisodeStatus.PUBLISHED,
                    processed_audio_path=None, processed_size_bytes=None,
                    pubdate=base + timedelta(days=2),
                ),
            ]
        )
        session.commit()

    response = client.get("/feed/master.xml")

    assert response.text.count("<item>") == 2
    assert "Middle, has audio" in response.text
    assert "Oldest, has audio" in response.text
    assert "Newest, evicted audio" not in response.text
