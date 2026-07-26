from app.services.ad_detector import BeatConfig, Candidate, ScoringConfig, build_candidates_from_beat_windows, score_candidate
from app.services.beat_detector import BeatWindow

BEAT_CFG = BeatConfig(
    step_seconds=2.0,
    min_periodicity=0.55,
    min_bass_rms=3.0,
    min_beat_seconds=4.0,
    min_consecutive_windows=3,
    max_period_relative_deviation=0.15,
)


def test_consecutive_beat_windows_with_matching_period_merge_into_one_candidate():
    windows = [
        BeatWindow(start_s=0.0, end_s=4.0, periodicity=0.7, bass_rms=5.0, period_s=0.5),
        BeatWindow(start_s=2.0, end_s=6.0, periodicity=0.8, bass_rms=5.0, period_s=0.52),
        BeatWindow(start_s=4.0, end_s=8.0, periodicity=0.75, bass_rms=5.0, period_s=0.49),
    ]
    candidates = build_candidates_from_beat_windows(windows, BEAT_CFG, audio_duration_s=600)

    assert len(candidates) == 1
    assert candidates[0].start_ms == 0
    assert candidates[0].end_ms == 8000
    assert candidates[0].beat_score == 0.8
    assert candidates[0].source == "beat"


def test_windows_below_periodicity_threshold_are_ignored():
    windows = [BeatWindow(start_s=0.0, end_s=4.0, periodicity=0.3, bass_rms=5.0, period_s=0.5)]
    candidates = build_candidates_from_beat_windows(windows, BEAT_CFG, audio_duration_s=600)
    assert candidates == []


def test_windows_below_bass_loudness_gate_are_ignored():
    # High periodicity but very quiet - a near-silent passage whose noise floor happens
    # to autocorrelate strongly should not count as a beat.
    windows = [BeatWindow(start_s=0.0, end_s=4.0, periodicity=0.9, bass_rms=0.1, period_s=0.5)]
    candidates = build_candidates_from_beat_windows(windows, BEAT_CFG, audio_duration_s=600)
    assert candidates == []


def test_short_beat_span_below_min_duration_is_dropped():
    windows = [BeatWindow(start_s=10.0, end_s=12.0, periodicity=0.9, bass_rms=5.0, period_s=0.5)]
    candidates = build_candidates_from_beat_windows(windows, BEAT_CFG, audio_duration_s=600)
    assert candidates == []


def test_isolated_high_periodicity_window_is_rejected_without_neighbors():
    # A single window clearing periodicity/loudness but with no consecutive neighbors
    # agreeing on the same tempo - e.g. one burst of deep laughter - must not become a
    # candidate on its own (falls below min_consecutive_windows).
    windows = [
        BeatWindow(start_s=40.0, end_s=44.0, periodicity=0.9, bass_rms=6.0, period_s=0.4),
    ]
    candidates = build_candidates_from_beat_windows(windows, BEAT_CFG, audio_duration_s=600)
    assert candidates == []


def test_windows_with_drifting_period_are_not_merged_even_if_adjacent():
    # Simulates rhythmic-but-not-looping audio (e.g. a laugh whose pulse spacing isn't
    # constant): three adjacent windows all clear the periodicity/loudness gates, but
    # each reports a substantially different period, so they must not merge into one
    # sustained-beat candidate.
    windows = [
        BeatWindow(start_s=0.0, end_s=4.0, periodicity=0.7, bass_rms=5.0, period_s=0.35),
        BeatWindow(start_s=2.0, end_s=6.0, periodicity=0.75, bass_rms=5.0, period_s=0.6),
        BeatWindow(start_s=4.0, end_s=8.0, periodicity=0.8, bass_rms=5.0, period_s=0.9),
    ]
    candidates = build_candidates_from_beat_windows(windows, BEAT_CFG, audio_duration_s=600)
    assert candidates == []


def test_separated_beat_spans_stay_distinct_candidates():
    windows = [
        BeatWindow(start_s=0.0, end_s=4.0, periodicity=0.7, bass_rms=5.0, period_s=0.5),
        BeatWindow(start_s=2.0, end_s=6.0, periodicity=0.7, bass_rms=5.0, period_s=0.5),
        BeatWindow(start_s=4.0, end_s=8.0, periodicity=0.7, bass_rms=5.0, period_s=0.5),
        BeatWindow(start_s=100.0, end_s=104.0, periodicity=0.7, bass_rms=5.0, period_s=0.5),
        BeatWindow(start_s=102.0, end_s=106.0, periodicity=0.7, bass_rms=5.0, period_s=0.5),
        BeatWindow(start_s=104.0, end_s=108.0, periodicity=0.7, bass_rms=5.0, period_s=0.5),
    ]
    candidates = build_candidates_from_beat_windows(windows, BEAT_CFG, audio_duration_s=600)
    assert len(candidates) == 2


def test_beat_score_contributes_to_confidence_via_weighted_term():
    scoring = ScoringConfig(
        keyword_weight=0.35,
        jingle_weight=0.45,
        acoustic_weight=0.2,
        silence_snap_bonus=0.15,
        rms_jump_bonus=0.15,
        keyword_normalization=1.5,
        auto_cut_default_threshold=0.75,
        beat_weight=0.3,
    )
    candidate = Candidate(start_ms=0, end_ms=8000, beat_score=0.8)
    confidence = score_candidate(candidate, scoring, rms_jump_fraction=0.0)
    assert abs(confidence - 0.8 * 0.3) < 1e-6
