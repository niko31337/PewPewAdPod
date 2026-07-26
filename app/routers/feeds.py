import logging
import shutil

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models import AdSegment, Episode, Feed
from app.services import feed_ingest
from app.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
def index(request: Request, opml_imported: int | None = None, session: Session = Depends(get_session)):
    feeds = session.exec(select(Feed).order_by(Feed.created_at)).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "feeds": feeds,
            "default_threshold": settings.default_auto_cut_threshold,
            "opml_imported": opml_imported,
        },
    )


@router.post("/feeds")
def create_feed(
    request: Request,
    rss_url: str = Form(...),
    auto_cut: bool = Form(False),
    confidence_threshold: str = Form(""),
    session: Session = Depends(get_session),
):
    threshold = float(confidence_threshold) if confidence_threshold.strip() else None
    feed = Feed(
        title=rss_url,
        original_rss_url=rss_url,
        auto_cut=auto_cut,
        confidence_threshold=threshold,
    )
    session.add(feed)
    session.commit()
    session.refresh(feed)

    try:
        feed_ingest.poll_feed(session, feed)
    except Exception:
        log.exception("Initial poll failed for new feed %s", feed.id)

    return RedirectResponse(url="/", status_code=303)


@router.post("/feeds/{feed_id}/delete")
def delete_feed(feed_id: int, session: Session = Depends(get_session)):
    feed = session.get(Feed, feed_id)
    if feed:
        episodes = session.exec(select(Episode).where(Episode.feed_id == feed_id)).all()
        for ep in episodes:
            for seg in session.exec(select(AdSegment).where(AdSegment.episode_id == ep.id)).all():
                session.delete(seg)
            session.delete(ep)
        session.delete(feed)
        session.commit()

        cover_dir = settings.covers_dir / str(feed_id)
        shutil.rmtree(cover_dir, ignore_errors=True)

    return RedirectResponse(url="/", status_code=303)


@router.post("/feeds/{feed_id}/toggle-auto-cut")
def toggle_auto_cut(feed_id: int, session: Session = Depends(get_session)):
    feed = session.get(Feed, feed_id)
    if feed:
        feed.auto_cut = not feed.auto_cut
        session.add(feed)
        session.commit()
    return RedirectResponse(url=f"/feeds/{feed_id}", status_code=303)


@router.post("/feeds/{feed_id}/toggle-active")
def toggle_active(feed_id: int, session: Session = Depends(get_session)):
    """Inactive feeds are skipped by poll_all_feeds() (no new episodes discovered) and
    excluded from the master feed - existing episodes/subscriptions are untouched, this
    just pauses picking up anything new."""
    feed = session.get(Feed, feed_id)
    if feed:
        feed.active = not feed.active
        session.add(feed)
        session.commit()
    return RedirectResponse(url=f"/feeds/{feed_id}", status_code=303)


@router.post("/feeds/{feed_id}/update-threshold")
def update_threshold(feed_id: int, confidence_threshold: str = Form(""), session: Session = Depends(get_session)):
    feed = session.get(Feed, feed_id)
    if feed:
        feed.confidence_threshold = float(confidence_threshold) if confidence_threshold.strip() else None
        session.add(feed)
        session.commit()
    return RedirectResponse(url=f"/feeds/{feed_id}", status_code=303)


@router.post("/feeds/{feed_id}/refresh")
def refresh_feed(feed_id: int, session: Session = Depends(get_session)):
    feed = session.get(Feed, feed_id)
    if feed:
        try:
            feed_ingest.poll_feed(session, feed)
        except Exception:
            log.exception("Manual refresh failed for feed %s", feed_id)
    return RedirectResponse(url=f"/feeds/{feed_id}", status_code=303)
