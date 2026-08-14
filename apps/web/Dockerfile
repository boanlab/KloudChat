# Two stages: build the bundle, then let nginx serve it and proxy /api.
#
# The browser only ever talks to the kchat API. Neither the LiteLLM URL nor the
# master key reaches this image — every model call is made server-side by the
# API process.
FROM node:22-alpine AS build
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

FROM nginx:1.31-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template

# The upstream is resolved at run time, so moving the API to another host does
# not require rebuilding this image.
ENV KCHAT_API_URL=http://kchat-api:8100

EXPOSE 80
