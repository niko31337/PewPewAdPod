from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.feed_playlist import select_playlist_entries


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Tiny ordered response stub: feed query first, then one episode query per feed."""

    def __init__(self, responses):
        self._responses = list(responses)

    def exec(self, _statement):
        if not self._responses:
            raise AssertionError("Unexpected additional query")
        return _Result(self._responses.pop(0))


def _feed(feed_id, title, preferred=False):
    return SimpleNamespace(
        id=feed_id,
        title=title,
        preferred=preferred,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _episode(episode_id, feed_id, days_ago, playlist_position=None):
    date = datetime(2026, 10, 1, tzinfo=timezone.utc) - timedelta(days=days_ago)
    return SimpleNamespace(
        id=episode_id,
        feed_id=feed_id,
        processed_audio_path=f"/{episode_id}.mp3",
        pubdate=date,
        created_at=date,
        playlist_position=playlist_position,
    )


def test_playlist_defaults_to_newest_episode_first_without_manual_positions():
    feed_a = _feed(1, "A")
    feed_b = _feed(2, "B")
    older = _episode(11, 1, 5)
    newer = _episode(22, 2, 1)
    session = _FakeSession([[feed_a, feed_b], [older], [newer]])

    result = select_playlist_entries(session, episodes_per_feed=1)

    assert [episode.id for _, episode in result] == [22, 11]


def test_manual_positions_override_natural_order_and_new_entries_append():
    feed_a = _feed(1, "A")
    feed_b = _feed(2, "B")
    feed_c = _feed(3, "C")
    manual_late = _episode(11, 1, 3, playlist_position=20)
    manual_first = _episode(22, 2, 2, playlist_position=10)
    newly_published = _episode(33, 3, 0, playlist_position=None)
    session = _FakeSession([
        [feed_a, feed_b, feed_c],
        [manual_late],
        [manual_first],
        [newly_published],
    ])

    result = select_playlist_entries(session, episodes_per_feed=1)

    assert [episode.id for _, episode in result] == [22, 11, 33]
