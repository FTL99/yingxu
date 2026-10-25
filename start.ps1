param([switch]$NoBrowser, [switch]$Build)
$ErrorActionPreference = 'Stop'
$studioRoot = $PSScriptRoot
$studioPython = Join-Path $studioRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $studioPython)) {
    throw 'Missing .venv. Follow README.md to install the dependencies first.'
}
Set-Location -LiteralPath $studioRoot
try {
    $studioHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8811/api/health' -TimeoutSec 2
    if ($studioHealth.status -eq 'ok' -and $studioHealth.version -eq '0.1.0' -and $studioHealth.token) {
        if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8811/' }
        Write-Output 'Studio is already running: http://127.0.0.1:8811/'
        return
    }
} catch { }
if ($Build -or -not (Test-Path -LiteralPath (Join-Path $studioRoot 'frontend/dist/index.html'))) {
    & npm run build --prefix frontend
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
}
if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8811/' }
& $studioPython -X utf8 -m uvicorn studio.server:app --host 127.0.0.1 --port 8811
