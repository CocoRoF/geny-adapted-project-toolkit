# syntax=docker/dockerfile:1.7
#
# Caddy image for the tunnel deployment. Behind a Cloudflare tunnel
# the apex serves the GAPT SPA statically (no Vite dev server), so we
# build the SPA here and bake it into the Caddy image at
# `/srv/gapt-web`. Caddyfile.tunnel's `/_gapt/app/*` file_server roots
# there.
#
# Build context = repo root:
#   docker build -f compose/caddy/Dockerfile -t gapt/caddy .
#
# The SPA is built with `base: "/_gapt/app/"` (vite.config.ts), so its
# asset URLs line up with the path Caddy fans here.

FROM node:24-alpine AS web
WORKDIR /web
RUN corepack enable
# Lockfile-first for layer caching, then the rest of the SPA source.
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM caddy:2.10-alpine
COPY --from=web /web/dist /srv/gapt-web
