# Starts the CaseLine Guava voice agent with apps\voice\.env (git-ignored) loaded into the environment.
# The Guava SDK reads GUAVA_API_KEY from the process environment, so a .env file alone is not enough.
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root "apps\voice\.env"
if (-not (Test-Path $envFile)) { throw "Missing $envFile (see docs/demo-runbook.md)" }
Get-Content $envFile | Where-Object { $_ -match '^[A-Z_]+=' } | ForEach-Object {
    $k, $v = $_ -split '=', 2
    Set-Item -Path "Env:$k" -Value $v
}
Set-Location (Join-Path $root "apps\voice")
& .\.venv\Scripts\python.exe main.py @args
