from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from api.deps import current_user_id
from api.interview import router as interview_router
from api.profile import router as profile_router
from api.sources import router as sources_router
from rumo_core.config import get_settings
from rumo_core.db import get_session

__all__ = ["app", "current_user_id"]

app = FastAPI(title="Rumo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)

app.include_router(profile_router)
app.include_router(interview_router)
app.include_router(sources_router)


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
