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
