Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd.exe /c """"C:\Python313\python.exe"" ""E:\Users\Alejandro\Opal\CannonsLevelGen\run_learning_cycle.py"" >> ""E:\Users\Alejandro\Opal\CannonsLevelGen\state\task_scheduler.log"" 2>&1""", 0, True

