#!/usr/bin/env bash
# run.sh — Mac (Terminal) and Windows (Git Bash)
# Usage: bash run.sh [--port 8502]
set -euo pipefail

PORT="${PORT:-8502}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# ── 1. Ensure uv is installed ────────────────────────────────────────
if ! command -v uv &>/dev/null; then
  echo "[setup] uv not found — installing via official script..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# ── 2. Ensure Python 3.12 is available ──────────────────────────────
if ! uv python find 3.12 &>/dev/null; then
  echo "[setup] Python 3.12 not found — installing via uv..."
  uv python install 3.12
fi

# ── 3. Create .venv if missing ───────────────────────────────────────
if [ ! -d ".venv" ]; then
  echo "[setup] Creating .venv with Python 3.12..."
  uv venv --python 3.12
fi

# ── 4. Install / sync dependencies ───────────────────────────────────
echo "[setup] Syncing dependencies..."
uv pip install -e ".[dev]" --quiet

# ── 5. Locate .env ───────────────────────────────────────────────────
if [ -f ".env" ]; then
  ENV_FILE=".env"
elif [ -f ".worktrees/feature-travel-planner-mvp/.env" ]; then
  ENV_FILE=".worktrees/feature-travel-planner-mvp/.env"
  echo "[warn] Using .env from worktree. Copy it to project root for a cleaner setup:"
  echo "       cp \"\$ENV_FILE\" .env"
else
  echo "[error] .env not found. Copy .env.example and fill in API keys:"
  echo "        cp .env.example .env"
  exit 1
fi
set -a; source "$ENV_FILE"; set +a

# ── 6. Detect platform Python binary ─────────────────────────────────
if [ -f ".venv/Scripts/python.exe" ]; then
  PYTHON=".venv/Scripts/python.exe"   # Windows (Git Bash)
else
  PYTHON=".venv/bin/python"           # Mac / Linux
fi

# ── 7. Launch ────────────────────────────────────────────────────────
echo ""
echo "[start] Travel Planner → http://localhost:$PORT"
echo "        Ctrl-C to stop"
echo ""
"$PYTHON" -m streamlit run src/travel_planner/ui/app.py \
  --server.port "$PORT" \
  --server.headless true \
  --browser.gatherUsageStats false
