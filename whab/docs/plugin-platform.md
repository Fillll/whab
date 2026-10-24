# Plugin platform execution checklist

Implementation follows the existing [architecture](architecture.md),
[budget ownership](target-architecture.md), [SDK](plugins.md), and
[contribution checks](development.md). Feature branches and draft PRs keep the
increments reviewable. The maintainer has authorized merging and household
deployment of the completed, verified stack; releases require a separate request.

| Package | Deliverable and verification | Current state |
| --- | --- | --- |
| 1 | Versioned capabilities, deterministic resolution, authority policy, compatible manifests and staged saved-state upgrades | Initial implementation tested in draft PR #76; live commit controls pending |
| 2 | Independent public conformance kit; capability applicability; ten distinct replayable configurations | Public suites and ten resolver/hook configurations tested in [#82](https://github.com/Fillll/whab/pull/82); versioned native HTTPS lifecycle/migration/copy/promotion profiles and all exact replays tested across four generated families in [#93](https://github.com/Fillll/whab/pull/93); older-host runtime N/A is counted separately, while worker code upgrades and private commit faults remain outside this sampler |
| 3 | Independent durable JSON storage, strict validation, interruption tests and fictional benchmarks | Implemented and tested in draft PR [#79](https://github.com/Fillll/whab/pull/79); migration/copy authority remains separate |
| 4 | Per-budget generations, request/background leases, admission gates, draining and stable services | Coordinator and report integration tested in draft PR [#80](https://github.com/Fillll/whab/pull/80); remaining family adapters pending |
| 5 | Bounded worker protocol, immutable versioned environments, readiness and durable mutation outcomes | Stateless report upgrades and recovery tested in draft PR [#81](https://github.com/Fillll/whab/pull/81); private durable namespace effects and encrypted continuation foundations tested in [#91](https://github.com/Fillll/whab/pull/91); isolated categorization and complete model effects tested in [#95](https://github.com/Fillll/whab/pull/95); visual workers, browser handovers and retained cleanup ownership tested in [#97](https://github.com/Fillll/whab/pull/97); bank/storage mutation workers pending |
| 6 | Live report/export, branding and parser replacement; then categorization/banks; then storage/UI/API interfaces | Report selection/upgrades tested in [#80](https://github.com/Fillll/whab/pull/80) and [#81](https://github.com/Fillll/whab/pull/81); live branding/parser selection and code upgrades tested in [#88](https://github.com/Fillll/whab/pull/88); host-process categorization selection tested in [#89](https://github.com/Fillll/whab/pull/89); host-managed API selection and worker upgrades tested in [#92](https://github.com/Fillll/whab/pull/92); isolated categorization upgrades and model-effect recovery tested in [#95](https://github.com/Fillll/whab/pull/95); visual selection, distinct worker versions, headless operation and browser handovers tested in [#97](https://github.com/Fillll/whab/pull/97); banks/storage adapters remain separate |
| 7 | Previewed SQLite/JSON migration, protected same-device transfer, authority journal and restart recovery | Implemented and tested in draft PR [#84](https://github.com/Fillll/whab/pull/84), including process termination and installed native HTTPS round trips/restarts |
| 8 | Single-primary backend copies, monotonic watermarks, retry and explicit validated promotion in both directions | Implemented and tested in draft PR [#87](https://github.com/Fillll/whab/pull/87), including 14 process-termination cases and installed native HTTPS promotion/reopen in both directions; Web review/status controls tested in [#94](https://github.com/Fillll/whab/pull/94); storage workers remain separate |
| 9 | Independently installed generator/examples, clean external wheel CI, static metadata and compatibility docs | Four independent project generators and external wheel/replay CI tested in [#82](https://github.com/Fillll/whab/pull/82); maintained branding/parser/categorization examples tested in [#83](https://github.com/Fillll/whab/pull/83); static curated catalog tested in [#86](https://github.com/Fillll/whab/pull/86); reusable external-project CI and pinned generated workflows passed local and actual GitHub invocation checks in [#90](https://github.com/Fillll/whab/pull/90); native runtime profiles extended the external gate in [#93](https://github.com/Fillll/whab/pull/93); the API generator, independent teaching package and genuine minimum/current native worker profiles passed in [#96](https://github.com/Fillll/whab/pull/96); broader family examples pending |
| 10 | Recovery controls, attributed diagnostics, curated static catalog and verified private security-reporting route | Private route enabled and policy prepared in draft PR [#77](https://github.com/Fillll/whab/pull/77); minimal startup recovery tested in draft PR [#85](https://github.com/Fillll/whab/pull/85); static catalog tested in draft PR [#86](https://github.com/Fillll/whab/pull/86); visual repair, disabling and retained failed-owner cleanup tested in [#97](https://github.com/Fillll/whab/pull/97) |

Dependencies: contracts precede the conformance kit; JSON storage and runtime
coordination can then advance in parallel. Workers and capability-family
adapters precede storage migration; durable authority precedes backend copies
and promotion. Generator, metadata, catalog and security work can progress once
their contracts are stable.

Each increment includes focused regressions and the full existing
`scripts/check.py` submission gate, owner versions, documentation and actual
PR-linked changelogs. Financial fixtures remain synthetic, runtime homes
temporary, provider transports mocked, and HTTPS/authorization boundaries
host-owned. Plugins remain trusted executable code; workers contain failures
without implying an operating-system security sandbox.

The objective remains open until both real backends, all supported live
families, interruption-safe migration, both primary/copy directions, independent
generated plugins, ten replayable configurations and usable recovery and security
processes pass their acceptance checks. Draft PRs must distinguish implemented,
tested, committed, pushed, merged, installed and runtime-verified stages.
