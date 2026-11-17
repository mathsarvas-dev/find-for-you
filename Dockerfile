FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY rumo_core/ rumo_core/
COPY api/ api/
COPY worker/ worker/
COPY alembic/ alembic/
COPY alembic.ini ./
RUN apt-get update && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
ARG WITH_EMBEDDINGS=0
RUN if [ "$WITH_EMBEDDINGS" = "1" ]; then       pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu &&       pip install --no-cache-dir ".[embeddings]";     else       pip install --no-cache-dir .;     fi
