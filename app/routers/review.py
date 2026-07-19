import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.database import get_session
from app.models import AdSegment, Episode, Feed, SegmentSource, SegmentStatus
from app.paths import resolve_stored_path
from app.services import audio_editor, pipeline
from app.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/episodes/{episode_id}/review")
def review_episode(episode_id: int, request: Request, session: Session = Depends(get_session)):
    episode = session.get(Episode, episode_id)
    if episode is None:
        return RedirectResponse(url="/", status_code=303)
    feed = session.get(Feed, episode.feed_id)

    resolved_audio = resolve_stored_path(episode.original_audio_path)
    if episode.duration_seconds is None and resolved_audio:
        episode.duration_seconds = audio_editor.get_duration_seconds(resolved_audio)
        if episode.duration_seconds is not None:
            session.add(episode)
            session.commit()

    segments = session.exec(
        select(AdSegment)
        .where(AdSegment.episode_id == episode_id)
        .where(AdSegment.status == SegmentStatus.PENDING)
        .where(AdSegment.is_correction == False)  # noqa: E712 - correction candidates are a separate track
        .order_by(AdSegment.start_ms)
    ).all()

    threshold = feed.confidence_threshold or settings.default_auto_cut_threshold
    segment_rows = [
        {
            "segment": s,
            "keywords": json.loads(s.matched_keywords or "[]"),
            "jingles": json.loads(s.matched_jingles or "[]"),
            "preselected": s.confidence >= threshold,
        }
        for s in segments
    ]

    duration_ms = int(episode.duration_seconds * 1000) if episode.duration_seconds else 0

    return templates.TemplateResponse(
        request,
        "episode_review.html",
        {
            "episode": episode,
            "feed": feed,
            "segment_rows": segment_rows,
            "threshold": threshold,
            "duration_ms": duration_ms,
        },
    )


@router.post("/episodes/{episode_id}/apply-cut")
async def apply_cut_route(episode_id: int, request: Request, session: Session = Depends(get_session)):
    episode = session.get(Episode, episode_id)
    if episode is None:
        return RedirectResponse(url="/", status_code=303)

    form = await request.form()

    accepted_ranges: list[tuple[int, int]] = []

    pending_segments = session.exec(
        select(AdSegment)
        .where(AdSegment.episode_id == episode_id)
        .where(AdSegment.status == SegmentStatus.PENDING)
        .where(AdSegment.is_correction == False)  # noqa: E712 - never touch correction candidates here
    ).all()

    for seg in pending_segments:
        accept_key = f"seg_accept_{seg.id}"
        if accept_key in form:
            start_s = form.get(f"seg_start_{seg.id}")
            end_s = form.get(f"seg_end_{seg.id}")
            if start_s is not None:
                seg.start_ms = int(float(start_s) * 1000)
            if end_s is not None:
                seg.end_ms = int(float(end_s) * 1000)
            seg.status = SegmentStatus.ACCEPTED
            accepted_ranges.append((seg.start_ms, seg.end_ms))
        else:
            seg.status = SegmentStatus.REJECTED
        session.add(seg)

    manual_starts = form.getlist("manual_start")
    manual_ends = form.getlist("manual_end")
    for start_s, end_s in zip(manual_starts, manual_ends):
        if not str(start_s).strip() or not str(end_s).strip():
            continue
        start_ms = int(float(start_s) * 1000)
        end_ms = int(float(end_s) * 1000)
        if end_ms <= start_ms:
            continue
        manual_seg = AdSegment(
            episode_id=episode_id,
            start_ms=start_ms,
            end_ms=end_ms,
            confidence=1.0,
            matched_keywords="[]",
            transcript_snippet="(manuell hinzugefügt)",
            source=SegmentSource.MANUAL,
            status=SegmentStatus.ACCEPTED,
        )
        session.add(manual_seg)
        accepted_ranges.append((start_ms, end_ms))

    session.commit()

    await run_in_threadpool(pipeline.apply_cut, session, episode, accepted_ranges)

    return RedirectResponse(url=f"/feeds/{episode.feed_id}", status_code=303)
