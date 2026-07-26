from datetime import datetime, timedelta, timezone

from sqlmodel import Session, SQLModel, create_engine

from app.models import Episode, EpisodeStatus, Feed
from app.routers.episodes import _queue_positions


def _make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make_feed(session):
    feed = Feed(title="Feed", original_rss_url="https://example.test/feed.xml")
    session.add(feed)
    session.commit()
    session.refresh(feed)
    return feed


def test_queue_positions_orders_new_and_reanalyze_by_created_at():
    # Mirrors process_queue_job's own query (scheduler.py) exactly, so a shown
    # position always matches what will actually get claimed next.
    session = _make_session()
    feed = _make_feed(session)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    first = Episode(
        feed_id=feed.id, guid="g1", title="e1", original_audio_url="https://example.test/1.mp3",
        status=EpisodeStatus.NEW, created_at=base,
    )
    second = Episode(
        feed_id=feed.id, guid="g2", title="e2", original_audio_url="https://example.test/2.mp3",
        status=EpisodeStatus.REANALYZE, created_at=base + timedelta(minutes=1),
    )
    third = Episode(
        feed_id=feed.id, guid="g3", title="e3", original_audio_url="https://example.test/3.mp3",
        status=EpisodeStatus.NEW, created_at=base + timedelta(minutes=2),
    )
    session.add_all([third, first, second])  # insertion order deliberately scrambled
    session.commit()
    for ep in (first, second, third):
        session.refresh(ep)

    positions = _queue_positions(session)

    assert positions == {first.id: 1, second.id: 2, third.id: 3}


def test_queue_positions_excludes_episodes_not_queued():
    session = _make_session()
    feed = _make_feed(session)

    active = Episode(
        feed_id=feed.id, guid="g1", title="e1", original_audio_url="https://example.test/1.mp3",
        status=EpisodeStatus.ANALYZING,
    )
    published = Episode(
        feed_id=feed.id, guid="g2", title="e2", original_audio_url="https://example.test/2.mp3",
        status=EpisodeStatus.PUBLISHED,
    )
    queued = Episode(
        feed_id=feed.id, guid="g3", title="e3", original_audio_url="https://example.test/3.mp3",
        status=EpisodeStatus.NEW,
    )
    session.add_all([active, published, queued])
    session.commit()
    session.refresh(queued)

    positions = _queue_positions(session)

    assert positions == {queued.id: 1}


def test_queue_positions_empty_when_nothing_queued():
    session = _make_session()
    assert _queue_positions(session) == {}
