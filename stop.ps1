$ErrorActionPreference = 'Stop'
$studioPidFile = Join-Path $PSScriptRoot 'data/logs/server.pid'
if (-not (Test-Path -LiteralPath $studioPidFile)) {
    Write-Output 'No managed background process. Use Ctrl+C in the foreground terminal.'
    return
}
$studioPid = [int](Get-Content -LiteralPath $studioPidFile -Raw)
$studioProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $studioPid"
$studioPython = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '.venv/Scripts/python.exe')).Path
if (-not $studioProcess) {
    Write-Output 'Studio background process is already stopped.'
    return
}
if ($studioProcess.ExecutablePath -ne $studioPython -or $studioProcess.CommandLine -notmatch 'studio\.server:app') {
    throw 'Saved PID does not match this studio process. Refusing to stop another process.'
}
Stop-Process -Id $studioPid
Write-Output 'Studio stopped. In-flight external tasks will require reconciliation on next start.'
