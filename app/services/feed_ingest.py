import logging
import time
from datetime import datetime, timezone
from time import mktime

import feedparser
import httpx
from sqlmodel import Session, select

from app.config import settings
from app.database import session_scope
from app.models import CorrectionStatus, Episode, EpisodeStatus, Feed
from app.services.downloader import download_image

log = logging.getLogger(__name__)


def _struct_to_dt(struct_time) -> datetime | None:
    if not struct_time:
        return None
    return datetime.fromtimestamp(mktime(struct_time), tz=timezone.utc)


def _find_audio_url(entry) -> str | None:
    for enc in getattr(entry, "enclosures", []) or []:
        enc_type = enc.get("type", "")
        href = enc.get("href")
        if href and (enc_type.startswith("audio/") or not enc_type):
            return href
    for link in getattr(entry, "links", []) or []:
        if link.get("rel") == "enclosure" and link.get("href"):
            return link["href"]
    return None


def _find_episode_image(entry) -> str | None:
    image = entry.get("image")
    if image and image.get("href"):
        return image["href"]
    itunes_image = entry.get("itunes_image")
    if itunes_image and itunes_image.get("href"):
        return itunes_image["href"]
    return None


def _sync_feed_metadata(session: Session, feed: Feed, parsed) -> None:
    channel = parsed.feed
    feed.title = channel.get("title", feed.title) or feed.title
    feed.description = channel.get("subtitle") or channel.get("description") or feed.description
    feed.author = channel.get("author") or feed.author
    feed.language = channel.get("language", feed.language) or feed.language

    cover_url = None
    image = channel.get("image")
    if image and image.get("href"):
        cover_url = image["href"]
    else:
        itunes_image = channel.get("itunes_image")
        if itunes_image and itunes_image.get("href"):
            cover_url = itunes_image["href"]

    if cover_url and cover_url != feed.cover_image_source_url:
        dest = settings.covers_dir / str(feed.id) / "cover.jpg"
        if download_image(cover_url, dest):
            feed.cover_image_source_url = cover_url
            feed.cover_image_local_path = str(dest)

    feed.last_polled_at = datetime.now(timezone.utc)
    session.add(feed)


def _handle_possible_correction(session: Session, episode: Episode, new_audio_url: str) -> None:
    """Podcasters occasionally swap out an episode's audio (e.g. to fix an error in the
    original file). If we haven't downloaded anything for this episode yet, there's
    nothing to preserve - just point it at the new URL. Otherwise, never touch the
    already-downloaded/cut files; queue the new file as a correction to be downloaded and
    processed on the side, so the user can review and apply it explicitly."""
    if not episode.original_audio_path:
        episode.original_audio_url = new_audio_url
        session.add(episode)
        session.commit()
        return

    if episode.correction_audio_url == new_audio_url:
        return  # already tracking this exact replacement

    log.info(
        "Detected replaced audio for episode %s (feed %s) - queuing as a correction, "
        "original stays untouched",
        episode.id,
        episode.feed_id,
    )
    episode.correction_audio_url = new_audio_url
    episode.correction_status = CorrectionStatus.DETECTED
    episode.correction_error_message = None
    episode.correction_detected_at = datetime.now(timezone.utc)
    session.add(episode)
    session.commit()


_FEED_FETCH_TIMEOUT = httpx.Timeout(30.0)
_FEED_FETCH_DEADLINE_S = 90.0
_FEED_ACCEPT = (
    "application/atom+xml,application/rdf+xml,application/rss+xml,application/xml;q=0.9,"
    "text/xml;q=0.2,*/*;q=0.1"
)


def _fetch_feed_content(url: str) -> bytes | None:
    """Downloads the raw feed document with real timeouts. feedparser.parse(url) does its
    own HTTP via urllib with NO timeout, so a server that accepts the connection and then
    never answers hangs the call forever - and since poll_feeds_job allows a single
    instance, one such feed silently stops all polling (every later run is logged as
    "skipped: maximum number of running instances reached"). The per-read timeout alone
    doesn't stop a server that trickles bytes, hence the overall deadline too."""
    try:
        deadline = time.monotonic() + _FEED_FETCH_DEADLINE_S
        chunks: list[bytes] = []
        with httpx.stream(
            "GET",
            url,
            follow_redirects=True,
            timeout=_FEED_FETCH_TIMEOUT,
            headers={"User-Agent": feedparser.USER_AGENT, "Accept": _FEED_ACCEPT},
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                chunks.append(chunk)
                if time.monotonic() > deadline:
                    raise httpx.ReadTimeout(f"feed download exceeded {_FEED_FETCH_DEADLINE_S:.0f}s")
        return b"".join(chunks)
    except httpx.HTTPError as exc:
        log.warning("Could not fetch feed %s: %s", url, exc)
        return None


def poll_feed(session: Session, feed: Feed) -> int:
    """Poll a single feed, create Episode rows for new entries (and flag replaced audio
    on already-known ones as a correction). Returns count of new episodes."""
    log.info("Polling feed %s (%s)", feed.id, feed.original_rss_url)
    content = _fetch_feed_content(feed.original_rss_url)
    if content is None:
        return 0
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        log.warning("Feed %s failed to parse: %s", feed.id, getattr(parsed, "bozo_exception", "unknown"))
        return 0

    _sync_feed_metadata(session, feed, parsed)

    existing_episodes = {e.guid: e for e in session.exec(select(Episode).where(Episode.feed_id == feed.id)).all()}
    is_first_poll = len(existing_episodes) == 0
    entries = parsed.entries[:1] if is_first_poll else parsed.entries[: settings.feed_backfill_check]

    created = 0
    for entry in entries:
        guid = entry.get("id") or entry.get("link")
        if not guid:
            continue
        audio_url = _find_audio_url(entry)

        existing = existing_episodes.get(guid)
        if existing is not None:
            if audio_url and audio_url != existing.original_audio_url:
                _handle_possible_correction(session, existing, audio_url)
            continue

        if not audio_url:
            log.warning("No audio enclosure for entry %s in feed %s", guid, feed.id)
            continue

        episode = Episode(
            feed_id=feed.id,
            guid=guid,
            title=entry.get("title", "Untitled"),
            description=entry.get("summary") or entry.get("description"),
            pubdate=_struct_to_dt(entry.get("published_parsed") or entry.get("updated_parsed")),
            original_audio_url=audio_url,
            itunes_image_url=_find_episode_image(entry),
            itunes_duration=entry.get("itunes_duration"),
            itunes_season=entry.get("itunes_season"),
            itunes_episode=entry.get("itunes_episode"),
            status=EpisodeStatus.NEW,
        )
        session.add(episode)
        created += 1

    if created:
        session.commit()
        log.info("Feed %s: %d new episode(s) queued", feed.id, created)
    return created


def poll_all_feeds() -> None:
    with session_scope() as session:
        feeds = session.exec(select(Feed).where(Feed.active == True)).all()  # noqa: E712
        for feed in feeds:
            try:
                poll_feed(session, feed)
            except Exception:
                log.exception("Error polling feed %s", feed.id)
        log.info("Finished polling %d active feed(s)", len(feeds))
