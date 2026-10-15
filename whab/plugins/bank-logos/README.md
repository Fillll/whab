# Bank logos plugin

`src/whab_bank_logos/` owns curated institution-name resolution and bounded public icon fetching. Keep its helpers and mocked provider tests in this folder. The host owns image-route validation and caching through the public branding contract.

Resolution stays local. Fetch only allowlisted public bank domains with bounded bytes, short timeouts and no redirects. Provider tests must mock HTTP requests.

From the application directory, run `.venv/bin/python -m pytest plugins/bank-logos/tests`. See [plugin contribution rules](../README.md) for all checks and packaging.
