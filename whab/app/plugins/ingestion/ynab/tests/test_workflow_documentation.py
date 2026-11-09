"""The packaged migration guide owns the ordered decisions and safety limits."""

import re

from whab.plugins.documentation import operation, plugin_document


def test_packaged_guide_covers_stages_and_keeps_sdk_callback_separate():
    document = plugin_document('ynab')
    guide = operation(document, 'local', 'import.workflow')
    assert operation(document, 'sdk', 'FileImport.parse')['availability'].startswith('SDK callback only')
    assert not operation(document, 'command', 'FileImport.parse')
    assert re.findall(r'^([1-8])\. ', guide['summary'], re.MULTILINE) == list('12345678')
    for meaning in ['not one script', 'resume from its actual state', 'canImport', 'alreadyImported',
                    'same reviewed fingerprint', 'Do not infer', 'off-budget', 'ALL AVAILABLE HISTORY',
                    'latestDate', 'other budgets', 'separate writes', 'NOT automatically matched',
                    'FILTERED lists', 'unverified estimates', 'rollover', '--every-hours 6',
                    '21600', 'Hourly launchd wakeups', 'runtime home', 'does not change saved budget cadences']:
        assert meaning in guide['summary']
    assert guide['input']['additionalProperties'] is False
    assert 'Documentation topic only' in guide['availability']
