$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Falta el entorno .venv. Consulta README.md para instalarlo.'
}
& $projectPython -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',8000)); s.close()"
if ($LASTEXITCODE -ne 0) {
    throw 'El puerto 8000 esta ocupado. No se ha detenido ni modificado ningun proceso.'
}
Write-Host 'Nexo disponible en http://localhost:8000. Ctrl+C para detener esta aplicacion.'
& $projectPython -m uvicorn server:app --port 8000 --host 127.0.0.1
