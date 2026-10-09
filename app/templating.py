from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR
from app.paths import cross_platform_basename



def _ui_context(request: Request) -> dict[str, str]:
    # Read-only lookup so every rendered page can apply the persisted UI theme without
    # requiring each individual router to add it to its template context.
    try:
        from sqlmodel import Session
        from app.database import engine
        from app.models import AppConfig

        with Session(engine) as session:
            config = session.get(AppConfig, 1)
        return {"ui_theme": config.ui_theme if config and config.ui_theme in {"light", "dark"} else "light"}
    except Exception:
        # Template rendering must remain available even if the optional UI preference
        # lookup encounters a transient database problem.
        return {"ui_theme": "light"}


templates = Jinja2Templates(
    directory=str(BASE_DIR / "app" / "templates"),
    context_processors=[_ui_context],
)


def format_mmss(ms: float) -> str:
    total_seconds = max(0, int(ms) // 1000)
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


def confidence_class(confidence: float) -> str:
    if confidence >= 0.75:
        return "conf-high"
    if confidence >= 0.4:
        return "conf-medium"
    return "conf-low"


def basename(path: str | None) -> str:
    return cross_platform_basename(path)


templates.env.filters["mmss"] = format_mmss
templates.env.filters["conf_class"] = confidence_class
templates.env.filters["basename"] = basename
