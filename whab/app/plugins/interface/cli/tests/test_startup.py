"""Fresh-process help remains complete without loading unrelated execution code."""

import contextlib
import io
import json
import os
import subprocess
import sys

import pytest
from whab_plugin_cli import console


@pytest.mark.parametrize('action', [
    'non-reviewed', 'unreviewed', 'uncategorized', 'uncategorised', 'list',
    'categories', 'categorize', 'categorise', 'note', 'edit', 'create', 'review',
    'review-audit', 'edit-many', 'transfers', 'match-transfer', 'unmatch-transfer',
])
def test_selected_transaction_help_matches_full_parser(action):
    arguments = ['BUDGET', 'Fictional budget', 'trnx', action, '--help']
    outputs = []
    for selected in ((), arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), pytest.raises(SystemExit) as exited:
            console.parser(console.options_parser(), selected).parse_args(arguments)
        assert exited.value.code == 0
        outputs.append(output.getvalue())
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize('arguments,unrelated', [
    (['--help'], []),
    (['Fictional budget', 'trnx', '--help'], []),
    (['Fictional budget', 'trnx', 'list', '--help'], [
        'whab_plugin_cli.bulk_commands', 'whab_plugin_cli.record_edits',
        'whab_plugin_cli.transfer_commands', 'whab_plugin_cli.transaction_changes',
        'whab_plugin_cli.review_commands', 'whab.domain.formatting',
    ]),
    (['Fictional budget', 'trnx', 'categorize', '--help'], [
        'whab_plugin_cli.record_edits', 'whab_plugin_cli.transfer_commands',
        'whab_plugin_cli.transaction_changes', 'whab_plugin_cli.review_commands',
        'whab_plugin_cli.account_commands', 'whab.domain.bulk',
    ]),
])
def test_fresh_help_defers_transport_and_unrelated_commands(tmp_path, arguments, unrelated):
    code = '''
import contextlib, io, json, sys
from whab_plugin_cli.console import main
with contextlib.redirect_stdout(io.StringIO()):
    try:
        main(json.loads(sys.argv[1]))
    except SystemExit as outcome:
        assert outcome.code == 0
print(json.dumps(sorted(sys.modules)))
'''
    environment = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'LANG', 'LC_ALL') if key in os.environ}
    environment.update(HOME=str(tmp_path), WHAB_HOME=str(tmp_path / 'absent-runtime'), WHAB_ENV='sandbox')
    result = subprocess.run([sys.executable, '-c', code, json.dumps(arguments)],
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    modules = json.loads(result.stdout)
    assert not any(name in modules for name in ['httpx', 'whab.plugins.local', *unrelated])
    assert not (tmp_path / 'absent-runtime').exists()


def test_unknown_transaction_action_retains_complete_discovery(capsys):
    arguments = ['BUDGET', 'Fictional budget', 'trnx', 'unknown-action']
    with pytest.raises(SystemExit) as exited:
        console.parser(console.options_parser(), arguments).parse_args(arguments)
    assert exited.value.code == 2
    diagnostic = capsys.readouterr().err
    for action in ('list', 'categorize', 'edit-many', 'note', 'review', 'match-transfer'):
        assert action in diagnostic
