/* Polling observes server-owned work. Disposing a view never stops that work. */
export const classificationRunning = job => ['preparing', 'running', 'stopping'].includes(job?.status);

export function createSubscriptionJobs({call, scope, onChange, onComplete, onError,
  setTimer = setTimeout, clearTimer = clearTimeout, newId = () => crypto.randomUUID()}) {
  let current = null, timer = null, generation = 0, disposed = false, pendingStart = null;
  function cancelTimer() { if (timer !== null) clearTimer(timer); timer = null; }
  const valid = (token, owner) => !disposed && token === generation && owner === scope();
  function schedule() {
    cancelTimer();
    if (!disposed && classificationRunning(current)) timer = setTimer(() => {void poll();}, 1500);
  }
  function receive(job) {
    const finished = current?.id === job.id && classificationRunning(current) && !classificationRunning(job);
    current = job;
    if (job.id === pendingStart) pendingStart = null;
    onChange(job); schedule();
    if (finished) onComplete(job);
    return job;
  }
  async function poll() {
    const token = generation, owner = scope();
    try {
      const job = await call('job');
      if (valid(token, owner)) receive(job);
    } catch (error) {
      if (valid(token, owner)) { onError(error); schedule(); }
    }
  }
  function accept(job) { if (disposed) return; ++generation; cancelTimer(); return receive(job); }
  async function refresh() { if (disposed) return; ++generation; cancelTimer(); await poll(); }
  async function start(config) {
    if (disposed) return;
    const token = ++generation, owner = scope(); cancelTimer();
    pendingStart ||= newId();
    onChange({id: pendingStart, status: 'preparing', total: 0, processed: 0, classified: 0, reused: 0, skipped: 0});
    try {
      const job = await call('start', {operationId: pendingStart, ...(config ? {config} : {})});
      if (valid(token, owner)) return receive(job);
    } catch (error) {
      // Read the durable receipt after a lost reply; never retry paid work here.
      if (valid(token, owner)) { void refresh(); throw error; }
    }
  }
  async function stop() {
    if (disposed || !classificationRunning(current)) return;
    const token = ++generation, owner = scope(); cancelTimer();
    try {
      const job = await call('stop', {jobId: current.id});
      if (valid(token, owner)) return receive(job);
    } catch (error) { if (valid(token, owner)) {schedule(); throw error;} }
  }
  function dispose() { disposed = true; ++generation; cancelTimer(); }
  return {accept, refresh, start, stop, dispose};
}

export function renderSubscriptionProgress(panel, job) {
  panel.hidden = !job?.id;
  if (panel.hidden) return;
  const label = panel.querySelector('[data-subscriptions-job-label]');
  const count = panel.querySelector('[data-subscriptions-job-count]');
  const help = panel.querySelector('[data-subscriptions-job-help]');
  const bar = panel.querySelector('progress');
  const labels = {preparing: 'Preparing classification', running: 'Classifying in background',
    stopping: 'Stopping after the current batch', completed: 'Classification complete',
    stopped: 'Classification stopped', interrupted: 'Classification interrupted', failed: 'Classification failed'};
  const text = (node, value) => {if (node.textContent !== value) node.textContent = value;};
  text(label, labels[job.status] || 'Classification');
  bar.max = Math.max(1, job.total);
  if (job.status === 'preparing') bar.removeAttribute('value');
  else bar.value = job.total === 0 && job.status === 'completed' ? 1 : job.processed;
  const progress = `${job.processed.toLocaleString()} of ${job.total.toLocaleString()} transactions checked`;
  bar.setAttribute('aria-valuetext', job.status === 'preparing' ? 'Preparing transaction list' : progress);
  text(count, job.status === 'preparing' ? 'Preparing the unchecked transaction list…' :
    `${progress} · ${job.reused.toLocaleString()} reused${job.skipped ? ` · ${job.skipped.toLocaleString()} changed or removed (skipped)` : ''}`);
  text(help, job.error || (classificationRunning(job) ?
    'You can leave this page or close the browser tab. Classification continues while WHAB is running; completed batches are saved.' :
    job.status === 'completed' ? 'Results are saved. New imports can be checked in the next scan.' :
    'Completed batches are saved. Classify unchecked transactions again to finish.'));
}
