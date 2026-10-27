[CmdletBinding()]
param(
    [string]$Python = '',
    [ValidateSet('cn','official')][string]$DownloadSource = 'cn',
    [ValidateRange(0,100)][int]$DownloadRetries = 10,
    [ValidateRange(10,600)][int]$DownloadTimeout = 90,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$DownloadArguments
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:LYRIFRAME_DOWNLOAD_SOURCE = $DownloadSource
$env:LYRIFRAME_DOWNLOAD_RETRIES = [string]$DownloadRetries
$env:LYRIFRAME_DOWNLOAD_TIMEOUT = [string]$DownloadTimeout
try {
    . (Join-Path $PSScriptRoot 'scripts\python_runtime.ps1')
    $runtime = Resolve-ProjectPython $Python
    [string[]]$prefix = $runtime.Prefix
    & $runtime.Command @prefix scripts/download_models.py @DownloadArguments
    exit $LASTEXITCODE
} catch {
    Write-Error -ErrorAction Continue $_.Exception.Message
    exit 1
}
