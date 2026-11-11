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
    return JSONResponse(status_code=503, content={"status": "degraded", "db": "down"})


def current_user_id() -> int:
    """Single point to swap for real authentication in the SaaS."""
    return get_settings().default_user_id


@app.get("/health")
def health(session: Session = Depends(get_session)):
    session.execute(text("SELECT 1"))
    return {"status": "ok", "db": "up"}
