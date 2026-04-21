#!/usr/bin/env bash
# Run the Namescape server.
# Assumes your conda env with deps installed is already active (see README).
set -euo pipefail
cd "$(dirname "$0")"

# If districts file is missing, fetch it once.
if [ ! -f backend/data/istanbul_districts.geojson ]; then
  echo "First-time setup: fetching Istanbul district polygons from Overpass..."
  python -m backend.setup_districts
fi

PORT="${PORT:-8765}"
echo "Starting server on http://127.0.0.1:${PORT}"
exec uvicorn backend.app:app --host 127.0.0.1 --port "${PORT}" --reload
