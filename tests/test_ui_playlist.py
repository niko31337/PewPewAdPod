from datetime import datetime, timedelta, timezone

from sqlmodel import Session, SQLModel, create_engine

from app.models import AppConfig, Episode, EpisodeStatus, Feed
from app.services.feed_playlist import select_playlist_entries


def _make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_playlist_uses_same_preferred_order_as_master_feed():
    session = _make_session()
    preferred = Feed(title="Preferred", original_rss_url="https://example.test/p.xml", preferred=True)
    normal = Feed(title="Normal", original_rss_url="https://example.test/n.xml")
    session.add_all([preferred, normal])
    session.commit()
    session.refresh(preferred); session.refresh(normal)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session.add_all([
        Episode(feed_id=normal.id, guid="n", title="Normal", original_audio_url="x",
                status=EpisodeStatus.PUBLISHED, processed_audio_path="/n.mp3", pubdate=base + timedelta(days=2)),
        Episode(feed_id=preferred.id, guid="p", title="Preferred", original_audio_url="x",
                status=EpisodeStatus.PUBLISHED, processed_audio_path="/p.mp3", pubdate=base),
    ])
    session.commit()

    result = select_playlist_entries(session, 1, preferred_first=True)
    assert [feed.title for feed, _ in result] == ["Preferred", "Normal"]


def test_playlist_filters_to_requested_feeds_and_skips_uncached_episodes():
    session = _make_session()
    feed_a = Feed(title="A", original_rss_url="https://example.test/a.xml")
    feed_b = Feed(title="B", original_rss_url="https://example.test/b.xml")
    session.add_all([feed_a, feed_b])
    session.commit()
    session.refresh(feed_a); session.refresh(feed_b)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session.add_all([
        Episode(feed_id=feed_a.id, guid="a1", title="A cached", original_audio_url="x",
                status=EpisodeStatus.PUBLISHED, processed_audio_path="/a.mp3", pubdate=base),
        Episode(feed_id=feed_a.id, guid="a2", title="A missing audio", original_audio_url="x",
                status=EpisodeStatus.PUBLISHED, processed_audio_path=None, pubdate=base + timedelta(days=1)),
        Episode(feed_id=feed_b.id, guid="b1", title="B cached", original_audio_url="x",
                status=EpisodeStatus.PUBLISHED, processed_audio_path="/b.mp3", pubdate=base + timedelta(days=2)),
    ])
    session.commit()

    result = select_playlist_entries(session, 2, feed_ids={feed_a.id})
    assert [(feed.title, ep.title) for feed, ep in result] == [("A", "A cached")]


def test_app_config_persists_dark_theme():
    session = _make_session()
    config = AppConfig(id=1, ui_theme="dark")
    session.add(config)
    session.commit()
    loaded = session.get(AppConfig, 1)
    assert loaded.ui_theme == "dark"
