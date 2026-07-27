from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.routers import public_feed
from app.services.feed_generator import build_master_feed_xml


def test_build_master_feed_xml_defaults_self_link_to_plain_path():
    xml = build_master_feed_xml([], "http://localhost:8000/", "http://localhost:8000/media/covers/master/cover.jpg")
    xml = xml.decode("utf-8")

    assert "http://localhost:8000/feed/master.xml" in xml


def test_build_master_feed_xml_uses_the_given_feed_path_for_self_link():
    xml = build_master_feed_xml(
        [],
        "http://localhost:8000/",
        "http://localhost:8000/media/covers/master/cover.jpg",
        feed_path="s3cr3t/master.xml",
    )
    xml = xml.decode("utf-8")

    assert "http://localhost:8000/s3cr3t/master.xml" in xml
    # the plain path must not appear anywhere - that's the whole point of hiding it
    assert "http://localhost:8000/feed/master.xml" not in xml


def _make_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    app = FastAPI()
    app.include_router(public_feed.router)

    def _override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    return TestClient(app)


def test_master_feed_route_self_link_uses_plain_path_when_no_token_configured(monkeypatch):
    monkeypatch.setattr(public_feed.settings, "master_feed_secret_token", None)
    client = _make_client()

    response = client.get("/feed/master.xml")

    assert response.status_code == 200
    assert "feed/master.xml" in response.text


def test_master_feed_route_self_link_uses_secret_path_when_token_configured(monkeypatch):
    monkeypatch.setattr(public_feed.settings, "master_feed_secret_token", "s3cr3t")
    client = _make_client()

    response = client.get("/feed/master.xml")

    assert response.status_code == 200
    assert "s3cr3t/master.xml" in response.text
    assert "http://testserver/feed/master.xml" not in response.text
