# Brings up the local stack: db, migrate (alembic upgrade head), worker, api.
# Usage: .\scripts\dev-up.ps1            (start; images are rebuilt only if missing)
#        .\scripts\dev-up.ps1 -Build     (rebuild migrate, worker and api first; the
#                                         worker build is slow the first time: PyTorch)
# Restart policies (db, worker, api) bring the stack back after a Docker restart.

param([switch]$Build)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Fail($msg) {
    Write-Host "ERROR: $msg" -ForegroundColor Red
    exit 1
}

& docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker is not running. Start Docker Desktop and try again." }

if ($Build) {
    Write-Host "==> Building migrate, worker, api (first build is slow: PyTorch)"
    & docker compose build migrate worker api
    if ($LASTEXITCODE -ne 0) { Fail "build failed (network errors: run the script again)" }
}

Write-Host "==> Starting db"
& docker compose up -d db
if ($LASTEXITCODE -ne 0) { Fail "could not start db" }

$dbId = (& docker compose ps -q db).Trim()
$healthy = $false
for ($i = 0; $i -lt 60; $i++) {
    $state = (& docker inspect --format "{{.State.Health.Status}}" $dbId).Trim()
    if ($state -eq "healthy") { $healthy = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $healthy) { Fail "db did not become healthy in 60 s" }

Write-Host "==> Running migrations (alembic upgrade head)"
& docker compose run --rm migrate
if ($LASTEXITCODE -ne 0) { Fail "migration failed: see the output above" }
$head = (& docker compose run --rm --no-deps migrate alembic current | Out-String).Trim()

Write-Host "==> Starting worker and api"
& docker compose up -d worker api
if ($LASTEXITCODE -ne 0) { Fail "could not start worker/api" }

Write-Host ""
Write-Host "==> Status"
& docker compose ps
Write-Host ("Schema: " + $head)

try {
    $health = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 5
    Write-Host ("API:    up (db " + $health.db + ") http://localhost:8000")
} catch {
    Write-Host "API:    not answering yet (give it a few seconds)" -ForegroundColor Yellow
}

Start-Sleep -Seconds 12  # the worker writes its first heartbeat within 10 s
try {
    $worker = Invoke-RestMethod -Uri "http://localhost:8000/worker/status" -TimeoutSec 5
    if ($worker.status -eq "up") {
        Write-Host "Worker: up (last heartbeat $($worker.seen_at))"
    } else {
        Write-Host "Worker: down (no heartbeat yet; check: docker compose logs worker)" -ForegroundColor Yellow
    }
} catch {
    Write-Host "Worker: unknown (API not answering)" -ForegroundColor Yellow
}
