# Plugin platform execution checklist

Implementation follows the existing [architecture](architecture.md),
[budget ownership](target-architecture.md), [SDK](plugins.md), and
[contribution checks](development.md). Feature branches and draft PRs keep the
increments reviewable; the maintainer owns approval and merging. Source work does
not update the household installation or publish releases.

| Package | Deliverable and verification | Current state |
| --- | --- | --- |
| 1 | Versioned capabilities, deterministic resolution, authority policy, compatible manifests and staged saved-state upgrades | Initial implementation tested in draft PR #76; live commit controls pending |
| 2 | Independent public conformance kit; capability applicability; ten distinct replayable configurations | Public suites and ten distinct resolver/hook configurations tested in draft PR [#82](https://github.com/Fillll/whab/pull/82); live/migration/copy sampler adapters pending |
| 3 | Independent durable JSON storage, strict validation, interruption tests and fictional benchmarks | Implemented and tested in draft PR [#79](https://github.com/Fillll/whab/pull/79); migration/copy authority remains separate |
| 4 | Per-budget generations, request/background leases, admission gates, draining and stable services | Coordinator and report integration tested in draft PR [#80](https://github.com/Fillll/whab/pull/80); remaining family adapters pending |
| 5 | Bounded worker protocol, immutable versioned environments, readiness and durable mutation outcomes | Stateless report upgrades and recovery tested in draft PR [#81](https://github.com/Fillll/whab/pull/81); financial mutation workers pending |
| 6 | Live report/export, branding and parser replacement; then categorization/banks; then storage/UI/API interfaces | Already installed stateless reports tested in draft PR [#80](https://github.com/Fillll/whab/pull/80); code upgrades and other families pending |
| 7 | Previewed SQLite/JSON migration, protected same-device transfer, authority journal and restart recovery | Pending |
| 8 | Single-primary backend copies, monotonic watermarks, retry and explicit validated promotion in both directions | Pending |
| 9 | Independently installed generator/examples, clean external wheel CI, static metadata and compatibility docs | Four independent project generators and external wheel/replay CI tested in draft PR [#82](https://github.com/Fillll/whab/pull/82); static catalog and remaining maintained examples pending |
| 10 | Recovery controls, attributed diagnostics, curated static catalog and verified private security-reporting route | Pending |

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
