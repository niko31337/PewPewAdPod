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
        "pubdate_display": (episode.pubdate.strftime("%Y-%m-%d") if episode.pubdate else ""),
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


@router.post("/queue/episodes/{episode_id}/heard-elsewhere")
def mark_episode_heard_elsewhere(episode_id: int, session: Session = Depends(get_session)):
    """Permanently exclude a published episode the user has already heard elsewhere.

    The Episode row/GUID is retained as a tombstone so RSS polling won't re-create it.
    Database state is committed before deleting files, and file deletion is restricted to
    the app's configured audio/transcript/cover directories.
    """
    episode = session.get(Episode, episode_id)
    if episode is None:
        return RedirectResponse(url="/queue#playlist", status_code=303)
    if episode.heard_elsewhere:
        return RedirectResponse(url="/queue#playlist", status_code=303)
    # This action is intended for published rows shown in the playlist, not work in progress.
    if episode.status != EpisodeStatus.PUBLISHED:
        return RedirectResponse(url="/queue#playlist", status_code=303)

    stored_paths = {
        "original_audio_path": episode.original_audio_path,
        "processed_audio_path": episode.processed_audio_path,
        "correction_original_path": episode.correction_original_path,
        "correction_processed_path": episode.correction_processed_path,
        "local_image_path": episode.local_image_path,
    }
    feed_id = episode.feed_id

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
    episode.updated_at = datetime.now(timezone.utc)

    for segment in session.exec(select(AdSegment).where(AdSegment.episode_id == episode_id)).all():
        session.delete(segment)
    session.add(episode)
    session.commit()

    _delete_episode_local_files(episode_id, feed_id, stored_paths)
    log.info("Episode %s (%s) marked as heard elsewhere; local files removed", episode_id, episode.title)
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
