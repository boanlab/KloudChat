# Deployment

This guide covers running KloudChat for real users. For a laptop trial, the
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

Four long-running containers, one initialization container, and bind-mounted
data directories:

| Container | Port | Holds |
| --- | --- | --- |
| `kloudchat-web` | 5173 → 80 | Static bundle, nginx proxying `/api` and `/llm` |
| `kloudchat-api` | 8100 | All application logic; **the only process with the LiteLLM master key** |
| `kloudchat-db` | 5433 → 5432 | Postgres 16 |
| `kloudchat-print` | internal | Headless Chromium, HTML to PDF; reachable from the API only |

```
./data/postgres     database files
./data/files        uploaded blobs and generated media
./data/uv-cache     MCP connector dependency cache
```

The `kloudchat-init` container runs once before the API and exits.
Docker creates bind-mount directories as root and the API runs as uid 1000, so
it creates `files` and `uv-cache` and hands them over — without it, uploads
fail with "permission denied" on a first boot only. It shows up as `Exited (0)`
in `docker compose ps -a`, which is the expected state.

Models and tools are **not** part of this deployment. They live in
[`KloudChat-LLM`](https://github.com/boanlab/KloudChat-LLM) and are
reached over one gateway URL. If that backend is unavailable, sign-in, history,
workspace and settings continue to work; model calls and tools fail with an
explicit "not connected".

## Prerequisites

- Docker Engine 24+ with Compose v2
- ~2 GB disk for images, plus whatever generated media will need — a single
  video clip is tens of megabytes
- A TLS terminator in front (see below)
- Network reachability from `kloudchat-api` to the backend gateway. The browser
  needs no route to it at all.

## Install

From the published images, [`boanlab/KloudChat`](https://github.com/boanlab/KloudChat) is the shorter path.
From this checkout:

```bash
git clone https://github.com/boanlab/KloudChat-dev.git
cd KloudChat-dev

cp .env.example .env
```

Edit `.env`. At minimum:

```bash
KCHAT_JWT_SECRET=$(openssl rand -hex 32)   # generate, never reuse the example
KCHAT_COOKIE_SECURE=true                   # you are behind TLS
KCHAT_CORS_ORIGINS=["https://kchat.example.com"]
KCHAT_DB_PASSWORD=<something long>
BACKEND_BASE_URL=http://<llm-host-ip>:8080        # the KloudChat-LLM gateway
```

Then pin the images. `docker-compose.yml` ships `:latest`, which moves with
every push to main; a deployment wants a version tag:

```yaml
  api:
    image: boanlab/kloudchat-api:1.0.0
  print:
    image: boanlab/kloudchat-print:1.0.0
  web:
    image: boanlab/kloudchat-web:1.0.0
```

Then:

```bash
docker compose up -d
curl -fsS localhost:8100/api/health
```

Create the administrator either by signing up first (the first account becomes
administrator) or by setting `KCHAT_ADMIN_EMAIL` and `KCHAT_ADMIN_PASSWORD`
before the first start. Those two apply **only** when the database has no
accounts, so they cannot be used to reset a forgotten password later.

Finish in the UI: **Settings → System → Integrations**, paste the gateway
address and the LiteLLM master key, and use each field's connection test.

## TLS and reverse proxy

KloudChat does not terminate TLS. Put nginx, Caddy or a load balancer in front of
`kloudchat-web` and set `KCHAT_COOKIE_SECURE=true`.

Three requirements the proxy must satisfy:

**Do not buffer responses.** Answers stream token by token over SSE. A
buffering proxy makes them appear all at once, which reads as a hang.

**Allow long-lived responses.** A tool-using turn on a local model can run for
minutes; `CHAT_TIMEOUT_SEC` defaults to 900. The API writes an SSE comment
every 15 seconds while a turn is silent, so a proxy *idle* timeout of 60
seconds is fine — only a cap on the total response time has to be raised.

**Forward the client's address.** `X-Forwarded-For` is the only way KloudChat
learns who is connecting; without it every audit row, share visit and 접속기록
line records the proxy. The `kloudchat-web` container then has to be told which
hops to believe — `KCHAT_TRUSTED_PROXIES`, and narrow it to this proxy's own
address wherever port 5173 is reachable by anyone else. See
[Behind a reverse proxy](configuration.md#behind-a-reverse-proxy).

```nginx
# At http level: $uri is the normalised path with the query string already
# stripped, which is what keeps access tokens out of the log (see below).
log_format KloudChat '$remote_addr - $remote_user [$time_local] '
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
  access_log /var/log/nginx/kchat.log KloudChat;

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

- **LiteLLM is not publicly reachable.** KloudChat's `/llm` route is the intended
  path to it, authenticated with per-user API keys.
- **Postgres is not published** beyond the compose network in production.
  Remove the `ports:` mapping on `db` if the host is exposed.

## Published images

`docker-compose.yml` runs these images; it builds nothing. They publish to
Docker Hub for `linux/amd64` and `linux/arm64`.

| Trigger | Tags |
| --- | --- |
| push to `main` | `:latest` |
| tag `v1.2.3` | `:1.2.3`, `:1.2`, `:latest` |
| tag `v1.2.3-rc1` | `:1.2.3-rc1` — a prerelease never moves `:latest` |

Version tags are immutable. `:latest` is not: both a release and a later push to
`main` write it, so it points at whichever happened most recently — after
tagging `v1.2.3`, the next merge moves `:latest` past it.

**Pin a version tag in production**, by editing the three `image:` lines in
`docker-compose.yml`. `:latest` is only ever a convenience, and a pinned tag
committed to your deployment branch is a record of what is running.

Running a fork's own build means changing the `boanlab/` namespace on the same
three lines to the Docker Hub account that published it.

To build from a checkout instead — a patch you have not published, or an
architecture with no published tag — overlay `docker-compose.build.yml`:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

Those images are tagged `:local`, so a local build never occupies the cache
under a published version's name.

Only the image whose sources changed is rebuilt: a commit touching `apps/api/`
republishes `kloudchat-api` and leaves `kloudchat-web:latest` where it was. A tag
build publishes every image regardless, so a release always leaves a complete
set of tags behind.

Publishing from a fork needs two repository secrets — `DOCKERHUB_USERNAME` and
`DOCKERHUB_TOKEN` (an access token with Read & Write scope). The username
doubles as the image namespace, so no workflow edit is required.

## Backups

Two things are stateful and both matter:

```bash
# Database — users, sessions, artifacts, the credit ledger
docker compose exec -T db pg_dump -U kchat kchat | gzip > kloudchat-$(date +%F).sql.gz

# Blobs — uploads and generated media. The database references these by id.
tar czf kloudchat-files-$(date +%F).tar.gz data/files
```

Take both at the same time. A database restored without its blobs leaves
artifacts whose content 404s.

Restore:

```bash
docker compose up -d db
gunzip -c kloudchat-<date>.sql.gz | docker compose exec -T db psql -U kchat kchat
tar xzf kloudchat-files-<date>.tar.gz
docker compose up -d
```

`.env` is not in a backup by design. Keep `KCHAT_JWT_SECRET` in your secret
store — restoring a database with a different secret invalidates every issued
access token, and, because `system_settings` secrets are encrypted with a key
derived from it, makes the stored master key unreadable.

## Upgrades

```bash
git pull                      # compose file and .env.example changes
$EDITOR docker-compose.yml    # bump the three image tags to the new version
docker compose pull
docker compose up -d
docker compose logs -f api    # watch the migration
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
`"litellm":"unavailable"` with `"status":"ok"` means KloudChat is healthy and the
backend is not — sign-in and history still work.

```bash
docker compose logs api --tail=100
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

The current build assumes **one API instance**. Three things stand in the way
of running more:

- **Migrations run on container start.** With multiple replicas, two containers
  race the same migration. Move `alembic upgrade head` into a separate job
  first.
- **Runtime settings are cached in-process.** A change made on one replica
  reaches the others within the cache TTL, not instantly.
- **A running turn's stop signal is in-process.** `POST /sessions/{id}/stop`
  reaches the turn only on the replica generating it, so 중단 would become
  unreliable behind a round-robin balancer. Sticky sessions or a shared signal
  is the fix.

None is a hard barrier, but all three need addressing before adding replicas.

Vertical headroom is mostly about concurrent streaming turns: each holds an
upstream HTTP connection for as long as the answer takes. The database is not
the bottleneck at small-team scale.
