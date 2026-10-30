import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models import AdSegment, Episode, EpisodeStatus, Feed
from app.services import audio_editor, cache_manager
from app.services.feed_playlist import select_playlist_entries
from app.paths import resolve_stored_path
from app.routers.episodes import _QUEUED_STATUSES
from app.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


def _unlink_within_root(path: Path, root: Path) -> None:
    """Delete a file only if its resolved path stays beneath one of our media roots."""
    try:
        root_real = root.resolve()
        target = path.resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        return
    if not target.is_file() or target == root_real or root_real not in target.parents:
        if target.exists():
            log.warning("Refusing to delete episode file outside allowed directory: %s", target)
        return
    try:
        target.unlink()
        log.info("Deleted local episode file %s", target)
    except OSError:
        log.exception("Could not delete local episode file %s", target)


def _delete_episode_local_files(
    episode_id: int,
    feed_id: int,
    stored_paths: dict[str, str | None],
) -> None:
    """Remove audio, correction audio, transcript JSON and episode-specific artwork."""
    for attr, root in (
        ("original_audio_path", settings.originals_dir),
        ("processed_audio_path", settings.processed_dir),
        ("correction_original_path", settings.originals_dir),
        ("correction_processed_path", settings.processed_dir),
        ("local_image_path", settings.covers_dir),
    ):
        stored_path = stored_paths.get(attr)
        if stored_path:
            # Stored native paths are removed only when the resolved file remains in
            # the expected application-owned directory. Canonical paths below handle
            # older DB rows created on another OS.
            _unlink_within_root(Path(stored_path), root)

    # Also remove canonical paths if old/cross-platform DB paths were absent or stale.
    for root, names in (
        (settings.originals_dir, (f"{episode_id}.mp3", f"{episode_id}_correction.mp3")),
        (settings.processed_dir, (f"{episode_id}.mp3", f"{episode_id}_correction.mp3")),
        (settings.transcripts_dir, (f"{episode_id}.json", f"{episode_id}_correction.json")),
    ):
        for name in names:
            _unlink_within_root(root / name, root)

    cover_paths = (
        settings.covers_dir / str(feed_id) / "episodes" / f"{episode_id}.jpg",
        settings.covers_dir / "master" / "episodes" / f"{episode_id}.jpg",
    )
    for cover_path in cover_paths:
        _unlink_within_root(cover_path, settings.covers_dir)


def _format_playlist_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    total = int(round(seconds))
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}" if total >= 3600 else f"{total // 60}:{total % 60:02d}"


def _playlist_row(session: Session, feed: Feed, episode: Episode) -> dict:
    original_seconds = episode.duration_seconds
    processed_seconds = None
    processed = resolve_stored_path(episode.processed_audio_path)
    if processed and processed.exists():
        processed_seconds = audio_editor.get_duration_seconds(processed)

    delta_seconds = None
    if original_seconds is not None and processed_seconds is not None:
        delta_seconds = max(0.0, original_seconds - processed_seconds)

    return {
        "feed": feed,
        "episode": episode,
        "feed_name": feed.title,
        "play_duration": _format_playlist_duration(processed_seconds),
        "delta_duration": f"−{_format_playlist_duration(delta_seconds)}" if delta_seconds is not None and delta_seconds >= 0.5 else "—",
        "original_duration": _format_playlist_duration(original_seconds),
        # Original release date from the podcast RSS, formatted for German UI.
        # Deliberately do not fall back to created_at (ingestion date).
        "pubdate_display": (episode.pubdate.strftime("%d.%m.%Y") if episode.pubdate else "—"),
        "pubdate_iso": (episode.pubdate.date().isoformat() if episode.pubdate else ""),
        "pubdate_tooltip": (
            f"RSS-Veröffentlichungsdatum: {episode.pubdate.isoformat(sep=' ', timespec='minutes')}"
            if episode.pubdate
            else "Kein Veröffentlichungsdatum im Podcast-RSS angegeben"
        ),
        "feed_url": f"/feed/{feed.id}.xml",
    }


@router.get("/queue")
def queue_page(request: Request, session: Session = Depends(get_session)):
    episodes = session.exec(
        select(Episode)
        .where(Episode.status.in_(_QUEUED_STATUSES))
        .where(Episode.heard_elsewhere == False)  # noqa: E712
        .order_by(Episode.created_at)
    ).all()
    feed_titles = {f.id: f.title for f in session.exec(select(Feed)).all()}
    rows = [
        {"episode": episode, "feed_title": feed_titles.get(episode.feed_id, f"Feed {episode.feed_id}")}
        for episode in episodes
    ]

    app_config = cache_manager.get_or_create_config(session)
    playlist_feeds = session.exec(select(Feed).where(Feed.active == True).order_by(Feed.title)).all()  # noqa: E712
    playlist_entries = select_playlist_entries(
        session,
        episodes_per_feed=app_config.master_feed_episodes_per_podcast,
        preferred_first=app_config.feed_exposure_mode == "master_preferred",
    )
    playlist_rows = [_playlist_row(session, feed, episode) for feed, episode in playlist_entries]

    return templates.TemplateResponse(
        request,
        "queue.html",
        {
            "rows": rows,
            "playlist_rows": playlist_rows,
            "playlist_feeds": playlist_feeds,
            "playlist_mode": app_config.feed_exposure_mode,
            "playlist_per_feed": min(max(app_config.master_feed_episodes_per_podcast, 1), 50),
        },
    )


def _prepare_episode_as_heard_elsewhere(session: Session, episode: Episode):
    """Mark a published episode as heard elsewhere in the current transaction.

    Returns file-cleanup metadata; the caller commits first and deletes files only after
    the database transaction succeeds.
    """
    if episode.heard_elsewhere or episode.status != EpisodeStatus.PUBLISHED:
        return None

    stored_paths = {
        "original_audio_path": episode.original_audio_path,
        "processed_audio_path": episode.processed_audio_path,
        "correction_original_path": episode.correction_original_path,
        "correction_processed_path": episode.correction_processed_path,
        "local_image_path": episode.local_image_path,
    }
    feed_id = episode.feed_id
    episode_id = episode.id

    episode.heard_elsewhere = True
    episode.status = EpisodeStatus.SKIPPED
    episode.error_message = None
    episode.original_audio_path = None
    episode.processed_audio_path = None
    episode.processed_size_bytes = None
    episode.local_image_path = None
    episode.correction_audio_url = None
    episode.correction_original_path = None
    episode.correction_processed_path = None
    episode.correction_status = None
    episode.correction_error_message = None
    episode.correction_detected_at = None
    episode.playlist_position = None
    episode.updated_at = datetime.now(timezone.utc)

    for segment in session.exec(select(AdSegment).where(AdSegment.episode_id == episode_id)).all():
        session.delete(segment)
    session.add(episode)
    return (episode_id, feed_id, stored_paths, episode.title)


def _persist_playlist_order(session: Session, entries: list[tuple[Feed, Episode]]) -> None:
    """Persist dense manual positions for the current playlist and clear stale ranks."""
    selected_ids = {episode.id for _, episode in entries}
    positioned_episodes = session.exec(
        select(Episode).where(Episode.playlist_position.is_not(None))
    ).all()
    for episode in positioned_episodes:
        if episode.id not in selected_ids:
            episode.playlist_position = None
            session.add(episode)

    for new_position, (_, episode) in enumerate(entries, start=1):
        episode.playlist_position = new_position * 10
        session.add(episode)


def _move_selected_playlist_entries(
    entries: list[tuple[Feed, Episode]], selected_ids: set[int], direction: str
) -> list[tuple[Feed, Episode]]:
    """Move selected episodes one place as a block, preserving their relative order."""
    if direction == "move_up":
        for index in range(1, len(entries)):
            current_id = entries[index][1].id
            previous_id = entries[index - 1][1].id
            if current_id in selected_ids and previous_id not in selected_ids:
                entries[index - 1], entries[index] = entries[index], entries[index - 1]
    elif direction == "move_down":
        for index in range(len(entries) - 2, -1, -1):
            current_id = entries[index][1].id
            next_id = entries[index + 1][1].id
            if current_id in selected_ids and next_id not in selected_ids:
                entries[index], entries[index + 1] = entries[index + 1], entries[index]
    return entries


@router.post("/queue/episodes/{episode_id}/heard-elsewhere")
def mark_episode_heard_elsewhere(
    episode_id: int,
    back_to_feed: bool = False,
    session: Session = Depends(get_session),
):
    """Permanently exclude a published episode and delete its local media files.

    The optional boolean return flag is deliberately not an arbitrary URL: callers may
    return either to the Queue playlist (default) or to this episode's own feed page.
    Both entry points use this exact cleanup path.
    """
    episode = session.get(Episode, episode_id)
    feed_id = episode.feed_id if episode is not None else None
    cleanup = _prepare_episode_as_heard_elsewhere(session, episode) if episode else None
    if cleanup is None:
        target = f"/feeds/{feed_id}" if back_to_feed and feed_id is not None else "/queue#playlist"
        return RedirectResponse(url=target, status_code=303)

    session.commit()
    cleanup_episode_id, feed_id, stored_paths, title = cleanup
    _delete_episode_local_files(cleanup_episode_id, feed_id, stored_paths)
    log.info("Episode %s (%s) marked as heard elsewhere; local files removed", cleanup_episode_id, title)
    target = f"/feeds/{feed_id}" if back_to_feed else "/queue#playlist"
    return RedirectResponse(url=target, status_code=303)


@router.post("/feeds/{feed_id}/episodes/heard-all")
def mark_all_feed_episodes_heard_elsewhere(
    feed_id: int,
    session: Session = Depends(get_session),
):
    """Mark every still-published episode of one feed as heard elsewhere.

    Uses the same tombstone and guarded local-file cleanup as the queue's single- and
    multi-select actions. Non-published and already-excluded episodes are left alone.
    """
    feed = session.get(Feed, feed_id)
    if feed is None:
        return RedirectResponse(url="/", status_code=303)

    episodes = session.exec(
        select(Episode)
        .where(Episode.feed_id == feed_id)
        .where(Episode.status == EpisodeStatus.PUBLISHED)
        .where(Episode.heard_elsewhere == False)  # noqa: E712
        .order_by(Episode.id)
    ).all()

    cleanup_items = []
    for episode in episodes:
        cleanup = _prepare_episode_as_heard_elsewhere(session, episode)
        if cleanup is not None:
            cleanup_items.append(cleanup)

    # Persist all tombstones/segment deletions before touching filesystem data.
    if cleanup_items:
        session.commit()
        for cleanup_episode_id, cleanup_feed_id, stored_paths, title in cleanup_items:
            _delete_episode_local_files(cleanup_episode_id, cleanup_feed_id, stored_paths)
            log.info(
                "Feed-wide heard-elsewhere action: episode %s (%s) removed from feed %s",
                cleanup_episode_id,
                title,
                feed_id,
            )

    changed = len(cleanup_items)
    log.info("Marked %s published episodes as heard elsewhere for feed %s", changed, feed_id)
    return RedirectResponse(url=f"/?heard_count={changed}#feed-{feed_id}", status_code=303)


@router.post("/queue/playlist/bulk-action")
def bulk_playlist_action(
    action: str = Form(...),
    episode_ids: list[int] = Form(default=[]),
    session: Session = Depends(get_session),
):
    """Mark selected playlist episodes heard or move them up/down as one ordered group."""
    if action not in {"heard_elsewhere", "move_up", "move_down"} or not episode_ids:
        return RedirectResponse(url="/queue#playlist", status_code=303)

    app_config = cache_manager.get_or_create_config(session)
    entries = select_playlist_entries(
        session,
        episodes_per_feed=app_config.master_feed_episodes_per_podcast,
        preferred_first=app_config.feed_exposure_mode == "master_preferred",
    )
    current_ids = {episode.id for _, episode in entries}
    selected_ids = set(episode_ids) & current_ids
    if not selected_ids:
        return RedirectResponse(url="/queue#playlist", status_code=303)

    if action == "heard_elsewhere":
        cleanup_items = []
        for _, episode in entries:
            if episode.id in selected_ids:
                cleanup = _prepare_episode_as_heard_elsewhere(session, episode)
                if cleanup is not None:
                    cleanup_items.append(cleanup)
        if cleanup_items:
            # Commit all episode/segment changes together before removing local files.
            session.commit()
            for cleanup_episode_id, feed_id, stored_paths, title in cleanup_items:
                _delete_episode_local_files(cleanup_episode_id, feed_id, stored_paths)
                log.info(
                    "Episode %s (%s) marked as heard elsewhere via bulk action; local files removed",
                    cleanup_episode_id,
                    title,
                )
    else:
        entries = _move_selected_playlist_entries(entries, selected_ids, action)
        _persist_playlist_order(session, entries)
        session.commit()
        log.info(
            "Queue playlist bulk-moved episodes %s direction=%s",
            sorted(selected_ids),
            action,
        )

    return RedirectResponse(url="/queue#playlist", status_code=303)


@router.post("/queue/playlist/reset")
def reset_playlist_order(session: Session = Depends(get_session)):
    """Return the playlist to the normal preferred/date-based order."""
    positioned_episodes = session.exec(
        select(Episode).where(Episode.playlist_position.is_not(None))
    ).all()
    reset_count = len(positioned_episodes)
    for episode in positioned_episodes:
        episode.playlist_position = None
        session.add(episode)
    session.commit()
    log.info("Reset manual playlist order for %s episodes", reset_count)
    return RedirectResponse(url="/queue#playlist", status_code=303)


@router.post("/queue/playlist/reorder")
def reorder_playlist_episode(
    episode_id: int = Form(...),
    action: str = Form(...),
    position: str = Form(""),
    session: Session = Depends(get_session),
):
    """Move one visible playlist episode and persist the new order for this playlist.

    Positions are 1-based. Only entries currently selected for the Queue playlist can
    be moved, which keeps the action aligned with the master-feed selection settings.
    """
    if action not in {"top", "bottom", "position"}:
        return RedirectResponse(url="/queue#playlist", status_code=303)

    app_config = cache_manager.get_or_create_config(session)
    entries = select_playlist_entries(
        session,
        episodes_per_feed=app_config.master_feed_episodes_per_podcast,
        preferred_first=app_config.feed_exposure_mode == "master_preferred",
    )
    current_ids = [episode.id for _, episode in entries]
    if episode_id not in current_ids:
        return RedirectResponse(url="/queue#playlist", status_code=303)

    moving_index = current_ids.index(episode_id)
    moving = entries.pop(moving_index)
    if action == "top":
        target_index = 0
    elif action == "bottom":
        target_index = len(entries)
    else:
        try:
            requested_position = int(position)
        except (TypeError, ValueError):
            return RedirectResponse(url="/queue#playlist", status_code=303)
        if requested_position < 1:
            return RedirectResponse(url="/queue#playlist", status_code=303)
        target_index = min(requested_position - 1, len(entries))

    entries.insert(target_index, moving)
    _persist_playlist_order(session, entries)
    session.commit()
    log.info("Queue playlist reordered episode %s action=%s position=%s", episode_id, action, target_index + 1)
    return RedirectResponse(url="/queue#playlist", status_code=303)


@router.post("/episodes/{episode_id}/skip")
def skip_episode(episode_id: int, session: Session = Depends(get_session)):
    """Takes an episode out of the queue for good without ever running it through the
    pipeline - e.g. old backlog entries that will never become their feed's newest
    published episode anyway, so the master feed would never show them regardless of
    whether they're processed. The row (and its guid) is kept, not deleted, so
    feed_ingest.poll_feed() won't re-create/re-queue it on a later poll."""
    episode = session.get(Episode, episode_id)
    if episode:
        episode.status = EpisodeStatus.SKIPPED
        session.add(episode)
        session.commit()
    return RedirectResponse(url="/queue", status_code=303)


@router.post("/queue/skip-selected")
def skip_selected_episodes(episode_ids: list[int] = Form(default=[]), session: Session = Depends(get_session)):
    """Same as skip_episode() but for a whole multi-selected batch at once, so working
    through a large backlog of episodes that will never make it into the master feed
    doesn't mean confirming a browser dialog once per episode."""
    for episode_id in episode_ids:
        episode = session.get(Episode, episode_id)
        if episode:
            episode.status = EpisodeStatus.SKIPPED
            session.add(episode)
    session.commit()
    return RedirectResponse(url="/queue", status_code=303)


@router.post("/feeds/{feed_id}/episodes/bulk-action")
def bulk_feed_episode_action(
    feed_id: int,
    action: str = Form(...),
    episode_ids: list[int] = Form(default=[]),
    session: Session = Depends(get_session),
):
    """Apply feed-scoped actions to selected episodes using existing single-item logic."""
    allowed_actions = {"delete_audio", "reprocess", "heard_elsewhere"}
    if action not in allowed_actions or not episode_ids:
        return RedirectResponse(url=f"/feeds/{feed_id}#feed-episodes", status_code=303)

    feed = session.get(Feed, feed_id)
    if feed is None:
        return RedirectResponse(url="/", status_code=303)

    # Constrain posted IDs to the feed currently shown; do not accept foreign episode IDs.
    selected_ids = sorted(set(episode_ids))
    episodes = session.exec(
        select(Episode)
        .where(Episode.feed_id == feed_id)
        .where(Episode.id.in_(selected_ids))
        .order_by(Episode.created_at.desc())
    ).all()

    changed = 0
    heard_cleanup: list[tuple[int, int, dict[str, str | None], str]] = []
    active_statuses = {
        EpisodeStatus.DOWNLOADING,
        EpisodeStatus.TRANSCRIBING,
        EpisodeStatus.ANALYZING,
        EpisodeStatus.CUTTING,
    }

    if action == "delete_audio":
        # Same semantics as the per-episode "Löschen" button: evict cached audio,
        # but retain the DB row/GUID so the episode may be downloaded again later.
        for episode in episodes:
            if episode.heard_elsewhere or episode.status != EpisodeStatus.PUBLISHED:
                continue
            if not episode.original_audio_path and not episode.processed_audio_path:
                continue
            cache_manager.evict_episode_audio(session, episode)
            changed += 1
        session.commit()

    elif action == "reprocess":
        # Match the individual "Neu analysieren" action and avoid interrupting active workers.
        for episode in episodes:
            if episode.heard_elsewhere or episode.status in active_statuses:
                continue
            if episode.status not in {EpisodeStatus.PENDING_REVIEW, EpisodeStatus.PUBLISHED}:
                continue
            episode.status = EpisodeStatus.REANALYZE
            episode.error_message = None
            episode.updated_at = datetime.now(timezone.utc)
            session.add(episode)
            changed += 1
        session.commit()

    else:  # heard_elsewhere
        # The shared helper only accepts published episodes. Commit tombstones first,
        # then delete their local files after the database transaction succeeds.
        for episode in episodes:
            cleanup = _prepare_episode_as_heard_elsewhere(session, episode)
            if cleanup is not None:
                heard_cleanup.append(cleanup)
        if heard_cleanup:
            session.commit()
            for cleanup_episode_id, cleanup_feed_id, stored_paths, title in heard_cleanup:
                _delete_episode_local_files(cleanup_episode_id, cleanup_feed_id, stored_paths)
                log.info(
                    "Bulk marked episode %s (%s) as heard elsewhere; local files removed",
                    cleanup_episode_id,
                    title,
                )
        changed = len(heard_cleanup)

    log.info(
        "Feed bulk action %s changed %s/%s selected episodes for feed %s",
        action,
        changed,
        len(selected_ids),
        feed_id,
    )
    # URL remains the same (fragment only), allowing the scroll-restoration script
    # to recover the user's former feed-detail position after POST/Redirect/GET.
    return RedirectResponse(url=f"/feeds/{feed_id}#feed-episodes", status_code=303)
