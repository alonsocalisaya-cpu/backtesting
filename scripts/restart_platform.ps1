$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$appFile = Join-Path $projectRoot 'app.py'
$listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
foreach ($listener in $listeners) {
    $ownerProcess = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $listener.OwningProcess)
    $belongsToProject = $ownerProcess.CommandLine.Contains($appFile) -or ($ownerProcess.CommandLine.Contains($projectPython) -and $ownerProcess.CommandLine.Contains('server:app'))
    # El lanzador venv de Windows crea un hijo con el intérprete base.
    if (-not $belongsToProject -and $ownerProcess.CommandLine.Contains('uvicorn server:app')) {
        $parentProcess = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $ownerProcess.ParentProcessId)
        $belongsToProject = $parentProcess -and $parentProcess.ExecutablePath -eq $projectPython -and $parentProcess.CommandLine.Contains('uvicorn server:app')
    }
    if (-not $belongsToProject) { throw 'El puerto 8000 pertenece a otro proyecto. No se ha detenido.' }
    Stop-Process -Id $ownerProcess.ProcessId
}
New-Item -ItemType Directory -Path (Join-Path $projectRoot 'logs') -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$serverProcess = Start-Process -FilePath $projectPython -ArgumentList @('-m','uvicorn','server:app','--port','8000','--host','127.0.0.1') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot ('logs\platform-' + $stamp + '.log')) -RedirectStandardError (Join-Path $projectRoot ('logs\platform-' + $stamp + '-error.log')) -PassThru
$serverProcess.Id | Set-Content -LiteralPath (Join-Path $projectRoot 'logs\server.pid')
Write-Output ('Nexo React iniciado. PID: ' + $serverProcess.Id)
