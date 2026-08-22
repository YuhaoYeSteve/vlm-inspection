#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_PORT="${VLM_PORT:-8000}"
FRONTEND_PORT="${VLM_FRONTEND_PORT:-5173}"
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
  echo "Missing frontend dependencies. Run: npm --prefix frontend install" >&2
  exit 1
fi

"$ROOT/backend/.venv/bin/alembic" -c "$ROOT/backend/alembic.ini" upgrade head
"$ROOT/backend/.venv/bin/uvicorn" app.main:app \
  --app-dir "$ROOT/backend" --host 127.0.0.1 --port "$BACKEND_PORT" --reload &
BACKEND_PID=$!
npm --prefix "$ROOT/frontend" run dev -- \
  --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort &
FRONTEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Frontend: http://127.0.0.1:$FRONTEND_PORT"
echo "Backend:  http://127.0.0.1:$BACKEND_PORT"
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 1
done
exit 1
