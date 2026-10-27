import {api, getContext} from './client.js';
import {isSettingsPageOpen} from './settings-navigation.js';

/* Credentials use the plugin's local settings route, never ledger commands. */
export async function initLlmSettings(dialog, content) {
  const workspace = getContext().workspace;
  const pluginId = 'llm-categorization';
  const status = dialog.querySelector('[data-plugin-settings-status]');
  const error = dialog.querySelector('[data-plugin-settings-error]');
  const readOnly = dialog.dataset.pluginReadOnly === 'true';
  const local = (action, values = {}) => api('/plugin-settings/llm-categorization', {workspace, action, ...values});
  const call = (action, values = {}) => api('/categorization', {workspace, payload: {pluginId, action, ...values}});
  let busy = false;
  const text = (tag, value, cls = '') => {
    const node = document.createElement(tag); node.textContent = value; node.className = cls; return node;
  };
  const lock = value => {
    for (const node of content.querySelectorAll('input,select,button')) node.disabled = value || readOnly;
  };
  async function perform(work) {
    if (busy || readOnly || !isSettingsPageOpen(dialog)) return;
    busy = true; error.hidden = true; lock(true);
    try { await work(); }
    catch (failure) { error.textContent = failure.message; error.hidden = false; status.textContent = ''; }
    finally { busy = false; lock(false); }
  }
  function button(label, run) {
    const node = text('button', label, 'button secondary'); node.type = 'button';
    node.addEventListener('click', () => perform(run)); return node;
  }
  function field(form, label, name, value, type = 'text') {
    const wrapper = text('label', label); const input = document.createElement('input');
    input.name = name; input.type = type;
    if (type === 'checkbox') input.checked = value; else input.value = value;
    wrapper.append(input); form.append(wrapper); return input;
  }
  const close = () => { void local('cancel-sign-in').catch(() => {}); };
  dialog.addEventListener('close', close, {once: true});
  async function render() {
    const [connection, saved] = await Promise.all([local('status'), call('status')]);
    if (!isSettingsPageOpen(dialog)) return;
    content.replaceChildren();
    content.append(text('p', 'Suggest categories with your chosen LLM. Suggestions stay unreviewed until you accept them in Transactions.', 'settings-notice'));
    content.append(text('p', 'Requests send payee names, amounts, currencies, category names and up to 50 reviewed examples to the selected provider. Subscription sign-in still uses OpenAI’s cloud. API calls may incur charges. Account IDs, dates, notes, receipts and balances are excluded.', 'settings-notice'));
    if (saved.error) content.append(text('p', saved.error, 'field-error'));
    const form = document.createElement('form'); form.className = 'categorization-form';
    const providerLabel = text('label', 'Provider and connection'); const provider = document.createElement('select');
    provider.name = 'provider';
    for (const row of connection.providers) {
      const option = text('option', `${row.name}${row.connected ? ' · Connected' : ''}`); option.value = row.id;
      provider.append(option);
    }
    provider.value = 'openai'; providerLabel.append(provider); form.append(providerLabel);
    const credentials = document.createElement('section'); form.append(credentials);
    const model = field(form, 'Model ID', 'model', ''); model.required = true; model.maxLength = 200;
    const choices = document.createElement('datalist'); choices.id = 'llm-model-options';
    model.setAttribute('list', choices.id); form.append(choices);
    form.append(button('Load available models', async () => {
      const result = await local('models', {provider: provider.value}); choices.replaceChildren();
      for (const row of result.models) { const option = text('option', row.name); option.value = row.id; choices.append(option); }
      status.textContent = `${result.models.length} models loaded. Choose one or enter its model ID.`;
    }));
    const name = field(form, 'Profile name', 'name', 'LLM categories'); name.required = true; name.maxLength = 100;
    const threshold = field(form, 'Minimum model score', 'threshold', '0.8', 'number');
    threshold.min = '0.8'; threshold.max = '1'; threshold.step = '0.01'; threshold.required = true;
    const examples = field(form, 'Reviewed examples to include (0–50)', 'examples', '20', 'number');
    examples.min = '0'; examples.max = '50'; examples.step = '1'; examples.required = true;
    const automatic = field(form, 'Send eligible new transactions automatically after bank imports', 'automatic', false, 'checkbox');
    const consent = field(form, 'Allow this profile to send the data described above to this provider', 'consent', false, 'checkbox');
    consent.required = true;
    function connectionFields() {
      credentials.replaceChildren();
      if (provider.value === 'chatgpt') {
        credentials.append(button('Continue with ChatGPT', async () => {
          const result = await local('connect', {provider: 'chatgpt'});
          const link = text('a', 'Open ChatGPT sign-in', 'button secondary');
          link.href = result.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
          credentials.append(link);
          status.textContent = 'Open sign-in on the computer running WHAB, then choose Check sign-in. Your plan must allow WHAB access.';
        }), button('Check sign-in', async () => {
          const result = await local('auth-status', {provider: 'chatgpt'});
          if (result.error) throw new Error(result.error);
          status.textContent = result.status === 'connected' ? 'ChatGPT connected. Load models to see your account’s choices.' : 'Sign-in is still pending. Complete it in the sign-in tab.';
        }));
        const usage = text('a', 'Manage ChatGPT usage limits'); usage.href = 'https://chatgpt.com/settings/usage';
        usage.target = '_blank'; usage.rel = 'noopener noreferrer'; credentials.append(usage);
      } else {
        const key = field(credentials, 'API key (stored only on this machine)', 'api_key', '', 'password');
        key.autocomplete = 'new-password'; key.maxLength = 4096;
        credentials.append(button('Save API key', async () => {
          const value = key.value; key.value = '';
          await local('save-key', {provider: provider.value, api_key: value}); status.textContent = 'API key saved. Load models or enter a model ID.';
        }));
      }
      credentials.append(button('Disconnect provider', async () => {
        const result = await local('disconnect', {provider: provider.value}); status.textContent = result.message;
      }));
      lock(busy);
    }
    provider.addEventListener('change', () => { model.value = ''; choices.replaceChildren(); connectionFields(); });
    connectionFields();
    const save = text('button', 'Save prompt profile', 'button primary'); save.type = 'submit'; form.append(save);
    form.addEventListener('submit', event => {
      event.preventDefault();
      void perform(async () => {
        const parameters = {provider: provider.value, model: model.value.trim(), threshold: Number(threshold.value),
          examples: Number(examples.value), automatic: automatic.checked, consent: consent.checked, categories: connection.categories};
        let result = await call('train', {name: name.value, parameters}); const session = result.session;
        try {
          while (!result.complete) result = await call('step', {session});
        } catch (failure) { await call('cancel', {session}).catch(() => {}); throw failure; }
        status.textContent = 'Profile saved. Choose Use profile to enable its suggestions.'; await render();
      });
    });
    content.append(form, text('p', 'Profiles use your existing categories and optional reviewed examples. Saving does not train or fine-tune a remote model. Model scores are self-reported and are not calibrated probabilities.', 'settings-notice'), text('h3', 'Saved profiles'));
    if (!saved.models.length) content.append(text('p', 'Save a profile, then choose Use profile. A new budget can start with zero reviewed examples.', 'settings-notice'));
    for (const profile of saved.models) {
      const row = document.createElement('section'); row.className = 'categorization-model';
      row.append(text('strong', profile.name + (saved.active === profile.id ? ' · Active' : '')));
      const config = profile.parameters;
      row.append(text('p', `${config.provider} · ${config.model} · cutoff ${config.threshold} · ${config.automatic ? 'Automatic import suggestions enabled' : 'Manual suggestions only'}`, 'settings-notice'));
      const rename = field(row, 'Profile name', 'rename', profile.name); rename.maxLength = 100;
      row.append(button('Rename', async () => { await call('rename', {modelId: profile.id, name: rename.value}); await render(); }),
        button('Use profile', async () => { await call('select', {modelId: profile.id}); await render(); }),
        button('Delete', async () => { await call('delete', {modelId: profile.id}); await render(); }));
      content.append(row);
    }
    content.append(button('Suggest for existing transactions', async () => {
      const result = await call('preview'); status.textContent = `${result.suggested} category suggestions available. Review them in Transactions.`;
    }));
    lock(busy);
  }
  await render();
}
