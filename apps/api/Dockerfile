# slim (not alpine) — argon2-cffi and asyncpg ship manylinux wheels for glibc;
# musl would build them from source on every image rebuild.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# tzdata — services/credits.py resolves Asia/Seoul for the monthly cycle reset.
# curl/git — uv fetches PEP 723 script dependencies at first run.
# fonts-nanum — exported PDFs embed it. reportlab's bundled CID fonts render
# Korean only where the viewer ships the Adobe-Korea1 CMaps; without them the
# text is silently absent, which for a deck means a blank slide on a projector.
# An embedded TTF is ~10 MB of image for a file that renders anywhere.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl ca-certificates git fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

# MCP stdio servers come in two flavours and both have to be spawnable:
#   · PEP 723 scripts and PyPI servers  → uv / uvx
#   · the reference servers from the MCP project (npm) → node / npx
COPY --from=ghcr.io/astral-sh/uv:0.5.29 /uv /uvx /usr/local/bin/
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Read from pyproject rather than repeating it. The two lists drifted the first
# time a dependency was added — the declaration changed, the image did not, and
# the feature failed at runtime with "No module named …".
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# Uploaded blobs live here; the volume is mounted over it in compose.
# uv caches downloaded script dependencies here; without a writable home the
# first connector spawn fails with a permission error rather than a clear one.
ENV UV_CACHE_DIR=/srv/data/uv-cache \
    NPM_CONFIG_CACHE=/srv/data/npm-cache
RUN mkdir -p /srv/data/files /srv/data/uv-cache /srv/data/npm-cache \
    && useradd --uid 1000 --create-home kchat \
    && chown -R kchat:kchat /srv
USER kchat

EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
