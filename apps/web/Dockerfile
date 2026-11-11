# Two stages: build the bundle, then nginx serves it and proxies /api and /llm.
#
# The browser only ever talks to the KloudChat API; neither the LiteLLM URL
# nor the master key reaches this image.
#
# The bundle is architecture-independent, so the build stage runs on the
# build host: rolldown hangs at "rendering chunks" under QEMU, and only the
# nginx stage is built per platform.
FROM --platform=$BUILDPLATFORM node:22-alpine AS build
WORKDIR /app

# Dependency layer separate from source, so a source-only rebuild skips it.
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY . .
# Names this bundle (and its /version.json) so open pages notice a deploy; unset, the build
# time names it. CI can pass the commit: --build-arg KCHAT_BUILD_VERSION=$(git rev-parse --short HEAD)
ARG KCHAT_BUILD_VERSION=
ENV KCHAT_BUILD_VERSION=${KCHAT_BUILD_VERSION}
# `VITE_API_BASE_URL` stays unset: the default `/api` is relative, so nginx on
# the same origin proxies it and no API address is baked into the bundle.
RUN node node_modules/typescript/bin/tsc -b \
    && node node_modules/vite/bin/vite.js build

# The HWP editor (rhwp-studio), pinned in rhwp/build-studio.sh and built from its source
# and the published wasm, so it is served from this origin. Its own stage: it changes only
# when that script does.
FROM --platform=$BUILDPLATFORM node:22-alpine AS rhwp
RUN apk add --no-cache git
WORKDIR /rhwp
COPY rhwp/build-studio.sh ./
RUN ./build-studio.sh /rhwp/studio

FROM nginx:1.31-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY --from=rhwp /rhwp/studio /usr/share/nginx/html/rhwp
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY security-headers.conf /etc/nginx/snippets/kchat-security-headers.conf
COPY rhwp-security-headers.conf /etc/nginx/snippets/rhwp-security-headers.conf
# Run by the image's entrypoint (`*.envsh` is sourced) before the template is filled in.
COPY --chmod=755 kchat-body-size.envsh /docker-entrypoint.d/15-kchat-body-size.envsh

# Upstream resolved at run time, so the API can move hosts without a rebuild.
ENV KCHAT_API_URL=http://api:8100

# Hops allowed to name the client in `X-Forwarded-For`: private ranges plus
# loopback. Narrow to the edge proxy's address wherever this port is reachable
# by anyone else — see nginx.conf.
ENV KCHAT_TRUSTED_PROXIES="set_real_ip_from 10.0.0.0/8; set_real_ip_from 172.16.0.0/12; set_real_ip_from 192.168.0.0/16; set_real_ip_from 127.0.0.0/8; set_real_ip_from ::1/128; set_real_ip_from fc00::/7;"

# The API's upload limit, MB; the same KCHAT_MAX_UPLOAD_MB the compose file gives the API.
ENV KCHAT_MAX_UPLOAD_MB=200

EXPOSE 80
