# syntax=docker/dockerfile:1
# MSB v3 — the real runtime image (close-out Phase 1, FR-1.1 / FR-1.2).
#
# Builds msb_v3 from a clean checkout. The image deliberately carries NO
# secrets, NO vault, NO host state — backends (ollama, qdrant) are reached
# over the network via OLLAMA_URL / QDRANT_HOST / QDRANT_PORT, all
# overridable at run time. Multi-stage: locked deps first (cached layer),
# then the package source, so dependency layers cache across builds.
#
# Run (host ollama):
#   docker run --rm -p 8766:8766 \
#     -e OLLAMA_URL=http://host.docker.internal:11434 \
#     msb-v3:latest
#
# Smoke: curl -f http://127.0.0.1:8766/health

FROM python:3.12-slim AS deps
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock

FROM python:3.12-slim AS runtime
WORKDIR /app
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY src ./src

# PYTHONPATH mirrors how the repo runs everywhere (scripts/run.sh) — no
# build-backend dance needed to ship the package.
ENV PYTHONPATH=/app/src \
    MSB_HOST=0.0.0.0 \
    MSB_PORT=8766 \
    MSB_ACTIVE_BACKEND=ollama \
    OLLAMA_URL=http://host.docker.internal:11434 \
    QDRANT_HOST=localhost \
    QDRANT_PORT=6333

EXPOSE 8766
CMD ["python", "-m", "msb_v3"]
