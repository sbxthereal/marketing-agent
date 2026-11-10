[CmdletBinding(SupportsShouldProcess = $true)]
param([string]$TaskName = 'MeDo-Reddit-Agent', [switch]$StartNow, [switch]$Uninstall)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Run this script on Windows.' }
$PilotRoot = Split-Path -Parent $PSScriptRoot
$PilotPython = Join-Path $PilotRoot '.venv\Scripts\python.exe'
$PilotRunner = Join-Path $PSScriptRoot 'runner.py'
if ($Uninstall) {
    if ($StartNow) { throw 'Choose either StartNow or Uninstall.' }
    if ($PSCmdlet.ShouldProcess($TaskName, 'Stop and unregister the pilot task')) {
        $PilotTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($PilotTask) {
            if ($PilotTask.Actions.Execute -ne $PilotPython) { throw 'This task belongs to a different installation; inspect it before removing it.' }
            Stop-ScheduledTask -TaskName $TaskName
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
    }
    return
}
if (-not (Test-Path -LiteralPath $PilotPython)) { throw 'Run windows\setup.ps1 first.' }
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    throw 'A task with this name already exists. Use -Uninstall first, or choose another TaskName.'
}
$PilotUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$PilotAction = New-ScheduledTaskAction -Execute $PilotPython -Argument ('-X utf8 -u "' + $PilotRunner + '"') -WorkingDirectory $PilotRoot
$PilotTrigger = New-ScheduledTaskTrigger -AtLogOn -User $PilotUser
$PilotPrincipal = New-ScheduledTaskPrincipal -UserId $PilotUser -LogonType Interactive -RunLevel Limited
$PilotSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
if ($PSCmdlet.ShouldProcess($TaskName, 'Register a worker task for the current user at logon')) {
    Register-ScheduledTask -TaskName $TaskName -Action $PilotAction -Trigger $PilotTrigger -Principal $PilotPrincipal -Settings $PilotSettings -Description 'MeDo Reddit worker; OneAPI runtime, explicit review, persistent cooldowns.' | Out-Null
    if ($StartNow) { Start-ScheduledTask -TaskName $TaskName }
    Write-Host "Registered $TaskName. Logs: $PilotRoot\runtime\worker.log"
    Write-Host 'This task runs in this user session. Disconnecting RDP is different from signing out. After reboot, sign in again.'
}
