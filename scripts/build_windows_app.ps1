$buildName = 'IWTT-Thought-Communities'
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)"
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller is required. Install it with: python -m pip install pyinstaller'
}

python -m PyInstaller --noconfirm --clean --onefile --console `
    --name $buildName `
    --add-data "web;web" `
    scripts/launch_iwtt.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

Write-Host "Built: $repoRoot\dist\$buildName.exe"
