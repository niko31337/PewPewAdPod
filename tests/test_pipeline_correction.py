from sqlmodel import Session, SQLModel, create_engine

from app.models import AdSegment, CorrectionStatus, Episode, EpisodeStatus, Feed, SegmentStatus
from app.services import pipeline


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


def test_apply_correction_promotes_files_without_deleting_the_original(tmp_path):
    session = _make_session()
    feed = _make_feed(session)

    original_processed = tmp_path / "1.mp3"
    original_processed.write_bytes(b"old cut audio")
    correction_processed = tmp_path / "1_correction.mp3"
    correction_processed.write_bytes(b"new corrected cut audio, longer than before")

    episode = Episode(
        feed_id=feed.id,
        guid="g1",
        title="Ep",
        original_audio_url="https://example.test/old.mp3",
        original_audio_path=str(tmp_path / "1_orig.mp3"),
        processed_audio_path=str(original_processed),
        processed_size_bytes=original_processed.stat().st_size,
        status=EpisodeStatus.PUBLISHED,
        correction_audio_url="https://example.test/new.mp3",
        correction_processed_path=str(correction_processed),
        correction_status=CorrectionStatus.READY,
    )
    session.add(episode)
    session.commit()
    session.refresh(episode)

    pipeline.apply_correction(session, episode)

    session.refresh(episode)
    assert episode.processed_audio_path == str(correction_processed)
    assert episode.processed_size_bytes == correction_processed.stat().st_size
    assert episode.original_audio_url == "https://example.test/new.mp3"
    assert episode.correction_status is None
    assert episode.correction_audio_url is None
    assert episode.correction_processed_path is None
    # the old processed file itself was never touched/deleted
    assert original_processed.exists()
    assert original_processed.read_bytes() == b"old cut audio"


def test_discard_correction_removes_only_correction_files_and_segments(tmp_path):
    session = _make_session()
    feed = _make_feed(session)

    original_audio = tmp_path / "1_orig.mp3"
    original_audio.write_bytes(b"original")
    correction_original = tmp_path / "1_correction_orig.mp3"
    correction_original.write_bytes(b"correction original")
    correction_processed = tmp_path / "1_correction.mp3"
    correction_processed.write_bytes(b"correction cut")

    episode = Episode(
        feed_id=feed.id,
        guid="g1",
        title="Ep",
        original_audio_url="https://example.test/old.mp3",
        original_audio_path=str(original_audio),
        status=EpisodeStatus.PUBLISHED,
        correction_audio_url="https://example.test/new.mp3",
        correction_original_path=str(correction_original),
        correction_processed_path=str(correction_processed),
        correction_status=CorrectionStatus.READY,
    )
    session.add(episode)
    session.commit()
    session.refresh(episode)

    original_segment = AdSegment(episode_id=episode.id, start_ms=0, end_ms=1000, is_correction=False)
    correction_segment = AdSegment(episode_id=episode.id, start_ms=0, end_ms=2000, is_correction=True)
    session.add(original_segment)
    session.add(correction_segment)
    session.commit()

    pipeline.discard_correction(session, episode)

    session.refresh(episode)
    assert episode.correction_status is None
    assert episode.correction_audio_url is None
    assert not correction_original.exists()
    assert not correction_processed.exists()
    # the original episode's own audio and ad-segment are left alone
    assert original_audio.exists()

    from sqlmodel import select

    remaining = session.exec(select(AdSegment).where(AdSegment.episode_id == episode.id)).all()
    assert len(remaining) == 1
    assert remaining[0].is_correction is False


def test_persist_ad_segments_keeps_original_and_correction_pending_sets_separate(tmp_path):
    from app.services.ad_detector import Candidate

    session = _make_session()
    feed = _make_feed(session)
    episode = Episode(
        feed_id=feed.id, guid="g1", title="Ep", original_audio_url="https://example.test/a.mp3"
    )
    session.add(episode)
    session.commit()
    session.refresh(episode)

    pipeline.persist_ad_segments(session, episode, [Candidate(start_ms=0, end_ms=1000)], is_correction=False)
    pipeline.persist_ad_segments(session, episode, [Candidate(start_ms=0, end_ms=2000)], is_correction=True)

    from sqlmodel import select

    original_rows = session.exec(
        select(AdSegment).where(AdSegment.episode_id == episode.id).where(AdSegment.is_correction == False)  # noqa: E712
    ).all()
    correction_rows = session.exec(
        select(AdSegment).where(AdSegment.episode_id == episode.id).where(AdSegment.is_correction == True)  # noqa: E712
    ).all()

    assert len(original_rows) == 1 and original_rows[0].end_ms == 1000
    assert len(correction_rows) == 1 and correction_rows[0].end_ms == 2000

    # re-persisting a fresh correction candidate must not disturb the original's segment
    pipeline.persist_ad_segments(session, episode, [Candidate(start_ms=5000, end_ms=6000)], is_correction=True)
    original_rows_after = session.exec(
        select(AdSegment).where(AdSegment.episode_id == episode.id).where(AdSegment.is_correction == False)  # noqa: E712
    ).all()
    assert len(original_rows_after) == 1 and original_rows_after[0].end_ms == 1000


def test_find_previous_episode_audio_picks_newest_other_episode_with_cached_audio(tmp_path):
    from datetime import datetime, timedelta, timezone

    session = _make_session()
    feed = _make_feed(session)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    older_audio = tmp_path / "older.mp3"
    older_audio.write_bytes(b"x")
    newer_audio = tmp_path / "newer.mp3"
    newer_audio.write_bytes(b"x")

    older = Episode(
        feed_id=feed.id, guid="g1", title="older", pubdate=base,
        original_audio_url="https://example.test/1.mp3", original_audio_path=str(older_audio),
    )
    newer = Episode(
        feed_id=feed.id, guid="g2", title="newer", pubdate=base + timedelta(days=1),
        original_audio_url="https://example.test/2.mp3", original_audio_path=str(newer_audio),
    )
    current = Episode(
        feed_id=feed.id, guid="g3", title="current", pubdate=base + timedelta(days=2),
        original_audio_url="https://example.test/3.mp3",
    )
    session.add_all([older, newer, current])
    session.commit()
    session.refresh(current)

    result = pipeline._find_previous_episode_audio(session, feed.id, current.id)

    assert result == newer_audio


def test_find_previous_episode_audio_skips_evicted_episodes(tmp_path):
    from datetime import datetime, timedelta, timezone

    session = _make_session()
    feed = _make_feed(session)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    still_cached = tmp_path / "cached.mp3"
    still_cached.write_bytes(b"x")

    evicted = Episode(
        feed_id=feed.id, guid="g1", title="evicted", pubdate=base + timedelta(days=1),
        original_audio_url="https://example.test/1.mp3", original_audio_path=None,
    )
    cached = Episode(
        feed_id=feed.id, guid="g2", title="cached", pubdate=base,
        original_audio_url="https://example.test/2.mp3", original_audio_path=str(still_cached),
    )
    current = Episode(
        feed_id=feed.id, guid="g3", title="current", pubdate=base + timedelta(days=2),
        original_audio_url="https://example.test/3.mp3",
    )
    session.add_all([evicted, cached, current])
    session.commit()
    session.refresh(current)

    result = pipeline._find_previous_episode_audio(session, feed.id, current.id)

    assert result == still_cached
