# Web interface plugin

Distributed independently as `whab-web-plugin`. Its version and dependency compatibility range are defined in [pyproject.toml](pyproject.toml); its dated history belongs in [CHANGELOG.md](CHANGELOG.md). Material changes confined to this plugin update its version and changelog, without an app version or app changelog change.

`src/whab_web/` owns the bundled visual descriptor, view models, icons, Jinja templates and browser assets. Keep rendering and presentation tests alongside them in this folder. The host owns HTTPS routes and access guards through the public UI contract. Every request requires TLS; ordinary visual/private routes remain loopback-only, with only bearer-authorized remote protocol and verified bank-webhook routes exempt from local Host/origin checks.

Templates and static files must ship in the wheel. Use demo data for browser checks and preserve escaping and visual fallbacks.

The budget workspace uses a dark navy skin with three consecutive months aligned over one category table. Each month shows its core-calculated **To assign**, **Assigned**, **Spent** and **Available** summary, with **Assigned**, **Activity** and **Available** columns below. The month strip moves the visible window; category groups collapse together across months. Select a category to open its details, or edit its assigned amount directly in the appropriate month. Assignments before the budget start month remain disabled, and every save goes through core validation, including future funding limits. Settings remains anchored at the bottom left on desktop and mobile.

The descriptor requests `budget_months=3` from WHAB 2.2 or newer. The host supplies selected-currency public projections in `data['periods']`; the plugin only arranges and formats them. Rollover, spending, card reserves, available money and funding rules are not calculated in browser JavaScript or duplicated in the skin. Storage plugins continue to implement the existing single-month query contract.

Transactions display merchant artwork supplied by the enabled Plaid plugin through a local image route. The payee name and notes remain visible; artwork loads independently and falls back to the merchant's initial on failure. Transactions without supplied artwork keep their text-only presentation.

Accounts and the sidebar separate **On-budget** everyday accounts from **Off-budget** assets and debts. On-budget checking, savings and credit accounts participate in budget income and spending. Off-budget investments, retirement accounts, property, loans and deposits (including CDs) contribute to net worth while their transactions remain available for review. Assets, liabilities and net worth use current known balances in the selected currency; stale and unavailable snapshots are excluded. Currencies are never combined.

**Add manual account** offers budget scope, account type and relevant account details, such as brokerage, Roth IRA, 401(k), home, mortgage or CD. Enter liabilities as the positive amount owed. **Edit classification** changes an existing account's scope, type and detail; its scope applies to every transaction in that account and updates budget totals accordingly.

From the application directory, run `.venv/bin/python -m pytest plugins/interface/web/tests`. See [plugin contribution rules](../../README.md) for all checks and packaging.

Settings contains **Budgets**, **Plugins**, and **About this prototype**. The Budgets entry shows the current budget and retains separate personal/demo workspace choices. Workspace switching preserves the page, month and currency and clears the previous category selection. The portable budget panel creates, opens and switches `.whab` containers, manages saved copies and displays pending-save status. A remote connection container leaves financial records on the server. The same Settings controls work on desktop and mobile.

Open **Settings → Plugins → Installed** to select plugins. The category filter starts with **All categories**, with alphabetical category sections and plugin names. Example plugins are hidden, and valid existing example selections survive saving other choices. Required storage and interface plugins stay selected. Saving an optional selection applies it on the next restart and preserves the running plugins until then.

Every visible installed-plugin row has an **Options** page; hidden teaching examples have no ordinary Settings row or Options page. Each visible page includes its category, version, saved selection and running state. Running plugins can expose generic settings information and actions through the public SDK; inactive plugins show enable/restart guidance without running their hooks. Settings values and action feedback render as text. The bank-logo plugin uses this page for its one-year cache policy and **Flush cache** action.

**Download budget** keeps credentials on the device. The additional export form can explicitly include a password-encrypted remote access credential. Only that credential is encrypted; the rest of the container remains readable plugin data, and bank-provider tokens are never included. Upload and saved-budget switching accept an optional password without placing it in URLs or browser storage.
