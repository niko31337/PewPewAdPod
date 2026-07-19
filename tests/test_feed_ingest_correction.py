from sqlmodel import Session, SQLModel, create_engine

from app.models import CorrectionStatus, Episode, Feed
from app.services.feed_ingest import _handle_possible_correction


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


def test_updates_url_directly_when_nothing_downloaded_yet():
    session = _make_session()
    feed = _make_feed(session)
    episode = Episode(
        feed_id=feed.id,
        guid="g1",
        title="Ep",
        original_audio_url="https://example.test/old.mp3",
    )
    session.add(episode)
    session.commit()
    session.refresh(episode)

    _handle_possible_correction(session, episode, "https://example.test/new.mp3")

    session.refresh(episode)
    assert episode.original_audio_url == "https://example.test/new.mp3"
    assert episode.correction_status is None
    assert episode.correction_audio_url is None


def test_queues_correction_without_touching_original_when_already_downloaded(tmp_path):
    session = _make_session()
    feed = _make_feed(session)
    original_path = tmp_path / "1.mp3"
    original_path.write_bytes(b"original audio")
    episode = Episode(
        feed_id=feed.id,
        guid="g1",
        title="Ep",
        original_audio_url="https://example.test/old.mp3",
        original_audio_path=str(original_path),
    )
    session.add(episode)
    session.commit()
    session.refresh(episode)

    _handle_possible_correction(session, episode, "https://example.test/new.mp3")

    session.refresh(episode)
    # the original is completely untouched
    assert episode.original_audio_url == "https://example.test/old.mp3"
    assert episode.original_audio_path == str(original_path)
    assert original_path.read_bytes() == b"original audio"
    # the correction is queued separately
    assert episode.correction_audio_url == "https://example.test/new.mp3"
    assert episode.correction_status == CorrectionStatus.DETECTED
    assert episode.correction_detected_at is not None


def test_is_idempotent_for_the_same_replacement_url(tmp_path):
    session = _make_session()
    feed = _make_feed(session)
    original_path = tmp_path / "1.mp3"
    original_path.write_bytes(b"x")
    episode = Episode(
        feed_id=feed.id,
        guid="g1",
        title="Ep",
        original_audio_url="https://example.test/old.mp3",
        original_audio_path=str(original_path),
    )
    session.add(episode)
    session.commit()
    session.refresh(episode)

    _handle_possible_correction(session, episode, "https://example.test/new.mp3")
    session.refresh(episode)
    first_detected_at = episode.correction_detected_at

    _handle_possible_correction(session, episode, "https://example.test/new.mp3")
    session.refresh(episode)

    assert episode.correction_detected_at == first_detected_at


RSS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Test Show</title>
    <item>
      <title>Episode One</title>
      <guid>episode-1</guid>
      <enclosure url="{audio_url}" length="1000" type="audio/mpeg"/>
      <pubDate>Mon, 01 Jun 2026 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""


def test_poll_feed_detects_replaced_enclosure_on_second_poll(tmp_path, monkeypatch):
    session = _make_session()
    feed = _make_feed(session)

    # poll_feed always re-fetches feed.original_rss_url via feedparser.parse(); patch it to
    # return our crafted XML directly, keeping this test fully offline.
    import feedparser
    from sqlmodel import select

    from app.services import feed_ingest

    original_parse = feedparser.parse

    first_xml = RSS_TEMPLATE.format(audio_url="https://example.test/episode1_v1.mp3")
    monkeypatch.setattr(feedparser, "parse", lambda *_a, **_kw: original_parse(first_xml))
    feed_ingest.poll_feed(session, feed)

    episode = session.exec(select(Episode).where(Episode.feed_id == feed.id)).first()
    assert episode is not None
    assert episode.original_audio_url == "https://example.test/episode1_v1.mp3"

    downloaded_path = tmp_path / f"{episode.id}.mp3"
    downloaded_path.write_bytes(b"v1 audio")
    episode.original_audio_path = str(downloaded_path)
    session.add(episode)
    session.commit()

    second_xml = RSS_TEMPLATE.format(audio_url="https://example.test/episode1_v2_fixed.mp3")
    monkeypatch.setattr(feedparser, "parse", lambda *_a, **_kw: original_parse(second_xml))
    feed_ingest.poll_feed(session, feed)

    session.refresh(episode)
    assert episode.original_audio_url == "https://example.test/episode1_v1.mp3"
    assert episode.original_audio_path == str(downloaded_path)
    assert episode.correction_audio_url == "https://example.test/episode1_v2_fixed.mp3"
    assert episode.correction_status == CorrectionStatus.DETECTED
