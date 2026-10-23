# Development and verification

Use the [contribution guide](../app/CONTRIBUTING.md) for installation, task ownership, checks and PR policy. This guide covers reproducible previews, verification of running changes and troubleshooting. All application commands below run from `whab/app/`.

## Isolated preview

Install the development environment using the contribution guide, then run:

```sh
.venv/bin/python scripts/preview.py
```

The helper creates a fresh temporary runtime with fictional accounts, categories and transactions, its own SQLite database and HTTPS certificates. It selects only Web and SQLite, supplies settings explicitly and does not load the household `.env`, `WHAB_HOME`, saved budgets or bank connections. The default port is 5174; use `--port 5180` if occupied or `--port 0` to choose a free port. It prints its URL, temporary runtime and CA certificate path. Ctrl-C stops the owned server and removes the temporary runtime. Preview edits are disposable.

For browser review, explicitly trust that preview's CA if needed using the [local HTTPS procedure](usage.md#local-https). The helper never changes OS trust or borrows another runtime's keys. Do not disable browser or client TLS validation. A fresh run creates a fresh CA; remove a temporary CA from your trust store when finished if you added it.

For a bounded check without changing OS trust:

```sh
.venv/bin/python scripts/preview.py --check --port 0
```

This uses real HTTPS with the generated CA to check health, rendering and a saved edit, then closes/reopens the fictional database to verify persistence and removes its runtime. It complements the [web acceptance criteria](../app/plugins/interface/web/README.md#ui-acceptance-criteria); browser layout, keyboard interaction and visual response still need browser review.

### Performance changes

Use the existing fictional benchmark for representative large budgets:

```sh
.venv/bin/python scripts/benchmark_register.py --all-accounts
.venv/bin/python scripts/benchmark_register.py --page transaction-edit --portable
```

Record before/after timings with the same data size, flags and machine. The benchmark measures server work, excluding browser layout and network time. Also inspect the affected flow in the built-in browser at the reported viewport. Check shell stability, request behavior and persistence; do not treat a fast server response alone as proof of a responsive interaction. Preserve the benchmark's independent saved-file verification when measuring portable writes.

## Completion and running-version verification

Report only the stages actually reached: implemented, validated, committed, pushed, PR opened, merged, installed and verified. The requested scope and [authorization policy](../app/CONTRIBUTING.md#authorization-and-completion-stages) determine which stages to perform.

1. Review the final diff and run the relevant focused checks and full submission checks. Record the changed owner(s), versions and any material limitations in the PR.
2. When installation/deployment is requested, identify the target runtime home and launcher channel before updating. Preserve that runtime's keys and use its supported update/backup procedure. A source checkout and the launcher-managed installation can run different revisions.
3. Inspect the launcher version line or [manager `status`/`doctor`](../launchers/macos/manager/README.md) for the installed build, running instance, interpreter, runtime home and health. The selected future-update channel and installed provenance are separate fields. **Restart** runs the installed build; **Update & Restart** resolves and installs a candidate. See [launcher channels](../launchers/macos/README.md#first-launch).
4. Verify the running host version and the changed plugin's installed version through that installation's About/plugin details. `python -m whab --version` in the development environment describes that environment only. Plugin-only changes can retain the same host version.
5. Match the local HTTPS `/api/health` build/instance identity to the intended installation using its own CA or trusted configured certificate. Keep certificate and hostname verification enabled. Health confirms readiness and identity; verify the actual affected interaction separately.
6. Exercise the addressed budget's affected flow with fictional/Sandbox data where possible. For a save, change a value, navigate away and reopen it; for persistence work, restart/reopen the isolated budget too. For budget/context changes, perform the [two-budget isolation check](architecture.md#budget-isolation-verification).
7. Report the PR/commit, completed stages, checks and observed result. Keep private records, identifiers and runtime paths out of public PR evidence; use synthetic screenshots or sanitized summaries. State any unverified stage directly.

Documentation and tooling changes do not require updating a household installation. A deployment claim requires observing that installation after the update; successful CI or clicking Restart alone does not establish it.

## Troubleshooting

Start with the first diagnostic, then follow the linked supported procedure. Preserve originals and recovery state before repairs.

| Symptom | First diagnostic | Supported next step |
| --- | --- | --- |
| Imports or assets break after moving a checkout | Check the interpreter, `.venv/pyvenv.cfg`, editable `.pth`/`direct_url.json` source paths and `WHAB_HOME`; do not print `.env` | Create a fresh environment and reinstall local packages while preserving the existing runtime; see [checkout migration](../README.md#existing-development-checkouts) |
| A plugin is registered twice | Inspect installed distribution/entry-point names and confirm the checkout/environment | Rerun `scripts/install.py --dev`; its [scoped legacy cleanup](../app/CONTRIBUTING.md#development-environment) preserves current metadata. Do not broadly delete package metadata |
| A feature is merged but the app looks unchanged | Compare running source revision and changed plugin version with the intended PR | Follow the completion checklist and [channel eligibility](../launchers/macos/manager/README.md#installation-and-trust); inspect post-merge CI and choose the intended channel explicitly |
| HTTPS fails or the browser warns | Confirm hostname, runtime-owned CA, certificate paths and port | Follow [local HTTPS](usage.md#local-https); do not use HTTP, `--insecure` or automatic OS trust changes |
| Startup finds multiple SQLite files | Inspect matching filenames without opening private records | Preserve all candidates and resolve the intended original workspace using the [SQLite storage rules](../app/plugins/storage/sqlite/README.md); never silently select or merge one |
| Expected transactions are absent | Check the addressed budget/account, currency, date range, review filters and pagination | Compare the same scope before diagnosing ingestion; see [import handling](usage.md#importing-and-data-handling). Provider history limits and UI filters are separate causes |
| Models or settings appear in another budget | Compare the permanent addressed budget identities and saved plugin context using synthetic records | Run the [isolation recipe](architecture.md#budget-isolation-verification), preserve evidence and fix the owning context boundary |
| Launcher reports an incomplete transition | Read `doctor` and the private transition journal paths | Use [backup boundaries and recovery](../launchers/macos/manager/README.md#backup-boundaries-and-recovery); preserve the journal and matching keys/backup. Do not restart old code against possibly migrated data |

## Documentation ownership

README files introduce setup and current behavior; `usage.md` owns user workflows, `architecture.md` owns decisions and invariants, and `plugins.md` owns the public SDK contract. Concrete plugin READMEs own their implementation contracts and limitations. AGENTS routes mandatory work; CONTRIBUTING owns validation and contribution policy. Changelogs own release chronology. Plans record their status, delivery PRs and unresolved work and link back to current contracts after delivery.

Update the owning document in the same change instead of copying a new rule into several overviews. The [supported plugin inventory](../app/plugins/README.md#supported-plugins) is checked against package metadata by `scripts/check_docs.py`; use links to that inventory and the version sources instead of repeated counts or a manually maintained current-version banner. `scripts/check.py --docs` validates local documentation links, anchors, fences and the inventory without live network requests.
