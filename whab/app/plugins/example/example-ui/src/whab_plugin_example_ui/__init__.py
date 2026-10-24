"""Read-only fictional visual plugin; the host owns all financial operations."""

from whab.plugins import Plugin, PluginContract, PluginResources, UserInterface

PAGE = b'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{{ budget_name }}</title>
<link rel="stylesheet" href="{{ ui_asset_prefix }}/static/style.css"></head>
<body data-ui-generation="{{ client_context.uiGeneration }}" data-api="{{ api_prefix }}">
<header><p>Fictional teaching interface</p><h1>{{ budget_name }}</h1>
<nav>{% for item in ('budget','transactions','accounts','insights') %}<a href="{{ url(item) }}">{{ item|title }}</a> {% endfor %}</nav></header>
<main><h2>{{ page|title }}</h2><p>This example displays public budget projections. It has no editable fields.</p>
<p>Accounts: {{ data.accounts|length }}. Transactions: {{ data.transactions|length }}.</p>
<p>Currency: {{ data.currency }}. Monetary values use integer minor units.</p></main>
<script type="module" src="{{ ui_asset_prefix }}/static/app.js"></script></body></html>'''

SCRIPT = b'''// No form data or credentials enter the browser handover protocol.
const root = document.body;
const base = root.dataset.api + '/ui/views';
const generation = root.dataset.uiGeneration;
const json = async (url, method = 'GET', body) => {
  const response = await fetch(url, {method, headers: {'Content-Type':'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body), cache:'no-store'});
  if (!response.ok) throw new Error('View status unavailable');
  return response.json();
};
const view = await json(base, 'POST', {generation, dirty:false});
const tick = async () => {
  try {
    const state = await json(base + '/' + view.view);
    if (state.reload) { location.reload(); return; }
    if (state.pending) {
      root.inert = true;
      await json(base + '/' + view.view, 'PUT', {generation, dirty:false, prepared:state.pending.id});
    } else root.inert = false;
  } catch { /* Keep a prepared view frozen until its decision is observable. */ }
  setTimeout(tick, 1000);
};
tick();
addEventListener('pagehide', () => { fetch(base + '/' + view.view, {method:'DELETE', keepalive:true}); });
'''


def build_context(data, status, items, **values):
    return {"data": data}


plugin = Plugin("example-ui", "Fictional visual interface", contract=PluginContract(lifecycle="live"),
                ui=UserInterface(None, None, build_context, view_protocol=1, resources=PluginResources({
                    "templates/base.html": PAGE, "static/app.js": SCRIPT,
                    "static/style.css": b"body{max-width:60rem;margin:3rem auto;padding:0 1rem;font:1rem system-ui;line-height:1.6}nav a{margin-right:1rem}header{border-bottom:1px solid #bbb}body[inert]{opacity:.6}",
                })))
