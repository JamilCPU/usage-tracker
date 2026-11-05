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
    # Order: -PythonPath, uv-managed/known interpreters (3.11-3.13), py -3, python.
    # Microsoft Store Python is rejected: it virtualizes %LOCALAPPDATA%, so the shim could not
    # see the config, token and CA that this script writes there.
    $problems = @()
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    $tried = 0
    foreach ($round in 1, 2) {
        $cands = @()
        if ($PythonPath) { $cands += , @($PythonPath) }
        if ($uv) {
            $found = @(& $uv.Source python find '>=3.11,<3.14' 2>$null)
            if ($LASTEXITCODE -eq 0 -and $found.Count -gt 0 -and $found[0]) { $cands += , @("$($found[0])".Trim()) }
        }
        if ((Get-Command py -ErrorAction SilentlyContinue)) { $cands += , @('py', '-3') }
        if ((Get-Command python -ErrorAction SilentlyContinue)) { $cands += , @('python') }
        foreach ($cand in $cands) {
            $exe = $cand[0]
            $rest = @($cand | Select-Object -Skip 1)
            $label = ($cand -join ' ')
            $lines = @()
            try {
                $lines = @(& $exe @rest -c 'import sys; print(sys.version_info[:2] >= (3,11)); print(sys.base_prefix)' 2>$null)
            } catch {
                $problems += "$label failed to run"
                continue
            }
            if ($LASTEXITCODE -ne 0 -or $lines.Count -lt 2) {
                $problems += "$label is not a working Python (Microsoft Store alias?)"
                continue
            }
            $prefix = "$($lines[1])"
            $resolved = (Get-Command $exe -ErrorAction SilentlyContinue).Source
            if ($prefix -like '*\WindowsApps\*' -or $resolved -like '*\WindowsApps\*') {
                $problems += "$label is Microsoft Store Python (virtualizes AppData, so the shim could not read its config/token)"
                continue
            }
            if ("$($lines[0])" -ne 'True') {
                $problems += "$label is older than Python 3.11"
                continue
            }
            return , (@($exe) + $rest)
        }
        if ($round -eq 1 -and $uv -and -not $DryRun) {
            Write-Host 'no suitable Python found; installing Python 3.12 with uv (user-level)'
            & $uv.Source python install 3.12
            continue
        }
        break
    }
    $detail = $problems -join '; '
    throw "A real Python 3.11-3.13 is required (not Microsoft Store Python). Either install Python from python.org (and re-run), install uv (https://docs.astral.sh/uv/) so this script can fetch one, or pass -PythonPath. $detail"
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
    foreach ($ignored in 'IngestUrl', 'CaFile', 'IngestConnectHost', 'Device') {
        if ($PSBoundParameters.ContainsKey($ignored)) {
            Write-Warning "-$ignored was ignored because $cfgFile already exists; use -Force to rewrite it"
        }
    }
} else {
    Step "write $cfgFile via usage-sync init (device=$Device, ingest_url=$IngestUrl)"
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
        Start-Sleep -Seconds 20
        & $venvCli --config $cfgFile status
        Write-Host 'If ActivityWatch shows as unreachable above, it may still be starting; re-run: usage-sync status'
    }
}
Write-Host 'done.'
