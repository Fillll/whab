/* A discrete month selection with a continuous pointer preview. */
export function rangeStep(start, count, mode, delta, length) {
  const end = start + count - 1;
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  if (mode === 'start') {
    const next = clamp(start + delta, Math.max(0, end - 4), end);
    return {start: next, count: end - next + 1};
  }
  if (mode === 'end') return {start, count: clamp(end + delta, start, Math.min(length - 1, start + 4)) - start + 1};
  return {start: clamp(start + delta, 0, length - count), count};
}

export function initMonthRange({root = document, navigate}) {
  const strip = root.querySelector('[data-month-range]');
  if (!strip) return {dispose() {}};
  const frame = strip.querySelector('[data-month-frame]');
  const links = [...strip.querySelectorAll('a[data-month]')];
  const initial = {start: links.findIndex(link => link.classList.contains('is-selected')),
    count: Number(strip.closest('[data-month-count]').dataset.monthCount)};
  let selection = initial, drag = null;
  const controller = new AbortController();
  const listen = (target, event, callback) => target.addEventListener(event, callback, {signal: controller.signal});
  function bounds(index) {
    const rect = links[index].getBoundingClientRect(), parent = strip.getBoundingClientRect();
    return {left: rect.left - parent.left + strip.scrollLeft, width: rect.width, top: rect.top - parent.top + strip.scrollTop, height: rect.height};
  }
  function draw(value = selection, offset = 0, mode = 'move') {
    const first = bounds(value.start), last = bounds(value.start + value.count - 1);
    const left = first.left - 5 + (mode !== 'end' ? offset : 0);
    const right = last.left + last.width + 5 + (mode !== 'start' ? offset : 0);
    frame.hidden = false;
    frame.style.left = `${left}px`; frame.style.width = `${Math.max(28, right - left)}px`;
    frame.style.top = `${first.top - 6}px`; frame.style.height = `${first.height + 12}px`;
    links.forEach((link, index) => {
      link.classList.toggle('is-selected', index === value.start);
      link.classList.toggle('in-range', index > value.start && index < value.start + value.count);
    });
    frame.querySelector('[data-range-handle="move"]').setAttribute('aria-label',
      `Move ${value.count}-month range, ${links[value.start].getAttribute('aria-label')} through ${links[value.start + value.count - 1].getAttribute('aria-label')}. Use left and right arrow keys.`);
  }
  function commit(value, mode) {
    selection = value; draw();
    if (value.start === initial.start && value.count === initial.count) return;
    const url = new URL(links[value.start].href);
    url.searchParams.set('months', value.count);
    navigate(url, () => {if (selection === value) {selection = initial; draw();}}, mode);
  }
  listen(frame, 'pointerdown', event => {
    const handle = event.target.closest('[data-range-handle]');
    if (!handle || event.button !== 0 || drag) return;
    event.preventDefault();
    drag = {id: event.pointerId, x: event.clientX, mode: handle.dataset.rangeHandle, selection, preview: selection};
    frame.setPointerCapture(event.pointerId); frame.classList.add('is-dragging');
  });
  listen(frame, 'pointermove', event => {
    if (!drag || drag.id !== event.pointerId) return;
    const index = drag.selection.start + (drag.mode === 'end' ? drag.selection.count - 1 : 0);
    const source = bounds(index), x = source.left + source.width / 2 + event.clientX - drag.x;
    let nearest = 0, distance = Infinity;
    links.forEach((_, candidate) => {
      const box = bounds(candidate), difference = Math.abs(box.left + box.width / 2 - x);
      if (difference < distance) {distance = difference; nearest = candidate;}
    });
    drag.preview = rangeStep(drag.selection.start, drag.selection.count, drag.mode, nearest - index, links.length);
    // Most motion follows the pointer; the last pixels ease towards the nearest month.
    const targetIndex = drag.preview.start + (drag.mode === 'end' ? drag.preview.count - 1 : 0);
    const target = bounds(targetIndex);
    const allowed = (drag.mode === 'start' ? drag.preview.start : drag.mode === 'end' ? targetIndex : drag.preview.start);
    const limited = nearest !== allowed;
    const remainder = limited ? 0 : (x - (target.left + target.width / 2)) * 0.65;
    draw(drag.preview, remainder, drag.mode);
  });
  function finish(event, cancel = false) {
    if (!drag || drag.id !== event.pointerId) return;
    const value = cancel ? drag.selection : drag.preview, mode = drag.mode;
    drag = null; frame.classList.remove('is-dragging');
    if (frame.hasPointerCapture(event.pointerId)) frame.releasePointerCapture(event.pointerId);
    if (cancel) {selection = value; draw();} else commit(value, mode);
  }
  listen(frame, 'pointerup', event => finish(event));
  listen(frame, 'pointercancel', event => finish(event, true));
  listen(frame, 'lostpointercapture', event => finish(event, true));
  listen(frame, 'keydown', event => {
    const handle = event.target.closest('[data-range-handle]');
    if (!handle || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    commit(rangeStep(selection.start, selection.count, handle.dataset.rangeHandle, event.key === 'ArrowLeft' ? -1 : 1, links.length), handle.dataset.rangeHandle);
  });
  const observer = new ResizeObserver(() => {if (!drag) draw();});
  observer.observe(strip);
  draw();
  // Keep the first selected month visible when the navigation strip must scroll.
  strip.scrollLeft = Math.max(0, bounds(initial.start).left - strip.clientWidth / 3);
  return {dispose() {controller.abort(); observer.disconnect();}};
}
