#!/bin/sh
# Scheduled PostgreSQL backups. Writes a pg_dump custom-format archive to
# /backups every BACKUP_INTERVAL_HOURS and deletes archives older than
# BACKUP_RETENTION_DAYS.
#
# Restore:
#   docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
#     --clean --if-exists < backups/learnhouse-YYYYmmdd-HHMMSS.dump
set -eu

INTERVAL_HOURS="${BACKUP_INTERVAL_HOURS:-24}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
export PGPASSWORD="$POSTGRES_PASSWORD"

echo "[backup] every ${INTERVAL_HOURS}h, keeping ${RETENTION_DAYS} days"

while true; do
  file="/backups/learnhouse-$(date -u +%Y%m%d-%H%M%S).dump"
  # Write to a temp name first so a failed dump never looks like a good one.
  if pg_dump -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "$file.partial"; then
    mv "$file.partial" "$file"
    echo "[backup] wrote $file ($(du -h "$file" | cut -f1))"
  else
    rm -f "$file.partial"
    echo "[backup] ERROR: pg_dump failed" >&2
  fi
  find /backups -name 'learnhouse-*.dump' -mtime "+${RETENTION_DAYS}" -delete
  sleep "$((INTERVAL_HOURS * 3600))"
done
