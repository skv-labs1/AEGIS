# Aegis, in one container.
#
# Everything runs together: the three demo enterprise systems, the governance
# gateway, the console API and the built console. That is right for a demo and
# wrong for production, where the enterprise systems would be somebody else's
# software on somebody else's network. The split is a change to
# mcp_upstreams.yaml, not to any code.

# --- build the console -------------------------------------------------------
FROM node:22-alpine AS console
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- runtime -----------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    AEGIS_DATABASE_URL=sqlite:////data/aegis.db \
    AEGIS_DEMO_DB=/data/enterprise_demo.db

WORKDIR /app

COPY mcp-servers/ /app/mcp-servers/
COPY backend/ /app/backend/

# Editable installs on purpose. The risk policy, the upstream registry, the
# provider chain, the eval scenarios and the replay traces are editable files
# alongside the code rather than package data, so an operator can read and
# change them. A normal install would move the code and leave those behind.
RUN pip install --no-cache-dir -e ./mcp-servers -e ./backend

COPY --from=console /build/dist /app/frontend/dist
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh && mkdir -p /data

# The demo systems listen only on localhost inside the container. Nothing but
# the console and the gateway is reachable from outside, which mirrors the real
# arrangement where an agent never reaches an enterprise system directly.
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/app/entrypoint.sh"]
