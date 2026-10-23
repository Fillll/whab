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
      let dirty = Boolean(isDirty());
      if (dirty) prepared = null;
      const status = await request(view ? `/${view}` : '', {
        generation, dirty, ...(view ? {prepared: dirty ? null : prepared} : {}),
      }, view ? 'PUT' : 'POST');
      if (!view) view = status.view;
      if (status.pending) {
        if (prepared !== status.pending.id) {
          prepared = null;
          // Freeze synchronously before awaiting saves. Autosave and provider
          // callbacks keep running, but another user edit cannot race the ack.
          hold(true);
          if (await ready() && !isDirty()) {
            prepared = status.pending.id;
            const acknowledged = await request(`/${view}`, {generation, dirty: false, prepared}, 'PUT');
            if (acknowledged.pending?.id !== prepared) { prepared = null; hold(false); }
          } else hold(false);
        }
      } else {
        prepared = null;
        hold(false);
      }
      // A view returning after an offline period retains its draft. A clean
      // view flushes once more before accepting the host's reload instruction.
      if (status.reload && !isDirty()) {
        hold(true);
        if (await ready() && !isDirty()) { stopped = true; reload(); }
        else hold(false);
      }
    } catch (error) {
      // Unknown view after expiry/host restart: register again. Network errors
      // also unfreeze the old interface; no error ever discards a draft.
      if (error?.code === 'ui_view_unknown') view = null;
      prepared = null;
      hold(false);
    } finally {
      running = false;
      if (!stopped) timer = schedule(tick);
    }
  }
  return {tick, start: tick, stop() { stopped = true; cancel(timer); hold(false); }};
}
