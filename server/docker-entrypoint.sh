#!/bin/sh
# GAPT server container entrypoint.
#
# Runs the database migrations before handing off to the actual
# command (uvicorn). A fresh deploy therefore self-migrates — the
# prod/tunnel compose can `up` a clean Postgres and the schema is
# created on first boot, no manual `alembic upgrade head` step.
#
# Set GAPT_RUN_MIGRATIONS=0 to skip (e.g. a read-replica or a
# sidecar that shouldn't race the primary on the schema).
set -e

if [ "${GAPT_RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "[gapt-entrypoint] alembic upgrade head"
    alembic upgrade head
fi

echo "[gapt-entrypoint] exec: $*"
exec "$@"
