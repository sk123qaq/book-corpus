/* 伴读 · 顶栏搜索快捷键、滚动自动收起兜底与顶栏交互 */
(function () {
  const input = document.querySelector('.topbar .top-search input, .topbar .search input');
  if (input) {
    input.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      const q = input.value.trim();
      location.href = '/catalog' + (q ? '?q=' + encodeURIComponent(q) : '');
    });
  }
  document.addEventListener('keydown', (e) => {
    const tag = (document.activeElement && document.activeElement.tagName) || '';
    if (e.key !== '/' || /^(INPUT|TEXTAREA|SELECT)$/.test(tag) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (!input) return;
    e.preventDefault();
    input.focus();
    input.select();
  });

  if (!window.CSS?.supports?.('container-type', 'scroll-state')) {
    const bar = document.querySelector('.topbar');
    if (bar) {
      let lastY = window.scrollY;
      window.addEventListener('scroll', () => {
        const maxY = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
        const y = Math.max(0, Math.min(window.scrollY, maxY));
        if (y === lastY) return;
        bar.classList.toggle('is-hidden', y > lastY && y > bar.offsetHeight);
        lastY = y;
      }, { passive: true });
    }
  }
})();
