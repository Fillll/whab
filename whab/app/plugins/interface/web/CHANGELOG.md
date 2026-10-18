# Web interface plugin changelog

## 1.11.0 — 2026-10-18

PR: [#47](https://github.com/Fillll/whab/pull/47)

- Explain Carried in as previously unassigned money, funds set aside for this month and overall category carryover, with an expandable core-calculated breakdown. Keep by-category balances exclusively in category Carryover columns.
- Support independent next-month set-asides and show the exact monthly allocation formula from WHAB 2.9. Leave older-server funding controls read-only and incompatible totals unavailable until upgraded.
- Explain when actual cash, spending or account reserves constrain new assignments below the displayed monthly pool.

## 1.10.0 — 2026-10-17

PR: [#45](https://github.com/Fillll/whab/pull/45)

- Simplify month cards to their date and Carryover control, removing the repeated Available, Assigned, Spent and large To assign values.
- Show one amount per month for Income, Carried in, Assigned for next month, Assigned this month and Available to assign. Use core's existing assignment and remaining-funding totals, with negative funding clearly marked.
- Move detailed Carryover, Assigned, Activity and Available column headings below Spending categories, preserving category editors, totals and sticky headings.

## 1.9.0 — 2026-10-17

PR: [#44](https://github.com/Fillll/whab/pull/44)

- Combine Previous overspending and Balance carried in into one read-only Carried in row showing the signed net arriving from previous months. Identify whether balances remain in categories or arrive as an overall balance, without adding or deducting money again.
- Rename the monthly summary row Available to spend, keeping Income and Assigned for next month above the spending categories. Preserve the existing category carryover columns and calculated monthly totals.

## 1.8.0 — 2026-10-17

PR: [#43](https://github.com/Fillll/whab/pull/43)

- Show a read-only Carryover column before Assigned when the previous month carries balances by category. Include signed category balances and group and monthly totals, with the source month in the heading.
- Keep months receiving an overall balance and the first budget month in the three-column layout. Require WHAB 2.8 for currency-scoped incoming rollover metadata.

## 1.7.0 — 2026-10-17

PR: [#41](https://github.com/Fillll/whab/pull/41)

- Show Available, Assigned and Spent above the larger To assign amount, and remove the decorative four-square icon from month headings.
- Add Income, Previous overspending, Assigned for next month and Assigned this month rows above Spending categories. Make next-month funding and individual category assignments visibly editable with outlined fields and pencil marks, while monthly totals and parent groups carry read-only Summary labels.
- Save a chosen next-month funding total through core validation without distributing it among categories. Keep unsupported older-server rows read-only, preserve valid totals, and show dashes for historical or unavailable planning values.
- Add a per-month Carryover choice between By category and Overall balance, with the destination month shown explicitly. Show the signed balance carried into general funds and explain category Available amounts on hover.
- Serialize budget saves across assignments and rollover choices, retaining the current values and displaying errors when a save fails.
- Require WHAB 2.7 for the new core monthly projection fields.

## 1.6.1 — 2026-10-16

PR: [#40](https://github.com/Fillll/whab/pull/40)

- Rename the independent distribution and Python package to `whab_plugin_web` under the shared `whab_plugin_<name>` convention. Preserve the plugin ID and its existing saved data and settings.

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
