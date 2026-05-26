#!/usr/bin/env bash
# run_quick.sh — skip uv install (use existing .venv), just load env + launch
set -euo pipefail

PORT="${PORT:-8502}"

# Load .env
if [ -f ".env" ]; then
  set -a; source ".env"; set +a
elif [ -f ".worktrees/feature-travel-planner-mvp/.env" ]; then
  set -a; source ".worktrees/feature-travel-planner-mvp/.env"; set +a
else
  echo "[error] .env not found"; exit 1
fi

# Pick Python binary
if [ -f ".venv/Scripts/python.exe" ]; then
  PYTHON=".venv/Scripts/python.exe"
else
  PYTHON=".venv/bin/python"
fi

echo "[start] Travel Planner → http://localhost:$PORT"
"$PYTHON" -m streamlit run src/travel_planner/ui/app.py \
  --server.port "$PORT" \
  --server.headless true \
  --browser.gatherUsageStats false
