[CmdletBinding()]
param([ValidateRange(1,65535)][int]$Port = 8768)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$appPython = Join-Path $PSScriptRoot '.runtime\app\Scripts\python.exe'
if (!(Test-Path -LiteralPath $appPython)) { Write-Error 'Run install.cmd first.'; exit 1 }
$env:PYTHONUTF8 = '1'
$env:KTV_PORT = "$Port"
# Refuse duplicate starts without terminating or reconfiguring an existing app.
$portProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try {
    $portProbe.Start()
} catch {
    Write-Error "Port $Port is already in use. Open the existing app, or run start.cmd -Port 8769."
    exit 1
} finally {
    $portProbe.Stop()
}
Write-Host "LyriFrame: http://127.0.0.1:$Port"
Write-Host 'Closing the browser keeps processing active. Double-click stop.cmd to exit.'
# Open the browser only after the server responds; the helper exits after 60s.
$browserJob = Start-Job -ArgumentList $Port -ScriptBlock {
    param($LocalPort)
    $url = "http://127.0.0.1:$LocalPort/"
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1
            if ($response.StatusCode -eq 200) { Start-Process $url; return }
        } catch { }
        Start-Sleep -Seconds 1
    }
}
& $appPython -m ktv_app.serve
$serverExit = $LASTEXITCODE
Stop-Job $browserJob -ErrorAction SilentlyContinue
Remove-Job $browserJob -Force -ErrorAction SilentlyContinue
exit $serverExit
