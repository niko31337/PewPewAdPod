import logging

import pytest

from app.services.ad_detector import (
    WindowConfig,
    _log_stage,
    build_candidate_windows,
    find_keyword_hits,
    merge_candidates,
)
from app.services.transcriber import TranscriptSegment, Word


def _seg(start, end, text):
    words = []
    cursor = 0
    for token in text.split(" "):
        w_start = start + cursor * 0.1
        words.append(Word(start=w_start, end=w_start + 0.1, text=token))
        cursor += 1
    return TranscriptSegment(start=start, end=end, text=text, words=words)


class DummyKeyword:
    def __init__(self, phrase, weight):
        self.phrase = phrase
        self.weight = weight


class DummyConfig:
    def __init__(self, keywords):
        self.keywords = keywords


def test_find_keyword_hits_matches_case_insensitive():
    segments = [_seg(0.0, 5.0, "Hallo und willkommen zur Folge")]
    segments.append(_seg(5.0, 12.0, "Diese Folge wird euch praesentiert von Firma X"))
    config = DummyConfig([DummyKeyword("praesentiert von", 1.0)])

    hits = find_keyword_hits(segments, config)

    assert len(hits) == 1
    assert hits[0].keyword == "praesentiert von"
    assert hits[0].time_s >= 5.0


def test_build_candidate_windows_applies_context_and_clamps_to_duration():
    segments = [_seg(100.0, 101.0, "sponsored by")]
    config = DummyConfig([DummyKeyword("sponsored by", 1.0)])
    hits = find_keyword_hits(segments, config)

    window = WindowConfig(
        pre_context_seconds=5, post_context_seconds=45, max_block_seconds=90, merge_gap_seconds=5
    )
    candidates = build_candidate_windows(hits, window, audio_duration_s=120.0)

    assert len(candidates) == 1
    c = candidates[0]
    assert c.start_ms == int((hits[0].time_s - 5) * 1000)
    assert c.end_ms <= 120_000


def test_merge_candidates_joins_overlapping_windows():
    segments = [_seg(10.0, 11.0, "werbung"), _seg(12.0, 13.0, "rabattcode")]
    config = DummyConfig([DummyKeyword("werbung", 0.6), DummyKeyword("rabattcode", 0.9)])
    hits = find_keyword_hits(segments, config)

    window = WindowConfig(
        pre_context_seconds=2, post_context_seconds=10, max_block_seconds=90, merge_gap_seconds=5
    )
    candidates = build_candidate_windows(hits, window, audio_duration_s=60.0)
    merged = merge_candidates(candidates, merge_gap_ms=5000)

    assert len(merged) == 1
    assert len(merged[0].keyword_hits) == 2


def test_log_stage_logs_start_and_finish_with_elapsed_time(caplog):
    # detect_ad_segments() used to log nothing between "started" and "finished" for its
    # whole multi-stage analysis - for a long episode that meant hours of silence with
    # no way to tell which of its signals (jingle/keyword/duplicate/beat/LLM detection)
    # was actually slow. _log_stage() brackets each stage instead; this locks in that a
    # stage always logs both ends, identified by episode and stage name.
    with caplog.at_level(logging.INFO, logger="app.services.ad_detector"):
        with _log_stage("42", "jingle detection"):
            pass

    messages = [r.message for r in caplog.records]
    assert any("42" in m and "jingle detection" in m and "starting" in m for m in messages)
    assert any("42" in m and "jingle detection" in m and "finished" in m for m in messages)


def test_log_stage_still_logs_finish_when_the_stage_raises(caplog):
    # A stage that crashes must still report how long it ran before failing - that's
    # exactly the moment this diagnostic logging matters most.
    with caplog.at_level(logging.INFO, logger="app.services.ad_detector"):
        with pytest.raises(ValueError):
            with _log_stage("42", "beat detection"):
                raise ValueError("boom")

    messages = [r.message for r in caplog.records]
    assert any("finished" in m and "beat detection" in m for m in messages)
