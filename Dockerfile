FROM node:22-alpine AS frontend
WORKDIR /build/pipeline_ui
COPY pipeline_ui/package.json pipeline_ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY pipeline_ui/ ./
RUN npm run build

FROM python:3.11-slim-bookworm AS api
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app \
    PIPELINE_UI_HOST=0.0.0.0 PORT=8765 \
    PIPELINE_UI_SCRATCH=/tmp/normgraph-visitors \
    ONTOGPT=/app/.venv-ontogpt/bin/ontogpt
WORKDIR /app
COPY pipeline/requirements/ /app/pipeline/requirements/
RUN python -m venv /app/.venv-testgen && \
    /app/.venv-testgen/bin/pip install --no-cache-dir -r /app/pipeline/requirements/pipeline.txt && \
    python -m venv /app/.venv-ontogpt && \
    /app/.venv-ontogpt/bin/pip install --no-cache-dir -r /app/pipeline/requirements/ontogpt.txt
COPY pipeline/ /app/pipeline/
COPY pipeline_ui/backend/ /app/pipeline_ui/backend/
COPY seed_graphs/ /app/seed_graphs/
COPY sitecustomize.py /app/sitecustomize.py
WORKDIR /app/pipeline_ui
EXPOSE 8765
CMD ["/app/.venv-testgen/bin/python", "-B", "-m", "backend.public_server"]

FROM caddy:2-alpine AS web
COPY --from=frontend /build/pipeline_ui/dist/ /usr/share/caddy/
COPY deploy/Caddyfile /etc/caddy/Caddyfile

FROM api AS standalone
COPY --from=caddy:2-alpine /usr/bin/caddy /usr/bin/caddy
COPY --from=frontend /build/pipeline_ui/dist/ /usr/share/caddy/
COPY deploy/Caddyfile.standalone /etc/caddy/Caddyfile
COPY deploy/standalone.py /app/standalone.py
RUN mkdir -p /data /config
ENV SITE_ADDRESS=:80 PIPELINE_UI_HOST=127.0.0.1 \
    XDG_CONFIG_HOME=/config XDG_DATA_HOME=/data
EXPOSE 80 443
CMD ["/app/.venv-testgen/bin/python", "-B", "/app/standalone.py"]
