from datetime import datetime, timedelta, timezone

from sqlmodel import Session, SQLModel, create_engine

from app.models import Episode, EpisodeStatus, Feed
from app.routers.jingle_finder import _next_episode_without_audio


def _make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_next_episode_without_audio_skips_downloaded_ones(tmp_path):
    session = _make_session()
    feed = Feed(title="Feed", original_rss_url="https://example.test/feed")
    session.add(feed)
    session.commit()
    session.refresh(feed)

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    downloaded_path = tmp_path / "1.mp3"
    downloaded_path.write_bytes(b"x")

    ep_with_audio = Episode(
        feed_id=feed.id,
        guid="g1",
        title="Older, already downloaded",
        pubdate=base,
        original_audio_url="https://example.test/1.mp3",
        original_audio_path=str(downloaded_path),
    )
    ep_without_audio_older = Episode(
        feed_id=feed.id,
        guid="g2",
        title="Newer, not yet downloaded",
        pubdate=base + timedelta(days=1),
        original_audio_url="https://example.test/2.mp3",
    )
    ep_without_audio_newest = Episode(
        feed_id=feed.id,
        guid="g3",
        title="Newest, not yet downloaded",
        pubdate=base + timedelta(days=2),
        original_audio_url="https://example.test/3.mp3",
    )
    session.add_all([ep_with_audio, ep_without_audio_older, ep_without_audio_newest])
    session.commit()

    result = _next_episode_without_audio(session, feed.id)

    assert result is not None
    assert result.guid == "g3"  # newest one lacking local audio, most recent first


def test_next_episode_without_audio_returns_none_when_all_cached(tmp_path):
    session = _make_session()
    feed = Feed(title="Feed", original_rss_url="https://example.test/feed2")
    session.add(feed)
    session.commit()
    session.refresh(feed)

    path = tmp_path / "1.mp3"
    path.write_bytes(b"x")
    episode = Episode(
        feed_id=feed.id,
        guid="g1",
        title="Cached",
        original_audio_url="https://example.test/1.mp3",
        original_audio_path=str(path),
    )
    session.add(episode)
    session.commit()

    assert _next_episode_without_audio(session, feed.id) is None


def test_next_episode_without_audio_avoids_racing_the_background_scheduler():
    # An episode sitting at status=NEW is about to be downloaded by process_queue_job
    # any moment now; jingle-finder grabbing it too would race for the same destination
    # file. It should prefer a differently-statused candidate if one exists...
    session = _make_session()
    feed = Feed(title="Feed", original_rss_url="https://example.test/feed3")
    session.add(feed)
    session.commit()
    session.refresh(feed)

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    about_to_be_downloaded = Episode(
        feed_id=feed.id, guid="g1", title="queued", pubdate=base + timedelta(days=1),
        original_audio_url="https://example.test/1.mp3", status=EpisodeStatus.NEW,
    )
    safe_to_grab = Episode(
        feed_id=feed.id, guid="g2", title="failed earlier", pubdate=base,
        original_audio_url="https://example.test/2.mp3", status=EpisodeStatus.FAILED_DOWNLOAD,
    )
    session.add_all([about_to_be_downloaded, safe_to_grab])
    session.commit()

    result = _next_episode_without_audio(session, feed.id)
    assert result.guid == "g2"


def test_next_episode_without_audio_falls_back_to_queued_if_nothing_else_exists():
    # ...but still returns *something* rather than nothing, since a small residual race
    # window is better than the feature being unusable whenever only a queued episode
    # is available.
    session = _make_session()
    feed = Feed(title="Feed", original_rss_url="https://example.test/feed4")
    session.add(feed)
    session.commit()
    session.refresh(feed)

    only_candidate = Episode(
        feed_id=feed.id, guid="g1", title="queued", original_audio_url="https://example.test/1.mp3",
        status=EpisodeStatus.NEW,
    )
    session.add(only_candidate)
    session.commit()

    result = _next_episode_without_audio(session, feed.id)
    assert result is not None
    assert result.guid == "g1"
