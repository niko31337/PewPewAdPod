import logging

from sqlmodel import Session, select

from app.database import session_scope
from app.models import AppConfig, Episode, EpisodeStatus
from app.paths import resolve_stored_path

log = logging.getLogger(__name__)


def get_or_create_config(session: Session) -> AppConfig:
    config = session.get(AppConfig, 1)
    if config is None:
        config = AppConfig(id=1)
        session.add(config)
        session.commit()
        session.refresh(config)
    return config


def _episode_cache_bytes(episode: Episode) -> int:
    total = 0
    for path_str in (episode.original_audio_path, episode.processed_audio_path):
        p = resolve_stored_path(path_str)
        if p and p.exists():
            total += p.stat().st_size
    return total


def _has_cached_audio(episode: Episode) -> bool:
    return bool(
        (resolve_stored_path(episode.original_audio_path) or resolve_stored_path(episode.processed_audio_path))
    )


def _published_episodes(session: Session, feed_id: int | None = None) -> list[Episode]:
    stmt = select(Episode).where(Episode.status == EpisodeStatus.PUBLISHED)
    if feed_id is not None:
        stmt = stmt.where(Episode.feed_id == feed_id)
    return session.exec(stmt).all()


def evict_episode_audio(session: Session, episode: Episode) -> int:
    """Delete an episode's local audio files but keep the DB row/metadata (it simply
    drops out of the republished feed, since feed_generator only lists episodes that
    still have a processed_audio_path). Returns bytes freed."""
    freed = 0
    for attr in ("original_audio_path", "processed_audio_path"):
        path_str = getattr(episode, attr)
        if path_str:
            p = resolve_stored_path(path_str)
            if p:
                freed += p.stat().st_size
                p.unlink(missing_ok=True)
            setattr(episode, attr, None)
    episode.processed_size_bytes = None
    session.add(episode)
    log.info(
        "Evicted cached audio for episode %s (%s) - freed %.1f MB",
        episode.id,
        episode.title,
        freed / 1_000_000,
    )
    return freed


def enforce_cache_limits(session: Session | None = None) -> None:
    if session is None:
        with session_scope() as owned_session:
            enforce_cache_limits(owned_session)
        return

    config = get_or_create_config(session)

    if config.max_episodes_per_feed is not None:
        feed_ids = set(session.exec(select(Episode.feed_id)).all())
        for feed_id in feed_ids:
            episodes = sorted(
                (e for e in _published_episodes(session, feed_id) if _has_cached_audio(e)),
                key=lambda e: e.pubdate or e.created_at,
            )
            excess = len(episodes) - max(1, config.max_episodes_per_feed)
            for episode in episodes[: max(0, excess)]:
                evict_episode_audio(session, episode)
        session.commit()

    if config.max_cache_size_mb is not None:
        while True:
            cached = [e for e in _published_episodes(session) if _has_cached_audio(e)]
            total_mb = sum(_episode_cache_bytes(e) for e in cached) / 1_000_000
            if total_mb <= config.max_cache_size_mb:
                break

            per_feed_counts: dict[int, int] = {}
            for e in cached:
                per_feed_counts[e.feed_id] = per_feed_counts.get(e.feed_id, 0) + 1

            evictable = [e for e in cached if per_feed_counts[e.feed_id] > 1]
            if not evictable:
                log.info(
                    "Cache size %.1f MB exceeds limit %.1f MB, but every feed has only one "
                    "cached episode left - keeping at least one per feed and stopping.",
                    total_mb,
                    config.max_cache_size_mb,
                )
                break

            oldest = min(evictable, key=lambda e: e.pubdate or e.created_at)
            evict_episode_audio(session, oldest)
            session.commit()


def cache_summary(session: Session) -> dict:
    cached = [e for e in _published_episodes(session) if _has_cached_audio(e)]
    per_feed: dict[int, int] = {}
    for e in cached:
        per_feed[e.feed_id] = per_feed.get(e.feed_id, 0) + 1
    total_mb = sum(_episode_cache_bytes(e) for e in cached) / 1_000_000
    return {"total_episodes": len(cached), "total_mb": total_mb, "per_feed_counts": per_feed}
