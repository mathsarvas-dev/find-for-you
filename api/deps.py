"""Shared route dependencies (leaf module: imports nothing from api.*)."""

from rumo_core.config import get_settings


def current_user_id() -> int:
    """Single point to swap for real authentication in the SaaS."""
    return get_settings().default_user_id
