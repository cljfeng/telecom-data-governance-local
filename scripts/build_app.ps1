$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir

$Python = Join-Path $RootDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3.12 -m venv .venv
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        python -m venv .venv
    } else {
        throw "未找到 Python。请先安装 Python 3.12，或在 GitHub Actions 中使用 actions/setup-python。"
    }
}

& $Python -m pip install -e ".[package]"
& $Python -m PyInstaller `
    --clean `
    --onefile `
    --name zufeidianfei-governance `
    --add-data "src/governance_app/static;governance_app/static" `
    src/governance_app/desktop.py

Remove-Item -Recurse -Force build -ErrorAction SilentlyContinue
Remove-Item -Force zufeidianfei-governance.spec -ErrorAction SilentlyContinue

Write-Host "打包完成：$RootDir\dist\zufeidianfei-governance.exe"
