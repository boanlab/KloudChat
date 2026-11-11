#!/bin/sh
# Builds rhwp-studio, the HWP editor the HWP panel frames, into $1 (default ./studio) for
# serving at /rhwp/ on this origin; documents never go to the upstream demo site.
#
# Pinned: the studio source at a tag's commit, its own package-lock, and the published
# @rhwp/core wasm checked against its npm integrity, so no Rust toolchain is needed. Bump
# all three together; JS, wasm and studio must come from one release (upstream rule).
#
# Left out of the build: the AI panel (it calls model vendors from the browser), the
# hwpctrl plugin, CDN web fonts, the PWA service worker and the sample files.
set -eu

RHWP_VERSION=0.9.0
RHWP_COMMIT=c9095c5f67632e6721e800ccc7fb43ea49d26682
RHWP_CORE_INTEGRITY=sha512-dEe9gtEM5QXAoJjX+fwgbN5KIo0a6X/wgwEg0LDcq9oY1ga5lD5vNkediFSSX4Xxs8VfFk//dbk71aMQ8J1uAQ==

out=$(cd "$(dirname "${1:-studio}")" && pwd)/$(basename "${1:-studio}")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

git -c advice.detachedHead=false clone -q --filter=blob:none --no-checkout \
  https://github.com/edwardkim/rhwp "$work/rhwp"
cd "$work/rhwp"
git sparse-checkout set rhwp-studio npm rhwp-shared vendor/npm/ajv assets src/renderer
git checkout -q "$RHWP_COMMIT"

# The wasm, as published: the same files `wasm-pack` writes to pkg/.
mkdir -p pkg "$work/core"
npm pack -q --pack-destination "$work" "@rhwp/core@$RHWP_VERSION" >/dev/null
got=$(node -e "
  const c = require('crypto'), f = require('fs')
  const d = c.createHash('sha512').update(f.readFileSync(process.argv[1])).digest('base64')
  console.log('sha512-' + d)" "$work/rhwp-core-$RHWP_VERSION.tgz")
if [ "$got" != "$RHWP_CORE_INTEGRITY" ]; then
  echo "@rhwp/core $RHWP_VERSION integrity mismatch: $got" >&2
  exit 1
fi
tar -xzf "$work/rhwp-core-$RHWP_VERSION.tgz" -C "$work/core"
cp "$work/core/package/rhwp.js" "$work/core/package/rhwp_bg.wasm" pkg/

cd rhwp-studio
npm ci --no-audit --no-fund --ignore-scripts >/dev/null
RHWP_WITHOUT_AI=1 RHWP_WITHOUT_HWPCTRL=1 RHWP_DISABLE_EXTERNAL_WEBFONTS=1 \
  node node_modules/vite/bin/vite.js build --base=/rhwp/ --logLevel warn

cd dist
rm -rf samples sw.js registerSW.js workbox-*.js manifest.webmanifest rhwp.js rhwp.d.ts \
  rhwp_bg.wasm.d.ts
# Unregistered service worker: its precache would pull ~30 MB on the first open.
sed -i -e 's#<link rel="manifest" href="/rhwp/manifest.webmanifest">##' \
  -e 's#<script id="vite-plugin-pwa:register-sw" src="/rhwp/registerSW.js"></script>##' index.html
if grep -q 'registerSW\|manifest.webmanifest' index.html; then
  echo 'rhwp-studio index.html still registers the service worker' >&2
  exit 1
fi
# Licences ride with the files they cover.
mkdir -p licenses
cp ../../LICENSE licenses/rhwp-LICENSE
cp ../../THIRD_PARTY_LICENSES.md licenses/ 2>/dev/null || true
cp -r ../../assets/fonts/licenses licenses/fonts
cp -r "$work/core/package/notices" licenses/rhwp-core-notices
echo "{\"version\":\"$RHWP_VERSION\",\"commit\":\"$RHWP_COMMIT\"}" > rhwp-version.json

rm -rf "$out"
mkdir -p "$out"
cp -r . "$out"
echo "rhwp-studio $RHWP_VERSION -> $out ($(du -sh "$out" | cut -f1))"
