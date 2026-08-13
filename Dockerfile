# syntax=docker/dockerfile:1

FROM node:22-alpine AS registry-ui

WORKDIR /build/webui
COPY webui/package.json webui/package-lock.json ./
RUN npm ci
COPY webui/ ./
RUN npm run check && npm run build:fleet


FROM python:3.12-slim AS registry-wheel

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY --from=registry-ui /build/src/takt/registry/static/ ./src/takt/registry/static/
RUN python -m pip wheel --wheel-dir /wheels ".[registry]"


FROM python:3.12-slim AS registry

LABEL org.opencontainers.image.title="TAKT Fleet Registry" \
      org.opencontainers.image.description="Self-hosted management and data-mirror service for TAKT Raspberry Pis" \
      org.opencontainers.image.source="https://github.com/maxi-smidt/takt"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY --from=registry-wheel /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels "takt[registry]" \
    && rm -rf /wheels \
    && groupadd --gid 10001 takt \
    && useradd --uid 10001 --gid takt --create-home \
        --home-dir /home/takt --shell /usr/sbin/nologin takt \
    && install -d -o takt -g takt /data

COPY bundled-release/ /opt/takt/bundled-release/
ENV TAKT_BUNDLED_RELEASE_DIR=/opt/takt/bundled-release

USER takt
WORKDIR /home/takt

EXPOSE 8090
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=3).read()"]

ENTRYPOINT ["takt-registry"]
CMD ["--host", "0.0.0.0", "--port", "8090", "--data-directory", "/data"]
