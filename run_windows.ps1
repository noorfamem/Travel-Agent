# One-click launcher for TravelAI Pro (Windows PowerShell). Safe to run repeatedly.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  TravelAI Pro - Starting up..."         -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""

# 0. Sanity check - must be run from the project folder
if (-not (Test-Path "backend\agents\flight_agent.py")) {
    Write-Host "ERROR: run this from the project folder." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

# 1. Kill anything already using port 8000
Write-Host "[1/5] Checking port 8000..." -ForegroundColor DarkCyan
$existing = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    $existing | ForEach-Object {
        Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
    Write-Host "      Stopped previous server on port 8000." -ForegroundColor Yellow
} else {
    Write-Host "      Port 8000 is free." -ForegroundColor Green
}

# 2. Create virtual environment if missing
Write-Host "[2/5] Virtual environment..." -ForegroundColor DarkCyan
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "      Creating .venv (first run only)..."
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Python not found. Install Python 3.11+ and try again." -ForegroundColor Red
        Read-Host "Press Enter to close"
        exit 1
    }
}
$py = (Resolve-Path ".venv\Scripts\python.exe").Path
Write-Host "      Ready." -ForegroundColor Green

# 3. Install / verify all required packages
#    tzdata  = IANA timezone database (REQUIRED on Windows - zoneinfo has no built-in data)
#    certifi = TLS CA bundle fallback (REQUIRED on Windows - fixes SerpAPI HTTPS calls)
Write-Host "[3/5] Checking packages..." -ForegroundColor DarkCyan

$allOk = $true
$pkgChecks = @("fastapi","uvicorn","pydantic","requests","dotenv","tzdata","certifi")
foreach ($pkg in $pkgChecks) {
    & $py -c "import $pkg" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $allOk = $false
        Write-Host "      Missing: $pkg" -ForegroundColor Yellow
    }
}

if (-not $allOk) {
    Write-Host "      Installing from bundled wheels..." -ForegroundColor Yellow
    & $py -m pip install -q --no-index --find-links wheels -r requirements.txt 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "      Downloading from the internet..." -ForegroundColor Yellow
        & $py -m pip install -q --default-timeout=120 --retries 5 -r requirements.txt
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: Package install failed. Check your internet." -ForegroundColor Red
            Read-Host "Press Enter to close"
            exit 1
        }
    }
    Write-Host "      Packages installed." -ForegroundColor Green
} else {
    # Keep tzdata + certifi current even when everything else is already installed
    & $py -m pip install -q --upgrade tzdata certifi 2>&1 | Out-Null
    Write-Host "      All packages OK (tzdata + certifi verified)." -ForegroundColor Green
}

# 4. Private settings - open Notepad on first run
Write-Host "[4/5] Settings (.env)..." -ForegroundColor DarkCyan
if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
    }
    Write-Host "      No .env found - add your API keys, save, and close Notepad." -ForegroundColor Yellow
    Start-Process notepad.exe ".env" -Wait
} else {
    Write-Host "      .env found." -ForegroundColor Green
}

# 5. Launch server and open browser
Write-Host "[5/5] Starting server..." -ForegroundColor DarkCyan
Write-Host ""
Write-Host "  URL : http://127.0.0.1:8000/ui/" -ForegroundColor Cyan
Write-Host "  Stop: Ctrl+C" -ForegroundColor DarkGray
Write-Host ""

Start-Job -ScriptBlock { Start-Sleep -Seconds 4; Start-Process "http://127.0.0.1:8000/ui/" } | Out-Null

& $py -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --log-level warning
