# syntax=docker/dockerfile:1.7

ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.11-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.32

FROM ${NODE_IMAGE} AS frontend-builder
WORKDIR /build/frontend
COPY examples/knowledge_extraction_workbench/frontend/package.json ./
COPY examples/knowledge_extraction_workbench/frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --ignore-scripts --no-audit --no-fund
COPY examples/knowledge_extraction_workbench/frontend/ ./
RUN npm run build

FROM ${UV_IMAGE} AS uv-binary

FROM ${PYTHON_IMAGE} AS python-builder
COPY --from=uv-binary /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY openjiuwen/ ./openjiuwen/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime
ARG APP_VERSION=0.1.4
LABEL org.opencontainers.image.title="Knowledge Extraction Workbench" \
      org.opencontainers.image.description="Offline-deployable openJiuwen knowledge extraction workbench" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WORKBENCH_HOST=0.0.0.0 \
    WORKBENCH_PORT=8765 \
    WORKBENCH_DATA_DIR=/var/lib/knowledge-workbench

RUN groupadd --gid 10001 workbench \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/workbench workbench \
    && mkdir -p /app/examples/knowledge_extraction_workbench/frontend/dist /var/lib/knowledge-workbench \
    && chown -R workbench:workbench /app /var/lib/knowledge-workbench

WORKDIR /app
COPY --from=python-builder --chown=workbench:workbench /build/.venv/ ./.venv/
COPY --chown=workbench:workbench examples/__init__.py ./examples/__init__.py
COPY --chown=workbench:workbench examples/knowledge_extraction_workbench/backend/ ./examples/knowledge_extraction_workbench/backend/
COPY --chown=workbench:workbench examples/knowledge_extraction_workbench/__init__.py ./examples/knowledge_extraction_workbench/__init__.py
COPY --from=frontend-builder --chown=workbench:workbench /build/frontend/dist/ ./examples/knowledge_extraction_workbench/frontend/dist/

USER workbench
EXPOSE 8765
VOLUME ["/var/lib/knowledge-workbench"]

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/v1/health', timeout=3)" || exit 1

CMD ["python", "-m", "examples.knowledge_extraction_workbench.backend"]
