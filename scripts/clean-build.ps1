# Clean Build Script for Live Translate
# Ensures a clean PyInstaller build with no duplicate dependencies

param(
    [switch]$Build = $false,
    [switch]$Installer = $false
)

$ErrorActionPreference = "Stop"
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$liveDubbingDir = Join-Path $workspaceRoot "live-dubbing"

Write-Host "`n━━━ Live Translate Clean Build ━━━`n" -ForegroundColor Cyan

Push-Location $liveDubbingDir
try {
    # Step 1: Clean previous build artifacts
    Write-Host "Cleaning previous build artifacts..." -ForegroundColor Yellow
    if (Test-Path "build") {
        Remove-Item -Path "build" -Recurse -Force
        Write-Host "  ✓ Removed build/" -ForegroundColor Gray
    }
    if (Test-Path "dist") {
        Remove-Item -Path "dist" -Recurse -Force
        Write-Host "  ✓ Removed dist/" -ForegroundColor Gray
    }
    
    # Step 2: Clean venv (optional but recommended)
    $cleanVenv = Read-Host "`nClean and recreate venv to ensure no duplicate packages? (y/N)"
    if ($cleanVenv -eq "y" -or $cleanVenv -eq "Y") {
        Write-Host "Cleaning venv..." -ForegroundColor Yellow
        if (Test-Path "venv") {
            Remove-Item -Path "venv" -Recurse -Force
            Write-Host "  ✓ Removed venv/" -ForegroundColor Gray
        }
        
        Write-Host "Creating fresh venv..." -ForegroundColor Green
        python -m venv venv
        & ".\venv\Scripts\Activate.ps1"
        
        Write-Host "Installing CPU-only torch for distribution..." -ForegroundColor Green
        pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu --quiet
        
        Write-Host "Installing package..." -ForegroundColor Green
        pip install -e . --quiet
        
        Write-Host "Installing PyInstaller..." -ForegroundColor Green
        pip install pyinstaller --quiet
    } else {
        Write-Host "Activating existing venv..." -ForegroundColor Gray
        & ".\venv\Scripts\Activate.ps1"
    }
    
    # Step 3: Verify no duplicate packages
    Write-Host "`nChecking for duplicate package versions..." -ForegroundColor Cyan
    $packages = @("numpy", "elevenlabs", "websockets", "structlog", "attrs", "click", "importlib_metadata", "setuptools")
    $hasDuplicates = $false
    
    foreach ($pkg in $packages) {
        $versions = pip list --format=freeze | Select-String "^$pkg==" | ForEach-Object { $_.ToString() }
        if ($versions.Count -gt 1) {
            Write-Host "  ⚠ Multiple versions of $pkg found:" -ForegroundColor Yellow
            $versions | ForEach-Object { Write-Host "    $_" -ForegroundColor Gray }
            $hasDuplicates = $true
        }
    }
    
    if ($hasDuplicates) {
        Write-Host "`n⚠ Duplicate packages detected. Clean and recreate venv recommended." -ForegroundColor Yellow
        $continue = Read-Host "Continue anyway? (y/N)"
        if ($continue -ne "y" -and $continue -ne "Y") {
            Write-Host "Build cancelled." -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "  ✓ No duplicate packages found" -ForegroundColor Green
    }
    
    # Step 4: Build with PyInstaller
    if ($Build) {
        Write-Host "`n━━━ Building with PyInstaller ━━━`n" -ForegroundColor Cyan
        pyinstaller live_translate.spec --noconfirm
        
        if ($LASTEXITCODE -ne 0) {
            Write-Host "`n✗ PyInstaller build failed" -ForegroundColor Red
            exit $LASTEXITCODE
        }
        
        Write-Host "`n✓ PyInstaller build complete" -ForegroundColor Green
        Write-Host "Output: dist\LiveTranslate\" -ForegroundColor Gray
        
        # Show bundle size
        $bundleSize = (Get-ChildItem -Path "dist\LiveTranslate" -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
        Write-Host "Bundle size: $([math]::Round($bundleSize, 2)) MB" -ForegroundColor Cyan
    }
    
    # Step 5: Build installer
    if ($Installer) {
        Write-Host "`n━━━ Building Installer ━━━`n" -ForegroundColor Cyan
        
        if (-not (Test-Path "dist\LiveTranslate\LiveTranslate.exe")) {
            Write-Host "✗ dist\LiveTranslate\LiveTranslate.exe not found. Run with -Build first." -ForegroundColor Red
            exit 1
        }
        
        $innoPath = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
        if (-not (Test-Path $innoPath)) {
            Write-Host "✗ Inno Setup not found at: $innoPath" -ForegroundColor Red
            Write-Host "Install from: https://jrsoftware.org/isdl.php" -ForegroundColor Yellow
            exit 1
        }
        
        & $innoPath "installer.iss"
        
        if ($LASTEXITCODE -ne 0) {
            Write-Host "`n✗ Installer build failed" -ForegroundColor Red
            exit $LASTEXITCODE
        }
        
        Write-Host "`n✓ Installer build complete" -ForegroundColor Green
        Write-Host "Output: dist\LiveTranslate-Setup.exe" -ForegroundColor Gray
    }
    
    Write-Host "`n✓ Clean build complete!" -ForegroundColor Green
    
} finally {
    Pop-Location
}
