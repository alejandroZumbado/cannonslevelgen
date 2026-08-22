# Registers a Windows Task Scheduler task that runs one learning cycle every
# 15 minutes, all day, every day — this is what actually spends the month-1
# token budget. NOT run automatically by anything else in this project: run
# it yourself, once, when you're ready to start the learning phase for real.
#
# Usage (from an elevated or normal PowerShell prompt):
#   .\scripts\register_task_scheduler.ps1
#
# To stop the month-long run later:
#   Unregister-ScheduledTask -TaskName "CannonsLevelGen-LearningCycle" -Confirm:$false

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python).Source
$script = Join-Path $projectRoot "run_learning_cycle.py"
$logFile = Join-Path $projectRoot "state\task_scheduler.log"

$action = New-ScheduledTaskAction -Execute $python -Argument "`"$script`" >> `"$logFile`" 2>&1" -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration ([TimeSpan]::MaxValue)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName "CannonsLevelGen-LearningCycle" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Runs one Cannons AI learning cycle (strategy + level design) every 15 min. See CannonsLevelGen/README.md."

Write-Host "Registered. Check progress with: Get-ScheduledTaskInfo -TaskName 'CannonsLevelGen-LearningCycle'"
Write-Host "Logs at: $logFile"
