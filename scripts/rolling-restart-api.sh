#!/usr/bin/env bash
# Replaces the `api` replicas one at a time, so answers keep being served during a deploy.
#
#   docker compose pull api            # or build: the new image under the same tag
#   bash scripts/rolling-restart-api.sh
#
# For each running replica: start one new replica beside it (the current image), wait until
# it is healthy, then stop the old one with the full grace period — it stops accepting,
# lets its open answers finish for up to 120 s, stores the rest as interrupted and frees
# their conversations (docs/architecture.md, "Restarts and turn claims"). nginx in `web`
# re-resolves `api` every 10 s and moves on to a live replica when one refuses.
#
# Migrations run first (`migrate`), while the old replicas still serve: use this only when
# the release's migrations are additive. Otherwise stop everything and `docker compose up
# -d` (docs/deployment.md, "Upgrades"). SKIP_MIGRATE=1 skips the step.
#
# Run from anywhere; it works on the compose project at the repository root.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

HEALTH_WAIT_SEC=${HEALTH_WAIT_SEC:-180}
# Above TURN_DRAIN_SEC (120) plus the stop-and-store step; same as stop_grace_period.
STOP_SEC=${STOP_SEC:-150}

ids() { docker compose ps -q api | sort; }

mapfile -t old < <(ids)
if [ "${#old[@]}" -eq 0 ]; then
  echo "no running api replica; start the stack with: docker compose up -d" >&2
  exit 1
fi

if [ "${SKIP_MIGRATE:-0}" != 1 ]; then
  echo "== migrations"
  docker compose run --rm --no-deps migrate
fi

for id in "${old[@]}"; do
  mapfile -t before < <(ids)
  echo "== starting a replica beside ${id:0:12}"
  docker compose up -d --no-deps --no-recreate --scale api=$((${#before[@]} + 1)) api
  new=$(comm -13 <(printf '%s\n' "${before[@]}") <(ids) | head -1)
  if [ -z "$new" ]; then
    echo "no new replica appeared; old replicas left running" >&2
    exit 1
  fi
  waited=0
  until [ "$(docker inspect -f '{{.State.Health.Status}}' "$new")" = healthy ]; do
    if [ "$waited" -ge "$HEALTH_WAIT_SEC" ]; then
      echo "replica ${new:0:12} not healthy after ${HEALTH_WAIT_SEC}s; old replicas left" \
        "running — see: docker logs ${new:0:12}" >&2
      exit 1
    fi
    sleep 5
    waited=$((waited + 5))
  done
  echo "== ${new:0:12} healthy; stopping ${id:0:12} (up to ${STOP_SEC}s for open answers)"
  docker stop --time "$STOP_SEC" "$id" >/dev/null
  docker rm "$id" >/dev/null
done
echo "== done: $(ids | wc -l) api replica(s) on the current image"
