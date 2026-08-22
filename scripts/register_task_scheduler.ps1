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

# cmd.exe /c is required for ">>" redirection to actually work — Task
# Scheduler calls CreateProcess directly on $python, it does not go through a
# shell, so passing ">>" straight to python.exe would just be a literal (and
# meaningless) argument to python, not a redirect.
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"`"$python`" `"$script`" >> `"$logFile`" 2>&1`"" -WorkingDirectory $projectRoot
# Task Scheduler's XML schema rejects [TimeSpan]::MaxValue (duration out of
# range) — 10 years comfortably covers a month-long run without hitting that.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName "CannonsLevelGen-LearningCycle" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Runs one Cannons AI learning cycle (strategy + level design) every 15 min. See CannonsLevelGen/README.md."

Write-Host "Registered. Check progress with: Get-ScheduledTaskInfo -TaskName 'CannonsLevelGen-LearningCycle'"
Write-Host "Logs at: $logFile"
