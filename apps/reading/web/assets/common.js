(function () {
  const input = document.querySelector('.topbar .search input');
  if (input) {
    input.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      const q = input.value.trim();
      location.href = '/catalog' + (q ? '?q=' + encodeURIComponent(q) : '');
    });
  }
  document.addEventListener('keydown', (e) => {
    if (e.key !== '/' || /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) return;
    if (!input) return;
    e.preventDefault();
    input.focus();
    input.select();
  });
})();
