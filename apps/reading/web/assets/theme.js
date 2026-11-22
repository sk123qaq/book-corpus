/* 伴读 · 三态纸色主题切换（暖纸 / 冷纸 / 夜读）与本地链接延续 */
(function () {
  const root = document.documentElement;
  const THEME_KEY = 'reading-theme';
  const NIGHT_KEY = 'reading-night';
  const META_COLORS = { warm: '#F4F1E8', cold: '#EEF1F5', night: '#151615' };

  function readPaperMode() {
    let paper = 'warm';
    try {
      if (localStorage.getItem(THEME_KEY) === 'cold') paper = 'cold';
    } catch (e) {}
    const q = new URLSearchParams(location.search).get('theme');
    if (q === 'cold' || q === 'warm') {
      paper = q;
      try { localStorage.setItem(THEME_KEY, paper); } catch (e) {}
    }
    return paper;
  }

  function readNightOn() {
    const q = new URLSearchParams(location.search).get('theme');
    if (q === 'cold' || q === 'warm') return false;
    if (root.dataset.bootNight === '1') return true;
    try { return localStorage.getItem(NIGHT_KEY) === '1'; } catch (e) { return false; }
  }

  let paperMode = readPaperMode();
  let nightOn = readNightOn();

  function syncDom() {
    const isCold = paperMode === 'cold';
    root.classList.toggle('cold', isCold);
    if (document.body) {
      document.body.classList.toggle('night', nightOn);
    }
    const activeTheme = nightOn ? 'night' : (isCold ? 'cold' : 'warm');
    root.setAttribute('data-theme', activeTheme);
    const meta = document.getElementById('meta-theme-color');
    if (meta) meta.setAttribute('content', META_COLORS[activeTheme] || META_COLORS.warm);

    document.querySelectorAll('[data-theme-choice]').forEach((btn) => {
      const choice = btn.dataset.themeChoice;
      btn.setAttribute('aria-pressed', String(choice === activeTheme));
    });
    document.querySelectorAll('[data-theme-toggle]').forEach((btn) => {
      const coldActive = !nightOn && isCold;
      btn.setAttribute('aria-pressed', coldActive ? 'true' : 'false');
      if (!btn.dataset.themeChoice) {
        btn.setAttribute('aria-label', coldActive ? '切换暖纸版' : '切换冷纸版');
        btn.setAttribute('title', coldActive ? '当前冷纸版，点击切回暖纸' : '当前暖纸版，点击切换冷纸');
      }
    });
    const nightBtn = document.getElementById('night');
    if (nightBtn) {
      nightBtn.setAttribute('aria-pressed', nightOn ? 'true' : 'false');
    }
  }

  function setThemeMode(choice) {
    if (choice === 'night') {
      nightOn = true;
      try { localStorage.setItem(NIGHT_KEY, '1'); } catch (e) {}
    } else if (choice === 'cold' || choice === 'warm') {
      paperMode = choice;
      nightOn = false;
      try {
        localStorage.setItem(THEME_KEY, paperMode);
        localStorage.setItem(NIGHT_KEY, '0');
      } catch (e) {}
    }
    syncDom();
  }

  syncDom();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', syncDom, { once: true });
  }

  window.__banduSyncTheme = syncDom;
  window.__banduSetTheme = setThemeMode;

  document.addEventListener('click', (e) => {
    const choiceBtn = e.target.closest('[data-theme-choice]');
    if (choiceBtn) {
      const choice = choiceBtn.dataset.themeChoice;
      if (choice === 'night') {
        if (nightOn) setThemeMode(paperMode);
        else setThemeMode('night');
      } else if (choice === 'cold') {
        if (!nightOn && paperMode === 'cold') setThemeMode('warm');
        else setThemeMode('cold');
      } else {
        setThemeMode('warm');
      }
      return;
    }

    const legacyToggle = e.target.closest('[data-theme-toggle]');
    if (legacyToggle) {
      const next = (!nightOn && paperMode === 'cold') ? 'warm' : 'cold';
      setThemeMode(next);
      return;
    }

    const nightBtn = e.target.closest('#night');
    if (nightBtn) {
      if (nightOn) setThemeMode(paperMode);
      else setThemeMode('night');
    }
  });

  // file:// 或跨页链接透传冷/暖纸模式
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
