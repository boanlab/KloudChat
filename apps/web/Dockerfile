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
# `VITE_API_BASE_URL` stays unset: the default `/api` is relative, so nginx on
# the same origin proxies it and no API address is baked into the bundle.
RUN node node_modules/typescript/bin/tsc -b \
    && node node_modules/vite/bin/vite.js build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template

# Upstream resolved at run time, so the API can move hosts without a rebuild.
ENV KCHAT_API_URL=http://kloudchat-api:8100

# Hops allowed to name the client in `X-Forwarded-For`: private ranges plus
# loopback. Narrow to the edge proxy's address wherever this port is reachable
# by anyone else — see nginx.conf.
ENV KCHAT_TRUSTED_PROXIES="set_real_ip_from 10.0.0.0/8; set_real_ip_from 172.16.0.0/12; set_real_ip_from 192.168.0.0/16; set_real_ip_from 127.0.0.0/8; set_real_ip_from ::1/128; set_real_ip_from fc00::/7;"

EXPOSE 80
