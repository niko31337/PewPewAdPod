from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.models import Episode, EpisodeStatus, Feed
from app.routers import feeds as feeds_router


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(feeds_router.router)

    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    return TestClient(app, follow_redirects=False), engine


def _feed_with_review_episode(session, *, title: str, auto_cut: bool):
    feed = Feed(title=title, original_rss_url=f"https://example.test/{title}.xml", auto_cut=auto_cut)
    session.add(feed)
    session.commit()
    session.refresh(feed)
    episode = Episode(
        feed_id=feed.id,
        guid=f"{title}-episode",
        title=f"{title} episode",
        original_audio_url=f"https://example.test/{title}.mp3",
        status=EpisodeStatus.PENDING_REVIEW,
    )
    session.add(episode)
    session.commit()
    session.refresh(episode)
    return feed.id, episode.id


def test_enabling_auto_cut_requeues_pending_review_for_single_feed():
    client, engine = _client()
    with Session(engine) as session:
        feed_id, episode_id = _feed_with_review_episode(session, title="single", auto_cut=False)

    response = client.post(f"/feeds/{feed_id}/toggle-auto-cut")
    assert response.status_code == 303
    assert response.headers["location"] == f"/feeds/{feed_id}?requeued=1"

    with Session(engine) as session:
        assert session.get(Feed, feed_id).auto_cut is True
        assert session.get(Episode, episode_id).status == EpisodeStatus.REANALYZE


def test_bulk_enable_auto_cut_requeues_reviews_including_already_enabled_feeds():
    client, engine = _client()
    with Session(engine) as session:
        disabled_id, disabled_episode_id = _feed_with_review_episode(session, title="disabled", auto_cut=False)
        enabled_id, enabled_episode_id = _feed_with_review_episode(session, title="enabled", auto_cut=True)

    response = client.post(
        "/feeds/bulk-action",
        data={"action": "enable_auto_cut", "feed_ids": [str(disabled_id), str(enabled_id)]},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/?requeued=2"

    with Session(engine) as session:
        assert session.get(Feed, disabled_id).auto_cut is True
        assert session.get(Episode, disabled_episode_id).status == EpisodeStatus.REANALYZE
        assert session.get(Episode, enabled_episode_id).status == EpisodeStatus.REANALYZE


def test_bulk_review_backlog_skips_feeds_without_auto_cut():
    client, engine = _client()
    with Session(engine) as session:
        enabled_id, enabled_episode_id = _feed_with_review_episode(session, title="enabled", auto_cut=True)
        disabled_id, disabled_episode_id = _feed_with_review_episode(session, title="disabled", auto_cut=False)

    response = client.post(
        "/feeds/bulk-action",
        data={"action": "reprocess_review", "feed_ids": [str(enabled_id), str(disabled_id)]},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/?requeued=1"

    with Session(engine) as session:
        assert session.get(Episode, enabled_episode_id).status == EpisodeStatus.REANALYZE
        assert session.get(Episode, disabled_episode_id).status == EpisodeStatus.PENDING_REVIEW
