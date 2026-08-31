# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Artha.AI — frontend (React 18 + Vite, served by nginx)
#
# In dev, Vite's own dev server proxies /api to the gateway. In production
# there is no Vite: nginx serves the static build AND takes over that proxy
# role, so the client keeps using same-origin relative /api URLs and needs no
# build-time knowledge of where the backend lives.
# ---------------------------------------------------------------------------

ARG NODE_VERSION=20
ARG NGINX_VERSION=1.27


# ===========================================================================
# Stage 1 — build the static bundle
# ===========================================================================
FROM node:${NODE_VERSION}-alpine AS builder

WORKDIR /build

# `npm ci` (not `npm install`) so the lockfile is authoritative — a
# production build must not silently float a transitive dependency.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# The app reads no import.meta.env values, so the bundle is deployment-neutral:
# one image can be promoted from staging to production unchanged. All runtime
# configuration is applied by nginx in the next stage.
RUN npm run build


# ===========================================================================
# Stage 2 — serve
# ===========================================================================
FROM nginxinc/nginx-unprivileged:${NGINX_VERSION}-alpine AS runtime

ARG TZ=Asia/Kolkata
ENV TZ=${TZ}

# The unprivileged image runs as uid 101 and cannot apt/apk as that user.
USER root
RUN apk add --no-cache tzdata \
 && cp "/usr/share/zoneinfo/${TZ}" /etc/localtime \
 && echo "${TZ}" > /etc/timezone
USER nginx

# Files placed in /etc/nginx/templates/ are rendered through envsubst by the
# base image's entrypoint before nginx starts. NGINX_ENVSUBST_FILTER limits
# substitution to ARTHA_* names, so nginx's own $uri / $host / $upstream_*
# variables are left untouched — without the filter, an unlucky environment
# variable name could silently rewrite them.
ENV NGINX_ENVSUBST_FILTER="^ARTHA_"
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template

# Outside conf.d/ deliberately: that directory is a tmpfs at runtime (it is the
# envsubst output target and must be writable under a read-only rootfs), so a
# static file placed there at build time would be hidden by the mount.
COPY nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf

COPY --from=builder /build/dist /usr/share/nginx/html

# 8080, not 80: a non-root process cannot bind a privileged port. The host-side
# port is mapped in docker-compose.yml.
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/bin/sh", "-c", "wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1"]
