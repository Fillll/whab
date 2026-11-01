/* Shared provider connections. No secrets are retained in browser storage. */
import {api, getContext} from './client.js';
import {isSettingsPageOpen} from './settings-navigation.js';

export function element(tag, value = '', className = '') {
  const node = document.createElement(tag); node.textContent = value;
  if (className) node.className = className;
  return node;
}

export function field(parent, label, name, type = 'text') {
  const wrapper = element('label', label), input = document.createElement(type === 'select' ? 'select' : 'input');
  if (type !== 'select') input.type = type;
  input.name = name; wrapper.append(input); parent.append(wrapper); return input;
}

export function option(select, value, label) {
  const item = element('option', label); item.value = value; select.append(item);
}

export const credentials = (action, values = {}) => api('/plugin-settings/ai-credentials', {workspace: getContext().workspace, action, ...values});

export async function initAiCredentials(dialog, content) {
  const status = dialog.querySelector('[data-plugin-settings-status]');
  const error = dialog.querySelector('[data-plugin-settings-error]');
  let busy = false;
  const data = await credentials('status');
  if (!isSettingsPageOpen(dialog)) return;
  content.className = 'plugin-settings llm-settings'; content.replaceChildren();
  content.append(element('p', 'Connect a provider once for AI transaction categorization and Subscriptions. Keys and sign-in tokens stay on this computer; each feature asks permission before sending transaction data.', 'settings-notice'));
  const form = element('div', '', 'llm-profile-form'); content.append(form);
  const provider = field(form, 'Provider', 'provider', 'select');
  for (const row of data.providers) option(provider, row.id, row.name + (row.connected ? ' · Connected' : ''));
  const controls = element('div', '', 'llm-provider-credentials'); form.append(controls);
  async function perform(work) {
    if (busy || dialog.dataset.pluginReadOnly === 'true') return;
    busy = true; error.hidden = true;
    for (const node of form.querySelectorAll('input,button,select')) node.disabled = true;
    try { await work(); }
    catch (failure) { error.textContent = failure.message; error.hidden = false; }
    finally { busy = false; for (const node of form.querySelectorAll('input,button,select')) node.disabled = false; }
  }
  function button(label, action) {
    const node = element('button', label, 'button secondary'); node.type = 'button';
    node.addEventListener('click', () => perform(action)); return node;
  }
  function render() {
    controls.replaceChildren();
    if (provider.value === 'chatgpt') {
      controls.append(button('Continue with ChatGPT', async () => {
        const result = await credentials('connect', {provider: 'chatgpt'});
        const link = element('a', 'Open ChatGPT sign-in', 'button secondary');
        link.href = result.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; controls.append(link);
        status.textContent = 'Complete sign-in on this computer, then choose Check sign-in.';
      }), button('Check sign-in', async () => {
        const result = await credentials('auth-status', {provider: 'chatgpt'});
        if (result.error) throw new Error(result.error);
        status.textContent = result.status === 'connected' ? 'ChatGPT connected.' : `Sign-in: ${result.status}.`;
      }));
    } else {
      const key = field(controls, 'API key', 'api_key', 'password'); key.autocomplete = 'new-password'; key.maxLength = 4096;
      controls.append(button('Save API key', async () => {
        const value = key.value; key.value = '';
        await credentials('save-key', {provider: provider.value, api_key: value}); status.textContent = 'API key saved.';
      }));
    }
    controls.append(button('Disconnect provider', async () => {
      const result = await credentials('disconnect', {provider: provider.value}); status.textContent = result.message;
    }));
  }
  provider.addEventListener('change', render); render();
  dialog.addEventListener('close', () => {void credentials('cancel-sign-in').catch(() => {});}, {once: true});
}
