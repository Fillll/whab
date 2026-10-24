/* Host coordination contains only opaque identities and edit/ready flags. */
export function createUIViews({generation, request, isDirty, ready, freeze, reload,
  schedule = callback => setTimeout(callback, 2000), cancel = clearTimeout}) {
  if (!/^[0-9a-f]{64}$/.test(generation || '')) return null;
  let view = null, prepared = null, frozen = false, stopped = false, running = false, timer = null;
  function hold(value) {
    if (frozen !== value) { freeze(value); frozen = value; }
  }
  async function tick() {
    if (stopped || running) return;
    running = true;
    try {
      let status = await request(view ? `/${view}` : '',
        view ? null : {generation, dirty: Boolean(isDirty())}, view ? 'GET' : 'POST');
      if (stopped) return;
      if (!view) view = status.view;
      if (prepared && status.pending?.id !== prepared) { prepared = null; hold(false); }
      if (status.pending) {
        if (prepared !== status.pending.id || isDirty()) {
          // Freeze synchronously before awaiting saves. Autosave and provider
          // callbacks keep running, but another user edit cannot race the ack.
          hold(true);
          const saved = await ready() && !isDirty();
          if (stopped) return;
          if (saved) {
            prepared = status.pending.id;
            status = await request(`/${view}`, {generation, dirty: false, prepared}, 'PUT');
            if (status.pending?.id !== prepared) { prepared = null; hold(false); }
          } else {
            // A previously accepted ack must be revoked durably before input
            // resumes. A lost revocation reply leaves the view paused.
            status = await request(`/${view}`, {generation, dirty: Boolean(isDirty()), prepared: null}, 'PUT');
            prepared = null;
            hold(false);
          }
        }
      } else {
        prepared = null;
        hold(false);
        if (!status.reload) await request(`/${view}`, {generation, dirty: Boolean(isDirty()), prepared: null}, 'PUT');
      }
      // A view returning after an offline period retains its draft. A clean
      // view flushes once more before accepting the host's reload instruction.
      if (status.reload && !isDirty()) {
        hold(true);
        if (await ready() && !isDirty() && !stopped) { stopped = true; reload(); }
        else hold(false);
      }
    } catch (error) {
      // An accepted ack with a lost reply still permits host commit. Preserve
      // the pause until a later status explicitly confirms commit or abort.
      if (error?.code === 'ui_view_unknown') { view = null; prepared = null; }
      if (!prepared) hold(false);
    } finally {
      running = false;
      if (!stopped) timer = schedule(tick);
    }
  }
  return {tick, start: tick, stop() { stopped = true; cancel(timer); if (!prepared) hold(false); }};
}
