<p align="center">
  <img src="whab/app/plugins/interface/web/src/whab_plugin_web/static/logo.svg" alt="WHAB logo" width="128" height="128">
</p>
<h1 align="center">WHAB</h1>
<p align="center"><strong>We Have A Budget</strong></p>

An agent-first, highly extensible budgeting app built with Python. Agents are the primary users we design for, with a CLI for everyday budgeting and Python plugins they can build and extend when a capability is missing. Plan with monthly envelopes and rollover, review transactions, explore spending analytics, and track on-budget accounts alongside off-budget assets and debts, with automatic bank imports. Choose plugins to keep your setup as simple or as capable as you need.

## Guides

- [Agentic Work 101: start locally with an agent](whab/docs/agentic-work-101.md)
- [Start without an agent: use the Web interface](whab/docs/web-quick-start.md)
- [Automatic bank imports with Plaid](whab/docs/plaid-quick-start.md)
- [Application setup and configuration](whab/app/README.md) · [Usage, portable budgets and backups](whab/docs/usage.md)
- [Architecture](architecture.md) · [Budget and plugin model](whab/docs/target-architecture.md)
- [Plugin API](whab/docs/plugins.md) · [Plugin folders](whab/app/plugins/README.md)
- [Contributing and checks](whab/app/CONTRIBUTING.md) · [Previews, verification and troubleshooting](whab/docs/development.md) · [Changelog](whab/app/CHANGELOG.md)
- [Private vulnerability reporting and supported versions](SECURITY.md)

## Privacy and control

Your budget, your setup. WHAB can run completely locally in an isolated environment. Enable plugins when you need their capabilities, choose which services they connect to, and disable them when you no longer need them. This keeps your environment minimal, understandable and under your control. Plugins run as trusted Python code, so choose the code and connections you enable deliberately.

## License

[MIT](LICENSE). Third-party dependencies, services and assets retain their own licenses or terms.
