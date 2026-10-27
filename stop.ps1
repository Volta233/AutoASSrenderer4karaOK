[CmdletBinding()]
param([ValidateRange(1,65535)][int]$Port = 8768)
$ErrorActionPreference = 'Stop'
$recordPath = Join-Path $PSScriptRoot ".runtime\server-$Port.json"
if (!(Test-Path -LiteralPath $recordPath)) { Write-Host 'LyriFrame is not running on this port.'; exit 0 }
try {
    $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    $serverProcess = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
    if (!$serverProcess) {
        Remove-Item -LiteralPath $recordPath
        Write-Host 'LyriFrame is already stopped.'
        exit 0
    }
    $expectedPython = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '.runtime\app\Scripts\python.exe'))
    $actualRoot = [IO.Path]::GetFullPath($PSScriptRoot)
    # Check process creation time as well as its executable to reject a reused PID.
    if ($record.root -ne $actualRoot -or [int]$record.port -ne $Port -or
        $record.app_python -ne $expectedPython -or $serverProcess.Path -ne $record.executable -or
        $serverProcess.StartTime.ToUniversalTime().ToFileTimeUtc() -ne [long]$record.creation_filetime) {
        throw 'The saved instance does not match this application. No process was stopped.'
    }
    & taskkill.exe /PID ([string]$record.pid) /T /F
    if ($LASTEXITCODE -ne 0) { throw 'Cannot stop the application process tree.' }
    Remove-Item -LiteralPath $recordPath -ErrorAction SilentlyContinue
    Write-Host 'LyriFrame and its workers have stopped. Saved projects are preserved.'
    exit 0
} catch {
    Write-Error -ErrorAction Continue $_.Exception.Message
    exit 1
}
