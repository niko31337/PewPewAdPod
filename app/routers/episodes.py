from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models import Episode, EpisodeStatus, Feed
from app.services import cache_manager, pipeline
from app.templating import templates

router = APIRouter()

RECENT_EPISODES_SHOWN = 5


@router.get("/feeds/{feed_id}")
def feed_detail(feed_id: int, request: Request, session: Session = Depends(get_session)):
    feed = session.get(Feed, feed_id)
    if feed is None:
        return RedirectResponse(url="/", status_code=303)
    episodes = session.exec(
        select(Episode)
        .where(Episode.feed_id == feed_id)
        .order_by(Episode.created_at.desc())
        .limit(RECENT_EPISODES_SHOWN)
    ).all()
    return templates.TemplateResponse(
        request,
        "feed_detail.html",
        {"feed": feed, "episodes": episodes, "default_threshold": settings.default_auto_cut_threshold},
    )


@router.post("/episodes/{episode_id}/delete-audio")
def delete_episode_audio_route(episode_id: int, session: Session = Depends(get_session)):
    episode = session.get(Episode, episode_id)
    if episode:
        cache_manager.evict_episode_audio(session, episode)
        session.commit()
        return RedirectResponse(url=f"/feeds/{episode.feed_id}", status_code=303)
    return RedirectResponse(url="/", status_code=303)


@router.post("/episodes/{episode_id}/retry")
def retry_episode(episode_id: int, session: Session = Depends(get_session)):
    episode = session.get(Episode, episode_id)
    if episode:
        episode.status = EpisodeStatus.NEW
        episode.retry_count = 0
        episode.error_message = None
        session.add(episode)
        session.commit()
        return RedirectResponse(url=f"/feeds/{episode.feed_id}", status_code=303)
    return RedirectResponse(url="/", status_code=303)


@router.post("/episodes/{episode_id}/reprocess")
def reprocess_episode_route(episode_id: int, session: Session = Depends(get_session)):
    episode = session.get(Episode, episode_id)
    if episode:
        episode.status = EpisodeStatus.REANALYZE
        episode.error_message = None
        session.add(episode)
        session.commit()
        return RedirectResponse(url=f"/feeds/{episode.feed_id}", status_code=303)
    return RedirectResponse(url="/", status_code=303)


@router.post("/episodes/{episode_id}/apply-correction")
def apply_correction_route(episode_id: int, session: Session = Depends(get_session)):
    episode = session.get(Episode, episode_id)
    if episode:
        pipeline.apply_correction(session, episode)
        return RedirectResponse(url=f"/feeds/{episode.feed_id}", status_code=303)
    return RedirectResponse(url="/", status_code=303)


@router.post("/episodes/{episode_id}/discard-correction")
def discard_correction_route(episode_id: int, session: Session = Depends(get_session)):
    episode = session.get(Episode, episode_id)
    if episode:
        pipeline.discard_correction(session, episode)
        return RedirectResponse(url=f"/feeds/{episode.feed_id}", status_code=303)
    return RedirectResponse(url="/", status_code=303)
