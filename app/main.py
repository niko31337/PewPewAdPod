import asyncio
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, settings
from app.database import init_db, session_scope
from app.logging_conf import setup_logging
from app.routers import config, episodes, feeds, jingle_finder, opml, public_feed, review
from app.services import branding, pipeline
from app.services.scheduler import shutdown_scheduler, start_scheduler


def _quiet_client_disconnect_errors(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """On Windows, ProactorEventLoop logs an ERROR-level traceback whenever a client
    (e.g. the browser's audio element seeking) aborts a range-request mid-stream. It's a
    normal, harmless disconnect - not an application bug - so don't let it spam the log."""
    exception = context.get("exception")
    if isinstance(exception, ConnectionResetError):
        return
    loop.default_exception_handler(context)

setup_logging()
settings.ensure_dirs()
init_db()
branding.ensure_master_cover()

with session_scope() as _startup_session:
    _recovered = pipeline.recover_interrupted_episodes(_startup_session)
    if _recovered:
        logging.getLogger(__name__).warning(
            "Requeued %d episode(s) left in an in-progress status by an interrupted previous run", _recovered
        )

app = FastAPI(title="PewPewAdPod")

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
app.mount("/media/audio/originals", StaticFiles(directory=str(settings.originals_dir)), name="originals")
app.mount("/media/audio/processed", StaticFiles(directory=str(settings.processed_dir)), name="processed")
app.mount("/media/covers", StaticFiles(directory=str(settings.covers_dir)), name="covers")

app.include_router(feeds.router)
app.include_router(episodes.router)
app.include_router(review.router)
app.include_router(config.router)
app.include_router(jingle_finder.router)
app.include_router(opml.router)
app.include_router(public_feed.router)


@app.on_event("startup")
def on_startup() -> None:
    asyncio.get_running_loop().set_exception_handler(_quiet_client_disconnect_errors)
    start_scheduler()


@app.on_event("shutdown")
def on_shutdown() -> None:
    shutdown_scheduler()
