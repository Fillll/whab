# Bank logos plugin

Distributed independently as `whab-bank-logos-plugin`. Its version and dependency compatibility range are defined in [pyproject.toml](pyproject.toml); its dated history belongs in [CHANGELOG.md](CHANGELOG.md). Material changes confined to this plugin update its version and changelog, without an app version or app changelog change.

`src/whab_bank_logos/` owns curated institution-name resolution, bounded public icon fetching and its cache. Keep its helpers and mocked provider tests in this folder. The host owns image-route validation and supplies the plugin cache directory through the public branding contract.

Successful logos stay cached for **1 year (365 days)** from download. The next image request after expiry fetches a fresh logo. Saved logos survive application restarts in `WHAB_HOME/cache/plugins/bank-logos/`; cache loading accepts only curated keys, bounded files and validated raster images. At most one download per key runs at a time. Failed requests are cached in memory for 5 minutes and retried after that interval.

Open **Settings → Plugins → Installed → Bank logos → Options** for cache duration, cached logo count and **Flush cache**. Flushing removes saved logos and transient failures; the next request downloads artwork again. Downloads already running when the cache is flushed cannot repopulate it. A failed disk deletion is reported as an action failure instead of claiming the saved cache was cleared. An unavailable cache directory falls back to memory without interrupting the ledger.

Resolution stays local. Only allowlisted public bank domains reach Icon Horse; no account details or bank credentials leave the application. Fetches use bounded bytes, short timeouts and no redirects. Provider tests must mock HTTP requests.

From the application directory, run `.venv/bin/python -m pytest plugins/interface/bank-logos/tests` and `.venv/bin/python scripts/check_plugin_coverage.py --plugin bank-logos`. See [plugin contribution rules](../../README.md) for all checks and packaging.
