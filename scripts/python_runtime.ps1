# Shared interpreter selection for installation and resource downloads.
function Resolve-ProjectPython([string]$ExplicitPython = '') {
    $policy = Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\requirements\python-compatibility.json') -Raw | ConvertFrom-Json
    $probe = "import sys,struct,sysconfig; print(str(sys.version_info.major)+'.'+str(sys.version_info.minor)+'/'+str(struct.calcsize('P')*8)+'/'+sys.implementation.name+'/'+sysconfig.get_platform()+'/'+str(sysconfig.get_config_var('Py_GIL_DISABLED') or 0))"
    $candidates = @()
    if ($ExplicitPython) {
        $candidates += @{ Command = $ExplicitPython; Prefix = @() }
    } else {
        $existing = Join-Path $PSScriptRoot '..\.runtime\app\Scripts\python.exe'
        if (Test-Path -LiteralPath $existing) {
            $candidates += @{ Command = $existing; Prefix = @() }
        }
        if (Get-Command py -ErrorAction SilentlyContinue) {
            foreach ($minor in $policy.preference) {
                $candidates += @{ Command = 'py'; Prefix = @("-$minor") }
            }
        }
        if (Get-Command python -ErrorAction SilentlyContinue) {
            $candidates += @{ Command = 'python'; Prefix = @() }
        }
    }
    foreach ($candidate in $candidates) {
        try {
            [string[]]$prefix = $candidate.Prefix
            $result = & $candidate.Command @prefix -c $probe 2>$null
            if ($LASTEXITCODE -eq 0 -and $result -and $result.Trim() -in @($policy.versions | ForEach-Object { "$_/64/cpython/win-amd64/0" })) {
                return @{ Command = $candidate.Command; Prefix = $prefix; Version = $result.Trim().Split('/')[0] }
            }
        } catch {
            # A missing launcher version must not prevent trying the next candidate.
        }
    }
    throw ('Python ' + ($policy.versions -join ', ') + ' (64-bit) is required. Use standard Windows x64 CPython, or use -Python with its executable path.')
}
