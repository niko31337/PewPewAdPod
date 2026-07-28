from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.database import get_session
from app.models import Episode, EpisodeStatus, Feed
from app.routers.episodes import _QUEUED_STATUSES
from app.templating import templates

router = APIRouter()


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
    return templates.TemplateResponse(request, "queue.html", {"rows": rows})


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
