import math

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from api.deps import current_user_id
from api.discovery import router as discovery_router
from api.interview import router as interview_router
from api.matching import router as matching_router
from api.profile import router as profile_router
from api.sources import router as sources_router
from rumo_core.config import get_settings
from rumo_core.db import get_session

__all__ = ["app", "current_user_id"]

app = FastAPI(title="Rumo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET", "POST", "PATCH", "PUT"],
    allow_headers=["*"],
)

app.include_router(profile_router)
app.include_router(interview_router)
app.include_router(sources_router)
app.include_router(matching_router)
app.include_router(discovery_router)


def _json_safe(value):
    """Replace non-finite floats (not valid JSON) with their string form."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Same body as FastAPI's default handler, but an echoed Infinity/NaN input
    # would make the 422 itself fail to serialise (500).
    return JSONResponse(
        status_code=422,
        content={"detail": _json_safe(jsonable_encoder(exc.errors()))},
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
