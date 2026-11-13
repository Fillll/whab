/* Explicit selected-row categories use one native commit; unknown outcomes retain intent. */
export function createCategoryBatch({workspace, api, before, saved, restore, state, error}) {
  let running = null, intent = null, targets = [], choice = null, uncertain = false, disposed = false;
  function verified(result, categoryId = intent.categoryId) {
    const data = result?.data;
    if (!result?.revision || data?.count !== targets.length || !Array.isArray(data.changes) || data.changes.length !== targets.length ||
        data.changes.some((change, i) => change.id !== targets[i].dataset.transactionId || change.after?.id !== change.id || change.after.categoryId !== categoryId)) {
      throw new Error('The saved category result could not be verified.');
    }
    return data;
  }
  async function submit() {
    try {
      const result = await api('/transactions/categories', intent);
      const data = verified(result);
      intent = null; uncertain = false;
      if (!disposed) {
        try {saved(targets, data.changes, choice);} catch {error('Category changes were saved. Refresh this view to display the verified result.');}
      }
      return true;
    } catch (failure) {
      if (failure.code === 'category_revision_conflict' && !uncertain) {
        intent = null;
        if (!disposed) error('The budget changed. No category changes were applied by this request. Refresh and choose the category again.');
      } else {
        uncertain = true;
        if (!disposed) error(`${failure.message} Category batch not confirmed; it may have committed. Retry save retains the same ${targets.length}-transaction request (${intent.requestId}). Do not start a new change until this result is resolved.`);
      }
      return false;
    }
  }
  function launch(work) {
    state(true);
    running = (async () => {try {return await work();} finally {running = null; if (!disposed) state(Boolean(intent));}})();
    return running;
  }
  return {
    get dirty() {return Boolean(running || intent);},
    apply(rows, categoryId, option) {
      if (running || intent || disposed) {restore(rows); return Promise.resolve(false);}
      if (rows.length < 1 || rows.length > 100 || new Set(rows.map(row => row.dataset.transactionId)).size !== rows.length) {
        restore(rows); error('Select 1–100 distinct transactions for one atomic category change. Nothing was submitted; the selection is never split.');
        return Promise.resolve(false);
      }
      targets = [...rows]; choice = option;
      return launch(async () => {
        try {
          if (!await before() || disposed) {if (!disposed) restore(targets); return false;}
          const transactionIds = targets.map(row => row.dataset.transactionId);
          const preview = await api('/transactions/categories/preview', {workspace, transactionIds, categoryId});
          if (disposed) return false;
          verified(preview, categoryId);
          intent = {workspace, transactionIds, categoryId, revision:preview.revision, requestId:crypto.randomUUID()};
          return await submit();
        } catch (failure) {if (!disposed) error(failure.message); return false;}
        finally {if (!disposed && !intent) restore(targets);}
      });
    },
    async flush() {if (running) return running; if (intent && !disposed) return launch(submit); return !intent;},
    dispose() {disposed = true;},
  };
}
