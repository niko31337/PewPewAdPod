from app.services.ad_detector import (
    Candidate,
    LlmDetectionConfig,
    ScoringConfig,
    build_candidates_from_llm_hits,
    score_candidate,
)
from app.services.llm_detector import LlmHit

LLM_CFG = LlmDetectionConfig(window_seconds=30.0, min_confidence=0.6)


def test_consecutive_ad_windows_merge_into_one_candidate():
    hits = [
        LlmHit(start_s=0.0, end_s=30.0, confidence=0.7, text="a"),
        LlmHit(start_s=30.0, end_s=60.0, confidence=0.9, text="b"),
    ]
    candidates = build_candidates_from_llm_hits(hits, LLM_CFG, audio_duration_s=600)

    assert len(candidates) == 1
    assert candidates[0].start_ms == 0
    assert candidates[0].end_ms == 60000
    assert candidates[0].llm_score == 0.9
    assert candidates[0].source == "llm"


def test_windows_below_confidence_threshold_are_ignored():
    hits = [LlmHit(start_s=0.0, end_s=30.0, confidence=0.4, text="editorial content")]
    candidates = build_candidates_from_llm_hits(hits, LLM_CFG, audio_duration_s=600)
    assert candidates == []


def test_non_adjacent_ad_windows_stay_distinct_candidates():
    hits = [
        LlmHit(start_s=0.0, end_s=30.0, confidence=0.8, text="a"),
        LlmHit(start_s=300.0, end_s=330.0, confidence=0.8, text="b"),
    ]
    candidates = build_candidates_from_llm_hits(hits, LLM_CFG, audio_duration_s=600)
    assert len(candidates) == 2


def test_llm_score_contributes_to_confidence_via_weighted_term():
    scoring = ScoringConfig(
        keyword_weight=0.35,
        jingle_weight=0.45,
        acoustic_weight=0.2,
        silence_snap_bonus=0.15,
        rms_jump_bonus=0.15,
        keyword_normalization=1.5,
        auto_cut_default_threshold=0.75,
        llm_weight=0.35,
    )
    candidate = Candidate(start_ms=0, end_ms=30000, llm_score=0.9)
    confidence = score_candidate(candidate, scoring, rms_jump_fraction=0.0)
    assert abs(confidence - 0.9 * 0.35) < 1e-6
