# WHAB product

Keep WHAB's hexagonal financial core and plugin architecture central to every change. Before implementation, choose the domain, shared host/SDK or concrete plugin owner using the [architecture placement guide](docs/architecture.md#choosing-the-owner-of-a-change). Categorize replaceable behavior deliberately; plugins may declare dependencies through public contracts and packaged JSON metadata, without importing another plugin's private implementation.

The Python application, core, SDK and plugins live in `app/`; shared product documentation lives in `docs/`; platform companions live in `launchers/`. Read and follow [`app/AGENTS.md`](app/AGENTS.md) before changing application code, plugins, tests or product documentation. Run application commands from `whab/app/`. Follow each launcher's own instructions for native builds and lifecycle tests.

Launchers manage installed WHAB versions and processes through the application CLI. Keep budgeting rules, data access and plugin behavior in the application. Keep runtime homes separate from code and preserve existing homes and encryption keys across upgrades. Existing ignored runtime files under `whab/` remain in place when application sources move; set `WHAB_HOME` explicitly to reuse them.

Keep secrets, runtime data, private backups and generated builds out of Git. Preserve HTTPS on every route and leave certificate trust changes to explicit user action. Use feature branches and pull requests; final approval and merging belong to the human maintainer unless explicitly delegated for the specific PR. Create version tags and GitHub releases only on an explicit release request.

Scheduled stable publication follows the maintainer-authorized [nightly release policy](app/CONTRIBUTING.md#nightly-stable-releases). Never move existing version tags or treat a push/manual certification run as release authorization.
