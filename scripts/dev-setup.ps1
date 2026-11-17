# Live Translate Development Setup Script for Windows
# Creates isolated venv, installs dependencies, runs tests

param(
    [switch]$RunTests = $false,
    [switch]$RunApp = $false,
    [switch]$Clean = $false
)

$ErrorActionPreference = "Stop"
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$liveDubbingDir = Join-Path $workspaceRoot "live-dubbing"

Write-Host "`n━━━ Live Translate Development Setup ━━━`n" -ForegroundColor Cyan

# Clean previous venv if requested
if ($Clean -and (Test-Path (Join-Path $liveDubbingDir "venv"))) {
    Write-Host "Removing existing venv..." -ForegroundColor Yellow
    Remove-Item -Path (Join-Path $liveDubbingDir "venv") -Recurse -Force
}

# Create venv
Push-Location $liveDubbingDir
try {
    if (-not (Test-Path "venv")) {
        Write-Host "Creating virtual environment..." -ForegroundColor Green
        python -m venv venv
    } else {
        Write-Host "Virtual environment already exists" -ForegroundColor Gray
    }

    # Activate venv
    Write-Host "Activating virtual environment..." -ForegroundColor Green
    & ".\venv\Scripts\Activate.ps1"

    # Upgrade pip
    Write-Host "Upgrading pip..." -ForegroundColor Green
    python -m pip install --upgrade pip --quiet

    # Install package with dev dependencies
    Write-Host "Installing live-dubbing package with dev dependencies..." -ForegroundColor Green
    pip install -e ".[dev]" --quiet

    Write-Host "`n✓ Setup complete!" -ForegroundColor Green
    Write-Host "`nTo activate the venv manually, run:" -ForegroundColor Cyan
    Write-Host "  cd $liveDubbingDir" -ForegroundColor Gray
    Write-Host "  .\venv\Scripts\Activate.ps1" -ForegroundColor Gray

    # Run tests if requested
    if ($RunTests) {
        Write-Host "`n━━━ Running Tests ━━━`n" -ForegroundColor Cyan
        pytest tests/ -v --tb=short
        if ($LASTEXITCODE -ne 0) {
            Write-Host "`n✗ Tests failed" -ForegroundColor Red
            exit $LASTEXITCODE
        }
        Write-Host "`n✓ All tests passed" -ForegroundColor Green
    }

    # Run app if requested
    if ($RunApp) {
        Write-Host "`n━━━ Starting Live Translate ━━━`n" -ForegroundColor Cyan
        Write-Host "Press Ctrl+C to stop`n" -ForegroundColor Yellow
        python -m live_dubbing
    }

} finally {
    Pop-Location
}
