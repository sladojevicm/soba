# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# CI guard for CLAUDE.md invariants 5/6: the committed frontend/dist/ must be
# exactly what `npm ci && npm run build` produces from frontend/src. Rebuilds
# the bundle into a separate directory and diffs it against the committed one;
# any difference fails the build (stage 2). Nothing is served from this image.
#
#   docker build -f docker/frontend-check.Dockerfile .
# ---------------------------------------------------------------------------
FROM node:20-bookworm-slim AS build
WORKDIR /fe
# Lockfile-exact dependencies first (cached across source edits).
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
# The whole frontend tree (its committed dist/ comes along; ignored below by
# building into /fe-out instead of dist/).
COPY frontend/ ./
# `npm run build` = `tsc -b && vite build`; npm appends the extra args to the
# script, i.e. to `vite build`, so the fresh bundle lands in /fe-out.
RUN npm run build -- --outDir /fe-out

FROM build AS check
COPY frontend/dist/ /committed/
RUN set -e; \
    if diff -r /committed /fe-out; then \
        echo "frontend-check: committed frontend/dist matches a fresh build"; \
    else \
        echo "frontend-check: committed frontend/dist DIFFERS from a fresh build (see diff above)." >&2; \
        echo "  Rebuild it: cd frontend && npm ci && npm run build, then commit dist/ (never hand-edit it)." >&2; \
        exit 1; \
    fi
