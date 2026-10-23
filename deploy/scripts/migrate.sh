#!/bin/sh
# One-shot database migration job. Runs before the app starts on every deploy.
#
# The API builds its schema with SQLModel.metadata.create_all on startup, and the
# Alembic chain in migrations/versions only holds deltas (its first revision
# alters existing tables), so `alembic upgrade` cannot build a schema from empty.
# The three database states are therefore handled separately:
#
#   fresh    no tables yet       -> create the schema the way the app does, then
#                                   stamp it at the current Alembic head
#   stamped  alembic_version     -> `alembic upgrade heads` applies the delta
#   legacy   tables, no stamp    -> refuse, unless ALEMBIC_BASELINE_REVISION says
#                                   which revision the schema corresponds to
#
# Starting a newer image against an un-migrated database makes the API crash
# loop, which is why this runs as its own container ahead of the app.
set -eu

API_DIR="${API_DIR:-/app/api}"
PY="$API_DIR/.venv/bin/python"
ALEMBIC="$API_DIR/.venv/bin/alembic"
cd "$API_DIR"

state=$("$PY" - <<'EOF'
import os
from sqlalchemy import create_engine, inspect, text

url = os.environ["LEARNHOUSE_SQL_CONNECTION_STRING"].replace(
    "postgresql+asyncpg://", "postgresql://", 1
)
engine = create_engine(url)
with engine.connect() as conn:
    tables = set(inspect(conn).get_table_names())
    stamped = "alembic_version" in tables and conn.execute(
        text("SELECT count(*) FROM alembic_version")
    ).scalar()
engine.dispose()

if stamped:
    print("stamped")
elif tables - {"alembic_version"}:
    print("legacy")
else:
    print("fresh")
EOF
)

echo "[migrate] database state: $state"

case "$state" in
  fresh)
    echo "[migrate] creating schema"
    # Importing app pulls in every router and therefore every table model,
    # exactly as at API startup. Lifespan hooks do not run on import.
    "$PY" - <<'EOF'
import asyncio
import app  # noqa: F401  (registers all SQLModel tables)
from src.core.events.database import _bootstrap_schema, engine

async def main():
    await _bootstrap_schema()
    await engine.dispose()

asyncio.run(main())
EOF
    "$ALEMBIC" stamp heads
    ;;
  stamped)
    "$ALEMBIC" upgrade heads
    ;;
  legacy)
    if [ -z "${ALEMBIC_BASELINE_REVISION:-}" ]; then
      echo "[migrate] ERROR: the database has tables but no Alembic revision." >&2
      echo "[migrate] Set ALEMBIC_BASELINE_REVISION to the revision its schema" >&2
      echo "[migrate] matches (run 'alembic heads' in the image that created it)," >&2
      echo "[migrate] then redeploy. Stamping blindly would skip migrations." >&2
      exit 1
    fi
    "$ALEMBIC" stamp "$ALEMBIC_BASELINE_REVISION"
    "$ALEMBIC" upgrade heads
    ;;
esac

echo "[migrate] current revision:"
"$ALEMBIC" current
