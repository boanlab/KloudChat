# slim, not alpine: argon2-cffi and asyncpg ship glibc wheels; musl builds them
# from source.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# tzdata: services/credits.py resolves Asia/Seoul for the monthly reset.
# curl/git: uv fetches PEP 723 script dependencies on first run.
# fonts-nanum: embedded in exported PDFs; reportlab's CID fonts render Korean
# only where the viewer ships the Adobe-Korea1 CMaps.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl ca-certificates git fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

# MCP stdio servers: PEP 723 scripts and PyPI servers via uv/uvx, the MCP
# project's reference servers via node/npx.
COPY --from=ghcr.io/astral-sh/uv:0.5.29 /uv /uvx /usr/local/bin/
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Dependencies read from pyproject so the two lists cannot drift.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# Uploaded blobs (compose mounts a volume over it) and the uv/npm caches for
# connector dependencies; all must be writable by the runtime user.
ENV UV_CACHE_DIR=/srv/data/uv-cache \
    NPM_CONFIG_CACHE=/srv/data/npm-cache
RUN mkdir -p /srv/data/files /srv/data/uv-cache /srv/data/npm-cache \
    && useradd --uid 1000 --create-home kchat \
    && chown -R kchat:kchat /srv
USER kchat

EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
