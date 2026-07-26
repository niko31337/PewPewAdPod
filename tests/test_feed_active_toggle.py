from sqlmodel import Session, SQLModel, create_engine

from app.models import Feed
from app.routers.feeds import toggle_active


def _make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_toggle_active_flips_active_feed_to_inactive():
    session = _make_session()
    feed = Feed(title="Feed", original_rss_url="https://example.test/feed.xml", active=True)
    session.add(feed)
    session.commit()
    session.refresh(feed)

    response = toggle_active(feed.id, session=session)

    assert response.status_code == 303
    assert response.headers["location"] == f"/feeds/{feed.id}"
    session.refresh(feed)
    assert feed.active is False


def test_toggle_active_flips_inactive_feed_back_to_active():
    session = _make_session()
    feed = Feed(title="Feed", original_rss_url="https://example.test/feed.xml", active=False)
    session.add(feed)
    session.commit()
    session.refresh(feed)

    toggle_active(feed.id, session=session)

    session.refresh(feed)
    assert feed.active is True


def test_toggle_active_redirects_home_for_unknown_feed():
    session = _make_session()

    response = toggle_active(999999, session=session)

    assert response.status_code == 303
    assert response.headers["location"] == "/feeds/999999"
