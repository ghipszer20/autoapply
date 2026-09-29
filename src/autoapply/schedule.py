"""Windows Task Scheduler entry: `pythonw.exe -m autoapply run` every 30 minutes.

Spike A (docs/spikes/2026-09-29-spikes.md): the action must be windowless pythonw.exe, never cmd.exe/python.exe,
or `claude -p` subprocesses die with 0xC000013A.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TASK = "autoapply"


def install_script(root: Path = ROOT, every_minutes: int = 30) -> str:
    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    return (
        f"$a = New-ScheduledTaskAction -Execute '{pythonw}' -Argument '-m autoapply run' -WorkingDirectory '{root}'; "
        f"$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) "
        f"-RepetitionInterval (New-TimeSpan -Minutes {every_minutes}); "
        "$s = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -MultipleInstances IgnoreNew "
        "-ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; "
        f"Register-ScheduledTask -TaskName '{TASK}' -Action $a -Trigger $t -Settings $s "
        "-Description 'autoapply: one bounded application pass (does nothing while autoapply is off)' -Force "
        "| Out-Null; 'installed'"
    )


def _ps(script: str) -> int:
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True)
    print((proc.stdout + proc.stderr).strip())
    return proc.returncode


def schedule_cmd(action: str) -> int:
    if action == "install":
        return _ps(install_script())
    if action == "remove":
        return _ps(f"Unregister-ScheduledTask -TaskName '{TASK}' -Confirm:$false; 'removed'")
    return _ps(f"$t = Get-ScheduledTask -TaskName '{TASK}'; $i = $t | Get-ScheduledTaskInfo; "
               "\"state: $($t.State)  last: $($i.LastRunTime) result: $($i.LastTaskResult)  next: $($i.NextRunTime)\"")
