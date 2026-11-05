<#
.SYNOPSIS
  Install ActivityWatch and usage-sync for the current user (no admin needed).
.PARAMETER Device      Device name shipped with every event (default: lower-cased computer name).
.PARAMETER IngestUrl   Base URL of the ingest API.
.PARAMETER CaFile      Optional CA bundle that signed the ingest certificate.
.PARAMETER IngestConnectHost  Optional host or host:port to connect to instead of resolving the ingest host name.
.PARAMETER PythonPath  Optional Python 3.11-3.13 interpreter to build the venv with (never Microsoft Store Python).
.PARAMETER NodeSsh     Optional user@host; used only to mint the device token over SSH (never echoed).
.PARAMETER AwVersion   ActivityWatch tag, e.g. v0.13.2 (default: newest release with a Windows zip).
.PARAMETER RepoPath    Root of this repository (default: derived from the script location).
.PARAMETER Force       Overwrite an existing config.toml and re-download ActivityWatch.
.PARAMETER NoStart     Register everything but do not start the tasks.
.PARAMETER DryRun      Print what would be done and change nothing.
#>
[CmdletBinding()]
param(
    [string]$Device = $env:COMPUTERNAME.ToLower(),
    [string]$IngestUrl = 'https://ingest.lab',
    [string]$CaFile,
    [string]$IngestConnectHost,
    [string]$NodeSsh,
    [string]$PythonPath,
    [string]$AwVersion,
    [string]$RepoPath = '',
    [switch]$Force,
    [switch]$NoStart,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$FallbackAwVersion = 'v0.13.2'   # last Windows build in the 0.13 line
# $PSScriptRoot is empty while param() defaults are evaluated under `powershell -File` (5.1)
if (-not $RepoPath) { $RepoPath = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path }

$awDir = Join-Path $env:LOCALAPPDATA 'Programs\activitywatch'
$awExe = Join-Path $awDir 'aw-qt.exe'
$syncDir = Join-Path $env:LOCALAPPDATA 'Programs\usage-sync'
$venv = Join-Path $syncDir 'venv'
$cfgDir = Join-Path $env:LOCALAPPDATA 'usage-sync'
$cfgFile = Join-Path $cfgDir 'config.toml'
$tokenFile = Join-Path $cfgDir 'token'
$syncPkg = Join-Path $RepoPath 'sync'
$venvPy = Join-Path $venv 'Scripts\python.exe'
$venvPyw = Join-Path $venv 'Scripts\pythonw.exe'
$venvCli = Join-Path $venv 'Scripts\usage-sync.exe'
$apiBase = 'https://api.github.com/repos/ActivityWatch/activitywatch/releases'

function Step([string]$text) {
    if ($DryRun) { Write-Host "[dry-run] $text" } else { Write-Host $text }
}

function Get-AwRelease {
    # Returns @{ Tag; Url } for the requested (or newest) release that has a Windows zip.
    $headers = @{ 'User-Agent' = 'usage-sync-installer' }
    # Windows PowerShell 5.1 returns a JSON array as ONE object, so always enumerate explicitly.
    function Get-Releases([string]$uri) {
        $resp = Invoke-RestMethod -Headers $headers -Uri $uri
        return @($resp | ForEach-Object { $_ })
    }
    function Find-WindowsZip($releases, [bool]$allowPre) {
        foreach ($r in $releases) {
            if (($r.prerelease -eq $true) -and -not $allowPre) { continue }
            foreach ($a in @($r.assets)) {
                if ($a.name -like 'activitywatch-v*-windows-x86_64.zip') {
                    return @{ Tag = $r.tag_name; Url = $a.browser_download_url }
                }
            }
        }
        return $null
    }
    if ($AwVersion) {
        $found = Find-WindowsZip (Get-Releases "$apiBase/tags/$AwVersion") $true
        if ($found) { return $found }
        throw "ActivityWatch $AwVersion has no Windows zip asset"
    }
    $found = $null
    try {
        $found = Find-WindowsZip (Get-Releases "${apiBase}?per_page=15") $false
    } catch {
        Write-Warning "release lookup failed ($($_.Exception.Message))"
    }
    if (-not $found) {
        Write-Warning "no release with a Windows zip found; using $FallbackAwVersion"
        $found = Find-WindowsZip (Get-Releases "$apiBase/tags/$FallbackAwVersion") $true
    }
    if (-not $found) { throw 'no ActivityWatch release with a Windows zip asset was found' }
    return $found
}

function Invoke-Native {
    # Run a native command without letting stderr output terminate the script; returns Out + Code.
    param([string]$Exe, [string[]]$ArgList)
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $all = @(& $Exe @ArgList 2>&1)
        $code = $LASTEXITCODE
    } catch {
        $all = @()
        $code = -1
    } finally {
        $ErrorActionPreference = $saved
    }
    $out = @($all | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { "$_" })
    $err = @($all | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { "$_" })
    return @{ Out = $out; Err = $err; Code = $code }
}

function Test-PythonCandidate([string[]]$Cand) {
    # Returns '' when $Cand (exe first) is a real, non-venv CPython 3.11-3.13, else the reason.
    $exe = $Cand[0]
    $rest = @($Cand | Select-Object -Skip 1)
    $label = ($Cand -join ' ')
    $probe = 'import sys; print(sys.version_info[:2] >= (3,11) and sys.version_info[:2] < (3,14)); print(sys.base_prefix); print(sys.prefix != sys.base_prefix)'
    $r = Invoke-Native $exe (@($rest) + @('-c', $probe))
    if ($r.Code -ne 0 -or $r.Out.Count -lt 3) { return "$label is not a working Python (Microsoft Store alias?)" }
    $resolved = (Get-Command $exe -ErrorAction SilentlyContinue).Source
    if ($r.Out[1] -like '*\WindowsApps\*' -or $resolved -like '*\WindowsApps\*') {
        return "$label is Microsoft Store Python (virtualizes AppData, so the shim could not read its config/token)"
    }
    if ($r.Out[2] -eq 'True') { return "$label is inside a virtual environment" }
    if ($r.Out[0] -ne 'True') { return "$label is not Python 3.11-3.13" }
    return ''
}

function Find-Python {
    # Returns an argv array (exe first) for a real CPython 3.11-3.13; fails with a clear message.
    # Order: -PythonPath, uv-managed, uv system, py -3, python. Rejected: Microsoft Store Python
    # (it virtualizes %LOCALAPPDATA%, so the shim could not see the config, token and CA this
    # script writes there) and anything inside a virtual environment (e.g. a project .venv).
    $problems = @()
    if ($PythonPath) {
        # an explicit choice is never silently replaced by another interpreter
        $bad = Test-PythonCandidate @($PythonPath)
        if ($bad) { throw "-PythonPath rejected: $bad" }
    }
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    foreach ($round in 1, 2) {
        $cands = @()
        if ($PythonPath) { $cands += , @($PythonPath) }
        if ($uv) {
            foreach ($mode in '--managed-python', '--system') {
                $r = Invoke-Native $uv.Source @('python', 'find', '--no-project', $mode, '>=3.11,<3.14')
                if ($r.Code -eq 0 -and $r.Out.Count -gt 0 -and $r.Out[0].Trim()) { $cands += , @($r.Out[0].Trim()) }
            }
        }
        if (Get-Command py -ErrorAction SilentlyContinue) { $cands += , @('py', '-3') }
        if (Get-Command python -ErrorAction SilentlyContinue) { $cands += , @('python') }
        foreach ($cand in $cands) {
            $exe = $cand[0]
            $rest = @($cand | Select-Object -Skip 1)
            $problem = Test-PythonCandidate $cand
            if ($problem) { $problems += $problem; continue }
            return , (@($exe) + $rest)
        }
        if ($round -eq 1 -and $uv -and -not $DryRun) {
            Write-Host 'no suitable Python found; installing Python 3.12 with uv (user-level)'
            $r = Invoke-Native $uv.Source @('python', 'install', '3.12')
            if ($r.Code -ne 0) { $problems += "uv python install 3.12 failed (exit $($r.Code))"; break }
            continue
        }
        break
    }
    $detail = $problems -join '; '
    throw "A real Python 3.11-3.13 is required (not Microsoft Store Python). Either install Python from python.org (and re-run), install uv (https://docs.astral.sh/uv/) so this script can fetch one, or pass -PythonPath. $detail"
}

function Test-Packaged {
    # True when this process has Windows package identity (e.g. a terminal inside an MSIX app).
    # If the check itself fails, warn and assume an ordinary (unpackaged) process.
    try {
        if (-not ('UsageSync.Pkg' -as [type])) {
            Add-Type -Namespace UsageSync -Name Pkg -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
public static extern int GetCurrentPackageFullName(ref int length, System.Text.StringBuilder name);
'@
        }
        $len = 0
        $rc = [UsageSync.Pkg]::GetCurrentPackageFullName([ref]$len, $null)
        return ($rc -ne 15700)   # 15700 = APPMODEL_ERROR_NO_PACKAGE
    } catch {
        Write-Warning "could not check for package identity ($($_.Exception.Message)); assuming an unpackaged process"
        return $false
    }
}

function New-OutsideDirectory([string]$Dir) {
    # A packaged process gets new top-level %LOCALAPPDATA% folders redirected to private storage,
    # invisible to Task Scheduler. Create the directory from a process outside the package.
    $name = 'usage-sync-mkdir'
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c if not exist `"$Dir`" mkdir `"$Dir`""
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    try {
        Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Force | Out-Null
        Start-ScheduledTask -TaskName $name
        for ($n = 0; $n -lt 20; $n++) {
            Start-Sleep -Milliseconds 500
            if ((Get-ScheduledTask -TaskName $name).State -ne 'Running' -and $n -ge 1) { break }
        }
    } finally {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    }
    # the task may still be starting: wait up to 10 s for the directory to appear
    for ($n = 0; $n -lt 20 -and -not (Test-Path $Dir); $n++) { Start-Sleep -Milliseconds 500 }
    if (-not (Test-Path $Dir)) {
        throw "$Dir was not created; re-run from a normal PowerShell window"
    }
    $stale = @(Get-Item -Path (Join-Path $env:LOCALAPPDATA 'Packages\*\LocalCache\Local\usage-sync') -ErrorAction SilentlyContinue)
    foreach ($copy in $stale) {
        Write-Warning "a stale redirected copy exists at $($copy.FullName); it is not used and can be deleted"
    }
}

function Get-ProcessesUnder([string[]]$Dirs) {
    # Processes whose executable lives under one of the given directories.
    foreach ($d in $Dirs) {
        Get-Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and $_.Path.StartsWith($d + '\', [StringComparison]::OrdinalIgnoreCase) }
    }
}

function Stop-ProcessesUnder([string[]]$Dirs) {
    $procs = @(Get-ProcessesUnder $Dirs)
    if ($procs.Count -eq 0) { return }
    $procs | Stop-Process -Force -ErrorAction SilentlyContinue
    for ($n = 0; $n -lt 20; $n++) {
        if (@(Get-ProcessesUnder $Dirs).Count -eq 0) { return }
        Start-Sleep -Milliseconds 500
    }
}

# 0. preflight: validate inputs and Python before touching anything (also under -DryRun)
if ($Device -cnotmatch '^[a-z0-9][a-z0-9._-]*$') {
    throw "invalid -Device '$Device': use lower-case letters, digits, '.', '_' or '-'"
}
if ($CaFile) {
    if (-not (Test-Path -LiteralPath $CaFile)) { throw "-CaFile not found: $CaFile" }
    $CaFile = (Resolve-Path -LiteralPath $CaFile).Path
}
if ($IngestConnectHost -and $IngestConnectHost -notmatch '^[A-Za-z0-9.\-:\[\]]+$') {
    throw "invalid -IngestConnectHost '$IngestConnectHost'"
}
if ($NodeSsh -and $NodeSsh -notmatch '^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$') {
    throw "invalid -NodeSsh '$NodeSsh': expected user@host"
}
$py = Find-Python
Step "python: $($py -join ' ')"

# packaged-process pitfall: create %LOCALAPPDATA%\usage-sync from outside the package
if (Test-Packaged) {
    if ($DryRun) {
        Step "running inside a packaged app: would create $cfgDir through a one-shot scheduled task (cmd /c mkdir), then verify it is not redirected"
    } else {
        Write-Warning 'running inside a packaged app (new %LOCALAPPDATA% folders are redirected); creating the data directory from outside the package'
        New-OutsideDirectory $cfgDir
    }
}

# 1. ActivityWatch
if ((Test-Path $awExe) -and -not $Force -and -not $AwVersion) {
    Step "keep existing ActivityWatch at $awDir (use -Force or -AwVersion to replace)"
} elseif ($DryRun) {
    $wanted = $AwVersion
    if (-not $wanted) { $wanted = "latest, fallback $FallbackAwVersion" }
    Step "look up the ActivityWatch Windows zip ($wanted)"
    Step "download it to $env:TEMP and expand it to $awDir (replacing any existing copy)"
} else {
    $release = Get-AwRelease
    Write-Host "ActivityWatch $($release.Tag)"
    $zip = Join-Path $env:TEMP "activitywatch-$($release.Tag).zip"
    $stage = Join-Path $env:TEMP "activitywatch-$($release.Tag)-extract"
    Invoke-WebRequest -UseBasicParsing -Uri $release.Url -OutFile $zip
    if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
    Expand-Archive -Path $zip -DestinationPath $stage -Force
    $inner = Get-ChildItem -Path $stage -Directory | Select-Object -First 1
    $src = $stage
    if ($inner -and (Test-Path (Join-Path $inner.FullName 'aw-qt.exe'))) { $src = $inner.FullName }
    Stop-ProcessesUnder @($awDir)
    if (Test-Path $awDir) { Remove-Item -Recurse -Force $awDir }
    New-Item -ItemType Directory -Force -Path (Split-Path $awDir) | Out-Null
    Move-Item -Path $src -Destination $awDir
    Remove-Item -Recurse -Force $stage, $zip -ErrorAction SilentlyContinue
    if (-not (Test-Path $awExe)) { throw "ActivityWatch install failed: $awExe is missing after the move" }
}

# 2. ActivityWatch logon task
Step "register logon task ActivityWatch -> $awExe"
if (-not $DryRun) {
    $action = New-ScheduledTaskAction -Execute $awExe -WorkingDirectory $awDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName 'ActivityWatch' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
}

# 3. venv + usage-sync (stop a running shim first so its files are not locked)
Step "stop task usage-sync if running, then create venv $venv and pip install $syncPkg"
if (-not $DryRun) {
    if (Get-ScheduledTask -TaskName 'usage-sync' -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName 'usage-sync' -ErrorAction SilentlyContinue
    }
    Stop-ProcessesUnder @($syncDir)
    $pyArgs = @($py | Select-Object -Skip 1)
    & $py[0] @pyArgs -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
    & $venvPy -m pip install --quiet --upgrade $syncPkg
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
    # a same-version re-install must still pick up changed code
    & $venvPy -m pip install --quiet --upgrade --force-reinstall --no-deps $syncPkg
    if ($LASTEXITCODE -ne 0) { throw 'pip reinstall failed' }
}

# 4. config
if ((Test-Path $cfgFile) -and -not $Force) {
    Step "keep existing $cfgFile (use -Force to overwrite)"
    foreach ($ignored in 'IngestUrl', 'CaFile', 'IngestConnectHost', 'Device') {
        if ($PSBoundParameters.ContainsKey($ignored)) {
            Write-Warning "-$ignored was ignored because $cfgFile already exists; use -Force to rewrite it"
        }
    }
} else {
    $extra = ''
    if ($CaFile) { $extra += ", ca_file=$CaFile" }
    if ($IngestConnectHost) { $extra += ", ingest_connect_host=$IngestConnectHost" }
    Step "write $cfgFile via usage-sync init (device=$Device, ingest_url=$IngestUrl$extra)"
    if (-not $DryRun) {
        $initArgs = @('--config', $cfgFile, 'init', '--device', $Device, '--ingest-url', $IngestUrl, '--force')
        if ($CaFile) { $initArgs += @('--ca-file', $CaFile) }
        if ($IngestConnectHost) { $initArgs += @('--ingest-connect-host', $IngestConnectHost) }
        & $venvCli @initArgs
        if ($LASTEXITCODE -ne 0) { throw 'usage-sync init failed' }
    }
}

# 5. token
if (Test-Path $tokenFile) {
    Step "keep existing token file $tokenFile"
} elseif ($NodeSsh) {
    Step "mint a device token over SSH ($NodeSsh) into $tokenFile (value is never printed)"
    if (-not $DryRun) {
        $remote = "kubectl -n lab exec deploy/ingest-api -c ingest-api -- store-admin token create --device $Device --source usage"
        $token = (& ssh $NodeSsh $remote) -join ''
        if ($LASTEXITCODE -ne 0 -or -not $token.Trim()) { throw 'token creation over SSH failed' }
        Set-Content -Path $tokenFile -Value $token.Trim() -NoNewline -Encoding ascii
    }
} else {
    Write-Warning "no token yet: put the device token in $tokenFile or re-run with -NodeSsh user@host"
}

# 6. usage-sync logon task
Step "register logon task usage-sync -> $venvPyw -m usage_sync run"
if (-not $DryRun) {
    $action = New-ScheduledTaskAction -Execute $venvPyw -Argument '-m usage_sync run' -WorkingDirectory $cfgDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName 'usage-sync' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
}

# 7. start (also restarts the shim stopped in step 3) + status
if ($NoStart) {
    Step 'start skipped (-NoStart)'
} else {
    Step 'start both tasks, wait 20 s, print usage-sync status'
    if (-not $DryRun) {
        Start-ScheduledTask -TaskName 'ActivityWatch'
        Start-ScheduledTask -TaskName 'usage-sync'
        # wait for the shim task to be Running; surface a failed start instead of hiding it
        $state = ''
        for ($n = 0; $n -lt 15; $n++) {
            $state = (Get-ScheduledTask -TaskName 'usage-sync').State
            if ($state -eq 'Running') { break }
            Start-Sleep -Seconds 1
        }
        $result = (Get-ScheduledTaskInfo -TaskName 'usage-sync').LastTaskResult
        if ($state -ne 'Running' -and $result -ne 0 -and $result -ne 267009 -and $result -ne 267011) {
            Write-Host ('usage-sync task failed to start: result 0x{0:X8}' -f ([uint32]([int64]$result -band 0xFFFFFFFF)))
            $logFile = Join-Path $cfgDir 'usage-sync.log'
            if (Test-Path $logFile) { Get-Content -Path $logFile -Tail 20 }
            exit 1
        }
        if ($state -ne 'Running') { Write-Warning "usage-sync task state is '$state' (last result $result)" }
        Start-Sleep -Seconds 5
        & $venvCli --config $cfgFile status
        Write-Host 'If ActivityWatch shows as unreachable above, it may still be starting; re-run: usage-sync status'
        # status does not exercise the ingest path, so ship once for real
        $once = Invoke-Native $venvPy @('-m', 'usage_sync', '--config', $cfgFile, 'once')
        $once.Out | ForEach-Object { Write-Host $_ }
        $once.Err | ForEach-Object { Write-Host "stderr: $_" }
        if ($once.Code -ne 0) { Write-Warning "usage-sync once exited with code $($once.Code); see the log at $(Join-Path $cfgDir 'usage-sync.log')" }
    }
}
Write-Host 'done.'
