# Two stages: build the bundle, then let nginx serve it and proxy /api.
#
# The browser only ever talks to the kchat API. Neither the LiteLLM URL nor the
# master key reaches this image — every model call is made server-side by the
# API process.
# The bundle is the same bytes on every architecture, so the build stage runs
# on the machine doing the building. Under QEMU the arm64 half of a
# multi-platform build never finishes — rolldown, the native bundler behind
# Vite, hangs at "rendering chunks" until the runner's six-hour limit kills
# the job. Only the nginx stage below is really built per platform.
FROM --platform=$BUILDPLATFORM node:22-alpine AS build
WORKDIR /app

# Dependency layer kept separate from source: a source-only rebuild skips the
# install entirely.
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY . .
# `VITE_API_BASE_URL` is deliberately left unset. The default `/api` is
# relative, so nginx on the same origin proxies it — the image does not have to
# be rebuilt per domain, and no API address is baked into the bundle.
RUN node node_modules/typescript/bin/tsc -b \
    && node node_modules/vite/bin/vite.js build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template

# The upstream is resolved at run time, so moving the API to another host does
# not require rebuilding this image.
ENV KCHAT_API_URL=http://kloudchat-api:8100

# Which hops may name the client in `X-Forwarded-For`. The private ranges plus
# loopback cover a proxy on the same host, the same Docker network, or the same
# LAN. Narrow it to the edge proxy's own address wherever this port is
# reachable by anyone else — see the note in nginx.conf.
ENV KCHAT_TRUSTED_PROXIES="set_real_ip_from 10.0.0.0/8; set_real_ip_from 172.16.0.0/12; set_real_ip_from 192.168.0.0/16; set_real_ip_from 127.0.0.0/8; set_real_ip_from ::1/128; set_real_ip_from fc00::/7;"

EXPOSE 80
