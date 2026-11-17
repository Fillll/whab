"""The installed console reaches offline SDK recovery without starting a host."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_installed_runtime_console_ignores_profile_and_default_environment(tmp_path):
    home = tmp_path / 'fictional runtime'
    home.mkdir(mode=0o700)
    default = tmp_path / 'must-not-create'
    profile = tmp_path / 'invalid.budget'
    profile.write_text('not a profile')
    environment = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'LANG', 'LC_ALL') if key in os.environ}
    environment.update(HOME=str(tmp_path), WHAB_HOME=str(default), WHAB_ENV='production')
    executable = Path(sys.executable).parent / 'whab'
    base = [str(executable), 'runtime', 'recover-journal-locks', '--home', str(home),
            '--environment', 'sandbox', '--profile', str(profile)]

    def run(*arguments):
        result = subprocess.run([*base, *arguments], env=environment, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    preview = run()
    assert preview['status'] == 'preview' and preview['count'] == 0 and preview['journals'] == []
    saved = run('--apply', preview['fingerprint'])
    assert saved == {**preview, 'status': 'complete', 'repaired': 0, 'backup': None}
    assert run('--apply', preview['fingerprint']) == saved
    assert not default.exists()
    assert {str(path.relative_to(home)) for path in home.rglob('*')} == {
        'run', 'run/sandbox', 'run/sandbox/host.lock'}
