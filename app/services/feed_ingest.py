import logging
from datetime import datetime, timezone
from time import mktime

import feedparser
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


def poll_feed(session: Session, feed: Feed) -> int:
    """Poll a single feed, create Episode rows for new entries (and flag replaced audio
    on already-known ones as a correction). Returns count of new episodes."""
    log.info("Polling feed %s (%s)", feed.id, feed.original_rss_url)
    parsed = feedparser.parse(feed.original_rss_url)
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
    # Keep the transaction lifetime per feed short. The HTTP parse/download work can
    # take a long time, and sharing one Session across dozens of feeds can leave a
    # SQLite write transaction open while the next feed is being fetched. That
    # contends with process_queue_job/correction_queue_job and can surface as
    # "database is locked".
    with session_scope() as session:
        feed_ids = list(session.exec(select(Feed.id).where(Feed.active == True)).all())  # noqa: E712

    for feed_id in feed_ids:
        try:
            with session_scope() as session:
                feed = session.get(Feed, feed_id)
                if feed is None or not feed.active:
                    continue
                poll_feed(session, feed)
        except Exception:
            # The failed feed gets its own rolled-back/closed Session, so one bad feed
            # cannot poison the Session used for all following feeds.
            log.exception("Error polling feed %s", feed_id)
