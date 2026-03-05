param(
  [switch]$Clean
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venv = Join-Path $root ".venv-build-full"
$py = Join-Path $venv "Scripts\\python.exe"

if (-not (Test-Path $py)) {
  python -m venv $venv
}

& $py -m pip install --upgrade pip
& $py -m pip install -r requirements-full.txt pyinstaller

$dist = Join-Path $root "dist\\full"
$build = Join-Path $root "build\\full"

if ($Clean) {
  Remove-Item -Recurse -Force $dist, $build -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force $dist | Out-Null
New-Item -ItemType Directory -Force $build | Out-Null

& $py -m PyInstaller --noconfirm --clean --distpath $dist --workpath $build (Join-Path $root "scripts\\workspace_brain_full.spec")
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller 실패: exit_code=$LASTEXITCODE"
}

$bundle = Join-Path $dist "Workspace-Brain"
New-Item -ItemType Directory -Force (Join-Path $bundle "config") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $bundle "docs") | Out-Null

Copy-Item -Force (Join-Path $root "config\\settings.json") (Join-Path $bundle "config\\settings.json")
Copy-Item -Force (Join-Path $root "README.md") (Join-Path $bundle "README.md")
Copy-Item -Force (Join-Path $root "LICENSE") (Join-Path $bundle "LICENSE")
Copy-Item -Force (Join-Path $root "docs\\USER_GUIDE.md") (Join-Path $bundle "docs\\USER_GUIDE.md")
Copy-Item -Force (Join-Path $root "docs\\PACKAGING.md") (Join-Path $bundle "docs\\PACKAGING.md")

Write-Host ""
Write-Host "[완료] full 빌드 결과: $dist\\Workspace-Brain" -ForegroundColor Green
