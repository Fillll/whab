"""Offline runtime maintenance through the public SDK, before host connection."""

import argparse

lazy from whab.plugins import JournalRecoveryError, recover_journal_locks

RUNTIME_HELP = """Recover a stopped runtime after a macOS mount identity change:
  whab runtime recover-journal-locks --home /private/fictional-runtime --environment sandbox
  whab runtime recover-journal-locks --home /private/fictional-runtime --environment sandbox --apply FINGERPRINT

Without --apply, preview the exact journal paths and fingerprint without changing
saved journals. Preview may create the private host ownership lock to exclude a
concurrent startup. Stop WHAB and all CLI jobs first. Review the preview, then use its
exact fingerprint to back up and repair only legacy journal lock identities.
Runtime maintenance requires explicit --home and --environment, even when a
profile or WHAB_HOME is configured. It never connects to or starts the host.
"""

RECOVERY_HELP = RUNTIME_HELP + """
This handles legacy macOS device numbers changing across reboot/remount. It does
not repair ledger data, reset journals, clear uncertain writes or retry financial
operations. Saved scopes, revisions, receipts, pending decisions and journal
payloads remain unchanged. New identities use the stable local volume identity.
Changed lock tokens/inodes, invalid journals and a stale preview are refused.
All journals are validated and backed up before any selected identity changes.

The private backup remains under plugin-workers/journal-recovery/FINGERPRINT in
the selected runtime home. After interruption, retry the SAME --apply fingerprint
to resume its exact saved plan. count is the full plan size; repaired counts writes
in this invocation. A completed retry keeps the same plan/backup with repaired: 0.
Do not delete or edit recovery files. After success, start WHAB normally and verify reads.
Unsupported platforms fail before applying a legacy rebind.

JSON is the default. --format table shows status, count, fingerprint and every
selected relative journal path/action. Neither format includes journal payloads,
financial records, secrets or keys. --profile and --ca do not supply scope or open
a connection for this command. Help works offline without a profile or runtime.
"""


def add_runtime_parser(groups):
    runtime = groups.add_parser(
        "runtime", help="Offline runtime maintenance and journal identity recovery",
        description="Inspect or recover private runtime metadata without starting WHAB.",
        epilog=RUNTIME_HELP, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = runtime.add_subparsers(dest="command", required=True)
    recovery = commands.add_parser(
        "recover-journal-locks", help="Preview or apply backed-up, identity-only recovery on a stopped runtime",
        description="Recover legacy journal lock identities after a macOS reboot/remount.",
        epilog=RECOVERY_HELP, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    recovery.add_argument("--apply", metavar="FINGERPRINT",
                          help="Exact 64-character fingerprint from a reviewed preview; omit for read-only preview. Reuse it after interruption")
    recovery.add_argument("--format", choices=("json", "table"), default="json",
                          help="Complete JSON (default) or readable status and journal table")


def execute_runtime(args, error):
    if not args.home or not args.environment:
        raise error("Runtime recovery requires explicit --home PATH and --environment sandbox|production; profiles and environment variables are not used.")
    try:
        return recover_journal_locks(args.home, args.environment, apply=args.apply)
    except JournalRecoveryError as failure:
        raise error(f"{failure.code}: {failure}") from None


def runtime_table(result):
    lines = [f"Status: {result['status']}", f"Journals: {result['count']}",
             f"Fingerprint: {result['fingerprint']}"]
    if result['status'] == 'complete':
        lines.extend([f"Repaired: {result['repaired']}", f"Backup: {result['backup'] or 'none'}"])
    lines.append("Path\tKind\tAction")
    lines.extend(f"{row['path']}\t{row['kind']}\t{row['action']}" for row in result['journals'])
    return "\n".join(lines)
