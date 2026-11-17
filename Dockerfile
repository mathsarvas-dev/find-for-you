FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 fonts-dejavu-core && rm -rf /var/lib/apt/lists/*

# Layer order matters: dependencies (and PyTorch for the worker) are installed
# from pyproject.toml alone, BEFORE the source is copied. A code change only
# rebuilds the last layers and never re-downloads torch.
ARG WITH_EMBEDDINGS=0
COPY pyproject.toml ./
RUN python -c "import tomllib; p=tomllib.load(open('pyproject.toml','rb'))['project']; open('/tmp/req-base.txt','w').write('\n'.join(p['dependencies'])); open('/tmp/req-emb.txt','w').write('\n'.join(p['optional-dependencies']['embeddings']))" \
    && pip install --no-cache-dir -r /tmp/req-base.txt \
    && if [ "$WITH_EMBEDDINGS" = "1" ]; then \
         pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
         && pip install --no-cache-dir -r /tmp/req-emb.txt; \
       fi

# Source last: only these layers change on a code edit.
COPY rumo_core/ rumo_core/
COPY api/ api/
COPY worker/ worker/
COPY alembic/ alembic/
COPY alembic.ini ./
RUN pip install --no-cache-dir --no-deps .
