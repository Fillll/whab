# IHAB

IHAB uses Python, FastAPI, Jinja, and SQLite. Read README.md for setup and current limitations.

- Keep monetary values in integer USD cents. Preserve demo/personal and Sandbox/production isolation.
- Never print, commit, or send `.env` contents, Plaid secrets, access tokens, or private data files. Configure credentials locally.
- Default to Sandbox. Production Item creation consumes a cumulative Trial allowance; avoid duplicate connections. Use Link update mode for existing connections.
- Store Plaid tokens encrypted on the server. Do not expose them to browser code, storage, exports, logs, or API errors.
- Save transaction changes and synchronization cursors atomically. Preserve user annotations through matched pending-to-posted transitions.
- Imported history before `budgetStartMonth` is analytics data, not envelope debt. Avoid adding imported income to account balance snapshots.
- This prototype is local only. Shared family access requires authentication and authorization before network deployment.
- For substantive changes, update relevant README behavior or limitations and run `.venv/bin/python -m pytest` and `.venv/bin/python -m ruff check ihab scripts tests`. Browser checks should use demo or Sandbox data.
- Keep browser JavaScript small; business logic and page rendering belong in Python. No Node build or React runtime is required.
- Keep private files and generated assets out of Git; stage source files explicitly.
- Use a feature branch and a pull request for every change to `main`; never push commits directly to `main`. Squash merge after reviewing the final diff and passing CI. The sole maintainer has authorized routine IHAB PR review and merging without another confirmation; fix failing checks or substantive review findings first. GitHub cannot approve a PR using its author's account, so no second-person approval is required.
- Follow SemVer from `1.0.0`. `ihab/__init__.py::__version__` is the version source for packaging and runtime. Update CHANGELOG.md for releases, merge through a PR, then create an immutable `vX.Y.Z` tag on the merged commit. See README.md for the compatibility contract and current branch-protection status.
