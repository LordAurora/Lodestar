# Windows equivalent of the Makefile:  .\make.ps1 setup | dev | build | run | test | lint | format | eval
param([Parameter(Position = 0)][string]$Target = "help")
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Py = Join-Path $Root "backend\.venv\Scripts\python.exe"

function In($dir, [scriptblock]$block) {
    Push-Location (Join-Path $Root $dir)
    try { & $block; if ($LASTEXITCODE) { exit $LASTEXITCODE } } finally { Pop-Location }
}

switch ($Target) {
    "setup" {
        In "." { python -m venv backend\.venv }
        In "." { & $Py -m pip install --upgrade pip }
        In "." { & $Py -m pip install -e "backend[dev]" }
        In "frontend" { npm ci }
        In "." { & $Py scripts\setup_models.py }
    }
    "dev" {
        # Backend in a new window, Vite in this one.
        Start-Process powershell -ArgumentList "-NoExit", "-Command",
            "cd '$Root\backend'; & '$Py' -m uvicorn --factory app.main:create_app --reload --host 127.0.0.1 --port 8000"
        In "frontend" { npm run dev }
    }
    "build" { In "frontend" { npm run build } }
    "run" {
        In "frontend" { npm run build }
        In "backend" { & $Py -m uvicorn --factory app.main:create_app --host 127.0.0.1 --port 8000 }
    }
    "test" {
        In "backend" { & $Py -m pytest }
        In "frontend" { npx tsc -b; if (-not $LASTEXITCODE) { npm run lint } }
    }
    "lint" {
        In "backend" { & $Py -m ruff check .; if (-not $LASTEXITCODE) { & $Py -m ruff format --check . } }
        In "frontend" { npm run lint; if (-not $LASTEXITCODE) { npm run format:check } }
    }
    "format" {
        In "backend" { & $Py -m ruff format .; & $Py -m ruff check --fix . }
        In "frontend" { npm run format }
    }
    "eval" { In "." { & $Py scripts\eval.py @args } }
    default { Write-Host "Usage: .\make.ps1 setup | dev | build | run | test | lint | format | eval" }
}
