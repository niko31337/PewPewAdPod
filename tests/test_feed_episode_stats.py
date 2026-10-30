from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import Episode, EpisodeStatus, Feed
from app.routers.feeds import _get_feed_episode_stats


def _make_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _episode(feed_id, guid, status, *, heard_elsewhere=False):
    return Episode(
        feed_id=feed_id,
        guid=guid,
        title=guid,
        original_audio_url=f"https://example.test/{guid}.mp3",
        status=status,
        heard_elsewhere=heard_elsewhere,
    )


def test_feed_episode_stats_classify_statuses_and_exclude_heard_tombstones():
    engine = _make_db()
    with Session(engine) as session:
        feed = Feed(title="Stats", original_rss_url="https://example.test/stats.xml")
        session.add(feed)
        session.commit()
        session.refresh(feed)

        session.add_all([
            _episode(feed.id, "published", EpisodeStatus.PUBLISHED),
            _episode(feed.id, "failed", EpisodeStatus.FAILED_ANALYZE),
            _episode(feed.id, "review", EpisodeStatus.PENDING_REVIEW),
            _episode(feed.id, "new", EpisodeStatus.NEW),
            _episode(feed.id, "skipped", EpisodeStatus.SKIPPED),
            _episode(feed.id, "heard", EpisodeStatus.PUBLISHED, heard_elsewhere=True),
        ])
        session.commit()

        stats = _get_feed_episode_stats(session, [feed.id])

    assert stats[feed.id] == {
        "total": 5,
        "published": 1,
        "errors": 1,
        "remaining": 2,
        "skipped": 1,
    }


def test_feed_episode_stats_returns_zeroes_for_empty_feed():
    engine = _make_db()
    with Session(engine) as session:
        feed = Feed(title="Empty", original_rss_url="https://example.test/empty.xml")
        session.add(feed)
        session.commit()
        session.refresh(feed)

        stats = _get_feed_episode_stats(session, [feed.id])

    assert stats[feed.id] == {
        "total": 0,
        "published": 0,
        "errors": 0,
        "remaining": 0,
        "skipped": 0,
    }
