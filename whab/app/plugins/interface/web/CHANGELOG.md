# Web interface plugin changelog

## 1.26.0 — 2026-10-23

PR: [#94](https://github.com/Fillll/whab/pull/94)

- Add Storage copies settings with primary health, financial and plugin-state watermarks, pending/failure status, retry and disable controls. Review an empty destination before attachment and review promotion separately; retain edit barriers and show completion only after the host confirms authority.

## 1.25.1 — 2026-10-22

PR: [#74](https://github.com/Fillll/whab/pull/74)

- Display sidebar account balances as whole currency amounts without decimal places or approximation markers. Round half units away from zero and retain balance-source descriptions on hover and for screen readers.

## 1.25.0 — 2026-10-20

PR: [#73](https://github.com/Fillll/whab/pull/73)

- Show signed YNAB transaction-derived balance estimates beside sidebar accounts instead of dashes. Mark estimates and stale snapshots with `≈` and accessible source descriptions, preserve current balances and unavailable snapshots, and keep amounts readable beside truncated account names.

## 1.24.0 — 2026-10-18

PR: [#70](https://github.com/Fillll/whab/pull/70)

- Add an explicit low-score preview button with cutoff-preservation guidance. Mark uncertain guesses with their score and Low score label, and identify model predictions and the Random forest plugin with a forest icon.

## 1.23.1 — 2026-10-18

PR: [#69](https://github.com/Fillll/whab/pull/69)

- Hide the training progress bar and cancel button after a failed request, preserving the error message and restoring parameter editing for a retry.

## 1.23.0 — 2026-10-18

PR: [#68](https://github.com/Fillll/whab/pull/68)

- Add random forest plugin settings with parameters, progress/cancellation, saved model selection, rename/delete and validation results. Show provisional ML category badges and apply the chosen category only during manual review, including selected-row review.

## 1.22.1 — 2026-10-18

PR: [#67](https://github.com/Fillll/whab/pull/67)

- Show Account immediately after Date when multiple accounts are selected, including All accounts. Keep the column visible across search results and omit it in single-account registers.

## 1.22.0 — 2026-10-18

PR: [#66](https://github.com/Fillll/whab/pull/66)

- Keep the app shell mounted when navigating between main views in the same budget and currency. Replace validated server-rendered content, remount editors and controls, refresh dialog choices, and preserve save barriers, history, scroll, focus and stale-response guards. Leave the old content visible while loading and retain full navigation for incompatible assets or scope changes.

## 1.21.0 — 2026-10-18

PR: [#65](https://github.com/Fillll/whab/pull/65)

- Request receipt markers only for the current register page when supported by the host, and skip the index request on empty pages. Preserve older-host requests, disabled controls until confirmation, disposal guards and existing image operations.

## 1.20.1 — 2026-10-18

PR: [#64](https://github.com/Fillll/whab/pull/64)

- Release detached budget editors after successful account navigation, including their saved-form references and listeners. Preserve save and validation barriers, and cancel pending account navigation when a new budget draft appears.

## 1.20.0 — 2026-10-18

PR: [#63](https://github.com/Fillll/whab/pull/63)

- Coalesce pure review changes during supported bulk actions into bounded atomic requests, retaining row writers, save barriers and confirmed retry order. Older providers retain their existing individual requests; queued memo and category changes remain ordered, and failed submitted batches are confirmed before later row edits.

## 1.19.3 — 2026-10-18

PR: [#61](https://github.com/Fillll/whab/pull/61)

- Omit the unused sidebar account-list template from ordinary register navigation responses. Explicit refreshes and older clients still receive the complete account list; summary requests retain their compact response and navigation keeps its existing save, race and focus guards.

## 1.19.2 — 2026-10-18

PR: [#60](https://github.com/Fillll/whab/pull/60)

- Populate the two hidden account dialogs' currency choices when opened, using one compact server-rendered catalogue. Preserve selected currencies, fetched account settings, form reset defaults, history locks and exact currency minor-unit parsing.

## 1.19.1 — 2026-10-18

PR: [#59](https://github.com/Fillll/whab/pull/59)

- Refresh the server-rendered review count with a compact summary after unfiltered review changes, preserving the existing transaction rows, editors and listeners. Keep full refreshes for search and review-filtered views, and reject late or mismatched responses.
- Require WHAB 2.14.1 or newer for the compact fragment response contract.

## 1.19.0 — 2026-10-18

PR: [#58](https://github.com/Fillll/whab/pull/58)

- Request bounded register pages and account metadata from the host, preserving full-selection totals, search, filters, pagination and review badges while avoiding full-history page inputs.
- Defer optional account and merchant artwork until visible and limit concurrent loads so image requests leave capacity for navigation and saves. Retain immediate explicit previews and decorative fallbacks.
- Refresh review counts after partial navigation and review changes, and keep the count readable in the selected navigation item.

## 1.18.0 — 2026-10-18

PR: [#57](https://github.com/Fillll/whab/pull/57)

- Render at most 100 transactions per page, with full-history payee/memo search and review filtering inside the selected account and date range. Preserve filters in page links and support search without JavaScript.
- Flush pending edits before pagination and filtering, cancel superseded searches, and validate search/page scope before replacing a register fragment.
- Version every web module dependency so the built-in browser loads matching scripts after an update.

## 1.17.0 — 2026-10-18

PR: [#56](https://github.com/Fillll/whab/pull/56)

- Use flat yellow navigation and selected category labels without the inset accent edge or rounded navigation corners.
- Add account search and explicit Select all/Select none controls to the multi-account picker. Keep filtered-out selections, show selected counts and require at least one account before applying a cleared draft.
- Rename user-facing plugin Options links and dialogs to Settings, including the Account logo shortcut and plugin manager descriptions.
- Align installed plugin rows as name, package/version, saved enabled state and Settings, retaining accessible selection controls and a compact mobile layout.

## 1.16.0 — 2026-10-18

PR: [#55](https://github.com/Fillll/whab/pull/55)

- Show optional bank logos in a keyboard-accessible account picker, with multiple selections applied together and generic artwork fallbacks.
- Replace the month field and All dates checkbox with current/previous-month presets and custom inclusive date bounds. Add a compact three-state R filter for Needs review, Reviewed and All.
- Preserve date/review choices during account navigation, retain latest rapid filter changes, and reject stale fragments whose account or date scope differs from the requested register.

## 1.15.2 — 2026-10-18

PR: [#54](https://github.com/Fillll/whab/pull/54)

- Reserve an in-flow line inside each month card for rollover Saving/Saved feedback, preventing overlap and clipping.
- Mark months with negative funding capacity using a compact red Funding shortfall label, retaining true signed balances and keeping the exact cash-constrained amount in the existing Available to assign disclosure.

## 1.15.1 — 2026-10-18

PR: [#53](https://github.com/Fillll/whab/pull/53)

- Place Retrieve logo beside the Bank website input with matching control heights. Press Enter in that field to preview the logo without submitting account settings, while retaining duplicate-request and active-save guards.

## 1.15.0 — 2026-10-18

PR: [#52](https://github.com/Fillll/whab/pull/52)

- Accept bare bank domains and add optional Retrieve logo with an immediate preview. Keep missing artwork and invalid website input from blocking account Save, and identify the external source and indefinite cache policy.
- Request lightweight transaction-register projections and render category choices once, populating native dropdowns on pointer or keyboard focus. Keep account switching fresh and avoid caching financial page HTML.

## 1.14.0 — 2026-10-18

PR: [#51](https://github.com/Fillll/whab/pull/51)

- Use a compact transaction table with independent multi-select, date, receipt, payee, category, memo, signed amount, currency and a small reviewed toggle. Save edits in place and finish pending writes before switching accounts or exporting.
- Replace only the main register during account navigation, preserve the sidebar and scroll, and use readable budget/account URLs. Add account settings for name, classification, empty-account currency and plugin-labelled bank artwork.
- Connect Assigned this month to its matching table total instead of listing every category. Move reserve wording into its disclosure and preserve the sticky title-column border while scrolling.

## 1.13.0 — 2026-10-18

PR: [#50](https://github.com/Fillll/whab/pull/50)

- Give the saved-budget list an Open budget heading, and label the file section and its action Add budget. Keep budget creation in its dedicated section and explain that adding a file also opens it.
- Use centered, inset arrows across native dropdowns, including account filters, transaction categories, currency and plugin category controls.
- Show enabled file import forms in their plugin Options dialog, including from Demo, instead of on Accounts.
- Open individual sidebar accounts in their transaction register across all dates, with account and date controls that preserve the selected view.

## 1.12.1 — 2026-10-18

PR: [#49](https://github.com/Fillll/whab/pull/49)

- Save budget assignments and rollover choices in place instead of reloading the page. Apply changed server-rendered values while preserving focus, unsaved edits, scroll position, expanded explanations and collapsed groups.
- Queue edits without locking unrelated fields, and show local saving, saved and error feedback. Keep calculations and validation in core.
- Remove the two budget footer notes and the duplicate bottom currency label.

## 1.12.0 — 2026-10-18

PR: [#48](https://github.com/Fillll/whab/pull/48)

- Add a per-month Where it comes from disclosure to Income, Assigned this month and Available to assign, alongside the existing carry-in breakdown. Explain received income, show category assignments and display the monthly funding formula with its actual component amounts.
- Keep next-month funding editable and unavailable totals clearly unavailable. Preserve cash-capacity guidance, currency precision and keyboard-accessible native disclosures.

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
