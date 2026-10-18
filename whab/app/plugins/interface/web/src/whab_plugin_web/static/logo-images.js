/* Keep optional artwork decorative and leave the fallback visible on failure. */
export function bindLogos(scope = document) {
  for (const image of scope.querySelectorAll('[data-bank-logo], [data-merchant-logo]')) {
    if (image.dataset.logoBound) continue;
    image.dataset.logoBound = 'true';
    const update = () => {
      const loaded = image.naturalWidth > 0;
      image.hidden = !loaded;
      image.previousElementSibling.hidden = loaded;
      image.parentElement.classList.toggle('has-logo', loaded);
    };
    image.addEventListener('load', update);
    image.addEventListener('error', update);
    if (image.complete) update();
  }
}
