# Bank logos plugin

Distributed independently as `whab_plugin_bank_logos`. Its version and dependency compatibility range are defined in [pyproject.toml](pyproject.toml); its dated history belongs in [CHANGELOG.md](CHANGELOG.md). Material changes confined to this plugin update its version and changelog, without an app version or app changelog change.

`src/whab_plugin_bank_logos/` owns curated institution-name and explicit public bank website resolution, bounded public icon fetching and its cache. Keep its helpers and mocked provider tests in this folder. The host owns image-route validation and supplies the plugin cache directory through the public branding contract.

Known institution aliases resolve automatically without a network request. For a bank that is not recognized, enter its **public bank website** in account settings, with or without `https://`. The host normalizes the hostname; optional **Retrieve logo** previews it before saving. Unusable input or missing artwork does not block saving the account. An explicit website takes precedence over automatic institution matching. The plugin validates the bare public hostname and gives it a stable opaque key; it does not infer a website from an account name.

Successful logos, including explicitly configured bank websites, stay cached **indefinitely**. They survive application restarts in `WHAB_HOME/cache/plugins/bank-logos/`, including logos saved by earlier versions. Age never triggers another download, and registering more websites does not remove saved artwork. Cache loading accepts only curated keys or explicit hostnames registered by the resolver, bounded files and validated raster images. Explicit hostname mappings and in-memory entries are each limited to 256; evicted images can be read from disk again without downloading. At most one download per key runs at a time. Failed requests are cached in memory for 5 minutes and retried after that interval.

Open **Settings → Plugins → Installed → Bank logos → Options** for cache duration, the in-memory cached logo count and artwork provider information. No cache maintenance is needed. An unavailable cache directory falls back to memory without interrupting the ledger.

Resolution stays local. Icon Horse is the fixed artwork provider at `https://icon.horse`; each image request uses only `https://icon.horse/icon/<public-hostname>`. The plugin sends a curated bank hostname or the public bank hostname explicitly entered in account settings. It never fetches the entered bank URL directly or sends account names, balances, transactions or bank credentials. Fetches use bounded bytes, short timeouts, no redirects and no environment proxies. Provider tests must mock HTTP requests.

From the application directory, run `.venv/bin/python -m pytest plugins/interface/bank-logos/tests` and `.venv/bin/python scripts/check_plugin_coverage.py --plugin bank-logos`. See [plugin contribution rules](../../README.md) for all checks and packaging.
