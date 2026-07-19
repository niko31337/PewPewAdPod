from app.services.ad_detector import Candidate, ScoringConfig, score_candidate
from app.services.jingle_detector import JingleHit

SCORING = ScoringConfig(
    keyword_weight=0.35,
    jingle_weight=0.45,
    acoustic_weight=0.2,
    silence_snap_bonus=0.15,
    rms_jump_bonus=0.15,
    keyword_normalization=1.5,
    auto_cut_default_threshold=0.75,
)


def test_jingle_hit_at_confident_threshold_pins_confidence_to_100_percent():
    hit = JingleHit(jingle_filename="intro.mp3", role="marker", time_s=10.0, score=0.82)
    candidate = Candidate(start_ms=10_000, end_ms=20_000, jingle_hits=[hit])

    confidence = score_candidate(candidate, SCORING, rms_jump_fraction=0.0, jingle_confident_threshold=0.8)

    assert confidence == 1.0


def test_jingle_hit_below_confident_threshold_is_not_pinned():
    hit = JingleHit(jingle_filename="intro.mp3", role="marker", time_s=10.0, score=0.65)
    candidate = Candidate(start_ms=10_000, end_ms=20_000, jingle_hits=[hit])

    confidence = score_candidate(candidate, SCORING, rms_jump_fraction=0.0, jingle_confident_threshold=0.8)

    assert confidence < 1.0
    assert confidence == 0.65 * 0.45  # only the weighted jingle_score term contributes


def test_paired_jingle_hits_both_above_threshold_pin_to_100_percent():
    start_hit = JingleHit(jingle_filename="start.mp3", role="start", time_s=10.0, score=0.9)
    end_hit = JingleHit(jingle_filename="end.mp3", role="end", time_s=60.0, score=0.85)
    candidate = Candidate(start_ms=10_000, end_ms=60_000, jingle_hits=[start_hit, end_hit])

    confidence = score_candidate(candidate, SCORING, rms_jump_fraction=0.0, jingle_confident_threshold=0.8)

    assert confidence == 1.0


def test_paired_jingle_hits_below_threshold_use_weaker_score_as_floor_not_100_percent():
    start_hit = JingleHit(jingle_filename="start.mp3", role="start", time_s=10.0, score=0.75)
    end_hit = JingleHit(jingle_filename="end.mp3", role="end", time_s=60.0, score=0.7)
    candidate = Candidate(start_ms=10_000, end_ms=60_000, jingle_hits=[start_hit, end_hit])

    confidence = score_candidate(candidate, SCORING, rms_jump_fraction=0.0, jingle_confident_threshold=0.8)

    assert confidence == 0.7
