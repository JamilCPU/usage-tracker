<#
.SYNOPSIS
  Install ActivityWatch and usage-sync for the current user (no admin needed).
.PARAMETER Device      Device name shipped with every event (default: lower-cased computer name).
.PARAMETER IngestUrl   Base URL of the ingest API.
.PARAMETER CaFile      Optional CA bundle that signed the ingest certificate.
.PARAMETER NodeSsh     Optional user@host; used only to mint the device token over SSH (never echoed).
.PARAMETER AwVersion   ActivityWatch tag, e.g. v0.13.2 (default: newest release with a Windows zip).
.PARAMETER RepoPath    Root of this repository (default: derived from the script location).
.PARAMETER Force       Overwrite an existing config.toml.
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
$FallbackAwVersion = 'v0.13.2'   # last Windows build in the 0.13 line

$awDir = Join-Path $env:LOCALAPPDATA 'Programs\activitywatch'
$venv = Join-Path $env:LOCALAPPDATA 'Programs\usage-sync\venv'
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
    foreach ($cand in @(@('py', '-3'), @('python'))) {
        $cmd = Get-Command $cand[0] -ErrorAction SilentlyContinue
        if ($cmd) { return , $cand }
    }
    throw 'Python 3 (py -3 or python) not found on PATH'
}

# 1. ActivityWatch
if ($DryRun) {
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
    Get-Process -Name 'aw-qt', 'aw-server', 'aw-watcher-afk', 'aw-watcher-window' -ErrorAction SilentlyContinue | Stop-Process -Force
    if (Test-Path $awDir) { Remove-Item -Recurse -Force $awDir }
    New-Item -ItemType Directory -Force -Path (Split-Path $awDir) | Out-Null
    Move-Item -Path $src -Destination $awDir
    Remove-Item -Recurse -Force $stage, $zip -ErrorAction SilentlyContinue
}

# 2. ActivityWatch logon task
Step "register logon task ActivityWatch -> $awDir\aw-qt.exe"
if (-not $DryRun) {
    $action = New-ScheduledTaskAction -Execute (Join-Path $awDir 'aw-qt.exe') -WorkingDirectory $awDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName 'ActivityWatch' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
}

# 3. venv + usage-sync
Step "create venv $venv and pip install $syncPkg"
if (-not $DryRun) {
    $py = Find-Python
    $pyArgs = @($py | Select-Object -Skip 1)
    & $py[0] @pyArgs -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
    & $venvPy -m pip install --quiet --upgrade $syncPkg
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
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
        Set-Content -Path $tokenFile -Value $token.Trim() -NoNewline
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

# 7. start + status
if ($NoStart) {
    Step 'start skipped (-NoStart)'
} else {
    Step 'start both tasks, wait 10 s, print usage-sync status'
    if (-not $DryRun) {
        Start-ScheduledTask -TaskName 'ActivityWatch'
        Start-ScheduledTask -TaskName 'usage-sync'
        Start-Sleep -Seconds 10
        & $venvCli --config $cfgFile status
    }
}
Write-Host 'done.'
