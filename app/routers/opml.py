import logging
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.database import get_session
from app.models import Feed
from app.services import feed_ingest, opml_import
from app.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/opml/import")
async def opml_import_preview(
    request: Request, file: UploadFile = File(...), session: Session = Depends(get_session)
):
    content = await file.read()
    try:
        podcasts = opml_import.parse_opml(content)
    except ET.ParseError:
        return templates.TemplateResponse(
            request,
            "opml_import.html",
            {"rows": [], "parse_error": "Die Datei konnte nicht als OPML gelesen werden."},
            status_code=400,
        )

    existing_urls = {f.original_rss_url for f in session.exec(select(Feed)).all()}
    rows = [
        {"title": p.title, "xml_url": p.xml_url, "already_subscribed": p.xml_url in existing_urls}
        for p in podcasts
    ]
    return templates.TemplateResponse(request, "opml_import.html", {"rows": rows})


@router.post("/opml/import/confirm")
async def opml_import_confirm(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    existing_urls = {f.original_rss_url for f in session.exec(select(Feed)).all()}

    imported = 0
    seen_this_submit: set[str] = set()
    index = 0
    while f"url_{index}" in form:
        if form.get(f"import_{index}"):
            url = str(form[f"url_{index}"]).strip()
            title = str(form.get(f"title_{index}", "")).strip() or url
            if url and url not in existing_urls and url not in seen_this_submit:
                feed = Feed(title=title, original_rss_url=url)
                session.add(feed)
                session.commit()
                session.refresh(feed)
                try:
                    feed_ingest.poll_feed(session, feed)
                except Exception:
                    log.exception("Initial poll failed for OPML-imported feed %s", feed.id)
                imported += 1
                seen_this_submit.add(url)
        index += 1

    return RedirectResponse(url=f"/?opml_imported={imported}", status_code=303)
