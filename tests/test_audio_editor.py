from app.services.audio_editor import compute_keep_ranges


def test_compute_keep_ranges_no_cuts_returns_full_range():
    assert compute_keep_ranges(10_000, []) == [(0, 10_000)]


def test_compute_keep_ranges_single_cut_in_middle():
    result = compute_keep_ranges(10_000, [(4_000, 6_000)])
    assert result == [(0, 4_000), (6_000, 10_000)]


def test_compute_keep_ranges_merges_overlapping_cuts():
    result = compute_keep_ranges(10_000, [(1_000, 3_000), (2_500, 4_000)])
    assert result == [(0, 1_000), (4_000, 10_000)]


def test_compute_keep_ranges_cut_at_start_and_end():
    result = compute_keep_ranges(10_000, [(0, 1_000), (9_000, 10_000)])
    assert result == [(1_000, 9_000)]


def test_compute_keep_ranges_clamps_out_of_bounds():
    result = compute_keep_ranges(10_000, [(-500, 500), (9_500, 12_000)])
    assert result == [(500, 9_500)]
