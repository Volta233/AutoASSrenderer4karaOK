[CmdletBinding()]
param(
    [ValidateSet('all','app','locator','tifa','kim')][string]$Component = 'all',
    [string]$Python = '',
    [string]$TorchIndex = '',
    [ValidateSet('cn','official')][string]$DownloadSource = 'cn',
    [ValidateRange(0,100)][int]$DownloadRetries = 10,
    [ValidateRange(10,600)][int]$DownloadTimeout = 90
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:LYRIFRAME_DOWNLOAD_SOURCE = $DownloadSource
$env:LYRIFRAME_DOWNLOAD_RETRIES = [string]$DownloadRetries
$env:LYRIFRAME_DOWNLOAD_TIMEOUT = [string]$DownloadTimeout

function Invoke-Checked([string]$Executable, [string[]]$Arguments, [string]$Failure) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Failure (exit $LASTEXITCODE)" }
}

try {
    . (Join-Path $PSScriptRoot 'scripts\python_runtime.ps1')
    $selectedPython = Resolve-ProjectPython $Python
    $pythonCommand = $selectedPython.Command
    [string[]]$pythonPrefix = $selectedPython.Prefix
    # Windows PowerShell 5.1 needs an array for splatting and strips embedded
    # double quotes in native arguments. Keep these Python probes quote-free.
    $versionCode = 'import sys; print(str(sys.version_info.major) + chr(46) + str(sys.version_info.minor))'
    $version = & $pythonCommand @pythonPrefix -c $versionCode
    if ($LASTEXITCODE -ne 0 -or !$version -or $version.Trim() -ne $selectedPython.Version) {
        throw 'The selected Python interpreter changed or could not be checked.'
    }
    $version = $version.Trim()
    $wheelTag = 'cp' + $version.Replace('.', '')
    $architecture = & $pythonCommand @pythonPrefix -c 'import struct; print(struct.calcsize(chr(80))*8)'
    if ($LASTEXITCODE -ne 0 -or !$architecture -or $architecture.Trim() -ne '64') { throw '64-bit Python is required.' }

    # Check model resources after the CUDA preflight and automatic download.

    $hasNvidia = $false
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $null = & nvidia-smi --query-gpu=name --format=csv,noheader 2>$null
        $hasNvidia = $LASTEXITCODE -eq 0
    }
    if (!$hasNvidia -and $Component -ne 'app') {
        throw 'A working NVIDIA CUDA driver is required. Install a compatible NVIDIA driver, then run install.cmd again.'
    }
    if (!$TorchIndex) {
        $TorchIndex = 'https://download.pytorch.org/whl/cu128'
    }
    if ($TorchIndex -notmatch '^https://download\.pytorch\.org/whl/cu\d+/?$') {
        throw '-TorchIndex must use an official PyTorch CUDA wheel index.'
    }
    $components = if ($Component -eq 'all') { @('app','locator','tifa','kim') } else { @($Component) }
    if ($Component -ne 'app') {
        Write-Host 'Downloading and verifying Kim, ASR, TIFA and FFmpeg. Re-run install.cmd after an interruption.'
        Invoke-Checked $pythonCommand ($pythonPrefix + @('scripts/download_models.py')) 'Model download failed. Existing verified files are preserved; run install.cmd again to resume.'
        Invoke-Checked $pythonCommand ($pythonPrefix + @('scripts/install_preflight.py')) 'Some downloaded resources are incomplete.'
        if ($TorchIndex.TrimEnd('/') -eq 'https://download.pytorch.org/whl/cu128') {
            Invoke-Checked $pythonCommand ($pythonPrefix + @('scripts/download_torch.py')) 'PyTorch download failed. Run install.cmd again to resume.'
        }
    }
    $stepNumber = 0
    foreach ($item in $components) {
        $stepNumber++
        Write-Host "[$stepNumber/$($components.Count)] Preparing $item environment. First installation may take several minutes."
        if ($item -eq 'tifa' -and !(Test-Path -LiteralPath 'models\forced_alignment\sources\TIFA\requirements.txt')) {
            throw 'TIFA source is missing. Use the Windows model package, or prepare resources as described in docs/SETUP.md.'
        }
        $runtimeName = if ($item -eq 'kim') { 'separation' } else { $item }
        $runtimeRoot = Join-Path $PSScriptRoot ".runtime\$runtimeName"
        $componentPython = Join-Path $runtimeRoot 'Scripts\python.exe'
        if (!(Test-Path -LiteralPath $componentPython)) {
            Invoke-Checked $pythonCommand ($pythonPrefix + @('-m','venv',$runtimeRoot)) "Cannot create the $item environment"
        }
        $runtimeVersion = & $componentPython -c $versionCode
        if ($LASTEXITCODE -ne 0 -or !$runtimeVersion -or $runtimeVersion.Trim() -ne $version) {
            throw "$item environment does not use Python $version. Choose the matching interpreter, or recreate only .runtime\$runtimeName. Existing files are preserved."
        }
        Invoke-Checked $componentPython @('scripts/bootstrap_pip.py') "Cannot prepare the package installer in $item"
        if ($item -ne 'app') {
            # Force the selected wheel build even when a different +cpu/+cu build
            # satisfies the same public version in a previously created venv.
            if ($TorchIndex.TrimEnd('/') -eq 'https://download.pytorch.org/whl/cu128') {
                # One verified download is shared by the three isolated environments.
                $torchWheel = Join-Path $PSScriptRoot ".runtime\wheels\torch-2.11.0+cu128-$wheelTag-$wheelTag-win_amd64.whl"
                $audioWheel = Join-Path $PSScriptRoot ".runtime\wheels\torchaudio-2.11.0+cu128-$wheelTag-$wheelTag-win_amd64.whl"
                Invoke-Checked $componentPython @('scripts/install_dependencies.py',$torchWheel,$audioWheel) "Cannot install CUDA PyTorch in $item"
            } else {
                Invoke-Checked $componentPython @('-m','pip','install','--force-reinstall','torch==2.11.0','torchaudio==2.11.0','--index-url',$TorchIndex,'--retries',[string]$DownloadRetries,'--resume-retries',[string]$DownloadRetries,'--timeout',[string]$DownloadTimeout) "Cannot install CUDA PyTorch in $item"
            }
        }
        $requirement = if ($item -eq 'app') { 'requirements.txt' } else { "requirements\$item.txt" }
        $arguments = @('scripts/install_dependencies.py','-r',$requirement)
        if ($item -ne 'app') { $arguments += @('-c','requirements\torch-constraints.txt') }
        Invoke-Checked $componentPython $arguments "Cannot install $item dependencies"
        Invoke-Checked $componentPython @('-m','pip','check') "$item has conflicting dependencies"
    }
    if (!(Test-Path -LiteralPath '.env')) {
        Copy-Item -LiteralPath '.env.example' -Destination '.env'
    }
    $appPython = Join-Path $PSScriptRoot '.runtime\app\Scripts\python.exe'
    if (Test-Path -LiteralPath $appPython) {
        Invoke-Checked $appPython @('scripts/configure_install.py','--torch-index',$TorchIndex) 'Cannot write local installation settings'
        if ($Component -eq 'all' -or $Component -eq 'tifa') {
            Invoke-Checked $appPython @('scripts/configure_g2p.py') 'TIFA pronunciation resources are incomplete'
        }
        if ($Component -eq 'all') {
            Write-Host 'Checking all model environments. First-time DLL loading may take several minutes; please keep this window open.'
            Invoke-Checked $appPython @('scripts/doctor.py','--json','.runtime/doctor.json') 'Installation check failed; see the failing component above'
        }
    }
    Write-Host 'Installation complete. Double-click start.cmd to open LyriFrame.'
    exit 0
} catch {
    Write-Error -ErrorAction Continue $_.Exception.Message
    Write-Host 'Installation stopped. Existing files and model weights were preserved. See docs/SETUP.md.'
    exit 1
}
