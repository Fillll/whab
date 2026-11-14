# Agentic Work 101

Use your coding agent as WHAB's everyday interface: ask it to inspect transactions, help with categories and plan a budget through `whab`. No WHAB-managed AI account is required. Start with fictional data; use your own budget when you are ready.

## 1. Install locally

Install **Python 3.15.x** and Git, then run:

```sh
git clone https://github.com/Fillll/whab.git
cd whab/whab/app
python3.15 -m venv .venv
.venv/bin/python scripts/install.py --dev
. .venv/bin/activate
```

Already cloned? Start in `whab/app/` and omit the first two commands. The installer installs the host, CLI and supported plugins from the checkout. It also installs development tools so an agent can extend the app. Before upgrading an existing setup, [preserve its runtime home and keys](../README.md#existing-development-checkouts).

## 2. Create a practice budget

Keep runtime files outside the checkout. In the same terminal:

```sh
export WHAB_HOME="$HOME/.local/share/whab-practice"
whab --environment sandbox budget create "Practice budget"
whab --environment sandbox budget list
whab --environment sandbox "Practice budget" account list --format table
whab --environment sandbox "Practice budget" trnx list --format table
```

This starts with an empty budget. CLI commands start a local HTTPS host as needed; no browser server or bank credentials are required. `--environment sandbox` is explicit because the ordinary CLI otherwise defaults to production. Your agent must use this same runtime home and environment.

## 3. Start with your agent

Open this checkout in your coding agent and give it a concrete task, for example:

> Use WHAB through its CLI. Our runtime home is `~/.local/share/whab-practice`, environment sandbox, budget "Practice budget". Read `AGENTS.md` and `whab --help`, inspect the budget, and help me set up fictional accounts and categories. Ask about choices that are unclear, preview bulk edits, and verify saved results. Label agent category decisions with `--agent`.

Every command has offline help. Useful starting points:

```sh
whab --help
whab "Practice budget" account --help
whab "Practice budget" category --help
whab "Practice budget" trnx --help
```

For a populated budget, the ordinary workflow is to list transactions, discuss uncertain choices, preview edits, apply your decisions and verify them. Classification and review are separate steps. See the [CLI workflow and examples](../app/plugins/interface/cli/README.md#agent-workflow).

When you are ready for real data, choose its runtime home and use `--environment production` explicitly. List budgets before acting so the agent addresses the intended budget. Add [Web](web-quick-start.md) or [Plaid bank imports](plaid-quick-start.md) when you need them.

## When a capability is missing

Ask the agent to extend an existing plugin or build one in Python, following the repository's `AGENTS.md`, [architecture ownership rules](architecture.md#choosing-the-owner-of-a-change) and [plugin SDK](plugins.md). Develop against isolated fictional budgets, validate the change and use a feature branch and PR. Budget operations use public CLI/SDK contracts; runtime databases and private plugin state are not an extension API.
