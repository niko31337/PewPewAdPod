from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models import Episode, EpisodeStatus, Feed
from app.services import branding
from app.services.feed_generator import build_feed_xml, build_master_feed_xml

router = APIRouter()


@router.api_route("/feed/master.xml", methods=["GET", "HEAD"])
def master_feed(request: Request, session: Session = Depends(get_session)):
    base_url = str(request.base_url)
    feeds = session.exec(select(Feed).where(Feed.active == True)).all()  # noqa: E712

    cover_path = branding.ensure_master_cover()
    master_cover_url = f"{base_url}media/covers/{cover_path.relative_to(settings.covers_dir).as_posix()}"

    entries = []
    for feed in feeds:
        episode = session.exec(
            select(Episode)
            .where(Episode.feed_id == feed.id)
            .where(Episode.status == EpisodeStatus.PUBLISHED)
            .order_by(Episode.pubdate.desc())
        ).first()
        if episode is None or not episode.processed_audio_path:
            continue

        source_image = episode.local_image_path or feed.cover_image_local_path
        watermarked = branding.ensure_watermarked_episode_image(source_image, feed.id, episode.id)
        image_url = (
            f"{base_url}media/covers/master/episodes/{episode.id}.jpg" if watermarked else master_cover_url
        )
        entries.append((feed, episode, image_url))

    xml_bytes = build_master_feed_xml(entries, base_url, master_cover_url)
    return Response(content=xml_bytes, media_type="application/rss+xml")


@router.api_route("/feed/{feed_id}.xml", methods=["GET", "HEAD"])
def public_feed(feed_id: int, request: Request, session: Session = Depends(get_session)):
    feed = session.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed not found")
    episodes = session.exec(select(Episode).where(Episode.feed_id == feed_id)).all()
    xml_bytes = build_feed_xml(feed, episodes, str(request.base_url))
    return Response(content=xml_bytes, media_type="application/rss+xml")
