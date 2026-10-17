# Web interface plugin changelog

## 1.5.0 — 2026-10-16

- Navigate with lowercase budget, page, month and currency path segments. Preserve `current-month` when changing page or currency and select a fixed month when using the month arrows.
- Present Demo Budget as one separate sample destination alongside My Budget and named budgets. Remove the personal/demo workspace segment and keep stable budget identities independent of display names.
- Scope browser requests, plugin controls and downloads to the address's budget. Require WHAB 2.5 or newer for its canonical routing context.

## 1.4.0 — 2026-10-16

- Add a prominent **New budget** action in Settings → Budgets with a dedicated name form, Create budget and Cancel controls. Open the new personal workspace after successful creation through the existing core budget API.
- Move saved-budget switching above portable-file controls and keep exports and saved copies in a separate lower section.

## 1.3.0 — 2026-10-16

- Add Light and Dark appearance options in Settings → Plugins → WHAB web → Options, with the compass logo's red and yellow accents. Apply changes immediately and remember the choice in this browser.
- Use warm light and charcoal dark surfaces with accessible action, selection and status colors. Restore the appearance before first paint and version browser assets with the web plugin's own version.

## 1.2.0 — 2026-10-16

- Replace the single-month budget layout with an Actual-inspired dark workspace: three aligned monthly summaries and a continuous category table with Assigned, Activity and Available columns.
- Add month-strip navigation and direct assignment editing using core-validated commands, with collapsible groups and category details opened on demand. Preserve currency precision, workspace isolation and disabled assignments before the budget start month.
- Keep Settings at the bottom left, including on narrow screens. Keep layout, colors, month labels and browser interactions in the web plugin without duplicating financial calculations.

## 1.1.0 — 2026-10-16

- Separate on-budget accounts from off-budget assets and debts in Accounts and the sidebar, with same-currency assets, liabilities and net worth.
- Create and reclassify accounts by budget scope, asset or liability type, and account detail, including investments, retirement, property, loans and deposits.
- Keep off-budget transactions visible while excluding their activity from budget spending insights and on-budget cash balances.

## 1.0.0 — 2026-10-15

- Introduce independent packaging and version ownership for the existing `web` plugin, compatible with WHAB 2.x. Earlier changes shipped inside the application distribution and were not independent plugin releases.
- Preserve its plugin ID, Python package, entry point and existing behavior.
