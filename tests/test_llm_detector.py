from app.services.llm_detector import _group_segments_into_windows, _parse_confidence
from app.services.transcriber import TranscriptSegment


def _seg(start, end, text):
    return TranscriptSegment(start=start, end=end, text=text, words=[])


def test_parse_confidence_extracts_integer_and_normalizes():
    assert _parse_confidence("85") == 0.85
    assert _parse_confidence(" 100 ") == 1.0
    assert _parse_confidence("0") == 0.0


def test_parse_confidence_clamps_out_of_range_values():
    assert _parse_confidence("150") == 1.0


def test_parse_confidence_falls_back_to_zero_when_unparseable():
    assert _parse_confidence("keine Ahnung") == 0.0
    assert _parse_confidence("") == 0.0


def test_group_segments_into_sequential_non_overlapping_windows():
    segments = [
        _seg(0.0, 10.0, "Hallo und willkommen zur Folge."),
        _seg(10.0, 20.0, "Heute geht es um dies und das."),
        _seg(20.0, 35.0, "Diese Folge wird gesponsert von Firma X."),
        _seg(35.0, 40.0, "Nutzt den Code SPAR10 fuer zehn Prozent Rabatt."),
    ]
    windows = _group_segments_into_windows(segments, window_seconds=20.0, audio_duration_s=40.0)

    assert len(windows) == 2
    assert windows[0][0] == 0.0
    assert windows[0][1] == 20.0
    assert "Hallo" in windows[0][2]
    assert "heute" in windows[0][2].lower()
    assert windows[1][0] == 20.0
    assert windows[1][1] == 40.0
    assert "gesponsert" in windows[1][2]
    assert "SPAR10" in windows[1][2]


def test_group_segments_skips_empty_windows():
    segments = [_seg(0.0, 5.0, "kurzer Satz")]
    windows = _group_segments_into_windows(segments, window_seconds=10.0, audio_duration_s=30.0)
    # only the first 10s window has any transcript text at all
    assert len(windows) == 1
    assert windows[0] == (0.0, 10.0, "kurzer Satz")


def test_group_segments_returns_empty_for_no_segments_or_zero_duration():
    assert _group_segments_into_windows([], window_seconds=10.0, audio_duration_s=100.0) == []
    assert _group_segments_into_windows([_seg(0.0, 5.0, "x")], window_seconds=10.0, audio_duration_s=0.0) == []
