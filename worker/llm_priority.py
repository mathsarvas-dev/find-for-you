"""Interactive LLM work (resume import, interview turns) has priority over the
background jobs (job field fill, match scoring): Groq's free tier gives only
8000 tokens/minute, so a long fill backlog would starve the person waiting."""

from sqlalchemy import exists, or_, select

from rumo_core.models import InterviewSession, ProfileImport

# interview states in which the interview job is about to call / calling the LLM
INTERVIEW_LLM_STATES = ("generating", "working")
# import states that still need the LLM
IMPORT_LLM_STATES = ("pending", "extracting")


def interactive_llm_busy(session_factory) -> bool:
    """True while any user waits for an interview turn or a resume extraction."""
    with session_factory() as s:
        return bool(
            s.scalar(
                select(
                    or_(
                        exists().where(InterviewSession.state.in_(INTERVIEW_LLM_STATES)),
                        exists().where(ProfileImport.status.in_(IMPORT_LLM_STATES)),
                    )
                )
            )
        )
