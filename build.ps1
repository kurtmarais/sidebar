# Builds dist\Sidebar.exe, a single self-contained executable.
# Run from a PowerShell prompt in this folder:  .\build.ps1
$ErrorActionPreference = "Stop"

# Prefer the Python launcher (py) so other apps' bundled Pythons (e.g. Inkscape) are not used.
$py = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
if (-not (Test-Path .venv)) { & $py -m venv .venv }
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements-dev.txt

.\.venv\Scripts\python tools\make_icon.py sidebar.ico
.\.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed `
    --name Sidebar --icon sidebar.ico run_sidebar.py

Write-Host ""
Write-Host "Built dist\Sidebar.exe"
