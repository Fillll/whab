# Web interface plugin changelog

## 1.6.0 — 2026-10-16

PR: [#35](https://github.com/Fillll/whab/pull/35)

- List every saved budget alongside the single Demo budget destination with a current selection and a Download action for each row. Downloads address their own budget independently of other tabs; demo downloads contain only fictional data.
- Place Create new budget and Open existing budget in separate sections of the same dialog. Red Open selected budget, Create budget and Open budget actions leave Settings only after success; password and file errors stay in the dialog.
- Keep saved-copy and encrypted remote-credential controls under Saved copies and export options. Require host 2.6 for targeted downloads and preserve canonical lowercase navigation.

## 1.5.2 — 2026-10-16

PR: [#38](https://github.com/Fillll/whab/pull/38)

- Place the status dot and app version on one sidebar footer row, with an icon-only Settings button aligned to the right. Preserve its accessible name and compact-screen touch target.

## 1.5.1 — 2026-10-16

PR: [#37](https://github.com/Fillll/whab/pull/37)

- Show the running WHAB application's version below Settings in the sidebar.

## 1.5.0 — 2026-10-16

PR: [#36](https://github.com/Fillll/whab/pull/36)

- Navigate with lowercase budget, page, month and currency path segments. Preserve `current-month` when changing page or currency and select a fixed month when using the month arrows.
- Present Demo Budget as one separate sample destination alongside My Budget and named budgets. Remove the personal/demo workspace segment and keep stable budget identities independent of display names.
- Scope browser requests, plugin controls and downloads to the address's budget. Require WHAB 2.5 or newer for its canonical routing context.

## 1.4.0 — 2026-10-16

PR: [#34](https://github.com/Fillll/whab/pull/34)

- Add a prominent **New budget** action in Settings → Budgets with a dedicated name form, Create budget and Cancel controls. Open the new personal workspace after successful creation through the existing core budget API.
- Move saved-budget switching above portable-file controls and keep exports and saved copies in a separate lower section.

## 1.3.0 — 2026-10-16

PR: [#33](https://github.com/Fillll/whab/pull/33)

- Add Light and Dark appearance options in Settings → Plugins → WHAB web → Options, with the compass logo's red and yellow accents. Apply changes immediately and remember the choice in this browser.
- Use warm light and charcoal dark surfaces with accessible action, selection and status colors. Restore the appearance before first paint and version browser assets with the web plugin's own version.

## 1.2.0 — 2026-10-16

PR: [#32](https://github.com/Fillll/whab/pull/32)

- Replace the single-month budget layout with an Actual-inspired dark workspace: three aligned monthly summaries and a continuous category table with Assigned, Activity and Available columns.
- Add month-strip navigation and direct assignment editing using core-validated commands, with collapsible groups and category details opened on demand. Preserve currency precision, workspace isolation and disabled assignments before the budget start month.
- Keep Settings at the bottom left, including on narrow screens. Keep layout, colors, month labels and browser interactions in the web plugin without duplicating financial calculations.

## 1.1.0 — 2026-10-16

PR: [#31](https://github.com/Fillll/whab/pull/31)

- Separate on-budget accounts from off-budget assets and debts in Accounts and the sidebar, with same-currency assets, liabilities and net worth.
- Create and reclassify accounts by budget scope, asset or liability type, and account detail, including investments, retirement, property, loans and deposits.
- Keep off-budget transactions visible while excluding their activity from budget spending insights and on-budget cash balances.

## 1.0.0 — 2026-10-15

PR: [#30](https://github.com/Fillll/whab/pull/30)

- Introduce independent packaging and version ownership for the existing `web` plugin, compatible with WHAB 2.x. Earlier changes shipped inside the application distribution and were not independent plugin releases.
- Preserve its plugin ID, Python package, entry point and existing behavior.
