from sqlmodel import Session, SQLModel, create_engine

from app.models import AppConfig, Episode, EpisodeStatus, Feed
from app.services import cache_manager


def _make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make_feed(session, title="Feed"):
    feed = Feed(title=title, original_rss_url=f"https://example.test/{title}")
    session.add(feed)
    session.commit()
    session.refresh(feed)
    return feed


def _make_published_episode(session, feed, guid, pubdate, base_path):
    original_path = base_path.with_suffix(".orig.mp3")
    processed_path = base_path.with_suffix(".proc.mp3")
    original_path.write_bytes(b"o" * 500)
    processed_path.write_bytes(b"p" * 500)
    episode = Episode(
        feed_id=feed.id,
        guid=guid,
        title=guid,
        pubdate=pubdate,
        original_audio_url="https://example.test/audio.mp3",
        original_audio_path=str(original_path),
        processed_audio_path=str(processed_path),
        processed_size_bytes=500,
        status=EpisodeStatus.PUBLISHED,
    )
    session.add(episode)
    session.commit()
    session.refresh(episode)
    return episode


def test_enforce_max_episodes_per_feed_keeps_newest(tmp_path):
    from datetime import datetime, timedelta, timezone

    session = _make_session()
    feed = _make_feed(session)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    episodes = [
        _make_published_episode(session, feed, f"g{i}", base + timedelta(days=i), tmp_path / f"{i}.mp3")
        for i in range(5)
    ]

    session.add(AppConfig(id=1, max_episodes_per_feed=2, max_cache_size_mb=None))
    session.commit()

    cache_manager.enforce_cache_limits(session)

    remaining = [e for e in episodes if cache_manager._has_cached_audio(session.get(Episode, e.id))]
    assert len(remaining) == 2
    # the two newest (highest index) must survive
    surviving_ids = {e.id for e in remaining}
    assert surviving_ids == {episodes[3].id, episodes[4].id}


def test_enforce_max_cache_size_keeps_at_least_one_per_feed(tmp_path):
    from datetime import datetime, timedelta, timezone

    session = _make_session()
    feed_a = _make_feed(session, "A")
    feed_b = _make_feed(session, "B")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # 1 episode for feed A, 2 for feed B, each ~1000 bytes -> total ~3000 bytes
    ep_a1 = _make_published_episode(session, feed_a, "a1", base, tmp_path / "a1.mp3")
    ep_b1 = _make_published_episode(session, feed_b, "b1", base, tmp_path / "b1.mp3")
    ep_b2 = _make_published_episode(session, feed_b, "b2", base + timedelta(days=1), tmp_path / "b2.mp3")

    # limit smaller than total size, so eviction must happen, but feed A's only
    # episode must never be touched
    session.add(AppConfig(id=1, max_episodes_per_feed=None, max_cache_size_mb=0.0015))
    session.commit()

    cache_manager.enforce_cache_limits(session)

    assert cache_manager._has_cached_audio(session.get(Episode, ep_a1.id))
    # the older of feed B's two episodes should have been evicted first
    assert not cache_manager._has_cached_audio(session.get(Episode, ep_b1.id))
    assert cache_manager._has_cached_audio(session.get(Episode, ep_b2.id))


def test_evict_episode_audio_deletes_files_and_clears_paths(tmp_path):
    session = _make_session()
    feed = _make_feed(session)
    from datetime import datetime, timezone

    episode = _make_published_episode(session, feed, "g", datetime.now(timezone.utc), tmp_path / "e")
    original_path = tmp_path / "e.orig.mp3"
    processed_path = tmp_path / "e.proc.mp3"
    assert original_path.exists() and processed_path.exists()

    freed = cache_manager.evict_episode_audio(session, episode)

    assert freed == 1000
    assert not original_path.exists()
    assert not processed_path.exists()
    assert episode.original_audio_path is None
    assert episode.processed_audio_path is None
    assert episode.processed_size_bytes is None
