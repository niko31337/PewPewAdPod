from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models import Episode, EpisodeStatus, Feed
from app.paths import resolve_cover_path
from app.services import branding, cache_manager
from app.services.feed_generator import build_feed_xml, build_master_feed_xml
from app.services.feed_playlist import select_playlist_entries

router = APIRouter()


@router.api_route("/feed/master.xml", methods=["GET", "HEAD"])
def master_feed(request: Request, session: Session = Depends(get_session)):
    base_url = str(request.base_url)
    feeds = session.exec(select(Feed).where(Feed.active == True)).all()  # noqa: E712

    cover_path = branding.ensure_master_cover()
    master_cover_url = f"{base_url}media/covers/{cover_path.relative_to(settings.covers_dir).as_posix()}"

    app_config = cache_manager.get_or_create_config(session)
    episodes_per_podcast = app_config.master_feed_episodes_per_podcast
    preferred_first = app_config.feed_exposure_mode == "master_preferred"

    entries = []
    selected = select_playlist_entries(
        session,
        episodes_per_feed=episodes_per_podcast,
        preferred_first=preferred_first,
        feed_ids={feed.id for feed in feeds},
    )
    for feed, episode in selected:
        episode_cover = resolve_cover_path(
            episode.local_image_path, settings.covers_dir / str(feed.id) / "episodes" / f"{episode.id}.jpg"
        )
        feed_cover = resolve_cover_path(
            feed.cover_image_local_path, settings.covers_dir / str(feed.id) / "cover.jpg"
        )
        source_image = episode_cover or feed_cover
        watermarked = branding.ensure_watermarked_episode_image(
            str(source_image) if source_image else None, feed.id, episode.id
        )
        image_url = (
            f"{base_url}media/covers/master/episodes/{episode.id}.jpg" if watermarked else master_cover_url
        )
        entries.append((feed, episode, image_url))

    feed_path = f"{settings.master_feed_secret_token}/master.xml" if settings.master_feed_secret_token else "feed/master.xml"
    xml_bytes = build_master_feed_xml(
        entries, base_url, master_cover_url, feed_path, preferred_first=preferred_first
    )
    return Response(content=xml_bytes, media_type="application/rss+xml")


@router.api_route("/feed/{feed_id}.xml", methods=["GET", "HEAD"])
def public_feed(feed_id: int, request: Request, session: Session = Depends(get_session)):
    feed = session.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed not found")
    episodes = session.exec(select(Episode).where(Episode.feed_id == feed_id)).all()
    xml_bytes = build_feed_xml(feed, episodes, str(request.base_url))
    return Response(content=xml_bytes, media_type="application/rss+xml")
