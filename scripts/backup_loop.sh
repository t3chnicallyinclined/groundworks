#!/bin/sh
# backup_loop.sh -- call `surreal export` on a schedule, in a container derived from the
# same surrealdb image (see ../Dockerfile.backup). The upstream image
# (surrealdb/surrealdb:v3.1.6, built on cgr.dev/chainguard/glibc-dynamic) ships no shell
# and no coreutils at all -- only /surreal. Dockerfile.backup adds a single static
# busybox binary so this loop can run; every busybox applet below is called through it
# explicitly ($BB sleep, $BB date) rather than relying on PATH or symlinks.
#
# Env: SURREAL_URL (default http://surrealdb:8000), SURREAL_USER / SURREAL_PASS (default
# root/root, must match the surrealdb service), SURREAL_NS / SURREAL_DB (default
# demo_incident, the demo instance's id -- run_instance.py uses the instance id as both
# when --ns/--db are not given), BACKUP_INTERVAL_SECONDS (default 86400 = 24h).
set -eu
BB=/bin/busybox
URL="${SURREAL_URL:-http://surrealdb:8000}"
SURREAL_USER="${SURREAL_USER:-root}"
SURREAL_PASS="${SURREAL_PASS:-root}"
NS="${SURREAL_NS:-demo_incident}"
DB="${SURREAL_DB:-demo_incident}"
INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"

echo "backup: will export ns=$NS db=$DB from $URL every ${INTERVAL}s into /backups"
while true; do
    stamp="$("$BB" date -u +%Y%m%dT%H%M%SZ)"
    out="/backups/${NS}_${DB}_${stamp}.surql"
    if /surreal export --conn "$URL" --user "$SURREAL_USER" --pass "$SURREAL_PASS" --ns "$NS" --db "$DB" "$out"; then
        echo "backup: wrote $out"
    else
        echo "backup: export FAILED for $NS/$DB at $URL (server may not be up yet); will retry after the interval"
    fi
    "$BB" sleep "$INTERVAL"
done
