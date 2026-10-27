import {api, getContext} from './client.js';
import {isSettingsPageOpen} from './settings-navigation.js';

/* Provider credentials stay on the local settings route, outside ledger commands. */
export async function initLlmSettings(dialog, content) {
  const workspace = getContext().workspace, pluginId = 'llm-categorization';
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
    for (const node of content.querySelectorAll('input,select,textarea,button')) node.disabled = value || readOnly;
  };
  async function perform(work) {
    if (busy || readOnly || !isSettingsPageOpen(dialog)) return;
    busy = true; error.hidden = true; lock(true);
    try { await work(); }
    catch (failure) { error.textContent = failure.message; error.hidden = false; status.textContent = ''; }
    finally { busy = false; lock(false); }
  }
  function button(label, run, primary = false, validate = null) {
    const node = text('button', label, `button ${primary ? 'primary' : 'secondary'}`); node.type = 'button';
    node.addEventListener('click', () => { if (!validate || validate()) void perform(run); }); return node;
  }
  function field(parent, label, name, value, type = 'text') {
    const wrapper = text('label', label); const input = document.createElement(type === 'select' || type === 'textarea' ? type : 'input');
    input.name = name;
    if (type === 'checkbox') { input.type = type; input.checked = value; wrapper.className = 'llm-check'; }
    else { if (!['select', 'textarea'].includes(type)) input.type = type; input.value = value; }
    wrapper.append(input); parent.append(wrapper); return input;
  }
  function option(select, value, label) { const node = text('option', label); node.value = value; select.append(node); }
  function disclosure(parent, title) {
    const node = document.createElement('details'); node.className = 'llm-disclosure';
    node.append(text('summary', title)); parent.append(node); return node;
  }
  dialog.addEventListener('close', () => { void local('cancel-sign-in').catch(() => {}); });
  async function render(selectedId) {
    const [connection, saved] = await Promise.all([local('status'), call('status')]);
    if (!isSettingsPageOpen(dialog)) return;
    const current = saved.models.find(row => row.id === (selectedId ?? saved.active));
    const config = {...saved.defaults, ...current?.parameters};
    content.replaceChildren(); content.className = 'plugin-settings llm-settings';
    content.append(text('p', 'Generate suggestions for uncategorized transactions, then review them in Transactions.', 'llm-intro'));
    if (saved.error) content.append(text('p', saved.error, 'field-error'));
    const form = document.createElement('form'); form.className = 'llm-profile-form'; content.append(form);
    const profile = field(form, 'Profile', 'profile', '', 'select');
    for (const row of saved.models) option(profile, row.id, row.name + (saved.active === row.id ? ' · Active' : ''));
    option(profile, '', 'New profile'); profile.value = current?.id || '';
    profile.addEventListener('change', () => perform(() => render(profile.value)));
    const grid = document.createElement('div'); grid.className = 'llm-grid'; form.append(grid);
    const provider = field(grid, 'Provider', 'provider', '', 'select');
    for (const row of connection.providers) option(provider, row.id, row.name + (row.connected ? ' · Connected' : ''));
    provider.value = config.provider || 'openai';
    const name = field(grid, 'Profile name', 'name', current?.name || 'LLM categories'); name.required = true; name.maxLength = 100;
    const model = field(grid, 'Model', 'model', config.model || ''); model.required = true; model.maxLength = 200;
    const choices = document.createElement('datalist'); choices.id = 'llm-model-options';
    model.setAttribute('list', choices.id); form.append(choices);
    const effort = field(grid, 'Effort level', 'effort', '', 'select');
    const modelActions = document.createElement('div'); modelActions.className = 'llm-inline-actions'; form.append(modelActions);
    modelActions.append(button('Load available models', async () => {
      const result = await local('models', {provider: provider.value}); choices.replaceChildren();
      for (const row of result.models) option(choices, row.id, row.name);
      status.textContent = `${result.models.length} models loaded. Choose one or enter its model ID.`;
    }));
    modelActions.append(text('span', 'Effort requires a compatible model. Higher effort can take longer and use more allowance.', 'settings-notice'));
    const prompt = field(form, 'Categorization prompt', 'prompt', config.prompt || saved.defaults.prompt, 'textarea');
    prompt.required = true; prompt.maxLength = 8000; prompt.rows = 5;
    const promptTools = document.createElement('div'); promptTools.className = 'llm-inline-actions'; form.append(promptTools);
    promptTools.append(button('Restore suggested prompt', async () => { prompt.value = saved.defaults.prompt; updatePrompt(); }));
    const fullPrompt = disclosure(form, 'Full prompt sent to the model');
    fullPrompt.append(text('p', 'Your prompt is followed by these fixed category, currency and response rules. The request also includes your categories, selected reviewed examples, and each eligible transaction’s payee, amount and currency.', 'settings-notice'));
    const preview = text('pre', '', 'llm-prompt-preview'); fullPrompt.append(preview);
    function updatePrompt() { preview.textContent = `${prompt.value}\n\n${connection.promptRules}`; }
    prompt.addEventListener('input', updatePrompt); updatePrompt();
    const advanced = disclosure(form, 'Examples and suggestion settings');
    const options = document.createElement('div'); options.className = 'llm-grid'; advanced.append(options);
    const threshold = field(options, 'Minimum model score', 'threshold', config.threshold ?? .8, 'number');
    threshold.min = '.8'; threshold.max = '1'; threshold.step = '.01'; threshold.required = true;
    const examples = field(options, 'Reviewed examples (0–50)', 'examples', config.examples ?? 20, 'number');
    examples.min = '0'; examples.max = '50'; examples.step = '1'; examples.required = true;
    advanced.append(text('p', 'Examples are taken from your most recent reviewed transactions when you save. Model scores are self-reported, not calibrated probabilities.', 'settings-notice'));
    const automatic = field(advanced, 'Suggest automatically after bank imports', 'automatic', config.automatic === true, 'checkbox');
    const privacy = disclosure(form, 'Provider connection and data sent');
    privacy.append(text('p', 'Requests send payee names, amounts, currencies, category names and your selected reviewed examples to this provider. Subscription sign-in uses OpenAI’s cloud; API calls may incur charges. Account IDs, dates, notes, receipts and balances are excluded. Credentials stay on this machine.', 'settings-notice'));
    const credentials = document.createElement('div'); credentials.className = 'llm-provider-credentials'; privacy.append(credentials);
    function connectionFields() {
      credentials.replaceChildren(); effort.replaceChildren();
      for (const value of connection.efforts[provider.value]) option(effort, value, value === 'default' ? 'Provider default' : value[0].toUpperCase() + value.slice(1));
      effort.value = connection.efforts[provider.value].includes(config.effort) ? config.effort : 'default';
      if (provider.value === 'chatgpt') {
        credentials.append(button('Continue with ChatGPT', async () => {
          const result = await local('connect', {provider: 'chatgpt'});
          const link = text('a', 'Open ChatGPT sign-in', 'button secondary');
          link.href = result.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; credentials.append(link);
          status.textContent = 'Complete sign-in on this computer, then choose Check sign-in.';
        }), button('Check sign-in', async () => {
          const result = await local('auth-status', {provider: 'chatgpt'});
          if (result.error) throw new Error(result.error);
          status.textContent = result.status === 'connected' ? 'ChatGPT connected. Load models to see your account’s choices.' : 'Sign-in is still pending. Complete it in the sign-in tab.';
        }));
        const usage = text('a', 'Manage ChatGPT usage limits'); usage.href = 'https://chatgpt.com/settings/usage';
        usage.target = '_blank'; usage.rel = 'noopener noreferrer'; credentials.append(usage);
      } else {
        const key = field(credentials, 'API key', 'api_key', '', 'password'); key.autocomplete = 'new-password'; key.maxLength = 4096;
        credentials.append(button('Save API key', async () => {
          const value = key.value; key.value = '';
          await local('save-key', {provider: provider.value, api_key: value}); status.textContent = 'API key saved. Load models or enter a model ID.';
        }));
      }
      credentials.append(button('Disconnect provider', async () => {
        const result = await local('disconnect', {provider: provider.value}); status.textContent = result.message;
      })); lock(busy);
    }
    provider.addEventListener('change', () => { model.value = ''; consent.checked = false; choices.replaceChildren(); connectionFields(); });
    const consent = field(form, 'Allow this profile to send the described transaction data to this provider', 'consent', config.consent === true, 'checkbox');
    consent.required = true; connectionFields();
    const actions = document.createElement('div'); actions.className = 'llm-profile-actions'; form.append(actions);
    async function saveProfile(replace = true) {
      const parameters = {provider: provider.value, model: model.value.trim(), prompt: prompt.value, effort: effort.value,
        threshold: Number(threshold.value), examples: Number(examples.value), automatic: automatic.checked,
        consent: consent.checked, categories: connection.categories};
      if (current && replace) parameters.replaceProfile = current.id;
      let result = await call('train', {name: name.value, parameters}); const session = result.session;
      try { while (!result.complete) result = await call('step', {session}); }
      catch (failure) { await call('cancel', {session}).catch(() => {}); throw failure; }
      await call('select', {modelId: result.model.id});
      return result.model.id;
    }
    const save = text('button', 'Save and use profile', 'button primary'); save.type = 'submit'; actions.append(save);
    form.addEventListener('submit', event => { event.preventDefault(); void perform(async () => {
      const id = await saveProfile(); status.textContent = 'Profile saved and active.'; await render(id);
    }); });
    actions.append(button('Suggest for uncategorized', async () => {
      // Validate before saving; never run with stale values from another profile.
      const id = await saveProfile();
      status.textContent = 'Generating suggestions… This can take a few minutes. Keep this window open.';
      const result = await call('preview');
      status.textContent = `${result.suggested} category suggestions available. Review them in Transactions.`; await render(id);
    }, false, () => form.reportValidity()));
    if (current) {
      const management = disclosure(content, 'Manage this profile');
      management.append(text('p', 'Saving edits replaces this profile with a new revision. Other saved profiles remain available above.', 'settings-notice'));
      management.append(button('Save as a new profile', async () => {
        const id = await saveProfile(false); status.textContent = 'New profile saved and active.'; await render(id);
      }, false, () => form.reportValidity()));
      management.append(button('Delete profile', async () => { await call('delete', {modelId: current.id}); status.textContent = 'Profile deleted.'; await render(); }));
    }
    lock(busy);
  }
  await render();
}
