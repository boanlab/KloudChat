# Deployment

This guide covers running kchat for real users. For a laptop trial, the
[quick start](../README.md#quick-start) is enough.

- [What you are deploying](#what-you-are-deploying)
- [Prerequisites](#prerequisites)
- [Install](#install)
- [TLS and reverse proxy](#tls-and-reverse-proxy)
- [Published images](#published-images)
- [Backups](#backups)
- [Upgrades](#upgrades)
- [Operational checks](#operational-checks)
- [Scaling notes](#scaling-notes)

## What you are deploying

Three containers and a bind-mounted data directory:

| Container | Port | Holds |
| --- | --- | --- |
| `kchat-web` | 5173 → 80 | Static bundle, nginx proxying `/api` and `/llm` |
| `kchat-api` | 8100 | All application logic; **the only process with the LiteLLM master key** |
| `kchat-db` | 5433 → 5432 | Postgres 16 |

```
./data/postgres     database files
./data/files        uploaded blobs and generated media
./data/uv-cache     MCP connector dependency cache
```

Models and tools are **not** part of this deployment. They live in
[`kloudchat-backend`](https://github.com/boanlab/kloudchat-backend) and are
reached over one gateway URL. If that backend is unavailable, sign-in, history,
workspace and settings continue to work; model calls and tools fail with an
explicit "not connected".

## Prerequisites

- Docker Engine 24+ with Compose v2
- ~2 GB disk for images, plus whatever generated media will need — a single
  video clip is tens of megabytes
- A TLS terminator in front (see below)
- Network reachability from `kchat-api` to the backend gateway. The browser
  needs no route to it at all.

## Install

```bash
git clone https://github.com/boanlab/kloudchat.git
cd kloudchat

cp .env.example .env
```

Edit `.env`. At minimum:

```bash
KCHAT_JWT_SECRET=$(openssl rand -hex 32)   # generate, never reuse the example
KCHAT_COOKIE_SECURE=true                   # you are behind TLS
KCHAT_CORS_ORIGINS=["https://kchat.example.com"]
KCHAT_DB_PASSWORD=<something long>
BACKEND_BASE_URL=https://backend.internal:8080
```

Then:

```bash
docker compose up -d --build
curl -fsS localhost:8100/api/health
```

Create the administrator either by signing up first (the first account becomes
administrator) or by setting `KCHAT_ADMIN_EMAIL` and `KCHAT_ADMIN_PASSWORD`
before the first start. Those two apply **only** when the database has no
accounts, so they cannot be used to reset a forgotten password later.

Finish in the UI: **Settings → System → Integrations**, paste the gateway
address and the LiteLLM master key, and use each field's connection test.

## TLS and reverse proxy

kchat does not terminate TLS. Put nginx, Caddy or a load balancer in front of
`kchat-web` and set `KCHAT_COOKIE_SECURE=true`.

Two requirements the proxy must satisfy:

**Do not buffer responses.** Answers stream token by token over SSE. A
buffering proxy makes them appear all at once, which reads as a hang.

**Allow long-lived responses.** A tool-using turn on a local model can run for
minutes; `CHAT_TIMEOUT_SEC` defaults to 900.

```nginx
# At http level: $uri is the normalised path with the query string already
# stripped, which is what keeps access tokens out of the log (see below).
log_format kchat '$remote_addr - $remote_user [$time_local] '
                 '"$request_method $uri $server_protocol" $status $body_bytes_sent '
                 '"$http_referer" "$http_user_agent"';

server {
  listen 443 ssl;
  http2 on;
  server_name kchat.example.com;

  ssl_certificate     /etc/letsencrypt/live/kchat.example.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/kchat.example.com/privkey.pem;

  # Generated media is fetched with the access token in the query string
  # (see docs/architecture.md §3), so this vhost logs paths rather than
  # full request lines.
  access_log /var/log/nginx/kchat.log kchat;

  location / {
    proxy_pass http://127.0.0.1:5173;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # Streaming.
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 1h;

    client_max_body_size 200m;   # matches MAX_UPLOAD_MB
  }
}
```

`client_max_body_size` must be at least `MAX_UPLOAD_MB`, or large uploads fail
at the proxy with a 413 that never reaches the application.

Also make sure that:

- **LiteLLM is not publicly reachable.** kchat's `/llm` route is the intended
  path to it, authenticated with per-user API keys.
- **Postgres is not published** beyond the compose network in production.
  Remove the `ports:` mapping on `kchat-db` if the host is exposed.

## Published images

Tagged releases publish to Docker Hub for `linux/amd64` and `linux/arm64`:

```
<namespace>/kchat-api:<version>   <namespace>/kchat-api:latest   <namespace>/kchat-api:edge
<namespace>/kchat-web:<version>   <namespace>/kchat-web:latest   <namespace>/kchat-web:edge
```

`:edge` tracks `main` and moves; pin a version tag in production.

To run published images instead of building locally, replace the `build:` keys
in `docker-compose.yml` with `image:`:

```yaml
  kchat-api:
    image: <namespace>/kchat-api:1.0.0
  kchat-web:
    image: <namespace>/kchat-web:1.0.0
```

Publishing from a fork needs two repository secrets — `DOCKERHUB_USERNAME` and
`DOCKERHUB_TOKEN` (an access token with Read & Write scope). The username
doubles as the image namespace, so no workflow edit is required.

## Backups

Two things are stateful and both matter:

```bash
# Database — users, sessions, artifacts, the credit ledger
docker compose exec -T kchat-db pg_dump -U kchat kchat | gzip > kchat-$(date +%F).sql.gz

# Blobs — uploads and generated media. The database references these by id.
tar czf kchat-files-$(date +%F).tar.gz data/files
```

Take both at the same time. A database restored without its blobs leaves
artifacts whose content 404s.

Restore:

```bash
docker compose up -d kchat-db
gunzip -c kchat-2026-08-14.sql.gz | docker compose exec -T kchat-db psql -U kchat kchat
tar xzf kchat-files-2026-08-14.tar.gz
docker compose up -d
```

`.env` is not in a backup by design. Keep `KCHAT_JWT_SECRET` in your secret
store — restoring a database with a different secret invalidates every issued
access token, and, because `system_settings` secrets are encrypted with a key
derived from it, makes the stored master key unreadable.

## Upgrades

```bash
git pull
docker compose up -d --build
docker compose logs -f kchat-api    # watch the migration
```

Migrations run automatically on container start (`alembic upgrade head`). Take
a database dump first — Alembic has no down-migration guarantee here.

Rolling back means restoring the dump, not running a downgrade.

## Operational checks

```bash
curl -fsS localhost:8100/api/health
# {"status":"ok","litellm":"ok"}
```

`status` and `litellm` are reported separately on purpose: they are different
facts, and an operator has to be able to tell which side is down.
`"litellm":"unavailable"` with `"status":"ok"` means kchat is healthy and the
backend is not — sign-in and history still work.

```bash
docker compose logs kchat-api --tail=100
docker compose ps
```

Non-destructive integration checks against a live stack:

```bash
bash scripts/smoke-test.sh
```

Each run creates its own accounts, exercises auth, approval, rotation and
suspension, then deletes what it created. It does not touch existing users or
conversations. Administrator credentials come from `.env`
(`KCHAT_ADMIN_EMAIL` / `KCHAT_ADMIN_PASSWORD`, or `ADMIN_EMAIL` / `ADMIN_PASS`
if the account differs); exporting `ADMIN_EMAIL` and `ADMIN_PASS` overrides
them for one run.

## Scaling notes

The current build assumes **one API instance**. Two things stand in the way of
running more:

- **Migrations run on container start.** With multiple replicas, two containers
  race the same migration. Move `alembic upgrade head` into a separate job
  first.
- **Runtime settings are cached in-process.** A change made on one replica
  reaches the others within the cache TTL, not instantly.

Neither is a hard barrier, but both need addressing before adding replicas.

Vertical headroom is mostly about concurrent streaming turns: each holds an
upstream HTTP connection for as long as the answer takes. The database is not
the bottleneck at small-team scale.
