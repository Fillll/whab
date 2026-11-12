"""Small guarded record edits; native persistence and audit stay behind public APIs."""

import argparse
import json
from copy import deepcopy
from uuid import uuid4

import httpx

from whab.plugins import validate_category_agent

from .transaction_commands import checked
from .transaction_reads import terminal_text

RECOVERY = ('A fresh revision and unique request ID are handled internally. No automatic retries. '
            'For an exact retry retain --revision, --request-id, original text/name and actor/batch metadata. '
            'Expired receipts with an old revision fail safely. After an uncertain response inspect the record and history; '
            'never replay with a new request ID or revision. --agent labels audit history only; it does not change category attribution.')


def edit_options(parser):
    parser.add_argument('--agent', type=validate_category_agent, help='Explicit self-declared agent audit label; default: manual User')
    parser.add_argument('--batch', help='Group permanent audit entries under this batch ID; separate commands remain separate commits')
    parser.add_argument('--revision', help='Original observed revision for recovery; normally read automatically')
    parser.add_argument('--request-id', help='Original unique intent ID for recovery; normally generated and returned')
    parser.add_argument('--format', choices=('json', 'table'), default='json', help='Verified output format (default: json); JSON preserves exact fields/text')


def add_rename_parser(actions):
    parser = actions.add_parser('rename', help='Rename an existing child category without replacing it',
        description='Change only the category name. Preserve ID, parent, currency, target, transactions, assignments and permanent history.',
        epilog='Example (fictional):\n  whab "My budget" category rename "House / Rent" --name "Main Residence" --agent Codex --batch housing-cleanup\n\n'
               'CATEGORY accepts a stable ID, unique exact name or "Group / Category". Name lookup ignores case/outer whitespace, never uses fuzzy matching and rejects ambiguity. '
               'New names are trimmed; duplicate sibling names (ignoring case/outer whitespace) are refused. Parents are not renamed. '
               'After renaming use the returned stable ID for recovery, because the old name/path no longer identifies the child. ' + RECOVERY,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('category', help='Existing child ID, exact unique name, or Group / Category; quote spaces/emoji/non-Latin text')
    parser.add_argument('--name', required=True, help='New child name; keeps the existing parent and all financial references')
    edit_options(parser)


def add_note_parser(actions):
    parser = actions.add_parser('note', help='Append text to one transaction memo without replacing existing text',
        description='Append exactly one newline when the saved note is nonempty, then the supplied text verbatim. Preserve imported YNAB notes and every unrelated field, including merchant whitespace and review/category attribution.',
        epilog='Examples (fictional):\n  whab "My budget" trnx note TRANSACTION_ID --append "Bicycle for Alex"\n  whab "My budget" trnx note TRANSACTION_ID --append "User-supplied wording" --agent Codex --batch memo-cleanup\n\n'
               'Use a stable ID from trnx list; no merchant/name inference. Empty text, missing/deleted transactions and a final memo over 1000 characters are refused without mutation. '
               'An existing trailing newline is preserved: the command still adds one newline. No text is trimmed, normalized or deduplicated by content. '
               'A new intent can deliberately append the same text again; an exact successful request replay cannot. ' + RECOVERY,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('transaction_id', help='Stable nondeleted transaction ID from trnx list')
    parser.add_argument('--append', required=True, help='Nonempty text to append exactly, including whitespace; final note limit: 1000 characters')
    edit_options(parser)


def intent(args, observed, operation, payload):
    history = {'actor': args.agent, 'actorKind': 'agent'} if args.agent is not None else {}
    if args.batch is not None:
        history['batchId'] = args.batch
    return {'operation': operation, 'workspace': 'personal', 'payload': payload,
            'expected_revision': args.revision or observed['revision'], 'request_id': args.request_id or str(uuid4()),
            **({'history': history} if history else {})}


def saved_edit(connection, identifier, prefix, args, exchange, error, original, observed, read, verify):
    try:
        saved = checked(exchange(connection, 'POST', prefix + '/commands', ledger_errors=True, json=original), identifier, error)
        verified = read()
        replay = args.revision is not None and args.revision != observed['revision']
        expected_revision = observed['revision'] if replay else saved['revision']
        if saved['data'] is not None or verified['revision'] != expected_revision:
            raise error('The saved revision/result could not be verified.')
        record = verify(verified)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, httpx.HTTPError) as failure:
        refused = getattr(failure, 'status_code', None) in {400, 409}
        raise error(('Record edit refused; no new edit was saved. ' if refused else 'Record edit was not confirmed; it may have been saved. ')
                    + (str(failure) + '. ' if refused else '')
                    + 'No automatic retry. Inspect category list or trnx list and history. Retain budgetId '
                    + identifier + ' and the exact original intent: ' + json.dumps(original) + '.') from failure
    return {**verified, 'budget': args.target, 'data': record,
            'requestId': original['request_id'], 'expectedRevision': original['expected_revision'],
            'savedRevision': saved['revision'], 'replayed': replay}


def execute_rename(connection, identifier, prefix, args, exchange, error, observed, read, selected):
    expected = deepcopy(observed['data'])
    siblings = next(group['children'] for group in expected if any(row['id'] == selected['id'] for row in group['children']))
    if any(row['id'] != selected['id'] and row['name'].strip().casefold() == args.name.strip().casefold() for row in siblings):
        raise error('A category with this name already exists under this parent; nothing was renamed.')
    actual = next(row for row in siblings if row['id'] == selected['id'])
    actual['name'] = args.name.strip()
    original = intent(args, observed, 'rename_category', {'categoryId': selected['id'], 'name': args.name})

    def verify(verified):
        if verified['data'] != expected:
            raise error('Saved category hierarchy differs from the requested name-only edit.')
        return actual

    return saved_edit(connection, identifier, prefix, args, exchange, error, original, observed, read, verify)


def execute_note(connection, identifier, prefix, args, exchange, error):
    def read():
        return checked(exchange(connection, 'GET', prefix + '/queries/transactions', params={'workspace': 'personal'}), identifier, error)

    observed = read()
    selected = next((row for row in observed['data'] if row['id'] == args.transaction_id), None)
    if selected is None:
        raise error('Transaction not found or deleted. Inspect trnx list in the addressed budget and use its stable ID.')
    if type(selected.get('note')) is not str:
        raise error('WHAB returned an invalid saved memo; no append was attempted.')
    replay = args.revision is not None and args.revision != observed['revision']
    expected_note = selected['note'] if replay else selected['note'] + ('\n' if selected['note'] else '') + args.append
    original = intent(args, observed, 'append_transaction_note', {'transactionId': selected['id'], 'text': args.append})

    def verify(verified):
        actual = next((row for row in verified['data'] if row['id'] == selected['id']), None)
        if actual != {**selected, 'note': expected_note}:
            raise error('Saved transaction differs from the requested note-only edit.')
        return actual

    return saved_edit(connection, identifier, prefix, args, exchange, error, original, observed, read, verify)


def note_table(result):
    return (f"Budget: {terminal_text(result['budget'], 100)} ({terminal_text(result['budgetId'], 200)})\n"
            f"Transaction: {terminal_text(result['data']['id'], 200)}\nMemo: "
            + json.dumps(result['data']['note'], ensure_ascii=True) + '\nSaved and verified.')
