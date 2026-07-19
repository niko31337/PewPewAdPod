from sqlmodel import Session, SQLModel, create_engine

from app.models import Episode, EpisodeStatus, Feed
from app.routers.episodes import delete_episode_audio_route


def _make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_delete_episode_audio_route_removes_files_and_redirects(tmp_path):
    session = _make_session()
    feed = Feed(title="Feed", original_rss_url="https://example.test/feed")
    session.add(feed)
    session.commit()
    session.refresh(feed)

    original = tmp_path / "1.mp3"
    processed = tmp_path / "1_cut.mp3"
    original.write_bytes(b"original")
    processed.write_bytes(b"processed")

    episode = Episode(
        feed_id=feed.id,
        guid="g1",
        title="Episode",
        original_audio_url="https://example.test/1.mp3",
        original_audio_path=str(original),
        processed_audio_path=str(processed),
        processed_size_bytes=len(b"processed"),
        status=EpisodeStatus.PUBLISHED,
    )
    session.add(episode)
    session.commit()
    session.refresh(episode)

    response = delete_episode_audio_route(episode.id, session=session)

    assert response.status_code == 303
    assert response.headers["location"] == f"/feeds/{feed.id}"
    assert not original.exists()
    assert not processed.exists()

    session.refresh(episode)
    assert episode.original_audio_path is None
    assert episode.processed_audio_path is None
    assert episode.processed_size_bytes is None
    # the DB row and its status survive - only the cached audio is gone
    assert episode.status == EpisodeStatus.PUBLISHED


def test_delete_episode_audio_route_redirects_home_for_unknown_episode():
    session = _make_session()

    response = delete_episode_audio_route(999999, session=session)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
