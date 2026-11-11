from typing import Protocol


class Embedder(Protocol):
    dimensions: int
    model_version: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...
