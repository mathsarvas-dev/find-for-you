"""LLM health for Configurações: model existence, quota meter, retry queue."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.deps import current_user_id
from rumo_core.config import get_settings
from rumo_core.db import get_session
from rumo_core.llm.status import llm_status

router = APIRouter(tags=["llm"])


@router.get("/llm/status")
def get_llm_status(
    session: Session = Depends(get_session), user_id: int = Depends(current_user_id)
):
    return llm_status(session, get_settings(), user_id)
