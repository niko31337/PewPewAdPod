import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from pydub import AudioSegment
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.database import get_session, session_scope
from app.models import Episode, EpisodeStatus, Feed
from app.paths import resolve_stored_path
from app.services import audio_editor, downloader, feed_ingest, fingerprint
from app.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


def _episodes_with_audio(session: Session, feed_id: int) -> list[Episode]:
    episodes = session.exec(
        select(Episode).where(Episode.feed_id == feed_id).order_by(Episode.pubdate.desc())
    ).all()
    return [e for e in episodes if resolve_stored_path(e.original_audio_path)]


def _episode_duration_ms(episode: Episode) -> int:
    if episode.duration_seconds:
        return int(episode.duration_seconds * 1000)
    resolved = resolve_stored_path(episode.original_audio_path)
    duration = audio_editor.get_duration_seconds(resolved) if resolved else None
    return int(duration * 1000) if duration else 0


_ALREADY_QUEUED_STATUSES = [EpisodeStatus.NEW, EpisodeStatus.DOWNLOADING]


def _next_episode_without_audio(session: Session, feed_id: int) -> Episode | None:
    # Prefer episodes the background scheduler isn't already about to download itself -
    # picking one of those too would race the scheduler for the same destination file.
    # Only fall back to them if there's truly nothing else to offer.
    not_already_queued = session.exec(
        select(Episode)
        .where(Episode.feed_id == feed_id)
        .where(Episode.original_audio_path.is_(None))
        .where(Episode.status.not_in(_ALREADY_QUEUED_STATUSES))
        .order_by(Episode.pubdate.desc())
    ).first()
    if not_already_queued:
        return not_already_queued

    return session.exec(
        select(Episode)
        .where(Episode.feed_id == feed_id)
        .where(Episode.original_audio_path.is_(None))
        .order_by(Episode.pubdate.desc())
    ).first()


def _find_next_downloadable_episode(session: Session, feed: Feed) -> Episode | None:
    """An episode row without local audio, if one is already known; otherwise polls the
    source feed once to discover further entries (the initial poll only ingests the single
    newest episode) and retries."""
    candidate = _next_episode_without_audio(session, feed.id)
    if candidate:
        return candidate

    try:
        feed_ingest.poll_feed(session, feed)
    except Exception:
        log.warning("Could not poll feed %s while looking for a next episode", feed.id, exc_info=True)
        return None

    return _next_episode_without_audio(session, feed.id)


def _download_episode_audio(episode_id: int) -> None:
    with session_scope() as session:
        episode = session.get(Episode, episode_id)
        if episode is None:
            return
        path = downloader.download_file(episode.original_audio_url, settings.originals_dir / f"{episode.id}.mp3")
        episode.original_audio_path = str(path)
        episode.duration_seconds = audio_editor.get_duration_seconds(path)
        session.add(episode)
        session.commit()


@router.get("/jingle-finder")
def jingle_finder_home(
    request: Request,
    saved: str | None = None,
    downloaded: str | None = None,
    session: Session = Depends(get_session),
):
    feeds = session.exec(select(Feed)).all()
    return templates.TemplateResponse(
        request, "jingle_finder.html", {"feeds": feeds, "saved": saved, "downloaded": downloaded}
    )


@router.post("/jingle-finder/auto")
async def jingle_finder_auto(request: Request, feed_id: int = Form(...), session: Session = Depends(get_session)):
    feed = session.get(Feed, feed_id)
    episodes = _episodes_with_audio(session, feed_id)
    if feed is None or len(episodes) < 2:
        detail = f' (aktuell {len(episodes)} bei "{feed.title}")' if feed else ""
        return templates.TemplateResponse(
            request,
            "jingle_finder.html",
            {
                "feeds": session.exec(select(Feed)).all(),
                "error": "Für den automatischen Modus werden mindestens 2 lokal vorhandene "
                f"Episoden-Audiodateien dieses Feeds benötigt{detail}.",
                "low_episode_feed": feed,
            },
        )

    episode_a, episode_b = episodes[0], episodes[1]
    candidates = await run_in_threadpool(
        fingerprint.find_repeated_segments,
        resolve_stored_path(episode_a.original_audio_path),
        resolve_stored_path(episode_b.original_audio_path),
    )
    duration_ms = await run_in_threadpool(_episode_duration_ms, episode_a)

    return templates.TemplateResponse(
        request,
        "jingle_finder_auto.html",
        {
            "feed": feed,
            "episode_a": episode_a,
            "episode_b": episode_b,
            "candidates": candidates,
            "duration_ms": duration_ms,
        },
    )


@router.get("/jingle-finder/manual")
def jingle_finder_manual(request: Request, feed_id: int, session: Session = Depends(get_session)):
    feed = session.get(Feed, feed_id)
    episodes = _episodes_with_audio(session, feed_id)
    if feed is None or not episodes:
        return templates.TemplateResponse(
            request,
            "jingle_finder.html",
            {
                "feeds": session.exec(select(Feed)).all(),
                "error": "Für den manuellen Modus wird mindestens 1 lokal vorhandene "
                "Episoden-Audiodatei dieses Feeds benötigt.",
            },
        )
    episode = episodes[0]
    duration_ms = _episode_duration_ms(episode)
    return templates.TemplateResponse(
        request, "jingle_finder_manual.html", {"feed": feed, "episode": episode, "duration_ms": duration_ms}
    )


def _safe_jingle_filename(name: str) -> str:
    name = Path(name.strip() or "jingle").stem
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "jingle"
    return f"{name}.mp3"


def _unique_jingle_path(filename: str) -> Path:
    settings.ad_jingles_dir.mkdir(parents=True, exist_ok=True)
    base = Path(filename).stem
    candidate = settings.ad_jingles_dir / filename
    counter = 2
    while candidate.exists():
        candidate = settings.ad_jingles_dir / f"{base}_{counter}.mp3"
        counter += 1
    return candidate


def _extract_clip(source_path: str, dest: Path, start_s: float, end_s: float) -> None:
    audio = AudioSegment.from_file(source_path)
    clip = audio[int(start_s * 1000) : int(end_s * 1000)]
    dest.parent.mkdir(parents=True, exist_ok=True)
    clip.export(dest, format="mp3")


@router.post("/jingle-finder/extract")
async def jingle_finder_extract(
    episode_id: int = Form(...),
    start_s: float = Form(...),
    end_s: float = Form(...),
    filename: str = Form(...),
    session: Session = Depends(get_session),
):
    episode = session.get(Episode, episode_id)
    resolved_audio = resolve_stored_path(episode.original_audio_path) if episode else None
    if episode is None or not resolved_audio or end_s <= start_s:
        return RedirectResponse(url="/jingle-finder", status_code=303)

    dest = _unique_jingle_path(_safe_jingle_filename(filename))
    await run_in_threadpool(_extract_clip, str(resolved_audio), dest, start_s, end_s)
    log.info("Saved new jingle %s (%.1fs-%.1fs from episode %s)", dest.name, start_s, end_s, episode_id)

    return RedirectResponse(url=f"/jingle-finder?saved={dest.name}", status_code=303)


@router.post("/jingle-finder/download-next")
async def jingle_finder_download_next(
    request: Request, feed_id: int = Form(...), session: Session = Depends(get_session)
):
    feed = session.get(Feed, feed_id)
    if feed is None:
        return RedirectResponse(url="/jingle-finder", status_code=303)

    episode = await run_in_threadpool(_find_next_downloadable_episode, session, feed)
    if episode is None:
        return templates.TemplateResponse(
            request,
            "jingle_finder.html",
            {
                "feeds": session.exec(select(Feed)).all(),
                "error": f'Keine weitere Episode zum Herunterladen für "{feed.title}" gefunden.',
            },
        )

    await run_in_threadpool(_download_episode_audio, episode.id)
    log.info("Downloaded episode %s for feed %s ahead of jingle-finder auto mode", episode.id, feed.id)

    return RedirectResponse(url=f"/jingle-finder?downloaded={episode.title}", status_code=303)
