# Windows development environment setup for Comet.
#
# Installs everything needed to run `uv sync` and `uv run python -m comet.main`
# on Windows:
#   1. Visual Studio Build Tools 2022 with the C++ workload (required to
#      compile torrent-parse-rank, a Rust extension without prebuilt wheels).
#   2. Rust toolchain (installed on demand by maturin/uv if missing).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1

$ErrorActionPreference = "Stop"

function Test-Command($Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Host "== Comet Windows setup ==" -ForegroundColor Cyan

# --- 1. MSVC Build Tools -----------------------------------------------------
$linkExists = $false
foreach ($root in @(
    "${env:ProgramFiles(x86)}\Microsoft Visual Studio",
    "$env:ProgramFiles\Microsoft Visual Studio"
)) {
    if (-not (Test-Path $root)) { continue }
    $found = Get-ChildItem $root -Recurse -Filter "link.exe" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($found) { $linkExists = $true; break }
}

if ($linkExists) {
    Write-Host "[OK] MSVC linker already installed." -ForegroundColor Green
}
elseif (-not (Test-Command "winget")) {
    Write-Warning @"
MSVC linker not found and winget is not available.
Install 'Visual Studio Build Tools 2022' manually with the
'Desktop development with C++' workload, then re-run this script.
"@
}
else {
    Write-Host "Installing Visual Studio Build Tools 2022 (C++ workload)..." -ForegroundColor Yellow
    Write-Host "This downloads ~2 GB and may take several minutes; a UAC prompt will appear."
    winget install --id Microsoft.VisualStudio.2022.BuildTools `
        --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended" `
        --accept-package-agreements --accept-source-agreements

    # winget may report a generic failure even when the install succeeds.
    $linkExists = $false
    foreach ($root in @(
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio",
        "$env:ProgramFiles\Microsoft Visual Studio"
    )) {
        if (-not (Test-Path $root)) { continue }
        $found = Get-ChildItem $root -Recurse -Filter "link.exe" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($found) { $linkExists = $true; break }
    }

    if ($linkExists) {
        Write-Host "[OK] MSVC Build Tools installed." -ForegroundColor Green
    }
    else {
        Write-Warning "Could not verify the MSVC installation; `uv sync` may still fail until it completes."
    }
}

# --- 2. uv -------------------------------------------------------------------
if (Test-Command "uv") {
    Write-Host "[OK] uv already installed ($(uv --version))." -ForegroundColor Green
}
else {
    Write-Host "Installing uv..." -ForegroundColor Yellow
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    # Refresh PATH for the current session.
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

# --- 3. Python dependencies --------------------------------------------------
Write-Host "Running uv sync (this compiles torrent-parse-rank on first run)..." -ForegroundColor Yellow
uv sync

Write-Host ""
Write-Host "== Setup complete ==" -ForegroundColor Cyan
Write-Host "Start the server with:  uv run python -m comet.main"
