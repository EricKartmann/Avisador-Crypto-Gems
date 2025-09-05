$ErrorActionPreference = "Stop"

if (-not (Test-Path .venv)) {
  python -m venv .venv
}

. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Write-Host "Iniciando monitor... (Ctrl+C para salir)" -ForegroundColor Green
python .\main.py



