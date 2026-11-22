#!/usr/bin/env bash
# Replaces the `api` replicas one at a time, so answers keep being served during a deploy.
#
#   docker compose pull api            # or build: the new image under the same tag
#   bash scripts/rolling-restart-api.sh
#
# Per replica: start a new one beside it, wait until healthy, then stop the old one with the full
# grace period (open answers finish for up to 120 s, the rest are stored as interrupted). nginx in
# `web` re-resolves `api` every 10 s.
#
# Migrations run first while old replicas still serve, so use this only for additive migrations;
# otherwise stop everything and `docker compose up -d`. SKIP_MIGRATE=1 skips the step.
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
