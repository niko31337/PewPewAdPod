from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.database import get_session
from app.models import Episode, EpisodeStatus, Feed
from app.services import audio_editor, cache_manager
from app.services.feed_playlist import select_playlist_entries
from app.paths import resolve_stored_path
from app.routers.episodes import _QUEUED_STATUSES
from app.templating import templates

router = APIRouter()


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
        select(Episode).where(Episode.status.in_(_QUEUED_STATUSES)).order_by(Episode.created_at)
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
