import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.config import settings
from app.database import session_scope
from app.models import AdSegment, CorrectionStatus, Episode, EpisodeStatus, Feed, SegmentSource, SegmentStatus
from app.paths import resolve_stored_path
from app.services import audio_editor, cache_manager, downloader, jingle_detector, transcriber
from app.services.ad_detector import Candidate, detect_ad_segments

log = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc)


def _originals_path(episode_id: int):
    return settings.originals_dir / f"{episode_id}.mp3"


def _processed_path(episode_id: int):
    return settings.processed_dir / f"{episode_id}.mp3"


def _correction_originals_path(episode_id: int):
    return settings.originals_dir / f"{episode_id}_correction.mp3"


def _correction_processed_path(episode_id: int):
    return settings.processed_dir / f"{episode_id}_correction.mp3"


def _find_previous_episode_audio(session: Session, feed_id: int, exclude_episode_id: int) -> Path | None:
    """The most recent other episode of this feed that still has its original audio
    cached locally. Every analysis compares against it: segments that turn out acoustically
    identical in both episodes can't be unique spoken content (intro, outro, an unchanged
    ad read) and are always safe to flag for removal."""
    others = session.exec(
        select(Episode)
        .where(Episode.feed_id == feed_id)
        .where(Episode.id != exclude_episode_id)
        .order_by(Episode.pubdate.desc())
    ).all()
    for other in others:
        resolved = resolve_stored_path(other.original_audio_path)
        if resolved:
            return resolved
    return None


def _set_status(session: Session, episode: Episode, status: str) -> None:
    episode.status = status
    episode.updated_at = _now()
    session.add(episode)
    session.commit()


def persist_ad_segments(
    session: Session, episode: Episode, candidates: list[Candidate], is_correction: bool = False
) -> list[AdSegment]:
    # Re-analysis (e.g. after adding new jingles, or retrying a failed episode past the
    # ANALYZING stage) must not pile up duplicates of a previous, still-undecided candidate
    # set. Already accepted/rejected segments are left untouched. Correction and original
    # segments are kept fully separate so re-analyzing one never touches the other's.
    stale = session.exec(
        select(AdSegment)
        .where(AdSegment.episode_id == episode.id)
        .where(AdSegment.status == SegmentStatus.PENDING)
        .where(AdSegment.is_correction == is_correction)
    ).all()
    for row in stale:
        session.delete(row)

    rows = []
    for c in candidates:
        row = AdSegment(
            episode_id=episode.id,
            start_ms=c.start_ms,
            end_ms=c.end_ms,
            confidence=c.confidence,
            matched_keywords=json.dumps(sorted({h.keyword for h in c.keyword_hits})),
            matched_jingles=json.dumps(sorted({h.jingle_filename for h in c.jingle_hits})),
            transcript_snippet=c.transcript_snippet,
            source=c.source or SegmentSource.MERGED,
            status=SegmentStatus.PENDING,
            is_correction=is_correction,
            is_duplicate_match=c.duplicate_confidence > 0,
        )
        session.add(row)
        rows.append(row)
    session.commit()
    return rows


def record_jingle_matches(session: Session, feed_id: int, candidates: list[Candidate]) -> None:
    filenames = {h.jingle_filename for c in candidates for h in c.jingle_hits}
    for filename in filenames:
        jingle_detector.record_jingle_match(session, feed_id, filename)
    if filenames:
        session.commit()


def apply_cut(session: Session, episode: Episode, accepted_ranges: list[tuple[int, int]]) -> None:
    source = _originals_path(episode.id)
    dest = _processed_path(episode.id)
    audio_editor.cut_and_export(source, dest, accepted_ranges, crossfade_ms=settings.crossfade_ms)
    episode.processed_audio_path = str(dest)
    episode.processed_size_bytes = dest.stat().st_size
    episode.status = EpisodeStatus.PUBLISHED
    episode.published_at = _now()
    episode.updated_at = _now()
    session.add(episode)
    session.commit()

    cache_manager.enforce_cache_limits(session)


def _analyze_and_finalize(
    session: Session, episode: Episode, feed: Feed, path: Path, transcript: list
) -> None:
    """Runs ad detection against already-downloaded audio + an existing transcript, then
    either auto-cuts and publishes or leaves the episode pending manual review. Shared by
    the full pipeline and by reprocess_episode() (e.g. after new jingles were added)."""
    _set_status(session, episode, EpisodeStatus.ANALYZING)
    previous_audio_path = _find_previous_episode_audio(session, feed.id, episode.id)
    candidates = detect_ad_segments(
        path,
        transcript,
        settings.ad_keywords_path,
        session=session,
        feed_id=feed.id,
        jingles_dir=settings.ad_jingles_dir,
        previous_episode_audio_path=previous_audio_path,
    )
    persist_ad_segments(session, episode, candidates)
    record_jingle_matches(session, feed.id, candidates)

    if feed.auto_cut:
        threshold = feed.confidence_threshold or settings.default_auto_cut_threshold
        accepted = [c for c in candidates if c.confidence >= threshold]
        for c in candidates:
            seg = session.exec(
                select(AdSegment)
                .where(AdSegment.episode_id == episode.id)
                .where(AdSegment.start_ms == c.start_ms)
                .where(AdSegment.end_ms == c.end_ms)
            ).first()
            if seg:
                seg.status = SegmentStatus.ACCEPTED if c in accepted else SegmentStatus.REJECTED
                session.add(seg)
        session.commit()

        _set_status(session, episode, EpisodeStatus.CUTTING)
        ranges = [(c.start_ms, c.end_ms) for c in accepted]
        apply_cut(session, episode, ranges)
        log.info("Episode %s auto-cut and published (%d segments removed)", episode.id, len(ranges))
    else:
        _set_status(session, episode, EpisodeStatus.PENDING_REVIEW)
        log.info("Episode %s awaiting manual review (%d candidates)", episode.id, len(candidates))


def _mark_failed(session: Session, episode: Episode, exc: Exception) -> None:
    log.exception("Pipeline failed for episode %s at status=%s", episode.id, episode.status)
    failed_status = {
        EpisodeStatus.DOWNLOADING: EpisodeStatus.FAILED_DOWNLOAD,
        EpisodeStatus.TRANSCRIBING: EpisodeStatus.FAILED_TRANSCRIBE,
        EpisodeStatus.ANALYZING: EpisodeStatus.FAILED_ANALYZE,
        EpisodeStatus.CUTTING: EpisodeStatus.FAILED_CUT,
    }.get(episode.status, EpisodeStatus.FAILED_DOWNLOAD)

    episode.status = failed_status
    episode.error_message = str(exc)
    episode.retry_count += 1
    if episode.retry_count >= settings.max_retries:
        episode.status = EpisodeStatus.ERROR_PERMANENT
    episode.updated_at = _now()
    session.add(episode)
    session.commit()


def process_episode(episode_id: int) -> None:
    with session_scope() as session:
        episode = session.get(Episode, episode_id)
        if episode is None:
            log.warning("process_episode: episode %s not found", episode_id)
            return
        feed = session.get(Feed, episode.feed_id)

        try:
            _set_status(session, episode, EpisodeStatus.DOWNLOADING)
            path = downloader.download_file(episode.original_audio_url, _originals_path(episode.id))
            episode.original_audio_path = str(path)
            episode.duration_seconds = audio_editor.get_duration_seconds(path)
            session.add(episode)
            session.commit()

            if episode.itunes_image_url:
                img_dest = settings.covers_dir / str(feed.id) / "episodes" / f"{episode.id}.jpg"
                if downloader.download_image(episode.itunes_image_url, img_dest):
                    episode.local_image_path = str(img_dest)
                    session.add(episode)
                    session.commit()

            _set_status(session, episode, EpisodeStatus.TRANSCRIBING)
            transcript = transcriber.transcribe_audio(path)
            transcriber.save_transcript_json(episode.id, transcript)

            _analyze_and_finalize(session, episode, feed, path, transcript)

        except Exception as exc:
            _mark_failed(session, episode, exc)


def reprocess_episode(episode_id: int) -> None:
    """Re-runs ad detection (e.g. after adding new jingles) without re-downloading or
    re-transcribing when a valid local copy of both already exists. Falls back to the full
    pipeline if the cached audio/transcript is missing (e.g. evicted by the cache limit)."""
    run_full_pipeline = False

    with session_scope() as session:
        episode = session.get(Episode, episode_id)
        if episode is None:
            log.warning("reprocess_episode: episode %s not found", episode_id)
            return
        feed = session.get(Feed, episode.feed_id)

        resolved_audio = resolve_stored_path(episode.original_audio_path)
        has_audio = resolved_audio is not None
        transcript = transcriber.load_transcript_json(episode.id) if has_audio else []

        if not has_audio or not transcript:
            log.info(
                "Episode %s has no cached audio/transcript to reuse - running the full pipeline instead",
                episode_id,
            )
            episode.status = EpisodeStatus.NEW
            episode.retry_count = 0
            episode.error_message = None
            session.add(episode)
            session.commit()
            run_full_pipeline = True
        else:
            try:
                _analyze_and_finalize(session, episode, feed, resolved_audio, transcript)
            except Exception as exc:
                _mark_failed(session, episode, exc)

    if run_full_pipeline:
        process_episode(episode_id)


def _mark_correction_failed(session: Session, episode: Episode, exc: Exception) -> None:
    log.exception("Correction processing failed for episode %s", episode.id)
    episode.correction_status = CorrectionStatus.FAILED
    episode.correction_error_message = str(exc)
    episode.updated_at = _now()
    session.add(episode)
    session.commit()


def process_episode_correction(episode_id: int) -> None:
    """Downloads and processes a replacement audio file the podcaster published for an
    already-processed episode. Entirely separate files/paths from the original - nothing
    under original_audio_path/processed_audio_path is read or written here."""
    with session_scope() as session:
        episode = session.get(Episode, episode_id)
        if episode is None or not episode.correction_audio_url:
            log.warning("process_episode_correction: episode %s has no pending correction", episode_id)
            return
        feed = session.get(Feed, episode.feed_id)

        try:
            episode.correction_status = CorrectionStatus.DOWNLOADING
            episode.updated_at = _now()
            session.add(episode)
            session.commit()

            path = downloader.download_file(episode.correction_audio_url, _correction_originals_path(episode.id))
            episode.correction_original_path = str(path)
            session.add(episode)
            session.commit()

            episode.correction_status = CorrectionStatus.TRANSCRIBING
            session.add(episode)
            session.commit()
            transcript = transcriber.transcribe_audio(path)
            transcriber.save_transcript_json(f"{episode.id}_correction", transcript)

            episode.correction_status = CorrectionStatus.ANALYZING
            session.add(episode)
            session.commit()
            previous_audio_path = _find_previous_episode_audio(session, feed.id, episode.id)
            candidates = detect_ad_segments(
                path,
                transcript,
                settings.ad_keywords_path,
                session=session,
                feed_id=feed.id,
                jingles_dir=settings.ad_jingles_dir,
                previous_episode_audio_path=previous_audio_path,
            )
            persist_ad_segments(session, episode, candidates, is_correction=True)
            record_jingle_matches(session, feed.id, candidates)

            threshold = feed.confidence_threshold or settings.default_auto_cut_threshold
            accepted_ranges = [(c.start_ms, c.end_ms) for c in candidates if c.confidence >= threshold]

            episode.correction_status = CorrectionStatus.CUTTING
            session.add(episode)
            session.commit()
            dest = _correction_processed_path(episode.id)
            audio_editor.cut_and_export(path, dest, accepted_ranges, crossfade_ms=settings.crossfade_ms)
            episode.correction_processed_path = str(dest)
            episode.correction_status = CorrectionStatus.READY
            episode.updated_at = _now()
            session.add(episode)
            session.commit()
            log.info(
                "Correction ready for episode %s (%d segment(s) removed) - awaiting review",
                episode.id,
                len(accepted_ranges),
            )
        except Exception as exc:
            _mark_correction_failed(session, episode, exc)


def apply_correction(session: Session, episode: Episode) -> None:
    """Promotes a ready correction to be the episode's published audio. The old
    processed/original files are left on disk untouched (just no longer referenced) -
    nothing gets deleted."""
    if not episode.correction_processed_path:
        return
    dest = resolve_stored_path(episode.correction_processed_path) or Path(episode.correction_processed_path)
    episode.processed_audio_path = str(dest)
    episode.processed_size_bytes = dest.stat().st_size if dest.exists() else None
    episode.original_audio_url = episode.correction_audio_url or episode.original_audio_url
    episode.status = EpisodeStatus.PUBLISHED
    episode.published_at = _now()
    episode.updated_at = _now()

    episode.correction_audio_url = None
    episode.correction_original_path = None
    episode.correction_processed_path = None
    episode.correction_status = None
    episode.correction_error_message = None
    episode.correction_detected_at = None
    session.add(episode)
    session.commit()

    cache_manager.enforce_cache_limits(session)
    log.info("Applied correction for episode %s - now published from %s", episode.id, dest.name)


def discard_correction(session: Session, episode: Episode) -> None:
    """Deletes the correction's own files (never the original) and clears its state."""
    for path_str in (episode.correction_original_path, episode.correction_processed_path):
        p = resolve_stored_path(path_str)
        if p:
            p.unlink(missing_ok=True)

    pending_correction_segments = session.exec(
        select(AdSegment).where(AdSegment.episode_id == episode.id).where(AdSegment.is_correction == True)  # noqa: E712
    ).all()
    for row in pending_correction_segments:
        session.delete(row)

    episode.correction_audio_url = None
    episode.correction_original_path = None
    episode.correction_processed_path = None
    episode.correction_status = None
    episode.correction_error_message = None
    episode.correction_detected_at = None
    episode.updated_at = _now()
    session.add(episode)
    session.commit()
    log.info("Discarded correction for episode %s", episode.id)
