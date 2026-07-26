from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.models import Episode, EpisodeStatus, Feed
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


def test_episodes_stuck_in_transient_statuses_are_requeued():
    session = _make_session()
    feed = _make_feed(session)

    episodes = [
        Episode(feed_id=feed.id, original_audio_url="https://example.test/x.mp3", guid="g1", title="e1", status=EpisodeStatus.DOWNLOADING),
        Episode(feed_id=feed.id, original_audio_url="https://example.test/x.mp3", guid="g2", title="e2", status=EpisodeStatus.TRANSCRIBING),
        Episode(feed_id=feed.id, original_audio_url="https://example.test/x.mp3", guid="g3", title="e3", status=EpisodeStatus.ANALYZING),
        Episode(feed_id=feed.id, original_audio_url="https://example.test/x.mp3", guid="g4", title="e4", status=EpisodeStatus.CUTTING, error_message="stale"),
    ]
    session.add_all(episodes)
    session.commit()

    recovered = pipeline.recover_interrupted_episodes(session)

    assert recovered == 4
    for ep in episodes:
        session.refresh(ep)
        # NEW, not REANALYZE: REANALYZE routes through reprocess_episode(), whose
        # "nothing cached" fallback unconditionally resets retry_count to 0 - which
        # would silently undo the increment this function just made (see
        # test_retry_count_survives_across_cycles_even_without_cached_audio below).
        assert ep.status == EpisodeStatus.NEW
        assert ep.error_message is None
        assert ep.retry_count == 1


def test_episode_exceeding_max_retries_is_marked_error_permanent_instead_of_looping():
    session = _make_session()
    feed = _make_feed(session)

    episode = Episode(
        feed_id=feed.id,
        original_audio_url="https://example.test/x.mp3",
        guid="g1",
        title="Giant episode that keeps crashing the process",
        status=EpisodeStatus.TRANSCRIBING,
        retry_count=settings.max_retries - 1,
    )
    session.add(episode)
    session.commit()

    recovered = pipeline.recover_interrupted_episodes(session)

    assert recovered == 1
    session.refresh(episode)
    assert episode.status == EpisodeStatus.ERROR_PERMANENT
    assert episode.retry_count == settings.max_retries
    assert episode.error_message  # explains why, so the user isn't left guessing


def test_repeated_interruptions_eventually_stop_the_crash_loop():
    # Simulates the real failure mode: the process dies every time it reaches this
    # episode (e.g. an out-of-memory kill on an exceptionally long file). Each
    # recover_interrupted_episodes() call stands in for one more restart-and-die cycle.
    session = _make_session()
    feed = _make_feed(session)

    episode = Episode(
        feed_id=feed.id,
        original_audio_url="https://example.test/x.mp3",
        guid="g1",
        title="Crash-looping episode",
        status=EpisodeStatus.DOWNLOADING,
    )
    session.add(episode)
    session.commit()

    for _ in range(settings.max_retries):
        session.refresh(episode)
        if episode.status == EpisodeStatus.ERROR_PERMANENT:
            break
        episode.status = EpisodeStatus.DOWNLOADING  # simulate the queue re-claiming it, then dying again
        session.add(episode)
        session.commit()
        pipeline.recover_interrupted_episodes(session)

    session.refresh(episode)
    assert episode.status == EpisodeStatus.ERROR_PERMANENT
    assert episode.retry_count == settings.max_retries


def test_episodes_in_stable_statuses_are_left_untouched():
    session = _make_session()
    feed = _make_feed(session)

    stable = [
        Episode(feed_id=feed.id, original_audio_url="https://example.test/x.mp3", guid="g1", title="e1", status=EpisodeStatus.NEW),
        Episode(feed_id=feed.id, original_audio_url="https://example.test/x.mp3", guid="g2", title="e2", status=EpisodeStatus.PENDING_REVIEW),
        Episode(feed_id=feed.id, original_audio_url="https://example.test/x.mp3", guid="g3", title="e3", status=EpisodeStatus.PUBLISHED),
        Episode(feed_id=feed.id, original_audio_url="https://example.test/x.mp3", guid="g4", title="e4", status=EpisodeStatus.FAILED_DOWNLOAD),
        Episode(feed_id=feed.id, original_audio_url="https://example.test/x.mp3", guid="g5", title="e5", status=EpisodeStatus.REANALYZE),
    ]
    session.add_all(stable)
    session.commit()
    original_statuses = [ep.status for ep in stable]

    recovered = pipeline.recover_interrupted_episodes(session)

    assert recovered == 0
    for ep, original in zip(stable, original_statuses):
        session.refresh(ep)
        assert ep.status == original


def test_recover_interrupted_episodes_returns_zero_when_nothing_to_do():
    session = _make_session()
    assert pipeline.recover_interrupted_episodes(session) == 0
