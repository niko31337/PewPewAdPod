from types import SimpleNamespace

from app.routers.queue import _move_selected_playlist_entries


def _entries(ids):
    return [(SimpleNamespace(id=100 + episode_id), SimpleNamespace(id=episode_id)) for episode_id in ids]


def _ids(entries):
    return [episode.id for _, episode in entries]


def test_move_selected_up_moves_each_selected_episode_one_slot_and_keeps_order():
    entries = _entries([1, 2, 3, 4, 5])

    result = _move_selected_playlist_entries(entries, {2, 3}, "move_up")

    assert _ids(result) == [2, 3, 1, 4, 5]


def test_move_selected_down_moves_selected_block_one_slot():
    entries = _entries([1, 2, 3, 4, 5])

    result = _move_selected_playlist_entries(entries, {2, 3}, "move_down")

    assert _ids(result) == [1, 4, 2, 3, 5]


def test_move_selected_up_and_down_does_not_change_relative_order_of_selection():
    entries = _entries([1, 2, 3, 4, 5, 6])

    moved_up = _move_selected_playlist_entries(entries, {2, 5}, "move_up")
    assert _ids(moved_up) == [2, 1, 3, 5, 4, 6]
    moved_down = _move_selected_playlist_entries(moved_up, {2, 5}, "move_down")
    assert _ids(moved_down) == [1, 2, 3, 4, 5, 6]
