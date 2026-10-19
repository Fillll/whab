/* Explicit settings sessions: each request advances training; leaving cancels it. */
import {api, apiUrl, getContext} from './client.js';

export async function initCategorizationSettings(dialog, content) {
  const pluginId = dialog.dataset.pluginId;
  const workspace = getContext().workspace;
  const status = dialog.querySelector('[data-plugin-settings-status]');
  const error = dialog.querySelector('[data-plugin-settings-error]');
  let session = null;
  let stopped = false;
  let busy = false;
  const call = (action, values = {}) => api('/categorization', {workspace, payload: {action, pluginId, ...values}});
  function text(tag, value, cls) {
    const node = document.createElement(tag); node.textContent = value;
    if (cls) node.className = cls;
    return node;
  }
  async function cancel() {
    stopped = true;
    if (session) {
      const old = session; session = null;
      await call('cancel', {session: old}).catch(() => {});
    }
  }
  dialog.addEventListener('close', cancel, {once: true});
  const unload = () => {
    if (session) fetch(apiUrl('/categorization'), {method: 'POST', keepalive: true,
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({workspace, payload: {action: 'cancel', pluginId, session}})});
  };
  window.addEventListener('pagehide', unload, {once: true});
  dialog.addEventListener('close', () => window.removeEventListener('pagehide', unload), {once: true});
  async function perform(work) {
    if (busy || dialog.dataset.pluginReadOnly === 'true') return;
    busy = true; error.hidden = true;
    for (const button of content.querySelectorAll('button')) button.disabled = true;
    try { await work(); }
    catch (failure) { await cancel(); error.textContent = failure.message; error.hidden = false; status.textContent = ''; }
    finally { busy = false; for (const control of content.querySelectorAll('input,button')) control.disabled = false; }
  }
  function button(label, handler) {
    const node = text('button', label, 'button secondary'); node.type = 'button';
    node.addEventListener('click', () => perform(handler)); return node;
  }
  async function render() {
    const data = await call('status');
    if (!dialog.open) return;
    const readOnly = dialog.dataset.pluginReadOnly === 'true';
    content.replaceChildren();
    content.append(text('p', `${data.eligible} reviewed, categorized transactions available. Training and evaluation use reviewed transactions only.`, 'settings-notice'));
    if (data.error) content.append(text('p', data.error, 'field-error'));
    const form = document.createElement('form'); form.className = 'categorization-form';
    const fields = [['name', 'Model name', 'text', 'Random forest'],
      ['trees', 'Number of trees', 'number', data.defaults.trees, 10, 500],
      ['depth', 'Maximum tree depth', 'number', data.defaults.depth, 2, 24],
      ['leaf', 'Minimum examples per leaf', 'number', data.defaults.leaf, 1, 100],
      ['vocabulary', 'Maximum payee text features', 'number', data.defaults.vocabulary, 50, 5000],
      ['threshold', 'Suggestion score cutoff', 'number', data.defaults.threshold, 0, 1],
      ['start', 'History start (optional)', 'date', ''], ['end', 'History end (optional)', 'date', '']];
    for (const [key, label, type, value, min, max] of fields) {
      const wrapper = text('label', label); const input = document.createElement('input');
      input.name = key; input.type = type; input.value = value;
      if (min !== undefined) { input.min = min; input.max = max; input.step = key === 'threshold' ? '.01' : '1'; }
      if (key === 'name') { input.maxLength = 100; input.required = true; }
      wrapper.append(input); form.append(wrapper);
    }
    const weighting = text('label', 'Balance category weights'); const check = document.createElement('input');
    check.name = 'balanced'; check.type = 'checkbox'; weighting.append(check); form.append(weighting);
    const train = text('button', 'Train new model', 'button primary'); train.type = 'submit'; form.append(train);
    const progress = document.createElement('progress'); progress.max = 100; progress.value = 0; progress.hidden = true;
    progress.setAttribute('aria-label', 'Model training progress');
    const stop = text('button', 'Cancel training', 'button secondary'); stop.type = 'button'; stop.hidden = true;
    stop.addEventListener('click', async () => { await cancel(); status.textContent = 'Training canceled.'; });
    form.addEventListener('submit', event => {
      event.preventDefault();
      void perform(async () => {
        stopped = false;
        const parameters = {};
        for (const [key, , type] of fields) if (key !== 'name') parameters[key] = type === 'number' ? Number(form.elements[key].value) : form.elements[key].value;
        parameters.balanced = check.checked;
        try {
          let result = await call('train', {parameters, name: form.elements.name.value});
          session = result.session;
          progress.hidden = false; stop.hidden = false; stop.disabled = false;
          for (const input of form.querySelectorAll('input')) input.disabled = true;
          while (session && !stopped && dialog.open) {
            status.textContent = result.stage; progress.value = result.progress;
            // Yield a paint before advancing the next bounded block of trees.
            await new Promise(resolve => requestAnimationFrame(resolve));
            if (stopped || !dialog.open) break;
            result = await call('step', {session});
            if (result.complete) { session = null; progress.value = 100; status.textContent = result.stage; }
          }
          await cancel();
          if (dialog.open) await render();
        } finally { progress.hidden = true; stop.hidden = true; }
      });
    });
    content.append(form, progress, stop, text('h3', 'Saved models'));
    if (!data.models.length) content.append(text('p', 'Train a model, then choose Use model to enable automatic suggestions.', 'settings-notice'));
    for (const model of data.models) {
      const row = document.createElement('section'); row.className = 'categorization-model';
      row.append(text('strong', model.name + (data.active === model.id ? ' · Active' : '')));
      row.append(text('p', `${new Date(model.trainedAt).toLocaleString()} · ${model.partitions.train.count} training / ${model.validation.count} validation / ${model.test.count} test transactions`, 'settings-notice'));
      const percent = value => value === null ? 'No suggestions' : `${Math.round(value * 100)}%`;
      row.append(text('p', `Test accuracy ${percent(model.test.accuracy)} · Macro F1 ${model.test.macroF1.toFixed(2)} · Suggestion precision ${percent(model.test.suggestionPrecision)} · Coverage ${percent(model.test.coverage)}`));
      row.append(text('p', `Validation accuracy ${percent(model.validation.accuracy)} · Precision ${percent(model.validation.suggestionPrecision)} · Coverage ${percent(model.validation.coverage)} · Score cutoff ${model.parameters.threshold}`, 'settings-notice'));
      const detail = document.createElement('details'); detail.append(text('summary', 'Parameters and category results'));
      detail.append(text('p', `${model.parameters.trees} trees · depth ${model.parameters.depth} · minimum leaf ${model.parameters.leaf} · vocabulary ${model.parameters.vocabulary}`));
      for (const category of model.test.categories) detail.append(text('p', `${data.categoryLabels[category.id] || 'Unavailable category'}: ${category.count} test examples · F1 ${category.f1.toFixed(2)}`));
      detail.append(text('p', 'Scores are model scores, not calibrated probabilities. Small category samples give limited evidence.', 'settings-notice'));
      const rename = document.createElement('input'); rename.value = model.name; rename.maxLength = 100;
      rename.setAttribute('aria-label', 'Name for saved model');
      row.append(rename, button('Rename', async () => { await call('rename', {modelId: model.id, name: rename.value}); await render(); }));
      row.append(detail, button('Use model', async () => { await call('select', {modelId: model.id}); await render(); }),
        button('Delete', async () => { await call('delete', {modelId: model.id}); await render(); }));
      content.append(row);
    }
    content.append(button('Suggest for existing transactions', async () => {
      const result = await call('suggest'); status.textContent = `${result.suggested} category suggestions available. Review them in Transactions.`;
    }));
    if (data.previewAvailable) {
      content.append(button('Preview low-score guesses', async () => {
        const result = await call('preview');
        status.textContent = `${result.suggested} category suggestions available, including ${result.lowScore} low-score guesses. Check them carefully in Transactions. Your automatic score cutoff is unchanged.`;
      }));
      content.append(text('p', 'Low-score previews include uncertain guesses below the saved cutoff. They stay unreviewed and are marked in Transactions. Automatic suggestions keep the saved cutoff.', 'settings-notice'));
    }
    if (readOnly) for (const control of content.querySelectorAll('input,button')) control.disabled = true;
  }
  await render();
}
