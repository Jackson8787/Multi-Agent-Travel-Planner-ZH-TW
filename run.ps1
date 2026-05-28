# run.ps1 — Windows PowerShell (5.1+)
# Usage: .\run.ps1 [-Port 8502]
param([int]$Port = 8502)
$ErrorActionPreference = "Stop"

# ── 1. Ensure uv is installed ────────────────────────────────────────
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[setup] uv not found — installing via official script..."
    irm https://astral.sh/uv/install.ps1 | iex
    $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
}

# ── 2. Ensure Python 3.12 is available ──────────────────────────────
$pyCheck = uv python find 3.12 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[setup] Python 3.12 not found — installing via uv..."
    uv python install 3.12
}

# ── 3. Create .venv if missing ───────────────────────────────────────
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[setup] Creating .venv with Python 3.12..."
    uv venv --python 3.12
}

# ── 4. Install / sync dependencies ───────────────────────────────────
Write-Host "[setup] Syncing dependencies..."
uv pip install -e ".[dev]" --quiet

# ── 5. Locate .env ───────────────────────────────────────────────────
if (Test-Path ".env") {
    $envFile = ".env"
} elseif (Test-Path ".worktrees\feature-travel-planner-mvp\.env") {
    $envFile = ".worktrees\feature-travel-planner-mvp\.env"
    Write-Host "[warn] Using .env from worktree. Copy to project root for a cleaner setup:"
    Write-Host "       Copy-Item `"$envFile`" .env"
} else {
    Write-Error "[error] .env not found. Copy .env.example and fill in API keys:`n  Copy-Item .env.example .env"
    exit 1
}

# Load each KEY=VALUE line into the current process environment
Get-Content $envFile | Where-Object { $_ -match "^[A-Za-z_][A-Za-z0-9_]*=" } | ForEach-Object {
    $key, $val = $_ -split "=", 2
    [System.Environment]::SetEnvironmentVariable($key.Trim(), $val.Trim().Trim('"').Trim("'"), "Process")
}

# ── 6. Launch ────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[start] Travel Planner -> http://localhost:$Port"
Write-Host "        Ctrl-C to stop"
Write-Host ""
.venv\Scripts\python.exe -m streamlit run src\travel_planner\ui\app.py `
    --server.port $Port `
    --server.headless true `
    --browser.gatherUsageStats false
