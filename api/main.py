import math

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from api.applications import router as applications_router
from api.chat_filters import router as chat_filters_router
from api.deps import current_user_id
from api.discovery import router as discovery_router
from api.feeds import router as feeds_router
from api.funnel import router as funnel_router
from api.interview import router as interview_router
from api.matching import router as matching_router
from api.overview import router as overview_router
from api.pipeline import router as pipeline_router
from api.portals import router as portals_router
from api.profile import router as profile_router
from api.quality import router as quality_router
from api.sources import router as sources_router
from rumo_core.config import get_settings
from rumo_core.db import get_session
from rumo_core.matching.chat_filters import (
    INVALID_CHARS_MESSAGE,
    NEGATION_MESSAGE,
    QUERY_TOO_LONG_MESSAGE,
)

_QUERY_MESSAGES = {NEGATION_MESSAGE, QUERY_TOO_LONG_MESSAGE, INVALID_CHARS_MESSAGE}

__all__ = ["app", "current_user_id"]

app = FastAPI(title="Rumo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["*"],
)

app.include_router(profile_router)
app.include_router(interview_router)
app.include_router(sources_router)
app.include_router(matching_router)
app.include_router(discovery_router)
app.include_router(overview_router)
app.include_router(chat_filters_router)
app.include_router(quality_router)
app.include_router(feeds_router)
app.include_router(portals_router)
app.include_router(funnel_router)
app.include_router(applications_router)
app.include_router(pipeline_router)


def _json_safe(value):
    """Replace non-finite floats (not valid JSON) with their string form."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, str):  # lone surrogates cannot be encoded as UTF-8
        return value.encode("utf-8", "replace").decode("utf-8")
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _query_text_message(errors) -> str | None:
    """Specific PT-BR string for a rejected taste text (never echoes the input)."""
    for err in errors:
        loc = err.get("loc", ())
        if not loc or loc[0] != "body" or loc[-1] != "query_text":
            continue
        if err.get("type") == "string_too_long":
            return QUERY_TOO_LONG_MESSAGE
        if err.get("type") == "value_error":
            msg = str(err.get("msg", "")).removeprefix("Value error, ")
            if msg in _QUERY_MESSAGES:
                return msg
            return INVALID_CHARS_MESSAGE
    return None


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Same body as FastAPI's default handler, but an echoed Infinity/NaN input
    # would make the 422 itself fail to serialise (500).
    errors = exc.errors()
    message = _query_text_message(errors)
    if message is not None:
        return JSONResponse(status_code=422, content={"detail": message})
    return JSONResponse(
        status_code=422,
        content={"detail": _json_safe(jsonable_encoder(errors))},
    )


@app.exception_handler(OperationalError)
async def db_unavailable(request: Request, exc: OperationalError) -> JSONResponse:
    # Raised both while resolving the session dependency and while querying
    # (e.g. Postgres still starting): degrade instead of a 500.
    if request.url.path == "/health":
        return JSONResponse(
            status_code=503, content={"status": "degraded", "db": "down"}
        )
    return JSONResponse(
        status_code=503, content={"detail": "Banco de dados indisponível"}
    )


@app.get("/health")
def health(session: Session = Depends(get_session)):
    session.execute(text("SELECT 1"))
    return {"status": "ok", "db": "up"}
