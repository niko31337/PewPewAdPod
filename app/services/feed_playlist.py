from app.models import Episode, EpisodeStatus, Feed
from sqlmodel import Session, select


def select_playlist_entries(
    session: Session,
    episodes_per_feed: int,
    preferred_first: bool = False,
    feed_ids: set[int] | None = None,
) -> list[tuple[Feed, Episode]]:
    """Return the same published/cached episode selection used by the master feed.

    One or more feeds can be selected for the UI overview. When no feed filter is given,
    all active feeds are considered. The per-feed count is capped at 50 so the Queue page
    cannot accidentally turn into a large metadata-probing job.
    """
    limit = min(max(int(episodes_per_feed), 1), 50)
    feeds = session.exec(select(Feed).where(Feed.active == True).order_by(Feed.created_at)).all()  # noqa: E712
    if feed_ids is not None:
        feeds = [feed for feed in feeds if feed.id in feed_ids]

    entries: list[tuple[Feed, Episode]] = []
    for feed in feeds:
        candidates = session.exec(
            select(Episode)
            .where(Episode.feed_id == feed.id)
            .where(Episode.status == EpisodeStatus.PUBLISHED)
            .where(Episode.heard_elsewhere == False)  # noqa: E712
            .order_by(Episode.pubdate.desc())
        ).all()
        chosen = [episode for episode in candidates if episode.processed_audio_path][:limit]
        entries.extend((feed, episode) for episode in chosen)

    # Establish the natural order first. Episodes with explicit positions follow the
    # saved user order; unpositioned episodes (typically newly published episodes) are
    # appended in natural order so new arrivals do not disrupt the user's queue.
    # After the first manual move, the reorder endpoint assigns a position to every
    # currently selected entry.
    if preferred_first:
        entries.sort(key=lambda pair: (pair[0].preferred, pair[1].pubdate or pair[1].created_at), reverse=True)
    else:
        entries.sort(key=lambda pair: pair[1].pubdate or pair[1].created_at, reverse=True)

    if any(episode.playlist_position is not None for _, episode in entries):
        manually_positioned = sorted(
            (pair for pair in entries if pair[1].playlist_position is not None),
            key=lambda pair: pair[1].playlist_position,
        )
        natural_unpositioned = [pair for pair in entries if pair[1].playlist_position is None]
        entries = manually_positioned + natural_unpositioned
    return entries
