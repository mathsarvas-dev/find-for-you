from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class LLMInvalidResponse(LLMError):
    pass


class LLMRateLimited(LLMError):
    pass


class LLMModelMissing(LLMError):
    """The API answered 404 model_not_found: the configured model is gone."""


class LLMProvider(Protocol):
    async def complete_json(self, messages: list[dict[str, str]], schema: type[T]) -> T: ...
