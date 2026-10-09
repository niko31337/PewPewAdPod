import logging
import shutil

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models import AdSegment, Episode, Feed
from app.services import cache_manager, feed_ingest
from app.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
def index(request: Request, opml_imported: int | None = None, session: Session = Depends(get_session)):
    feeds = session.exec(select(Feed).order_by(Feed.created_at)).all()
    feed_exposure_mode = cache_manager.get_or_create_config(session).feed_exposure_mode
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "feeds": feeds,
            "default_threshold": settings.default_auto_cut_threshold,
            "opml_imported": opml_imported,
            "feed_exposure_mode": feed_exposure_mode,
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


@router.post("/feeds/bulk-action")
def bulk_action(
    action: str = Form(...),
    feed_ids: list[int] = Form(default=[]),
    session: Session = Depends(get_session),
):
    """Apply one safe bulk operation to the selected feeds."""
    allowed_actions = {
        "activate",
        "deactivate",
        "toggle_auto_cut",
        "enable_auto_cut",
        "disable_auto_cut",
        "set_preferred",
        "clear_preferred",
    }
    if action not in allowed_actions or not feed_ids:
        return RedirectResponse(url="/", status_code=303)

    feeds = session.exec(select(Feed).where(Feed.id.in_(feed_ids))).all()
    selected = {feed.id for feed in feeds}

    for feed in feeds:
        if action == "activate":
            feed.active = True
        elif action == "deactivate":
            feed.active = False
        elif action == "toggle_auto_cut":
            feed.auto_cut = not feed.auto_cut
        elif action == "enable_auto_cut":
            feed.auto_cut = True
        elif action == "disable_auto_cut":
            feed.auto_cut = False
        elif action == "set_preferred":
            feed.preferred = True
        elif action == "clear_preferred":
            feed.preferred = False
        session.add(feed)

    session.commit()
    log.info("Bulk action %s applied to feeds %s", action, sorted(selected))
    return RedirectResponse(url="/", status_code=303)


@router.post("/feeds/{feed_id}/toggle-preferred")
def toggle_preferred(feed_id: int, session: Session = Depends(get_session)):
    feed = session.get(Feed, feed_id)
    if feed:
        feed.preferred = not feed.preferred
        session.add(feed)
        session.commit()
    return RedirectResponse(url="/", status_code=303)


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
