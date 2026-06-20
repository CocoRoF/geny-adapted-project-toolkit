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
# --ignore-scripts: pnpm 10+ aborts (ERR_PNPM_IGNORED_BUILDS) when a
# dependency build script is unreviewed. The only one here is esbuild,
# whose binary already ships via the @esbuild/<platform> packages — no
# postinstall needed for `vite build` (lightningcss is the same). So
# skip all build scripts; the install is deterministic and the build
# works without them.
RUN pnpm install --frozen-lockfile --ignore-scripts
COPY web/ ./
RUN pnpm build

FROM caddy:2.10-alpine
COPY --from=web /web/dist /srv/gapt-web
