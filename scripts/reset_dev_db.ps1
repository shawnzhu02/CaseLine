# Drops and recreates the LOCAL development schema, then reseeds. Refuses to touch non-local databases.
# Usage (repo root, API venv active):  .\scripts\reset_dev_db.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$url = $env:DATABASE_URL
if (-not $url -and (Test-Path "$root\.env")) {
    $line = Get-Content "$root\.env" | Where-Object { $_ -match '^DATABASE_URL=' } | Select-Object -First 1
    if ($line) { $url = $line.Substring("DATABASE_URL=".Length) }
}
if (-not $url) { $url = "sqlite:///./caseline-dev.db" }
if ($url -notmatch '(localhost|127\.0\.0\.1|^sqlite)') {
    throw "Refusing to reset a non-local database."
}
Push-Location "$root\apps\api"
try {
    alembic downgrade base
    alembic upgrade head
    # Run the seed from apps/api too so a relative SQLite path resolves to the same file.
    python "$root\scripts\seed_demo.py"
} finally {
    Pop-Location
}
