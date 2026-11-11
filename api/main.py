from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from rumo_core.config import get_settings
from rumo_core.db import get_session

app = FastAPI(title="Rumo")


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


def current_user_id() -> int:
    """Single point to swap for real authentication in the SaaS."""
    return get_settings().default_user_id


from api.profile import router as profile_router  # noqa: E402

app.include_router(profile_router)


@app.get("/health")
def health(session: Session = Depends(get_session)):
    session.execute(text("SELECT 1"))
    return {"status": "ok", "db": "up"}
