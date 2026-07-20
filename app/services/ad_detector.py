import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydub import AudioSegment
from pydub.silence import detect_silence
from sqlmodel import Session

from app.services import beat_detector, cache_manager, fingerprint, jingle_detector
from app.services.beat_detector import BeatWindow
from app.services.jingle_detector import JingleHit
from app.services.transcriber import TranscriptSegment

log = logging.getLogger(__name__)


@dataclass
class KeywordHit:
    keyword: str
    weight: float
    time_s: float


@dataclass
class Candidate:
    start_ms: int
    end_ms: int
    keyword_hits: list[KeywordHit] = field(default_factory=list)
    jingle_hits: list[JingleHit] = field(default_factory=list)
    keyword_score: float = 0.0
    jingle_score: float = 0.0
    acoustic_score: float = 0.0
    duplicate_confidence: float = 0.0
    duplicate_support: int = 0
    beat_score: float = 0.0
    confidence: float = 0.0
    silence_snapped: bool = False
    rms_jump_db: float = 0.0
    transcript_snippet: str = ""
    source: str = "merged"


@dataclass
class KeywordConfig:
    phrase: str
    lang: str
    weight: float


@dataclass
class WindowConfig:
    pre_context_seconds: float
    post_context_seconds: float
    max_block_seconds: float
    merge_gap_seconds: float


@dataclass
class ScoringConfig:
    keyword_weight: float
    jingle_weight: float
    acoustic_weight: float
    silence_snap_bonus: float
    rms_jump_bonus: float
    keyword_normalization: float
    auto_cut_default_threshold: float
    beat_weight: float = 0.3


@dataclass
class SilenceConfig:
    min_silence_len_ms: int
    silence_thresh_offset_db: float
    search_radius_ms: int


@dataclass
class RmsConfig:
    context_seconds: float
    jump_threshold_db: float


@dataclass
class JingleConfig:
    target_sample_rate: int
    match_threshold: float
    confident_threshold: float
    min_pair_gap_seconds: float
    max_pair_gap_seconds: float
    default_block_seconds: float


@dataclass
class DuplicateConfig:
    min_support: int
    min_duplicate_seconds: float
    max_duplicate_seconds: float
    confidence_floor_base: float
    confidence_floor_max: float
    support_normalize: float
    duration_normalize_seconds: float
    verify_correlation_threshold: float = 0.6


@dataclass
class BeatConfig:
    target_sample_rate: int = 8000
    frame_size: int = 512
    hop_size: int = 128
    low_hz: float = 50.0
    high_hz: float = 150.0
    window_seconds: float = 4.0
    step_seconds: float = 2.0
    bpm_min: float = 60.0
    bpm_max: float = 180.0
    min_periodicity: float = 0.55
    min_bass_rms: float = 3.0
    min_beat_seconds: float = 8.0
    min_consecutive_windows: int = 3
    max_period_relative_deviation: float = 0.15


@dataclass
class DetectorConfig:
    keywords: list[KeywordConfig]
    window: WindowConfig
    scoring: ScoringConfig
    silence: SilenceConfig
    rms: RmsConfig
    jingles: JingleConfig
    duplicates: DuplicateConfig
    beat: BeatConfig = field(default_factory=BeatConfig)


def load_keyword_config(path: Path) -> DetectorConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return DetectorConfig(
        keywords=[KeywordConfig(**k) for k in raw["keywords"]],
        window=WindowConfig(**raw["window"]),
        scoring=ScoringConfig(**raw["scoring"]),
        silence=SilenceConfig(**raw["silence"]),
        rms=RmsConfig(**raw["rms"]),
        jingles=JingleConfig(**raw["jingles"]),
        duplicates=DuplicateConfig(**raw["duplicates"]),
        beat=BeatConfig(**raw["beat"]) if "beat" in raw else BeatConfig(),
    )


def find_keyword_hits(segments: list[TranscriptSegment], config: DetectorConfig) -> list[KeywordHit]:
    hits: list[KeywordHit] = []
    for seg in segments:
        text_lower = seg.text.lower()
        for kw in config.keywords:
            idx = text_lower.find(kw.phrase.lower())
            if idx == -1:
                continue
            time_s = _locate_time_in_segment(seg, idx)
            hits.append(KeywordHit(keyword=kw.phrase, weight=kw.weight, time_s=time_s))
    hits.sort(key=lambda h: h.time_s)
    return hits


def _locate_time_in_segment(seg: TranscriptSegment, char_idx: int) -> float:
    if not seg.words:
        return seg.start
    # approximate: find the word whose cumulative text offset is closest to char_idx
    cursor = 0
    for w in seg.words:
        cursor += len(w.text)
        if cursor >= char_idx:
            return w.start
    return seg.words[-1].start


def build_candidate_windows(
    hits: list[KeywordHit], config: WindowConfig, audio_duration_s: float
) -> list[Candidate]:
    if not hits:
        return []

    groups: list[list[KeywordHit]] = [[hits[0]]]
    for hit in hits[1:]:
        if hit.time_s - groups[-1][-1].time_s <= config.merge_gap_seconds:
            groups[-1].append(hit)
        else:
            groups.append([hit])

    candidates: list[Candidate] = []
    for group in groups:
        start_s = max(0.0, group[0].time_s - config.pre_context_seconds)
        end_s = min(audio_duration_s, group[-1].time_s + config.post_context_seconds)
        if end_s - start_s > config.max_block_seconds:
            end_s = start_s + config.max_block_seconds
        candidates.append(
            Candidate(start_ms=int(start_s * 1000), end_ms=int(end_s * 1000), keyword_hits=group)
        )
    return candidates


def merge_candidates(candidates: list[Candidate], merge_gap_ms: int) -> list[Candidate]:
    if not candidates:
        return []
    candidates = sorted(candidates, key=lambda c: c.start_ms)
    merged = [candidates[0]]
    for cur in candidates[1:]:
        last = merged[-1]
        if cur.start_ms - last.end_ms <= merge_gap_ms:
            last.end_ms = max(last.end_ms, cur.end_ms)
            last.keyword_hits.extend(cur.keyword_hits)
            last.jingle_hits.extend(cur.jingle_hits)
            if cur.duplicate_confidence > last.duplicate_confidence:
                last.duplicate_confidence = cur.duplicate_confidence
                last.duplicate_support = cur.duplicate_support
            last.beat_score = max(last.beat_score, cur.beat_score)
            last.source = "merged"
        else:
            merged.append(cur)
    return merged


def snap_to_silence(audio: AudioSegment, candidate: Candidate, silence_cfg: SilenceConfig) -> Candidate:
    radius = silence_cfg.search_radius_ms

    def _nearest_silence_midpoint(around_ms: int) -> int | None:
        window_start = max(0, around_ms - radius)
        window_end = min(len(audio), around_ms + radius)
        if window_end <= window_start:
            return None
        chunk = audio[window_start:window_end]
        thresh = chunk.dBFS - 16 if not math.isinf(chunk.dBFS) else silence_cfg.silence_thresh_offset_db
        silences = detect_silence(
            chunk, min_silence_len=silence_cfg.min_silence_len_ms, silence_thresh=thresh, seek_step=10
        )
        if not silences:
            return None
        best = min(silences, key=lambda s: abs((s[0] + s[1]) / 2 - (around_ms - window_start)))
        midpoint = window_start + (best[0] + best[1]) // 2
        return midpoint

    new_start = _nearest_silence_midpoint(candidate.start_ms)
    new_end = _nearest_silence_midpoint(candidate.end_ms)
    snapped = False
    if new_start is not None and new_start < candidate.end_ms:
        candidate.start_ms = new_start
        snapped = True
    if new_end is not None and new_end > candidate.start_ms:
        candidate.end_ms = new_end
        snapped = True
    candidate.silence_snapped = snapped
    return candidate


def compute_rms_jump(audio: AudioSegment, candidate: Candidate, rms_cfg: RmsConfig) -> float:
    context_ms = int(rms_cfg.context_seconds * 1000)
    window = audio[candidate.start_ms : candidate.end_ms]
    before = audio[max(0, candidate.start_ms - context_ms) : candidate.start_ms]
    after = audio[candidate.end_ms : min(len(audio), candidate.end_ms + context_ms)]

    window_db = window.dBFS
    if math.isinf(window_db):
        return 0.0

    surrounding_levels = [c.dBFS for c in (before, after) if len(c) > 0 and not math.isinf(c.dBFS)]
    if not surrounding_levels:
        return 0.0

    surrounding_db = sum(surrounding_levels) / len(surrounding_levels)
    jump_db = window_db - surrounding_db
    candidate.rms_jump_db = jump_db
    if jump_db <= 0:
        return 0.0
    return min(1.0, jump_db / max(rms_cfg.jump_threshold_db, 0.1))


def score_candidate(
    candidate: Candidate, scoring: ScoringConfig, rms_jump_fraction: float, jingle_confident_threshold: float = 0.8
) -> float:
    total_weight = sum(h.weight for h in candidate.keyword_hits)
    keyword_score = min(1.0, total_weight / scoring.keyword_normalization) if scoring.keyword_normalization else 0.0
    jingle_score = max((h.score for h in candidate.jingle_hits), default=0.0)

    acoustic_score = 0.0
    if candidate.silence_snapped:
        acoustic_score += scoring.silence_snap_bonus
    acoustic_score += scoring.rms_jump_bonus * rms_jump_fraction
    acoustic_score = min(1.0, acoustic_score)

    candidate.keyword_score = keyword_score
    candidate.jingle_score = jingle_score
    candidate.acoustic_score = acoustic_score
    confidence = min(
        1.0,
        keyword_score * scoring.keyword_weight
        + jingle_score * scoring.jingle_weight
        + acoustic_score * scoring.acoustic_weight
        + candidate.beat_score * scoring.beat_weight,
    )

    # A *paired* jingle match (the same or a start/end jingle bracketing the block from
    # both sides) is much stronger evidence than any single signal: it is a direct audio
    # fingerprint match at both boundaries, not an inference. Treat it as a confidence
    # floor rather than just one weighted term, using the weaker of the two matches so a
    # single spuriously perfect correlation can't mask an otherwise weak pairing.
    if len(candidate.jingle_hits) >= 2:
        paired_floor = min(h.score for h in candidate.jingle_hits)
        confidence = max(confidence, min(1.0, paired_floor))

    # A jingle match at or above the "confident" threshold is a clear, unambiguous
    # fingerprint hit - not a fuzzy inference whose certainty should scale with the raw
    # correlation value (0.85 vs 0.95 is noise from lossy re-encoding, not doubt about
    # whether it's the jingle). Once a hit clears that bar, treat it as certain.
    if any(h.score >= jingle_confident_threshold for h in candidate.jingle_hits):
        confidence = 1.0

    # A segment that is acoustically identical to something in the previous episode
    # cannot be unique spoken content - it's a rerun intro/outro/ad block. That's at
    # least as strong a signal as a paired jingle match, so it gets the same treatment:
    # a confidence floor rather than just another weighted term.
    if candidate.duplicate_confidence > 0:
        confidence = max(confidence, candidate.duplicate_confidence)

    candidate.confidence = confidence
    return candidate.confidence


def build_candidates_from_jingle_hits(
    hits: list[JingleHit], window: WindowConfig, jingle_cfg: JingleConfig, audio_duration_s: float
) -> list[Candidate]:
    hits_sorted = sorted(hits, key=lambda h: h.time_s)
    consumed = [False] * len(hits_sorted)
    candidates: list[Candidate] = []

    # Pair up hits that are a plausible "block start bumper" + "block end bumper"
    # distance apart (same or different jingle) before falling back to single markers.
    for i in range(len(hits_sorted) - 1):
        if consumed[i]:
            continue
        a, b = hits_sorted[i], hits_sorted[i + 1]
        gap = b.time_s - a.time_s
        if jingle_cfg.min_pair_gap_seconds <= gap <= jingle_cfg.max_pair_gap_seconds:
            start_ms = int(max(0.0, a.time_s) * 1000)
            end_ms = int(min(audio_duration_s, b.time_s) * 1000)
            candidates.append(Candidate(start_ms=start_ms, end_ms=end_ms, jingle_hits=[a, b], source="jingle"))
            consumed[i] = True
            consumed[i + 1] = True

    for i, h in enumerate(hits_sorted):
        if consumed[i]:
            continue
        if h.role == "start":
            start_s, end_s = h.time_s, h.time_s + jingle_cfg.default_block_seconds
        elif h.role == "end":
            start_s, end_s = max(0.0, h.time_s - jingle_cfg.default_block_seconds), h.time_s
        else:
            start_s = max(0.0, h.time_s - window.pre_context_seconds)
            end_s = h.time_s + jingle_cfg.default_block_seconds
        start_s = max(0.0, start_s)
        end_s = min(audio_duration_s, end_s)
        candidates.append(Candidate(start_ms=int(start_s * 1000), end_ms=int(end_s * 1000), jingle_hits=[h], source="jingle"))

    return candidates


def build_candidates_from_duplicate_matches(
    matches: list["fingerprint.MatchCandidate"], duplicate_cfg: DuplicateConfig, audio_duration_s: float
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for m in matches:
        if m.support < duplicate_cfg.min_support:
            continue
        start_s = max(0.0, m.start_a_s)
        end_s = min(audio_duration_s, m.end_a_s)
        if end_s - start_s > duplicate_cfg.max_duplicate_seconds:
            end_s = start_s + duplicate_cfg.max_duplicate_seconds
        if end_s - start_s < duplicate_cfg.min_duplicate_seconds:
            continue

        # A short match can rack up a high hash-support count from a spectrally dense
        # burst without actually covering much audio (a coincidence, not a real repeat),
        # while a genuine reused segment (intro/outro/ad read) is long *and* well
        # supported. Duration is therefore the primary signal; support only breaks ties
        # between two matches of similar length.
        duration_fraction = min(1.0, (end_s - start_s) / duplicate_cfg.duration_normalize_seconds)
        support_fraction = min(1.0, m.support / duplicate_cfg.support_normalize)
        combined_fraction = 0.8 * duration_fraction + 0.2 * support_fraction

        floor_range = duplicate_cfg.confidence_floor_max - duplicate_cfg.confidence_floor_base
        confidence_floor = duplicate_cfg.confidence_floor_base + floor_range * combined_fraction

        candidates.append(
            Candidate(
                start_ms=int(start_s * 1000),
                end_ms=int(end_s * 1000),
                duplicate_confidence=min(1.0, confidence_floor),
                duplicate_support=m.support,
                source="duplicate",
            )
        )
    return candidates


def build_candidates_from_beat_windows(
    windows: list[BeatWindow], beat_cfg: BeatConfig, audio_duration_s: float
) -> list[Candidate]:
    """Some podcasters run a steady background beat under sponsor reads that isn't
    present in the rest of the episode. Consecutive windows are merged into candidate
    blocks only if they (a) clear the periodicity and a minimum absolute bass loudness
    (so a quiet passage's noise floor can't look "periodic" by chance), AND (b) agree on
    close to the *same* beat period as their neighbors. That second check is what tells
    a genuinely looping music bed apart from a rhythmic burst that isn't one - a deep
    laugh's "ha-ha-ha" can score high periodicity in a single window too, but it decays
    and its pulse spacing drifts, so it won't hold a matching period across several
    consecutive windows the way an actual loop does. Independent of keywords or known
    jingles, so it still catches ad reads Whisper mis-transcribed entirely."""
    hits = [
        w for w in windows if w.periodicity >= beat_cfg.min_periodicity and w.bass_rms >= beat_cfg.min_bass_rms
    ]
    if not hits:
        return []
    hits.sort(key=lambda w: w.start_s)

    spans: list[list[BeatWindow]] = [[hits[0]]]
    for w in hits[1:]:
        prev = spans[-1][-1]
        adjacent = w.start_s - prev.end_s <= beat_cfg.step_seconds
        period_matches = (
            prev.period_s > 0
            and abs(w.period_s - prev.period_s) <= beat_cfg.max_period_relative_deviation * prev.period_s
        )
        if adjacent and period_matches:
            spans[-1].append(w)
        else:
            spans.append([w])

    candidates: list[Candidate] = []
    for span in spans:
        if len(span) < beat_cfg.min_consecutive_windows:
            continue
        start_s = max(0.0, span[0].start_s)
        end_s = min(audio_duration_s, span[-1].end_s)
        if end_s - start_s < beat_cfg.min_beat_seconds:
            continue
        candidates.append(
            Candidate(
                start_ms=int(start_s * 1000),
                end_ms=int(end_s * 1000),
                beat_score=max(w.periodicity for w in span),
                source="beat",
            )
        )
    return candidates


def _build_transcript_snippet(segments: list[TranscriptSegment], start_s: float, end_s: float) -> str:
    parts = [s.text.strip() for s in segments if s.end >= start_s and s.start <= end_s]
    snippet = " ".join(parts).strip()
    return snippet[:400]


def detect_ad_segments(
    audio_path: Path,
    transcript: list[TranscriptSegment],
    config_path: Path,
    session: Session | None = None,
    feed_id: int | None = None,
    jingles_dir: Path | None = None,
    previous_episode_audio_path: Path | None = None,
) -> list[Candidate]:
    config = load_keyword_config(config_path)
    audio = AudioSegment.from_file(audio_path)
    audio_duration_s = len(audio) / 1000.0

    if session is not None:
        app_config = cache_manager.get_or_create_config(session)
        if app_config.min_duplicate_seconds is not None:
            config.duplicates.min_duplicate_seconds = app_config.min_duplicate_seconds

    jingle_candidates: list[Candidate] = []
    if session is not None and feed_id is not None and jingles_dir is not None:
        jingle_hits = jingle_detector.find_jingle_hits(
            session,
            feed_id,
            audio,
            jingles_dir,
            config.jingles.target_sample_rate,
            config.jingles.match_threshold,
            config.jingles.confident_threshold,
        )
        jingle_candidates = build_candidates_from_jingle_hits(
            jingle_hits, config.window, config.jingles, audio_duration_s
        )

    keyword_hits = find_keyword_hits(transcript, config)
    keyword_candidates = (
        build_candidate_windows(keyword_hits, config.window, audio_duration_s) if keyword_hits else []
    )

    duplicate_candidates: list[Candidate] = []
    if previous_episode_audio_path is not None:
        try:
            matches = fingerprint.find_repeated_segments(
                audio_path,
                previous_episode_audio_path,
                min_verify_correlation=config.duplicates.verify_correlation_threshold,
            )
            duplicate_candidates = build_candidates_from_duplicate_matches(
                matches, config.duplicates, audio_duration_s
            )
        except Exception:
            log.warning(
                "Could not compare against previous episode's audio (%s) - skipping duplicate-segment "
                "detection for this episode",
                previous_episode_audio_path,
                exc_info=True,
            )

    beat_candidates: list[Candidate] = []
    try:
        beat_windows = beat_detector.find_beat_windows(
            audio_path,
            target_sample_rate=config.beat.target_sample_rate,
            frame_size=config.beat.frame_size,
            hop_size=config.beat.hop_size,
            low_hz=config.beat.low_hz,
            high_hz=config.beat.high_hz,
            window_seconds=config.beat.window_seconds,
            step_seconds=config.beat.step_seconds,
            bpm_min=config.beat.bpm_min,
            bpm_max=config.beat.bpm_max,
        )
        beat_candidates = build_candidates_from_beat_windows(beat_windows, config.beat, audio_duration_s)
    except Exception:
        log.warning("Could not run background-beat detection for %s", audio_path, exc_info=True)

    candidates = merge_candidates(
        jingle_candidates + keyword_candidates + duplicate_candidates + beat_candidates,
        int(config.window.merge_gap_seconds * 1000),
    )
    if not candidates:
        return []

    for candidate in candidates:
        snap_to_silence(audio, candidate, config.silence)
        rms_fraction = compute_rms_jump(audio, candidate, config.rms)
        score_candidate(candidate, config.scoring, rms_fraction, config.jingles.confident_threshold)
        candidate.transcript_snippet = _build_transcript_snippet(
            transcript, candidate.start_ms / 1000.0, candidate.end_ms / 1000.0
        )
        signal_count = sum(
            [
                bool(candidate.jingle_hits),
                bool(candidate.keyword_hits),
                candidate.duplicate_confidence > 0,
                candidate.beat_score > 0,
            ]
        )
        if signal_count > 1:
            candidate.source = "merged"
        elif candidate.duplicate_confidence > 0:
            candidate.source = "duplicate"
        elif candidate.jingle_hits:
            candidate.source = "jingle"
        elif candidate.beat_score > 0:
            candidate.source = "beat"
        else:
            candidate.source = "merged" if len(candidate.keyword_hits) > 1 else "keyword"

    candidates.sort(key=lambda c: c.start_ms)
    return candidates
