/* Shared local HTTP and feedback helpers. No provider integration runs here. */

const toast = document.getElementById('toast');
let toastTimer;

export function notify(message) {
  toast.querySelector('span').textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.hidden = true;
  }, 6000);
}

export async function api(path, body, method = 'POST') {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? 'GET' : method,
    ...(body === undefined ? {} : {
      body: JSON.stringify(body)
    }),
    headers: {
      'Content-Type': 'application/json'
    },
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || 'The request could not be completed.');
  return result;
}
