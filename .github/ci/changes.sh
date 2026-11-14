#!/usr/bin/env bash
# Decides which CI jobs a change needs. One mapping for GitHub (`ci.yml`, `codeql.yml`) and
# the local mirror, so both skip the same things.
#
#   git diff --name-only BASE HEAD | .github/ci/changes.sh      # what these files need
#   .github/ci/changes.sh --all                               # everything (main, releases)
#
# Prints `key=value` lines (append them to $GITHUB_OUTPUT):
#
#   api=full|docs|none   the API job: all tests, only the tests that read docs/ and
#                        website/ (`docs`), or none
#   web=true|false       web lint/build, the e2e shards and the HWP editor job
#   site=true|false      the user-guide build (website/)
#   codeql=[...]         CodeQL languages, a JSON list
#   images={...}         image build matrix; images_any=true|false
#   everything=true|false
#
# A path matched by nothing below runs everything: a new top-level directory, workflows,
# compose files, the Makefile. When in doubt, add a path to `everything`, not to a skip.
set -euo pipefail

api=none web=false site=false py=false js=false everything=false
img_api=false img_print=false img_web=false img_media=false
docs_api() { [ "$api" = full ] || api=docs; }

all() {
  everything=true api=full web=true site=true py=true js=true
  img_api=true img_print=true img_web=true img_media=true
}

if [ "${1:-}" = "--all" ]; then
  all
else
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    case "$path" in
      # The web app. The API suite reads web sources (shared tables, fixtures it checks
      # against its own answers), so a web change runs the API tests too.
      apps/web/*) web=true api=full js=true img_web=true ;;
      # The web e2e suite answers every call with fixtures and nothing under apps/web reads
      # apps/api, so an API change cannot change an e2e result; the API's own tests hold the
      # fixtures to its answers (e.g. test_the_chain_canvas_fixture_is_the_apis.py).
      apps/api/*) api=full py=true img_api=true ;;
      # The API suite reads the media worker and the MCP server sources.
      apps/media/*) api=full py=true img_media=true ;;
      mcp/*) api=full py=true ;;
      apps/print/*) py=true img_print=true ;;
      # The in-app help is built from the user guide (`app/help/docs.json`, checked by
      # test_app_help.py); docs/configuration.md is checked against the settings.
      website/*) site=true js=true; docs_api ;;
      .github/workflows/docs.yml) site=true ;;
      docs/* | *.md | LICENSE | NOTICE)
        # a Markdown file inside an app is that app's (an API skill's SKILL.md is code)
        case "$path" in */*) [[ "$path" == docs/* ]] || { all; break; } ;; esac
        docs_api ;;
      # The script lint and the compose check run on every change (seconds each).
      scripts/*) ;;
      *) all; break ;;
    esac
  done
fi

langs=()
$py && langs+=('"python"')
$js && langs+=('"javascript-typescript"')
codeql="[$(IFS=,; echo "${langs[*]:-}")]"

entries=()
$img_api && entries+=('{"name":"api","context":"apps/api","image":"kloudchat-api"}')
$img_print && entries+=('{"name":"print","context":"apps/print","image":"kloudchat-print"}')
$img_web && entries+=('{"name":"web","context":"apps/web","image":"kloudchat-web"}')
$img_media && entries+=('{"name":"media","context":"apps/media","image":"kloudchat-media"}')
images_any=false
[ ${#entries[@]} -gt 0 ] && images_any=true

echo "api=$api"
echo "web=$web"
echo "site=$site"
echo "codeql=$codeql"
echo "images={\"include\":[$(IFS=,; echo "${entries[*]:-}")]}"
echo "images_any=$images_any"
echo "everything=$everything"
