from datetime import datetime, timezone

from app.models import Episode, EpisodeStatus, Feed
from app.services.feed_generator import build_feed_xml


def test_enclosure_url_matches_actual_processed_file_after_a_correction_is_applied():
    feed = Feed(id=1, title="Feed", original_rss_url="https://example.test/feed.xml")
    episode = Episode(
        id=3,
        feed_id=1,
        guid="g3",
        title="Ep",
        original_audio_url="https://example.test/new.mp3",
        pubdate=datetime(2026, 1, 1, tzinfo=timezone.utc),
        status=EpisodeStatus.PUBLISHED,
        # After applying a correction, processed_audio_path points at the "_correction"
        # file rather than "{id}.mp3" - the enclosure must follow that, not assume the
        # old fixed naming.
        processed_audio_path=r"C:\data\audio\processed\3_correction.mp3",
        processed_size_bytes=12345,
    )

    xml_bytes = build_feed_xml(feed, [episode], "http://localhost:8000/")

    xml = xml_bytes.decode("utf-8")
    assert "media/audio/processed/3_correction.mp3" in xml
    assert 'length="12345"' in xml
    # must not fall back to the old "{id}.mp3" convention
    assert "media/audio/processed/3.mp3" not in xml
