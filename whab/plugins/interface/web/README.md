# Web interface plugin

`src/whab_web/` owns the bundled visual descriptor, view models, icons, Jinja templates and browser assets. Keep rendering and presentation tests alongside them in this folder. The host owns HTTP routes and access guards through the public UI contract.

Templates and static files must ship in the wheel. Use demo data for browser checks and preserve escaping and visual fallbacks.

From the application directory, run `.venv/bin/python -m pytest plugins/interface/web/tests`. See [plugin contribution rules](../../README.md) for all checks and packaging.
