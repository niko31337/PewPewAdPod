from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR
from app.paths import cross_platform_basename

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


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
