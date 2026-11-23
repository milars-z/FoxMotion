param([string]$Python, [switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Assert-Exit([string]$Action) {
    if ($LASTEXITCODE -ne 0) { throw "$Action failed (exit $LASTEXITCODE)." }
}
if (!(Test-Path .venv/Scripts/python.exe)) {
    if ($Python) { & $Python -m venv .venv }
    else { & py -3.12 -m venv .venv }
    Assert-Exit 'Creating Python 3.12 venv (use -Python for an alternate interpreter)'
}
$studioPython = (Resolve-Path .venv/Scripts/python.exe).Path
if (!(Test-Path .env)) { Copy-Item .env.example .env }
if (!$SkipInstall) {
    & $studioPython -m pip install -r requirements-dev.txt
    Assert-Exit 'Installing Python dependencies'
    Push-Location frontend
    try { & npm.cmd ci --no-audit --no-fund; Assert-Exit 'Installing frontend dependencies' }
    finally { Pop-Location }
}
if (!(Get-Command ffmpeg -ErrorAction SilentlyContinue) -or !(Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    throw 'Install FFmpeg (including ffprobe and libx264) and add its bin directory to PATH.'
}
$env:STUDIO_PORT = (& $studioPython -c 'from backend.config import Settings; print(Settings().studio_port)').Trim()
Assert-Exit 'Reading .env'
$env:PYTHONUTF8 = '1'
$dataPath = (& $studioPython -c 'from backend.config import Settings; print(Settings().data_dir.resolve())').Trim()
Assert-Exit 'Reading data directory'
$logDir = Join-Path $dataPath 'logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logPrefix = Join-Path $logDir (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
$studioNode = (Get-Command node).Source
$vite = (Resolve-Path frontend/node_modules/vite/bin/vite.js).Path
Write-Host "Studio: http://127.0.0.1:5173   API: http://127.0.0.1:$env:STUDIO_PORT   Ctrl+C stops all three processes."
$children = @()
try {
    $children += Start-Process -FilePath $studioPython -ArgumentList '-m backend' -WindowStyle Hidden -PassThru -RedirectStandardError "$logPrefix-api.log"
    $children += Start-Process -FilePath $studioPython -ArgumentList '-m backend.worker' -WindowStyle Hidden -PassThru -RedirectStandardError "$logPrefix-worker.log"
    $children += Start-Process -FilePath $studioNode -WorkingDirectory (Resolve-Path frontend) -ArgumentList ('"' + $vite + '"') -WindowStyle Hidden -PassThru -RedirectStandardError "$logPrefix-vite.log"
    while ($true) {
        Start-Sleep -Seconds 1
        foreach ($child in $children) {
            if ($child.HasExited) { throw "Studio process $($child.Id) exited with code $($child.ExitCode). Check ports 5173 and $env:STUDIO_PORT, dependencies and .env." }
        }
    }
} finally {
    foreach ($child in $children) {
        if (!$child.HasExited) { & taskkill.exe /PID $child.Id /T /F | Out-Null }
    }
}
