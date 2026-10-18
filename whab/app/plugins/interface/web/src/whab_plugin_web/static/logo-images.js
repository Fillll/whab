/* Optional artwork starts near visible fallback marks, with two downloads at a time. */
const bound = new WeakMap();
const waiting = new Set();
const active = new Set();
const targets = new Map();
const queue = [];
let draining = false;

const observer = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
  for (const entry of entries) {
    for (const task of targets.get(entry.target) || []) {
      task.visible = entry.isIntersecting && entry.boundingClientRect.width > 0 && entry.boundingClientRect.height > 0;
      if (task.visible && !task.queued) { task.queued = true; queue.push(task); }
    }
  }
  drain();
}, {rootMargin: '64px'}) : null;

function unobserve(task) {
  const siblings = targets.get(task.parent);
  siblings?.delete(task);
  if (siblings && !siblings.size) {
    observer?.unobserve(task.parent);
    targets.delete(task.parent);
  }
}

function finish(task, loaded, pump = true) {
  if (task.done) return;
  task.done = true;
  clearTimeout(task.timer);
  task.image.removeEventListener('load', task.loaded);
  task.image.removeEventListener('error', task.failed);
  active.delete(task);
  waiting.delete(task);
  unobserve(task);
  if (!loaded) task.image.removeAttribute('src');
  if (task.image.isConnected && task.image.parentElement === task.parent) {
    task.image.hidden = !loaded;
    if (task.image.previousElementSibling) task.image.previousElementSibling.hidden = loaded;
    task.parent.classList.toggle('has-logo', loaded);
  }
  if (pump) drain();
}

function prune() {
  for (const task of [...waiting, ...active]) {
    if (!task.image.isConnected || task.image.parentElement !== task.parent) finish(task, false, false);
  }
}

function drain() {
  if (draining) return;
  draining = true;
  try {
    prune();
    while (active.size < 2 && queue.length) {
      const task = queue.shift();
      task.queued = false;
      if (task.done || !task.visible) continue;
      waiting.delete(task);
      unobserve(task);
      active.add(task);
      task.loaded = () => finish(task, task.image.naturalWidth > 0);
      task.failed = () => finish(task, false);
      task.image.addEventListener('load', task.loaded);
      task.image.addEventListener('error', task.failed);
      // A missing browser event must not hold both artwork slots indefinitely.
      task.timer = setTimeout(task.failed, 10000);
      task.image.src = task.source;
      if (task.image.complete && task.image.naturalWidth > 0) task.loaded();
    }
  } finally { draining = false; }
}

function scanVisible() {
  prune();
  for (const task of waiting) {
    const rect = task.parent.getBoundingClientRect();
    task.visible = rect.width > 0 && rect.height > 0 && rect.bottom >= 0 && rect.right >= 0 &&
      rect.top <= window.innerHeight && rect.left <= window.innerWidth;
    if (task.visible && !task.queued) { task.queued = true; queue.push(task); }
  }
  drain();
}

if (!observer) {
  document.addEventListener('scroll', scanVisible, {capture: true, passive: true});
  document.addEventListener('click', scanVisible);
  document.addEventListener('focusin', scanVisible);
  window.addEventListener('resize', scanVisible, {passive: true});
}

export function bindLogos(scope = document) {
  prune();
  for (const image of scope.querySelectorAll('[data-bank-logo], [data-merchant-logo]')) {
    // Cloned picker marks copy data attributes but need their own listeners and observation.
    if (bound.has(image)) continue;
    const source = image.dataset.logoSrc || image.getAttribute('src');
    if (!source || !image.parentElement) continue;
    image.dataset.logoBound = 'true';
    const task = {image, parent: image.parentElement, source, visible: false, queued: false, done: false};
    bound.set(image, task);
    if (image.complete && image.naturalWidth > 0) { finish(task, true); continue; }
    image.removeAttribute('src');
    waiting.add(task);
    if (!targets.has(task.parent)) { targets.set(task.parent, new Set()); observer?.observe(task.parent); }
    targets.get(task.parent).add(task);
  }
  if (!observer) scanVisible();
  else drain();
}
