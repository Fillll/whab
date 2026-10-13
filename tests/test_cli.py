"""The installed app exposes its WHAB command and single-source version."""

import subprocess
import sys

from whab import __version__


def test_whab_launch_command():
    result = subprocess.run(
        [sys.executable, "-m", "whab", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == f"WHAB {__version__}"
