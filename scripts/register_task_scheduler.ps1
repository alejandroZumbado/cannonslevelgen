# Registers a Windows Task Scheduler task that runs one learning cycle every
# 15 minutes, all day, every day — this is what actually spends the month-1
# token budget. NOT run automatically by anything else in this project: run
# it yourself, once, when you're ready to start the learning phase for real.
#
# Runs fully hidden (via a generated scripts/run_hidden.vbs wrapper) — a
# cmd.exe window popping up on your screen every 15 minutes was the original
# version of this script; wscript.exe launching the actual work with window
# style 0 avoids that entirely, unlike "cmd /c ..." as the direct action.
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
$vbsPath = Join-Path $projectRoot "scripts\run_hidden.vbs"

# Build the same "cmd /c ...">> redirect command that worked when it was the
# direct scheduled action, then hand it to WshShell.Run with windowStyle=0
# (hidden) instead of executing it directly — that's what actually suppresses
# the console window; cmd.exe itself has no "run silently" flag of its own.
$cmdLine = "cmd.exe /c `"`"$python`" `"$script`" >> `"$logFile`" 2>&1`""
$vbsEscaped = $cmdLine.Replace('"', '""')
$vbsContent = "Set WshShell = CreateObject(`"WScript.Shell`")`r`nWshShell.Run `"$vbsEscaped`", 0, True`r`n"
Set-Content -Path $vbsPath -Value $vbsContent -Encoding ASCII

$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$vbsPath`"" -WorkingDirectory $projectRoot
# Task Scheduler's XML schema rejects [TimeSpan]::MaxValue (duration out of
# range) — 10 years comfortably covers a month-long run without hitting that.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
# AllowStartIfOnBatteries / DontStopIfGoingOnBatteries: this is meant to run
# unattended for months, including on a laptop that's sometimes unplugged —
# without these it silently stops (no error, just skipped runs) whenever the
# machine isn't on AC power, which could go unnoticed for a long time.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName "CannonsLevelGen-LearningCycle" `
    -Action $action -Trigger $trigger -Settings $settings -Force `
    -Description "Runs one Cannons AI learning cycle (strategy + level design) every 15 min, hidden (no window). See CannonsLevelGen/README.md."

Write-Host "Registered (hidden - no window will appear). Check progress with: Get-ScheduledTaskInfo -TaskName 'CannonsLevelGen-LearningCycle'"
Write-Host "Logs at: $logFile"
