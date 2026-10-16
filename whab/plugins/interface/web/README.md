# Web interface plugin

`src/whab_web/` owns the bundled visual descriptor, view models, icons, Jinja templates and browser assets. Keep rendering and presentation tests alongside them in this folder. The host owns HTTP routes and access guards through the public UI contract.

Templates and static files must ship in the wheel. Use demo data for browser checks and preserve escaping and visual fallbacks.

Transactions display merchant artwork supplied by the enabled Plaid plugin through a local image route. The payee name and notes remain visible; artwork loads independently and falls back to the merchant's initial on failure. Transactions without supplied artwork keep their text-only presentation.

From the application directory, run `.venv/bin/python -m pytest plugins/interface/web/tests`. See [plugin contribution rules](../../README.md) for all checks and packaging.

Settings contains **Budgets**, **Plugins**, and **About this prototype**. The Budgets entry shows the current budget and retains separate personal/demo workspace choices. Workspace switching preserves the page, month and currency and clears the previous category selection. The portable budget panel creates, opens and switches `.whab` containers, manages saved copies and displays pending-save status. A remote connection container leaves financial records on the server. The same Settings controls work on desktop and mobile.

Open **Settings → Plugins → Installed** to select plugins. The category filter starts with **All categories**, with alphabetical category sections and plugin names. Example plugins are hidden, and valid existing example selections survive saving other choices. Required storage and interface plugins stay selected. Saving an optional selection applies it on the next restart and preserves the running plugins until then.

Every installed plugin has an **Options** page with its category, version, saved selection and running state. Running plugins can expose generic settings information and actions through the public SDK; inactive plugins show enable/restart guidance without running their hooks. Settings values and action feedback render as text. The bank-logo plugin uses this page for its one-year cache policy and **Flush cache** action.

**Download budget** keeps credentials on the device. The additional export form can explicitly include a password-encrypted remote access credential. Only that credential is encrypted; the rest of the container remains readable plugin data, and bank-provider tokens are never included. Upload and saved-budget switching accept an optional password without placing it in URLs or browser storage.
