FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY rumo_core/ rumo_core/
COPY api/ api/
COPY worker/ worker/
COPY alembic/ alembic/
COPY alembic.ini ./
RUN pip install --no-cache-dir .
