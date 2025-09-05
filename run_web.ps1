$ErrorActionPreference = "Stop"

if (-not (Test-Path .venv)) {
  python -m venv .venv
}

. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Write-Host "Iniciando web en http://127.0.0.1:8000 ..." -ForegroundColor Green
uvicorn web:app --reload

