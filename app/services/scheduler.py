import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import select

from app.config import settings
from app.database import session_scope
from app.models import CorrectionStatus, Episode, EpisodeStatus
from app.services import cache_manager, feed_ingest
from app.services.pipeline import process_episode, process_episode_correction, reprocess_episode

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

_FAILED_STATUSES = [
    EpisodeStatus.FAILED_DOWNLOAD,
    EpisodeStatus.FAILED_TRANSCRIBE,
    EpisodeStatus.FAILED_ANALYZE,
    EpisodeStatus.FAILED_CUT,
]


def poll_feeds_job() -> None:
    try:
        feed_ingest.poll_all_feeds()
    except Exception:
        log.exception("poll_feeds_job failed")


def process_queue_job() -> None:
    try:
        with session_scope() as session:
            episode = session.exec(
                select(Episode)
                .where(Episode.status.in_([EpisodeStatus.NEW, EpisodeStatus.REANALYZE]))
                .order_by(Episode.created_at)
            ).first()
            episode_id = episode.id if episode else None
            status = episode.status if episode else None
        if episode_id is not None:
            if status == EpisodeStatus.REANALYZE:
                reprocess_episode(episode_id)
            else:
                process_episode(episode_id)
    except Exception:
        log.exception("process_queue_job failed")


def retry_failed_job() -> None:
    try:
        with session_scope() as session:
            failed = session.exec(
                select(Episode)
                .where(Episode.status.in_(_FAILED_STATUSES))
                .where(Episode.retry_count < settings.max_retries)
            ).all()
            for episode in failed:
                episode.status = EpisodeStatus.NEW
                session.add(episode)
            if failed:
                log.info("Requeued %d failed episode(s) for retry", len(failed))
    except Exception:
        log.exception("retry_failed_job failed")


def correction_queue_job() -> None:
    try:
        with session_scope() as session:
            episode = session.exec(
                select(Episode)
                .where(Episode.correction_status == CorrectionStatus.DETECTED)
                .order_by(Episode.correction_detected_at)
            ).first()
            episode_id = episode.id if episode else None
        if episode_id is not None:
            process_episode_correction(episode_id)
    except Exception:
        log.exception("correction_queue_job failed")


def enforce_cache_job() -> None:
    try:
        cache_manager.enforce_cache_limits()
    except Exception:
        log.exception("enforce_cache_job failed")


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        poll_feeds_job, "interval", minutes=settings.poll_interval_minutes, id="poll_feeds"
    )
    scheduler.add_job(
        process_queue_job,
        "interval",
        seconds=settings.process_interval_seconds,
        id="process_queue",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        retry_failed_job, "interval", minutes=settings.retry_interval_minutes, id="retry_failed"
    )
    scheduler.add_job(
        correction_queue_job,
        "interval",
        seconds=settings.process_interval_seconds,
        id="correction_queue",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(enforce_cache_job, "interval", minutes=15, id="enforce_cache")
    scheduler.start()
    _scheduler = scheduler
    log.info("Scheduler started")
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
