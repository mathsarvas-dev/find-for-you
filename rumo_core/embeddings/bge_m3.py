DEFAULT_MAX_TOKENS = 512


def model_version_for(max_tokens: int) -> str:
    return f"bge-m3@1:max{max_tokens}"


# Version string of the default embedder; importable without loading torch
# (sentence_transformers is only imported lazily in `_load`).
MODEL_VERSION = model_version_for(DEFAULT_MAX_TOKENS)


class BgeM3Embedder:
    dimensions = 1024

    def __init__(self, model_name: str = "BAAI/bge-m3",
                 max_tokens: int = DEFAULT_MAX_TOKENS):
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.model_version = model_version_for(max_tokens)
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(self.model_name)
            model.max_seq_length = self.max_tokens
            self._model = model
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        vectors = model.encode(
            texts, normalize_embeddings=True, convert_to_numpy=True
        )
        return [[float(x) for x in v] for v in vectors]
