# WHAB repository

The WHAB product lives in `whab/`, with the application in `whab/app/`, platform launchers in `whab/launchers/`, and shared documentation in `whab/docs/`. Read [`whab/AGENTS.md`](whab/AGENTS.md) for product ownership and [`whab/app/AGENTS.md`](whab/app/AGENTS.md) before changing application code, plugins, tests or documentation. Run application commands from `whab/app/`.

Keep credentials, runtime data and generated files, including `budgets/` and `tls/` under the runtime home, out of Git. Stage reviewed source paths explicitly. Use a feature branch and pull request for changes to `main`.

Every populated changelog entry must link the pull request(s) that introduced its changes, including core, all plugins, examples and platform launchers. Use the originating PR for historical entries, and cite individual bullets when a version combines multiple PRs. Open a draft PR to obtain its URL before finalizing a new entry; no placeholder links may remain before merge. See [the changelog policy](whab/app/CONTRIBUTING.md#changelog-pr-links).

Create version tags and GitHub releases only when the user explicitly requests a release. Version bumps and merges do not require publishing a release.
