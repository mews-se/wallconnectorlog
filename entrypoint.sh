#!/bin/sh
# Docker creates missing bind-mount directories owned by root, which neither
# this app (uid 1000) nor Grafana (uid 472) can write. Fix the ownership while
# still root, then drop privileges. A container started with user: set skips
# all of this.
set -e
if [ "$(id -u)" = "0" ]; then
    dbdir=$(dirname "${WC_DB:-/data/wallconnectorlog.db}")
    chown -R app:app "$dbdir" 2>/dev/null || true
    if [ -d /grafana-data ]; then
        chown -R 472:472 /grafana-data 2>/dev/null || true
    fi
    exec su-exec app "$@"
fi
exec "$@"
