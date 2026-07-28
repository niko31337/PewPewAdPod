from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models import Feed
from app.services import ad_detector, cache_manager
from app.templating import templates

router = APIRouter()


def _parse_optional_int(value: str) -> int | None:
    value = value.strip()
    return int(value) if value else None


def _parse_optional_float(value: str) -> float | None:
    value = value.strip()
    return float(value) if value else None


def _parse_positive_int(value: str, default: int) -> int:
    value = value.strip()
    if not value:
        return default
    return max(1, int(value))


@router.get("/config")
def config_page(request: Request, session: Session = Depends(get_session)):
    config = cache_manager.get_or_create_config(session)
    summary = cache_manager.cache_summary(session)
    feeds = {f.id: f.title for f in session.exec(select(Feed)).all()}
    per_feed = [
        {"feed_title": feeds.get(feed_id, f"Feed {feed_id}"), "count": count}
        for feed_id, count in summary["per_feed_counts"].items()
    ]
    default_min_duplicate_seconds = ad_detector.load_keyword_config(settings.ad_keywords_path).duplicates.min_duplicate_seconds
    return templates.TemplateResponse(
        request,
        "config.html",
        {
            "config": config,
            "summary": summary,
            "per_feed": per_feed,
            "default_min_duplicate_seconds": default_min_duplicate_seconds,
        },
    )


@router.post("/config")
def update_config(
    request: Request,
    max_episodes_per_feed: str = Form(""),
    max_cache_size_mb: str = Form(""),
    min_duplicate_seconds: str = Form(""),
    llm_ad_detection_enabled: bool = Form(False),
    master_feed_episodes_per_podcast: str = Form("1"),
    session: Session = Depends(get_session),
):
    config = cache_manager.get_or_create_config(session)
    config.max_episodes_per_feed = _parse_optional_int(max_episodes_per_feed)
    config.max_cache_size_mb = _parse_optional_float(max_cache_size_mb)
    config.min_duplicate_seconds = _parse_optional_float(min_duplicate_seconds)
    config.llm_ad_detection_enabled = llm_ad_detection_enabled
    config.master_feed_episodes_per_podcast = _parse_positive_int(master_feed_episodes_per_podcast, default=1)
    config.updated_at = datetime.now(timezone.utc)
    session.add(config)
    session.commit()

    cache_manager.enforce_cache_limits(session)

    return RedirectResponse(url="/config", status_code=303)
