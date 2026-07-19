from app.services.ad_detector import (
    Candidate,
    DuplicateConfig,
    ScoringConfig,
    build_candidates_from_duplicate_matches,
    score_candidate,
)
from app.services.fingerprint import MatchCandidate

DUP_CFG = DuplicateConfig(
    min_support=12,
    min_duplicate_seconds=2.0,
    max_duplicate_seconds=300,
    confidence_floor_base=0.85,
    confidence_floor_max=1.0,
    support_normalize=300,
    duration_normalize_seconds=10.0,
)


def test_weak_match_is_filtered_out_by_min_support():
    matches = [MatchCandidate(start_a_s=10.0, end_a_s=15.0, start_b_s=20.0, end_b_s=25.0, support=5)]
    candidates = build_candidates_from_duplicate_matches(matches, DUP_CFG, audio_duration_s=600)
    assert candidates == []


def test_confidence_floor_scales_primarily_with_duration_not_support():
    # Long match with minimal support (right at the min_support gate) vs. a short match
    # with ~170x more support - duration must still win out, since a short-but-densely-
    # hashed burst is a much more likely false positive than a genuinely long repeat.
    long_low_support = MatchCandidate(start_a_s=10.0, end_a_s=20.0, start_b_s=0, end_b_s=0, support=12)
    short_high_support = MatchCandidate(start_a_s=100.0, end_a_s=102.5, start_b_s=0, end_b_s=0, support=2000)
    candidates = build_candidates_from_duplicate_matches(
        [long_low_support, short_high_support], DUP_CFG, audio_duration_s=600
    )

    assert len(candidates) == 2
    long_c, short_c = sorted(candidates, key=lambda c: c.start_ms)
    assert abs(long_c.duplicate_confidence - 0.9712) < 0.001
    assert abs(short_c.duplicate_confidence - 0.91) < 0.001
    assert long_c.duplicate_confidence > short_c.duplicate_confidence
    assert long_c.source == "duplicate"


def test_confidence_floor_reaches_max_when_both_duration_and_support_are_high():
    match = MatchCandidate(start_a_s=100.0, end_a_s=160.0, start_b_s=0, end_b_s=0, support=2000)
    candidates = build_candidates_from_duplicate_matches([match], DUP_CFG, audio_duration_s=600)

    assert len(candidates) == 1
    assert candidates[0].duplicate_confidence == 1.0
    assert candidates[0].start_ms == 100_000
    assert candidates[0].end_ms == 160_000


def test_short_match_is_filtered_out_by_min_duplicate_seconds():
    # High support but a very short span - a likely spurious hash collision, not a
    # genuinely reused segment (real intros/outros/ad reads run several seconds).
    matches = [MatchCandidate(start_a_s=10.0, end_a_s=11.2, start_b_s=20.0, end_b_s=21.2, support=500)]
    candidates = build_candidates_from_duplicate_matches(matches, DUP_CFG, audio_duration_s=600)
    assert candidates == []


def test_overlong_match_is_capped_at_max_duplicate_seconds():
    matches = [MatchCandidate(start_a_s=0.0, end_a_s=900.0, start_b_s=0, end_b_s=0, support=500)]
    candidates = build_candidates_from_duplicate_matches(matches, DUP_CFG, audio_duration_s=1000)
    assert len(candidates) == 1
    assert candidates[0].end_ms - candidates[0].start_ms == 300_000


def test_score_candidate_duplicate_confidence_acts_as_a_floor():
    scoring = ScoringConfig(
        keyword_weight=0.35,
        jingle_weight=0.45,
        acoustic_weight=0.2,
        silence_snap_bonus=0.15,
        rms_jump_bonus=0.15,
        keyword_normalization=1.5,
        auto_cut_default_threshold=0.75,
    )
    # no keyword/jingle signal at all -> weighted formula alone would score 0, but the
    # duplicate floor must still win through
    candidate = Candidate(start_ms=0, end_ms=1000, duplicate_confidence=0.92)
    confidence = score_candidate(candidate, scoring, rms_jump_fraction=0.0)
    assert confidence == 0.92
