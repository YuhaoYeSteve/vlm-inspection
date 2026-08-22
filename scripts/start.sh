#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${VLM_PORT:-8000}"
PYTHON="$ROOT/backend/.venv/bin/python"

cd "$ROOT"

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing backend virtual environment. Follow the README installation steps first." >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js 18 or newer is required." >&2
  exit 1
fi
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or newer is required"'
node -e 'const major = Number(process.versions.node.split(".")[0]); if (major < 18) process.exit(1)'

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  npm --prefix "$ROOT/frontend" install
fi
if [[ ! -f "$ROOT/frontend/dist/index.html" ]]; then
  npm --prefix "$ROOT/frontend" run build
fi

"$ROOT/backend/.venv/bin/alembic" -c "$ROOT/backend/alembic.ini" upgrade head
"$ROOT/backend/.venv/bin/uvicorn" app.main:app \
  --app-dir "$ROOT/backend" --host 127.0.0.1 --port "$PORT" --workers 1 &
SERVER_PID=$!

cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for _attempt in {1..50}; do
  if curl --silent --fail "http://127.0.0.1:$PORT/api/health" >/dev/null; then
    if [[ "${VLM_NO_OPEN:-0}" != "1" ]] && command -v open >/dev/null 2>&1; then
      open "http://127.0.0.1:$PORT" >/dev/null 2>&1 || true
    fi
    wait "$SERVER_PID"
    exit $?
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    break
  fi
  sleep 0.2
done

echo "Seed VLM Inspector failed to become ready on 127.0.0.1:$PORT" >&2
exit 1
