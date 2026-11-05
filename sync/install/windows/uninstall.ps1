<#
.SYNOPSIS
  Stop and unregister the ActivityWatch and usage-sync scheduled tasks. Data, config and token are kept.
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
foreach ($name in 'usage-sync', 'ActivityWatch') {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($task) {
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "removed task $name"
    } else {
        Write-Host "task $name not present"
    }
}
# only processes that run from our own install directories
$dirs = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\usage-sync'),
    (Join-Path $env:LOCALAPPDATA 'Programs\activitywatch')
)
foreach ($d in $dirs) {
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($d + '\', [StringComparison]::OrdinalIgnoreCase) } |
        Stop-Process -Force
}
Write-Host 'Installed files, config, token and ActivityWatch data were left in place.'
