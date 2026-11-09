"""Every packaged CLI example must resolve to a supported, bounded public route."""

import argparse
import io
import json
import shlex

import pytest
from whab_plugin_cli import client, console
from whab_plugin_cli import help as help_module

from whab.plugins.documentation import installed_catalog


def test_all_packaged_operations_render_and_public_templates_parse():
    catalog = installed_catalog()
    documents = [catalog['core'], *[p['documentation'] for p in catalog['plugins'] if p['documented']]]
    covered = set()
    for document in documents:
        for row in document['operations']:
            text = help_module.render_operation(row, document['plugin_id'])
            assert row['summary'] in text and row['availability'] in text
            assert 'Retry:' in text and 'Side effects:' in text
            template = help_module.invocation(row, document['plugin_id'])
            if 'CLI template' not in template:
                assert 'not a generic CLI operation' in template
                continue
            argv = shlex.split(template.splitlines()[1])
            assert argv[:2] == ['whab', 'api']
            # Parse and build each complete intent without a runtime or credentials.
            args = client.parser(argv[2:]).parse_args(argv[2:])
            args.url = 'https://localhost'
            _, method, path, body = client.request(args, 'x' * 32)
            covered.add(row['kind'])
            assert path.startswith('/remote/v1/budgets/BUDGET_ID/')
            if row['kind'] in {'command', 'feature-command'}:
                assert method == 'POST' and body['json']['expected_revision'] == 'OBSERVED_REVISION'
            if row['name'].startswith('categorization.'):
                assert body['json']['operation'] == 'categorization'
                assert body['json']['payload']['action'] == row['name'].split('.')[1]
    assert covered == {'query', 'command', 'feature-query', 'feature-command'}
    # Unknown plugin commands must never acquire an invented core route.
    row = dict(catalog['core']['operations'][0], kind='command', name='unknown')
    assert 'not a generic CLI operation' in help_module.invocation(row, 'third-party')


def test_every_parser_help_is_complete_without_a_runtime():
    def walk(parser):
        assert parser.format_help().startswith('usage:')
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    walk(child)
            else:
                assert action.help, f'Missing help for {parser.prog}: {action.dest}'
    walk(console.parser(console.options_parser()))
    walk(client.parser())


@pytest.mark.parametrize('operation,parameters', [
    ('history', {'limit': 1, 'before': 'cursor'}),
    ('history_preview', {'action': 'snapshot', 'target': None}),
])
def test_parameterized_history_reads_preserve_fields_and_stdin(operation, parameters, monkeypatch):
    monkeypatch.setattr(client.sys, 'stdin', io.StringIO(json.dumps(parameters)))
    args = client.parser().parse_args(['--url', 'https://localhost', '--budget', 'default',
                                     'query', operation, '--parameters', '-'])
    _, method, path, body = client.request(args, 'x' * 32)
    assert method == 'POST' and path.endswith('/queries')
    assert body == {'json': {'operation': operation, 'workspace': 'personal', 'parameters': parameters}}


@pytest.mark.parametrize('arguments,message', [
    (['query', 'accounts', '--parameters', '{}'], 'supported by query'),
    (['query', 'history', '--parameters', '{}', '--month', '2026-11'], 'not both'),
])
def test_unsupported_parameter_combinations_fail_before_transport(arguments, message):
    args = client.parser().parse_args(['--url', 'https://localhost', '--budget', 'default', *arguments])
    with pytest.raises(client.ConfigError, match=message):
        client.request(args, 'x' * 32)


def test_human_schema_constraints_include_nullable_and_exact_values():
    schema = {'properties': {'token': {'type': ['string', 'null'], 'description': 'Opaque value.',
                                      'const': 'example', 'minLength': 1, 'maxLength': 64},
                             'count': {'type': 'integer', 'description': 'Page size.', 'minimum': 1, 'maximum': 100}}}
    text = '\n'.join(help_module._fields(schema))
    assert 'string | null' in text and 'const: "example"' in text
    assert 'minLength: 1' in text and 'maxLength: 64' in text
    assert 'minimum: 1' in text and 'maximum: 100' in text
