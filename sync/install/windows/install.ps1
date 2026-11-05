<#
.SYNOPSIS
  Install ActivityWatch and usage-sync for the current user (no admin needed).
.PARAMETER Device      Device name shipped with every event (default: lower-cased computer name).
.PARAMETER IngestUrl   Base URL of the ingest API.
.PARAMETER CaFile      Optional CA bundle that signed the ingest certificate.
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
    [string]$NodeSsh,
    [string]$AwVersion,
    [string]$RepoPath = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path,
    [switch]$Force,
    [switch]$NoStart,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$FallbackAwVersion = 'v0.13.2'   # last Windows build in the 0.13 line

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
    if ($AwVersion) {
        $releases = @(Invoke-RestMethod -Headers $headers -Uri "$apiBase/tags/$AwVersion")
    } else {
        try {
            $releases = @(Invoke-RestMethod -Headers $headers -Uri "${apiBase}?per_page=15")
        } catch {
            Write-Warning "release lookup failed ($($_.Exception.Message)); using $FallbackAwVersion"
            $releases = @(Invoke-RestMethod -Headers $headers -Uri "$apiBase/tags/$FallbackAwVersion")
        }
    }
    foreach ($r in $releases) {
        if ($r.prerelease -and -not $AwVersion) { continue }
        foreach ($a in $r.assets) {
            if ($a.name -like 'activitywatch-v*-windows-x86_64.zip') {
                return @{ Tag = $r.tag_name; Url = $a.browser_download_url }
            }
        }
    }
    throw 'no ActivityWatch release with a Windows zip asset was found'
}

function Find-Python {
    # Returns an argv array (exe first) for a Python >= 3.11; fails with a clear message.
    $problems = @()
    foreach ($cand in @(@('py', '-3'), @('python'))) {
        $cmd = Get-Command $cand[0] -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $rest = @($cand | Select-Object -Skip 1)
        $isStoreAlias = $cmd.Source -like '*\WindowsApps\*'
        $ok = $false
        try {
            $out = & $cmd.Source @rest -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>$null
            $code = $LASTEXITCODE
            $ok = ($code -eq 0)
            if (-not $ok -and ($code -eq 9009 -or ($isStoreAlias -and -not $out))) {
                $problems += "$($cmd.Source) is the Microsoft Store alias, not a real Python"
                continue
            }
        } catch {
            $problems += "$($cmd.Source) failed to run ($($_.Exception.Message))"
            continue
        }
        if ($ok) { return , (@($cmd.Source) + $rest) }
        $problems += "$($cmd.Source) is older than Python 3.11"
    }
    $detail = $problems -join '; '
    throw "Python 3.11 or newer is required (py -3 or python on PATH, not the Microsoft Store alias). $detail"
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
if ($Device -notmatch '^[a-z0-9][a-z0-9._-]*$') {
    throw "invalid -Device '$Device': use lower-case letters, digits, '.', '_' or '-'"
}
if ($CaFile) {
    if (-not (Test-Path -LiteralPath $CaFile)) { throw "-CaFile not found: $CaFile" }
    $CaFile = (Resolve-Path -LiteralPath $CaFile).Path
}
$py = Find-Python
Step "python: $($py -join ' ')"

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
} else {
    Step "write $cfgFile via usage-sync init (device=$Device, ingest_url=$IngestUrl)"
    if (-not $DryRun) {
        $initArgs = @('--config', $cfgFile, 'init', '--device', $Device, '--ingest-url', $IngestUrl, '--force')
        if ($CaFile) { $initArgs += @('--ca-file', $CaFile) }
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
        $remote = "kubectl -n lab exec deploy/ingest-api -- store-admin token create --device $Device --source usage"
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
        Start-Sleep -Seconds 20
        & $venvCli --config $cfgFile status
        Write-Host 'If ActivityWatch shows as unreachable above, it may still be starting; re-run: usage-sync status'
    }
}
Write-Host 'done.'
