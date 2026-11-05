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
Get-Process -Name 'pythonw', 'aw-qt', 'aw-server', 'aw-watcher-afk', 'aw-watcher-window' -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like "$env:LOCALAPPDATA\Programs\*" } |
    Stop-Process -Force
Write-Host 'Installed files, config, token and ActivityWatch data were left in place.'
