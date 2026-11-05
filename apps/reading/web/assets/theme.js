(function () {
  const root = document.documentElement;
  const KEY = 'reading-theme';
  const buttons = () => document.querySelectorAll('[data-theme-toggle]');

  function paint(mode) {
    const cold = mode === 'cold';
    root.classList.toggle('cold', cold);
    buttons().forEach((b) => {
      b.setAttribute('aria-pressed', cold ? 'true' : 'false');
      b.setAttribute('aria-label', cold ? '切换暖纸版' : '切换冷纸版');
      b.setAttribute('title', cold ? '当前冷纸版，点击切回暖纸' : '当前暖纸版，点击切换冷纸');
    });
  }

  let mode = 'warm';
  try { mode = localStorage.getItem(KEY) === 'cold' ? 'cold' : 'warm'; } catch (e) {}
  const q = new URLSearchParams(location.search).get('theme');
  if (q === 'cold' || q === 'warm') {
    mode = q;
    try { localStorage.setItem(KEY, mode); } catch (e) {}
  }
  paint(mode);

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-theme-toggle]');
    if (!btn) return;
    mode = root.classList.contains('cold') ? 'warm' : 'cold';
    try { localStorage.setItem(KEY, mode); } catch (e) {}
    paint(mode);
  });

  // file:// pages may not share localStorage, so carry the mode in local links.
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[href]');
    if (!a || a.target === '_blank' || a.hasAttribute('download')) return;
    const raw = a.getAttribute('href');
    if (!raw || raw.startsWith('#') || raw.startsWith('mailto:') || raw.startsWith('http')) return;
    let url;
    try { url = new URL(raw, location.href); } catch (err) { return; }
    if (url.protocol !== location.protocol || url.host !== location.host) return;
    if (root.classList.contains('cold')) url.searchParams.set('theme', 'cold');
    else url.searchParams.delete('theme');
    a.href = url.href;
  });
})();
