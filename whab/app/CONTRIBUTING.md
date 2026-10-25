# Contributing to WHAB

Start with [README.md](README.md) for setup, supported behavior and limitations, then read [the architecture guide](../docs/architecture.md). Keep a change focused on the layer that owns its behavior. The repository's current visibility and licensing are unchanged by these contributor instructions.

## Development environment

WHAB supports Python 3.14 and newer; CI exercises Python 3.14 and 3.15. Run all application commands below from the application directory (`whab/app/`):

```sh
python3.14 -m venv .venv
.venv/bin/python scripts/install.py --dev --example
.venv/bin/python -m whab --version
```

The source installer installs the host and the [supported plugin set](plugins/README.md#supported-plugins) as editable local distributions; `--dev` also installs the independent local developer toolkit used by conformance tests, and `--example` includes the teaching plugin used by development checks. For wheel installations, `pip install 'whab[bundled]'` selects the supported set when compatible distributions are available in the chosen package index or local wheelhouse; this does not imply these releases have been published. The host alone contains the core and SDK and needs installed storage/UI plugins for the default launch. Install Node.js 22 or newer for browser tests. There is no Node build or React runtime. The bundled visual interface's templates and static files belong to the installed `whab_plugin_web` package.

Direct source toolkit tests additionally need `scripts/install.py --runtime-fixtures`, which implies `--dev` and explicitly adds six reviewed fictional runtime registrations. Normal/default installation and plain `--dev` do not add them. The canonical full contribution gate runs toolkit tests in its own installed-wheel environments and supplies those fixtures there automatically; its initial source test run does not collect toolkit tests. See the [toolkit development recipe](devtools/plugin-devkit/README.md#scope-and-remaining-work).

When upgrading an older checkout, rerun `.venv/bin/python scripts/install.py --dev`. The helper installs the new `whab_plugin_<id>` distributions before uninstalling detected legacy official plugin distributions, avoiding duplicate entry-point registrations. If the legacy teaching example is installed, the helper includes its replacement even without `--example`. After successful installation and legacy-package removal, it also removes verified obsolete generated plugin `src/whab_<id>_plugin.egg-info` and application-root `whab.egg-info` metadata that could mask current registrations; it preserves current per-distribution metadata. No manual metadata deletion is needed for the standard source upgrade. For direct wheel installations, use a fresh environment or uninstall the old `whab-<id>-plugin` distributions when installing their `whab_plugin_<id>` replacements.

Runtime configuration lives under `WHAB_HOME`, or the launch directory when absent. Prefer the [isolated preview helper](../docs/development.md#isolated-preview) for experiments; it supplies explicit settings and fictional data without opening an existing runtime. For persistent development, choose a separate absolute runtime home. Preserve existing configuration, data and encryption/TLS keys together; follow the [checkout migration instructions](../README.md#existing-development-checkouts) after a source move. Never copy household credentials or private databases into fixtures.

Read [runtime setup and HTTPS](../docs/usage.md#runtime-setup), [budget-owned storage](../docs/target-architecture.md#installation-and-budget-ownership) and the owning plugin README before changing configuration, storage selection, saved paths or migrations. Setup provisions certificates but never changes OS trust. Storage selection does not migrate data between backends. The standard source installer owns scoped legacy distribution/metadata cleanup; no manual metadata deletion is needed for a standard upgrade.

## Tests and checks

Use focused local feedback and the authoritative required PR gate. During iteration, run:

```sh
.venv/bin/python scripts/check_pr.py local --base origin/main
```

This runs quick checks, always includes core financial/architecture regressions and selects owning tests from changed source paths. Add focused regressions from the task map when a change spans another contract. Local success is iteration feedback; it does not establish complete coverage or installed-wheel compatibility. `scripts/check.py --quick` remains available for Python lint, changelog provenance, documentation and JavaScript checks alone.

Required `CI (Python 3.14)` and `CI (Python 3.15)` checks retain their existing names and branch protection. For application, test, tooling, workflow, dependency and unknown changes, they require the **complete** source collection split across eight independent shards per Python version, with two pytest workers per shard. The final gates reject failed/cancelled jobs, missing shards, different source/interpreter identities, inconsistent collections, duplicated/omitted/skipped tests and missing coverage. Coverage is combined per Python version and every plugin must retain 100% statement and branch coverage, including unimported modules. Lint/docs/JavaScript and installed-wheel verification run once, in parallel with source tests. Documentation-only changes omit application regressions; launcher checks run for native changes and application CLI/installation contracts. Unknown changes fall back to the complete source suite.

The PR feedback target is **under five minutes from workflow creation to both required gates completing** on available hosted runners. Queue/cold-cache time counts in reported timings. A timeout or unfinished validation is a failure, never a shortened success. Measure actual runs and investigate overshoots; the target is not a guarantee of GitHub runner capacity. Do not rerun a successful full local suite, PR matrix and exhaustive certification for the same unchanged inputs before reporting a PR ready.

Exhaustive certification runs after every merge to `main`, nightly and through manual dispatch. To run it locally when explicitly needed:

```sh
.venv/bin/python scripts/check.py
```

The exhaustive command runs quick checks, the complete central/plugin Python suite **once** through `check_plugin_coverage.py`, independent toolkit wheels on genuine minimum/current hosts, generated-plugin configurations and exact-artifact replays, and isolated distribution verification. The separate certification workflow retains both supported Python versions and the reusable external-project conformance workflow. Historical compatibility or replay failures can therefore be discovered after merge; a green PR gate establishes source regressions and packaging, not exhaustive certification. Inspect certification before claiming a fully certified build and fix failures promptly.

Both runners require Python 3.14+, development dependencies and Node.js 22+ where browser checks run. They do not install missing dependencies. Child checks run with a fresh temporary `WHAB_HOME` and Sandbox defaults. Only explicit platform, executable-path, temporary-directory and locale variables are inherited; custom settings, credential aliases and caller Python/pip/pytest/coverage configuration are excluded. The external toolkit and generated-project checks use the same boundary and set their own reviewed test/index configuration. Native launcher changes also require the [platform checks](../launchers/macos/README.md#development-and-diagnostics).

For documentation iteration, `.venv/bin/python scripts/check.py --docs` checks local paths, anchors, fences and the canonical supported-plugin inventory without network requests. Run the changelog check as well if editing changelogs. Documentation-only changes need the docs/changelog checks and any checks for executable examples they change. Contribution tooling and code changes use focused local checks plus the complete required PR gate; exhaustive local certification is no longer a prerequisite for each PR.

The distribution check builds the host, supported plugin set and teaching example, installs independent wheels into an isolated temporary target and checks pages, packaged assets, version metadata, runtime paths and entry points outside the checkout. It uses fictional data and no financial institution; build dependencies may require a download.

Every plugin must retain **100% Python statement and branch coverage** across its complete `src/` tree, including unimported source. `scripts/check_plugin_coverage.py --plugin plaid` measures one plugin using the same full suite; it does not select only that plugin's tests. Keep coverage regressions in the owning plugin. Type-only imports are outside runtime coverage; do not exclude runtime code or add coverage pragmas. Rendering, Node behavior tests, browser review and installed-wheel checks cover templates/assets beyond Python coverage.

### Task ownership

Read the owner guide before changing its contract. Each material change updates its owner's version/changelog; documentation and contribution-tooling maintenance alone need no invented release version.

| Task | Source owner and current contract | Relevant tests/checks | Documentation and version owner |
| --- | --- | --- | --- |
| Money, assignments or rollover | `src/whab/domain/`; [financial invariants](../docs/architecture.md#financial-invariants) | `tests/domain/`, `tests/test_ledger_service.py`, `tests/test_architecture.py` | Usage/architecture; host |
| HTTP schemas, routing, sessions or budget creation | `src/whab/web/`, `src/whab/budgets/`; [budget addresses](../docs/usage.md#budget-addresses) | `tests/web/`, `tests/budgets/`; [two-budget verification](../docs/architecture.md#budget-isolation-verification) | Usage/SDK; host |
| Web layout, navigation, filters or controls | [Web plugin](plugins/interface/web/README.md#ui-acceptance-criteria) | `plugins/interface/web/tests/`, shared transport tests, Node tests, fictional browser preview | Web README; Web plugin |
| SQLite persistence, migrations or portable saves | [SQLite plugin](plugins/storage/sqlite/README.md) plus `src/whab/budgets/` for containers | SQLite plugin tests, `tests/test_budget_container.py`, `tests/test_portable.py` | SQLite/SDK/usage; affected plugin and host when shared contracts change |
| Plaid ingestion or artwork | [Plaid](plugins/ingestion/plaid/README.md), [Bank logos](plugins/interface/bank-logos/README.md) | Owning plugin tests, `tests/storage/`, shared bank/branding SDK tests | Owning plugin README/version; host for shared contracts |
| YNAB import or categorization | [YNAB](plugins/ingestion/ynab/README.md), [Random Forest](plugins/automation/random-forest-categorization/README.md) | Owning plugin tests, `tests/test_categorization.py`, import routes and two-budget verification | Owning plugin guides/versions; host for shared eligibility or SDK changes |
| Remote protocol or storage client | [API interface plugin](plugins/interface/server/README.md), [Client](plugins/storage/client/README.md) | Owning plugin tests, `tests/test_remote_protocol.py`, `tests/test_account_scope_remote.py` | SDK/owning guides; each affected owner |
| Plugin SDK, discovery or lifecycle | `src/whab/plugins/`; [public SDK](../docs/plugins.md) | `tests/plugins/`, architecture and affected plugin tests, isolated distribution check | SDK/architecture; host and affected plugins |
| Plugin author toolkit | [Independent devkit](devtools/plugin-devkit/README.md), outside runtime discovery | External minimum/candidate wheels through `scripts/check_plugin_devkit.py`; toolkit statement/branch coverage | Devkit README, package version and changelog |
| Teaching plugin | [Example](plugins/example/example/README.md) | `plugins/example/example/tests/`, distribution verification | Example README/version |
| Launcher lifecycle or native UI | [macOS launcher](../launchers/macos/README.md) | Manager lifecycle tests, bundle checks and native tests in platform guide | Launcher guides/version |
| Guidance or contribution tooling | This guide and [documentation ownership](../docs/development.md#documentation-ownership) | Docs/changelog checks; focused tooling tests and full runner for scripts/CI | Owning guide; no application version bump for tooling alone |

Keep concrete implementations, assets, docs and implementation tests in their `plugins/<category>/<id>/` folder. Shared host, SDK and cross-plugin contracts belong in `tests/`. Each plugin owns package data, entry points, compatibility and its local version/changelog; supported-set changes update the host's `bundled` extra and [canonical inventory](plugins/README.md#supported-plugins). The source installer discovers plugin packages automatically; concrete packages never move into the host wheel.

Application-root `conftest.py` supplies shared isolated fixtures. Plugin-specific records and mock transports belong in that plugin's tests. Tests must not read `.env`, open private databases, require a Plaid account or make live provider requests. Use `tmp_path` and explicit `WHAB_HOME` when testing configuration. Regression tests should demonstrate an observable failure or invariant, rather than mirror implementation steps.

Browser checks use fictional or Sandbox data. Follow the [UI acceptance criteria](plugins/interface/web/README.md#ui-acceptance-criteria) and [completion verification](../docs/development.md#completion-and-running-version-verification), including the saved result. A successful Link screen alone does not establish successful synchronization or production access.

## Code boundaries

Keep money and envelope rules in `whab.domain`; they must not import web, storage, interfaces, configuration, provider or plugin runtimes. Keep SQLite transactions, additive schema upgrades and demo seeding in `whab_plugin_sqlite`, provider HTTP and normalization in `whab_plugin_plaid`, and visual presentation/assets in `whab_plugin_web`. The `whab.web` package owns transport, request schemas, middleware and safe projections. `whab.storage.bank` scopes providers over the SDK's `StorageBackend` protocol. App, host web and generic plugin-runtime code must not import concrete storage, bank or visual implementations. Assemble selected descriptors/factories in `whab.app.create_app` with injectable backends/services for tests.

External extensions use the documented [`whab.plugins` API](../docs/plugins.md); that guide owns capability records, hooks and lifecycle contracts. Reports use immutable snapshots, bank factories use scoped persistence, and storage owns backend lifetime. Preserve the [accepted architectural decisions](../docs/architecture.md) and budget context boundaries. Enabled Python plugins are trusted code running in the server process, not sandboxed code. Never silently substitute a backend or selected plugin after a load failure.

Preserve these invariants when changing the corresponding layer:

- Use integer currency minor units (USD cents): positive transactions are outflows and negative transactions are inflows. Keep totals and allocations separate by currency and reject account/category currency mismatches. Avoid floats for stored money. Parent category groups are organizational; transactions, targets and allocations refer only to children.
- Keep demo/personal workspaces and Sandbox/production databases separate. Bank balance snapshots already include income; importing income must not add it again to assignable cash.
- Preserve `budgetStartMonth`, category overrides, notes and review state. Pending-to-posted matches must retain annotations, and all synchronization changes and their cursor must commit together.
- Preserve compatible SQLite ledgers in place and retain encrypted-token compatibility. New Plaid Link tokens use `whab-local-household`, and browser OAuth keys use `whab`; finish in-progress Link flows before upgrading. The package and launch command are only `whab`.
- Keep credentials on the server. Public responses, plugin snapshots, CSV files and exception messages must exclude tokens and configuration secrets.
- Require HTTPS everywhere, including localhost and webhooks, and retain loopback Host/origin checks for ordinary routes. Only explicitly enabled remote protocol routes and registered signed webhooks bypass those local checks; the former require bearer authorization and the latter provider signature verification. TLS is transport encryption, not user authentication. Validate request bodies, escape user and plugin text, and protect CSV headers and text cells from spreadsheet formulas.

## Pull requests and releases

WHAB follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html): patch releases fix compatible behavior, minor releases add compatible features, and major releases introduce breaking changes. The compatibility contract covers documented HTTP/CSV interfaces and the ability to open existing ledgers and encrypted connections through supported migrations. A schema change with a supported automatic migration can be compatible; dropping or changing documented fields, requiring manual data conversion, or invalidating existing tokens requires a major release. The public plugin API in `whab.plugins` is also covered: breaking its documented records or hook behavior requires a major release. Internal modules are not a stable import API. Internal refactoring and visual changes do not by themselves break this contract.

Use a feature branch and pull request for every change to `main`. Describe the concrete problem, resulting behavior and relevant validation. Include a fixture-based regression for a behavior fix and update the owning documentation when a contract or limitation changes. Do not commit `.env`, databases, private exports, generated build files or unrelated workspace changes; stage source paths explicitly.

Version the owner of the material change in the same PR. Host/core/public-SDK changes bump `src/whab/__init__.py::__version__` and add a dated entry to the application `CHANGELOG.md`. Each independently packaged plugin instead owns `plugins/<category>/<id>/pyproject.toml::project.version` and its local `CHANGELOG.md`; a plugin-only change does not bump the app or change the app changelog. Changes spanning owners update each affected owner. Apply SemVer separately to each distribution: patch for compatible fixes or maintenance, minor for compatible features, major for a breaking documented contract. Runtime and dependency support changes require the owning distribution's version bump. Small documentation-only changes may wait for its next version bump.

The host package version, `python -m whab --version`, FastAPI metadata and About dialog derive from the host version source; installed plugin versions come from their own distribution metadata. Review the final diff and pass CI before requesting a squash merge. Final approval and merging belong to the human maintainer @Fillll. Agents must not approve or merge a PR unless @Fillll explicitly delegates that action for that specific PR. Create an immutable `vX.Y.Z` tag and GitHub release on the merged commit only when the user explicitly requests a release; version bumps and merges do not require publishing one. Do not reuse or move an existing release tag.

### Nightly stable releases

The maintainer has explicitly authorized automatic stable publication after the daily scheduled exhaustive certification. The release job runs only in the official repository on scheduled `main` runs, after both supported Python certification jobs, reusable external-project conformance and native macOS launcher checks succeed. It also verifies both required Python CI checks from GitHub Actions on that exact commit, and skips publication if `main` has advanced. Push-triggered and manually dispatched certification remain validation-only.

The publisher reads the declared app version without importing application code, creates `vX.Y.Z` at the certified commit and publishes a non-draft, non-prerelease GitHub release marked Latest. Release notes include the certification run and automatically generated PR notes. Only the publisher job receives repository-content write access; test/build jobs remain read-only. This creates the source release consumed by the launcher's Stable channel; it does not upload native application installers or publish packages to an index.

Each declared app version is released once. A previously published stable version is skipped, including when newer plugin-only or documentation commits retain that host version. There are no automatic version bumps, tag moves or replacement releases. A new stable bundle therefore needs a new declared app version through the normal reviewed version/changelog process. A conflicting unpublished tag, existing draft/prerelease, failed/skipped/incomplete check, or newer stable version stops publication for maintainer review. Retries may complete an interrupted same-commit tag/release creation without replacing existing release state.

`scripts/publish_stable.py --sha <full-commit> --run-id <scheduled-certification-run>` performs read-only validation by default. The scheduled job supplies `--publish` and its scoped token. Outside this documented nightly policy, release creation still requires an explicit maintainer request.

### Authorization and completion stages

Keep the intended outcome concrete and carry existing authorization through its stated scope. Technical repository/admin access does not itself authorize merging, deployment, production banking or releases.

| Request | Work covered |
| --- | --- |
| Review, discuss or propose | Inspect and return findings or a concrete preview/proposal. Honor an explicit request to discuss before implementation |
| Implement or fix | Make the focused change, update owning docs, validate, commit/push the feature branch and open a reviewable PR. Final merge remains with the maintainer |
| Merge and deploy an identified PR | Treat this as explicit delegation for that PR. Complete required checks, merge through the allowed PR workflow, update the intended installation and verify the running result; do not ask for the same delegation again |
| Publish a release | An explicit release request authorizes creating the version tag/GitHub release. The documented nightly policy is standing authorization for its scheduled publisher only; a version bump, merge or deployment alone grants no other release authorization |

Resolve the identified PR and target from the current task context. Ask only when a material target or required authorization is genuinely missing, after completing independent reviewable work. New unrelated PRs do not inherit a prior PR's merge authorization. Never bypass failing CI, unresolved substantive findings, protected direct-push restrictions or an explicit request to wait. Certificate trust and creation of live banking connections retain their separate explicit-action boundaries.

Use the [PR template](../../.github/pull_request_template.md) to record the problem/result, affected owners, checks, previews, version/changelog provenance and completed delivery stages. Before reporting completion, apply the [running-version and saved-result checklist](../docs/development.md#completion-and-running-version-verification). State unverified stages directly; documentation/tooling changes do not require deployment into a household runtime.

### Changelog PR links

Every populated version or **Unreleased** section in every `CHANGELOG.md` must link the pull request(s) that introduced the recorded changes. This applies repository-wide: host/core, supported and example plugins, platform launchers, and future components. Changelog updates go through the same feature-branch and PR workflow as other changes.

For a section introduced by one PR, put a shared reference immediately below the heading, for example `PR: [#38](https://github.com/Fillll/whab/pull/38)`. That reference applies to all changes in the section. When a section combines changes from multiple PRs, list those PRs and also attach the appropriate link to each change bullet so its origin is clear. Use full links to this repository's pull requests; issue links, bare numbers and placeholders are insufficient.

Open a draft PR to obtain its number and URL before finalizing a new changelog entry, then push the real reference to that PR. An empty **Unreleased** section may stay empty. Once it contains a change it needs a PR reference; retain those references when moving changes into a versioned section. Verify the final links before merging.

Historical entries cite the PRs that introduced their recorded changes, rather than the PR that later adds links or corrects the changelog. Preserve the original version, date and release description during provenance backfills. Documentation and contribution-tooling maintenance alone do not require inventing a new application or plugin version.

Run `.venv/bin/python scripts/check_changelog_prs.py` from `whab/app/`. CI checks all tracked changelogs for a PR link in each populated section, without network access. This verifies link structure and scope; authors and reviewers must verify that the linked PR exists and actually introduced the described change.

## Branch protection

The repository is public, and two active rulesets protect `main`:

- [Main safeguards](https://github.com/Fillll/whab/rules/25219005) requires a pull request, passing `CI (Python 3.14)` and `CI (Python 3.15)` checks from GitHub Actions, and an up-to-date branch. It also requires resolved conversations and squash merges with linear history, and blocks force pushes and branch deletion. There are no bypass actors, including the maintainer.
- [Owner approval and merge gate](https://github.com/Fillll/whab/rules/25219007) allows only @Fillll to merge into `main` through a pull request. It requires one approving review from the code owner and dismisses stale approvals after new reviewable commits. [CODEOWNERS](../../.github/CODEOWNERS) designates @Fillll as the owner of every path. Only @Fillll can bypass this gate, and only through a pull request; direct pushes remain blocked.

GitHub does not allow authors to approve their own pull requests. For PRs authored as @Fillll, the human maintainer uses the PR-only owner exception to make the final merge decision; all Main safeguards still apply. Rulesets identify GitHub accounts, so tools authenticated as @Fillll have the same technical permissions and must follow the human approval requirement above.

The reproducible ruleset configurations are [main-safeguards.json](../../.github/main-safeguards.json) and [owner-gate.json](../../.github/owner-gate.json).

## Reporting sensitive bugs

Keep reports reproducible with synthetic records, redacted responses and public error messages. Do not put financial records, institution account identifiers, Plaid credentials, access tokens, encryption keys or private `.env` contents in an issue, PR, screenshot or log attachment. Report a suspected credential exposure privately to the repository maintainer rather than publishing the sensitive material.

## License

Contributions to WHAB's own code, documentation and assets are accepted under the [MIT License](LICENSE). Preserve applicable third-party notices. Independently written plugins may use their authors' chosen licenses.
