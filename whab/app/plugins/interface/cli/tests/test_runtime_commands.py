"""Offline recovery cannot use a profile, start a host or disclose journal data."""

import json

import pytest
from whab_plugin_cli import console, runtime_commands

from whab.plugins import JournalRecoveryError


@pytest.fixture
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Offline runtime recovery must not discover plugins, load profiles or connect.')
    monkeypatch.setattr(console, 'configuration', forbidden)
    monkeypatch.setattr(console, 'installed_catalog', forbidden)
    monkeypatch.setattr(console, 'execute', forbidden)
    calls = []
    result = {'status': 'preview', 'fingerprint': 'a' * 64, 'count': 1, 'journals': [
        {'path': 'visual-ui/fictional/visual-selection.json', 'kind': 'visual-selection.json',
         'action': 'bind-stable-volume-identity'}]}

    def recover(home, environment, *, apply):
        calls.append((home, environment, apply))
        return result
    monkeypatch.setattr(runtime_commands, 'recover_journal_locks', recover)
    return calls, result


@pytest.mark.parametrize('prefix,suffix', [
    (['--home', '/private/fictional', '--environment', 'sandbox'], []),
    ([], ['--home', '/private/fictional', '--environment', 'sandbox']),
])
def test_preview_bypasses_profiles_and_host_with_global_options_anywhere(offline, capsys, prefix, suffix):
    assert console.main([*prefix, 'runtime', 'recover-journal-locks', *suffix,
                         '--profile', '/nonexistent/invalid.budget', '--ca', '/nonexistent/ca']) == 0
    assert json.loads(capsys.readouterr().out) == offline[1]
    assert offline[0] == [('/private/fictional', 'sandbox', None)]


@pytest.mark.parametrize('arguments', [[], ['--home', '/private/fictional'], ['--environment', 'sandbox']])
def test_scope_must_be_explicit_even_with_environment_and_profile_defaults(offline, monkeypatch, capsys, arguments):
    monkeypatch.setenv('WHAB_HOME', '/private/default')
    monkeypatch.setenv('WHAB_ENV', 'sandbox')
    assert console.main(['runtime', 'recover-journal-locks', *arguments]) == 1
    assert 'explicit --home PATH and --environment' in json.loads(capsys.readouterr().err)['error']
    assert not offline[0]


def test_apply_forwards_exact_fingerprint_and_tables_remain_complete(offline, capsys):
    calls, result = offline
    base = ['runtime', 'recover-journal-locks', '--home', '/private/fictional', '--environment', 'production', '--format', 'table']
    assert console.main(base) == 0
    preview = capsys.readouterr().out
    assert 'Status: preview' in preview and result['fingerprint'] in preview
    assert all(value in preview for value in result['journals'][0].values())
    result.update(status='complete', repaired=1, backup='plugin-workers/journal-recovery/' + 'a' * 64)
    for _ in range(2):
        assert console.main([*base, '--apply', 'a' * 64]) == 0
        saved = capsys.readouterr().out
        assert 'Repaired: 1' in saved and result['backup'] in saved
    assert calls[1:] == [('/private/fictional', 'production', 'a' * 64)] * 2
    result.update(repaired=0, backup=None, count=0, journals=[])
    assert console.main([*base, '--apply', 'a' * 64]) == 0
    assert 'Backup: none' in capsys.readouterr().out


@pytest.mark.parametrize('code', ['busy', 'invalid_runtime', 'invalid_journal', 'conflict', 'unsupported', 'interrupted'])
def test_public_recovery_errors_are_actionable_and_safe(offline, monkeypatch, capsys, code):
    def refused(*args, **kwargs):
        raise JournalRecoveryError(code)
    monkeypatch.setattr(runtime_commands, 'recover_journal_locks', refused)
    assert console.main(['runtime', 'recover-journal-locks', '--home', '/private/fictional', '--environment', 'sandbox']) == 1
    assert json.loads(capsys.readouterr().err) == {'error': f'{code}: {JournalRecoveryError(code)}'}


def test_unexpected_recovery_errors_do_not_leak_payloads(offline, monkeypatch, capsys):
    def refused(*args, **kwargs):
        raise ValueError('secret journal body')
    monkeypatch.setattr(runtime_commands, 'recover_journal_locks', refused)
    assert console.main(['runtime', 'recover-journal-locks', '--home', '/private/fictional', '--environment', 'sandbox']) == 1
    assert 'secret journal body' not in capsys.readouterr().err


@pytest.mark.parametrize('arguments', [['runtime', '--help'], ['runtime', 'recover-journal-locks', '--help']])
def test_recovery_help_does_not_need_a_profile_runtime_or_plugins(offline, capsys, arguments):
    with pytest.raises(SystemExit) as outcome:
        console.main(arguments)
    assert outcome.value.code == 0
    output = capsys.readouterr().out
    assert 'explicit --home and --environment' in output and '--apply FINGERPRINT' in output
    assert 'private host ownership lock' in output and 'saved journals' in output
    assert not offline[0]
