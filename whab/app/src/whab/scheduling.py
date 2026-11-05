"""macOS user scheduling and a provider-neutral due-job entry point."""

import hashlib
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from whab.budgets.document import atomic_write


def configure_schedule(home, environment, *, enabled):
    from whab.hosting import runtime_directory

    directory = runtime_directory(home, environment)
    if sys.platform != "darwin":
        raise OSError("Automatic OS scheduling currently supports macOS. Schedule python -m whab --run-jobs on this platform.")
    home = Path(home).expanduser().resolve()
    label = "com.whab.jobs." + hashlib.sha256((str(home) + ":" + environment).encode()).hexdigest()[:20]
    path = Path.home() / "Library" / "LaunchAgents" / (label + ".plist")
    domain = f"gui/{os.getuid()}"
    arguments = [sys.executable, "-m", "whab", "--home", str(home), "--environment", environment, "--run-jobs"]
    if enabled:
        atomic_write(path, plistlib.dumps({"Label": label, "ProgramArguments": arguments,
            "StartCalendarInterval": {"Minute": 0}, "RunAtLoad": True,
            "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null"}))
    subprocess.run(["launchctl", "bootout", domain + "/" + label], capture_output=True, check=False)
    if enabled:
        result = subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True, check=False)
        if result.returncode:
            raise OSError("The OS job could not be registered. Inspect the user LaunchAgent before retrying.")
    else:
        path.unlink(missing_ok=True)
    record = {"enabled": enabled, "platform": "launchd", "interval_seconds": 3600}
    atomic_write(directory / "schedule.json", json.dumps(record).encode())
    return record


def run_due_jobs(home, environment):
    from whab.hosting import local_connection

    with local_connection(home, environment) as client:
        response = client.get("/api/local/v1/job-targets")
        response.raise_for_status()
        failed = False
        for identifier in response.json()["budgets"]:
            response = client.post("/budgets/" + quote(identifier, safe="") + "/api/local/v1/jobs/run", json={})
            failed |= response.status_code != 200
        if failed:
            raise OSError("One or more budgets could not complete their scheduled work. Inspect job status.")
