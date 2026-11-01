/* Independent subscription workspace; labels never change budget categories. */
import {api, getContext} from './client.js';
import {credentials, element as el, field, option} from './ai-credentials-settings.js';
import {classificationRunning, createSubscriptionJobs, renderSubscriptionProgress} from './subscription-jobs.js';
import {createPlanTableState, renderSubscriptionPlans} from './subscription-plans.js';

export function money(value, currency) {
  const digits = new Intl.NumberFormat('en', {style: 'currency', currency}).resolvedOptions().maximumFractionDigits;
  return new Intl.NumberFormat(undefined, {style: 'currency', currency}).format(value / 10 ** digits);
}

export function initSubscriptions() {
  const dialog = document.getElementById('subscriptions-workspace');
  if (!dialog) return;
  const cache = document.getElementById('subscriptions-workspace-cache');
  const active = () => getContext().page === 'subscriptions' && dialog.parentElement?.hasAttribute('data-subscriptions-mount');
  const content = dialog.querySelector('[data-subscriptions-content]');
  const status = dialog.querySelector('[data-subscriptions-status]');
  const error = dialog.querySelector('[data-subscriptions-error]');
  const progress = dialog.querySelector('[data-subscriptions-progress]');
  const stopButton = dialog.querySelector('[data-subscriptions-stop]');
  const badge = document.querySelector('[data-subscriptions-job-badge]');
  const planState = createPlanTableState();
  let busy = false, filter = null, searchQuery = '', limit = 100, last, job = null, viewGeneration = 0, jobSupported = true, refreshOnIdle = false;
  const call = (action, values = {}) => {
    const context = getContext();
    return api('/plugin-settings/subscriptions', {workspace: context.workspace, currency: context.currency, month: context.month, action, ...values});
  };
  const scope = () => `${getContext().apiPrefix}|${getContext().workspace}`;
  const jobs = createSubscriptionJobs({call, scope,
    onChange(value) {
      job = value; renderSubscriptionProgress(progress, job); lock(busy);
      if (error.dataset.subscriptionProgressError) {error.hidden = true; delete error.dataset.subscriptionProgressError;}
      if (badge) badge.hidden = !classificationRunning(job);
    },
    onComplete() {if (active()) void perform(refresh);},
    onError(failure) {if (active()) {error.dataset.subscriptionProgressError = 'true'; error.textContent = `Progress could not be refreshed. Classification may still be running. ${failure.message}`; error.hidden = false;}}
  });
  stopButton.addEventListener('click', () => {void perform(() => jobs.stop());});
  const format = value => money(value, getContext().currency);
  async function perform(work) {
    if (busy) return;
    busy = true; error.hidden = true; lock(true);
    try { await work(); }
    catch (failure) { error.textContent = failure.message; error.hidden = false; status.textContent = 'Your saved labels are preserved. You can retry.'; }
    finally { busy = false; lock(false); if (refreshOnIdle && active()) {refreshOnIdle = false; void perform(refresh);} }
  }
  function lock(value) {
    for (const node of content.querySelectorAll('button,input,select')) node.disabled = value ||
      classificationRunning(job) && Boolean(node.closest('[data-subscriptions-mutation]')) ||
      !jobSupported && Boolean(node.closest('[data-subscriptions-start]'));
    stopButton.hidden = !classificationRunning(job);
    stopButton.disabled = value || job?.status === 'stopping';
  }
  function button(label, handler, cls = 'button secondary') {
    const node = el('button', label, cls); node.type = 'button';
    node.addEventListener('click', () => perform(handler)); return node;
  }
  async function refresh() {
    const token = viewGeneration, owner = scope();
    const data = await call('status');
    if (!active() || token !== viewGeneration || owner !== scope()) return;
    refreshOnIdle = false; last = data; jobSupported = Boolean(data.job);
    if (data.job) jobs.accept(data.job);
    else {error.textContent = 'Update the Subscriptions plugin to 1.1.0 or newer for background classification.'; error.hidden = false;}
    const scroll = dialog.scrollTop; render(last); dialog.scrollTop = scroll;
    if (status.textContent === 'Loading subscriptions…') status.textContent = '';
  }
  async function classify(config) {
    dialog.scrollTop = 0;
    await jobs.start(config);
    if (active()) await refresh();
  }
  function breakdown(parent, title, values) {
    const section = el('section', '', 'subscription-breakdown'); section.append(el('h3', title));
    const maximum = Math.max(1, ...values.map(row => row.amount));
    for (const row of values.slice(0, 8)) {
      const line = el('div', '', 'subscription-bar-row');
      const label = el('div'); label.append(el('span', row.name), el('strong', format(row.amount))); line.append(label);
      const meter = el('meter'); meter.min = 0; meter.max = maximum; meter.value = row.amount;
      meter.setAttribute('aria-label', `${row.name}: ${format(row.amount)}`); line.append(meter); section.append(line);
    }
    if (!values.length) section.append(el('p', 'No classified subscriptions yet.', 'settings-notice'));
    parent.append(section);
  }
  function reviewRows(data, parent) {
    const toolbar = el('div', '', 'subscription-toolbar'); parent.append(toolbar);
    const select = field(toolbar, 'Show payments', 'filter', 'select');
    for (const [value, label] of [['subscriptions', 'Subscription charges'], ['needs-review', `Needs review (${data.needsReview})`], ['unclassified', `Not checked (${data.unclassified})`], ['all', 'All transactions']]) option(select, value, label);
    select.value = filter;
    select.addEventListener('change', () => { filter = select.value; limit = 100; render(data); });
    const search = field(toolbar, 'Merchant or category', 'search', 'search'); search.value = searchQuery;
    const results = el('div'); parent.append(results);
    function populate() {
      results.replaceChildren();
      const query = search.value.trim().toLocaleLowerCase();
      const rows = data.transactions.filter(row => (filter === 'all' || (filter === 'subscriptions' ? row.status === 'subscription' : row.status === filter)) && `${row.merchant} ${row.category}`.toLocaleLowerCase().includes(query));
      const table = el('table', '', 'subscription-table'); const head = el('thead'), headings = el('tr');
      for (const label of ['Date / merchant', 'Account / category', 'Charge', 'Classification / billing', 'Review']) headings.append(el('th', label));
      head.append(headings); table.append(head); const body = el('tbody'); table.append(body);
      for (const row of rows.slice(0, limit)) {
        const tr = el('tr'); const merchant = el('td'); merchant.append(el('strong', row.merchant), el('small', row.date));
        const category = el('td'); category.append(el('span', row.category), el('small', row.account));
        const label = el('td', row.status.replaceAll('-', ' ')); label.append(el('small', `${row.cadence} · ${row.source || 'unchecked'}`));
        const actions = el('td'); actions.dataset.subscriptionsMutation = '';
        if (row.status !== 'excluded') {
          const choice = field(actions, 'Subscription label', `label-${row.id}`, 'select');
          option(choice, '', 'Choose…'); option(choice, 'no', 'Not a subscription');
          for (const cadence of ['monthly', 'annual', 'quarterly', 'weekly', 'unknown']) option(choice, cadence, `Subscription · ${cadence}`);
          choice.value = row.status === 'not-subscription' ? 'no' : row.status === 'subscription' ? row.cadence : '';
          const save = button('Save label', async () => {
            if (!choice.value) throw new Error('Choose a subscription label and billing interval.');
            await call('override', {transactionId: row.id, fingerprint: row.fingerprint,
              decision: {isSubscription: choice.value !== 'no', cadence: choice.value === 'no' ? 'unknown' : choice.value, score: 1}});
            status.textContent = 'Saved for this payment and matching payments in this account.'; await refresh();
          }); actions.append(save);
        }
        tr.append(merchant, category, el('td', format(row.amount)), label, actions); body.append(tr);
      }
      const wrapper = el('div', '', 'subscription-table-scroll'); wrapper.append(table); results.append(wrapper);
      if (!rows.length) results.append(el('p', 'No payments match this view.', 'settings-notice'));
      if (rows.length > limit) results.append(button(`Show more (${rows.length - limit} remaining)`, async () => {limit += 100; populate();}));
      lock(busy);
    }
    search.addEventListener('input', () => {searchQuery = search.value; populate();}); populate();
  }
  async function settings(data, parent) {
    const connection = await credentials('status');
    if (!active() || last !== data) return;
    const form = el('form', '', 'llm-profile-form'); form.dataset.subscriptionsMutation = ''; form.dataset.subscriptionsStart = '';
    const provider = field(form, 'Provider', 'provider', 'select');
    for (const row of connection.providers) option(provider, row.id, `${row.name}${row.connected ? ' · Connected' : ' · Connect in AI credentials'}`);
    provider.value = data.config.provider;
    const model = field(form, 'Text model ID', 'model'); model.value = data.config.model; model.required = true; model.maxLength = 200;
    const models = el('datalist'); models.id = 'subscription-models'; form.append(models); model.setAttribute('list', models.id);
    form.append(button('Load available models', async () => {const result = await credentials('models', {provider: provider.value}); models.replaceChildren(); for (const row of result.models) option(models, row.id, row.name);}));
    const effort = field(form, 'Effort level', 'effort', 'select');
    function efforts() {effort.replaceChildren(); for (const item of connection.efforts[provider.value]) option(effort, item, item === 'default' ? 'Provider default' : item); effort.value = connection.efforts[provider.value].includes(data.config.effort) ? data.config.effort : 'default';}
    const consent = field(form, 'Allow payees, amounts, currencies, billing days and observed months to be sent to this provider. Account IDs, notes, balances and receipts are excluded. API usage may incur charges.', 'consent', 'checkbox');
    consent.checked = data.config.consent; consent.required = true;
    provider.addEventListener('change', () => {efforts(); model.value = ''; consent.checked = false;}); efforts();
    const save = el('button', 'Save and classify unchecked transactions', 'button primary'); save.type = 'submit'; form.append(save);
    form.addEventListener('submit', event => {event.preventDefault(); void perform(async () => {
      const chosen = connection.providers.find(row => row.id === provider.value);
      if (!chosen.connected) throw new Error('Connect this provider in Settings → AI credentials first.');
      await classify({provider: provider.value, connectionId: chosen.connectionId, model: model.value.trim(), effort: effort.value, consent: consent.checked});
    });}); parent.append(form); lock(busy);
  }
  function render(data) {
    content.replaceChildren();
    const actions = el('div', '', 'subscription-toolbar');
    const modeField = el('div'); modeField.dataset.subscriptionsMutation = ''; actions.append(modeField);
    const mode = field(modeField, 'Classification', 'classification-mode', 'select');
    option(mode, 'manual', 'Manual (no model)');
    if (data.aiAvailable) option(mode, 'ai', 'AI assisted');
    mode.value = data.classificationMode || 'manual';
    mode.addEventListener('change', () => perform(async () => {await call('set-mode', {mode: mode.value}); await refresh();}));
    if (filter === null) filter = mode.value === 'manual' ? 'all' : 'subscriptions';
    const start = button(['stopped','failed','interrupted'].includes(job?.status) ? 'Resume unchecked transactions' : 'Classify unchecked transactions', () => classify(), 'button primary'); start.dataset.subscriptionsMutation = ''; start.dataset.subscriptionsStart = '';
    actions.append(button('Refresh', refresh));
    if (mode.value === 'ai' && data.config.consent) actions.append(start);
    content.append(actions);
    const summary = el('div', '', 'subscription-summary');
    const active = data.subscriptions.filter(row => row.active);
    for (const [label, value] of [['Estimated monthly', format(data.estimatedMonthly)], ['Annualized estimate', data.estimatedAnnual == null ? '—' : format(data.estimatedAnnual)], ['Charged this month', format(data.actualMonth)], ['Active plans', String(active.length)]]) {
      const block = el('div'); block.append(el('small', label), el('strong', value)); summary.append(block);
    }
    content.append(summary, el('p', `${data.unclassified} unchecked · ${data.needsReview} uncertain. Monthly estimates include active plans with a known billing interval. No recent charge means a plan may have ended; it does not confirm cancellation.`, 'settings-notice'));
    const plans = el('section', '', 'subscription-plans'); plans.append(el('h3', 'Your plans'));
    renderSubscriptionPlans(plans, data, planState, {format, digits:new Intl.NumberFormat('en', {style:'currency',currency:getContext().currency}).resolvedOptions().maximumFractionDigits});
    content.append(plans);
    const breakdowns = el('div', '', 'subscription-breakdowns');
    breakdown(breakdowns, 'Monthly cost by category', data.byCategory); breakdown(breakdowns, 'Largest merchants', data.byMerchant); content.append(breakdowns);
    const history = el('section', '', 'subscription-history'); history.append(el('h3', 'Actual charges by month'));
    const maximum = Math.max(1, ...data.months.map(row => row.amount));
    for (const row of data.months) {
      const line = el('div', '', 'subscription-month'); line.append(el('span', row.month));
      const meter = el('meter'); meter.min = 0; meter.max = maximum; meter.value = row.amount; meter.setAttribute('aria-label', `${row.month}: ${format(row.amount)}`);
      line.append(meter, el('strong', format(row.amount)), el('small', row.change > 0 ? `+${format(row.change)} from prior month` : '')); history.append(line);
    }
    content.append(history);
    if (mode.value === 'ai') {
      const configuration = el('details', '', 'llm-disclosure'); configuration.open = !data.config.consent;
      configuration.append(el('summary', 'AI classification settings')); content.append(configuration);
      void settings(data, configuration).catch(failure => {error.textContent = failure.message; error.hidden = false;});
    } else {
      content.append(el('p', 'Label payments below to build your subscription reports. No model or AI account is required.', 'settings-notice'));
      if (!data.aiAvailable && data.config.consent) content.append(el('p', 'Your saved AI settings are preserved. Enable AI subscription classification in Settings → Plugins to use them again.', 'settings-notice'));
    }
    const review = el('section'); review.append(el('h3', 'Review payments'), el('p', 'Saving a label also corrects matching payments: same merchant, amount, billing day and account. Pending payments, transfers and inflows are excluded.', 'settings-notice'));
    content.append(review); reviewRows(data, review); lock(busy);
  }
  function detach() {++viewGeneration; refreshOnIdle = false; cache.append(dialog);}
  function mount() {
    const target = document.querySelector('[data-subscriptions-mount]');
    if (!target) return;
    target.replaceChildren(dialog);
    if (busy) {refreshOnIdle = true; return;}
    status.textContent = 'Loading subscriptions…'; void perform(refresh);
  }
  window.addEventListener('pagehide', () => jobs.dispose(), {once:true});
  document.addEventListener('whab:page-ready', () => {mount(); if (!busy) void jobs.refresh();});
  void jobs.refresh(); mount();
  return {detach};
}
