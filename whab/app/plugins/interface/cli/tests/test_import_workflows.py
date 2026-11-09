"""Importer-owned help stays offline, truthful and executable stage by stage."""

import copy
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from whab_plugin_cli import client, config, console
from whab_plugin_cli import help as help_module
from whab_plugin_cli.bank_commands import bank_request

from whab.plugins.documentation import installed_catalog, operation


def workflow():
    document = next(row['documentation'] for row in installed_catalog()['plugins'] if row['id'] == 'ynab')
    return operation(document, 'local', 'import.workflow')['summary']


@pytest.mark.parametrize('main,arguments', [
    (console.main, ['migration-example', 'import', 'ynab-zip-importer', '--help']),
    (console.main, ['migration-example', 'import', 'ynab', 'preview', '--help']),
    (console.main, ['migration-example', 'import', 'ynab', 'commit', '--help']),
    (console.main, ['api', 'describe', '--plugin', 'ynab-zip-importer', '--help']),
    (client.main, ['describe', '--plugin', 'ynab', '--operation', 'import.workflow', '--help']),
])
def test_each_help_entry_point_renders_the_same_ordered_guide_offline(main, arguments, monkeypatch, capsys):
    monkeypatch.setattr(console, 'configuration', lambda *a: pytest.fail('Help cannot read profiles'))
    monkeypatch.setattr(config, '_json_defaults', lambda *a: pytest.fail('Help cannot parse profiles'))
    monkeypatch.setattr(config, '_legacy_defaults', lambda *a: pytest.fail('Help cannot parse profiles'))
    monkeypatch.setattr(console, 'local_connection', lambda *a, **k: pytest.fail('Help cannot acquire a host'))
    monkeypatch.setattr(client.httpx, 'Client', lambda **k: pytest.fail('Help cannot connect'))
    with pytest.raises(SystemExit) as result:
        main(arguments)
    assert result.value.code == 0
    text = capsys.readouterr().out
    assert workflow() in text
    if '--plugin' in arguments and '--operation' not in arguments:
        assert 'sdk FileImport.parse' in text and 'SDK callback only' in text
        assert 'command FileImport.parse' not in text


def test_generic_import_help_discovers_topics_without_claiming_an_unknown_importer_supports_ynab(capsys):
    for args in [['migration-example', 'import', '--help'], ['migration-example', 'import', 'unknown-importer', '--help']]:
        with pytest.raises(SystemExit):
            console.main(args)
        text = capsys.readouterr().out
        assert 'Installed import workflows' in text
        assert 'whab BUDGET import ynab-zip-importer --help' in text
        assert '1. Create or select' not in text


def test_workflow_discovery_handles_legacy_sdk_only_and_other_importers():
    catalog = copy.deepcopy(installed_catalog())
    for plugin in catalog['plugins']:
        if plugin['documentation']:
            plugin['documentation']['operations'] = [row for row in plugin['documentation']['operations'] if row['name'] != 'import.workflow']
    catalog['plugins'].append({'id': 'legacy', 'documentation': None})
    assert 'No installed importer workflow' in help_module.import_help(['import'], catalog)
    catalog['plugins'].append({'id': 'fictional-importer', 'documentation': {'operations': [
        {'name': 'import.workflow', 'kind': 'local', 'summary': 'Fictional external workflow'}]}})
    assert help_module.import_help(['import', 'fictional-importer'], catalog) == 'Fictional external workflow'
    assert 'whab BUDGET import fictional-importer --help' in help_module.import_help([], catalog)


def test_every_workflow_command_parses_and_bank_intents_preserve_explicit_choices():
    guide = workflow()
    commands = [shlex.split(line.strip()) for line in guide.splitlines() if line.startswith('  whab ')]
    assert len(commands) >= 30
    intents = []
    for command in commands:
        assert command.pop(0) == 'whab'
        assert '--home' not in command and '--environment' not in command
        if command[0] != 'budget':
            command.insert(0, 'BUDGET')
        # All examples are complete, including JSON; no host/HTTP is involved.
        parser = console.parser(console.options_parser(), command)
        if '--help' in command:
            with pytest.raises(SystemExit) as result:
                parser.parse_args(command)
            assert result.value.code == 0
            continue
        args = parser.parse_args(command)
        if getattr(args, 'command', None) == 'bank':
            intents.append(bank_request(args, 'fictional-budget'))
    configs = [body['json'] for method, path, body in intents if path.endswith('/sync-settings')]
    assert {row['accounts'][0]['since'] for row in configs} == {'2026-01-01', None}
    assert all(row['accounts'][0]['accountId'] == 'WHAB_ACCOUNT_ID' for row in configs)
    assert any(method == 'PUT' and path.endswith('/schedule') and body['json']['interval_seconds'] == 21600
               for method, path, body in intents)
    assert any(path.endswith('/schedule/background') and body['json']['enabled'] for _, path, body in intents)
    assert not any(method == 'DELETE' for method, _, _ in intents)
    assert not any('--every-hours 1' in line or '--every-hours 3' in line for line in guide.splitlines() if line.startswith('  whab '))


def test_machine_readable_discovery_keeps_workflow_and_sdk_boundaries(capsys):
    assert console.main(['api', 'describe', '--offline', '--plugin', 'ynab-zip-importer', '--json']) == 0
    document = json.loads(capsys.readouterr().out)['plugins'][0]['documentation']
    assert operation(document, 'local', 'import.workflow')['summary'] == workflow()
    assert operation(document, 'sdk', 'FileImport.parse') is not None
    assert not any(row['kind'] in {'command', 'query'} for row in document['operations'])


@pytest.mark.parametrize('script,arguments', [
    ('whab', ['--profile', '/missing/fictional.budget', 'migration-example', 'import', 'ynab', '--help']),
    ('whab', ['migration-example', 'import', 'ynab-zip-importer', 'preview', '--help']),
    ('whab', ['api', 'describe', '--plugin', 'ynab-zip-importer', '--help']),
    ('whab-cli', ['describe', '--plugin', 'ynab-zip-importer', '--help']),
])
def test_actual_console_scripts_display_help_without_runtime_or_profile(tmp_path, script, arguments):
    executable = Path(sys.executable).parent / script
    env = {**os.environ, 'HOME': str(tmp_path), 'WHAB_HOME': str(tmp_path / 'unopened-runtime')}
    result = subprocess.run([str(executable), *arguments], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert workflow() in result.stdout
    assert not (tmp_path / 'unopened-runtime').exists()
