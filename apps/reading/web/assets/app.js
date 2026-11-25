/* 阅读工作台前端公共层：只读与伴读 API，数据全部来自本地服务。 */
(function () {
  const RD = (window.RD = {});

  RD.esc = (value) => String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  RD.norm = (value) => String(value == null ? '' : value)
    .normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');

  RD.fmt = (n) => Number(n || 0).toLocaleString('zh-CN');

  RD.wan = (n) => {
    const v = Number(n || 0);
    if (v >= 10000) return (v / 10000).toFixed(v >= 1000000 ? 0 : 1).replace(/\.0$/, '');
    return RD.fmt(v);
  };

  RD.hl = (text, q) => {
    const raw = String(text == null ? '' : text);
    const needle = String(q || '').trim();
    if (!needle) return RD.esc(raw);
    const pattern = needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    try {
      return raw.split(new RegExp('(' + pattern + ')', 'gi')).map((part, i) => (
        i % 2 ? '<mark>' + RD.esc(part) + '</mark>' : RD.esc(part)
      )).join('');
    } catch (err) {
      return RD.esc(raw);
    }
  };

  RD.api = async (path, options) => {
    const init = Object.assign({ headers: {} }, options || {});
    if (init.body && typeof init.body !== 'string') {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(init.body);
    }
    const res = await fetch(path, init);
    const type = res.headers.get('Content-Type') || '';
    const payload = type.includes('json') ? await res.json() : await res.text();
    if (!res.ok) {
      const message = (payload && payload.error) || ('请求失败：' + res.status);
      const error = new Error(message);
      error.status = res.status;
      error.payload = payload;
      error.code = payload && payload.code;
      throw error;
    }
    return payload;
  };

  RD.toast = (message, bad) => {
    let node = document.getElementById('rd-toast');
    if (!node) {
      node = document.createElement('div');
      node.id = 'rd-toast';
      node.className = 'toast';
      node.setAttribute('role', 'status');
      node.setAttribute('aria-live', 'polite');
      document.body.appendChild(node);
    }
    node.textContent = message;
    node.classList.toggle('bad', Boolean(bad));
    node.classList.add('show');
    clearTimeout(node._timer);
    node._timer = setTimeout(() => node.classList.remove('show'), 3200);
  };

  RD.fail = (mount, message) => {
    if (mount) {
      mount.innerHTML = '<div class="empty"><b>没读出来</b><p>' + RD.esc(message) + '</p>' +
        '<p><a class="btn btn-ghost" href="/">回书架</a></p></div>';
    }
  };

  RD.actionNames = {
    chapter_map: '知识地图',
    diagram: '图解此段',
    explain: '讲人话举例',
    translate: '翻译原意',
    critique: '拆解前提',
    ask: '自由追问',
    checklist: '行动自检清单',
    narrative_clues: '叙事视角与伏笔线索',
    imagery: '意象、潜台词与细读',
    historical_dossier: '人物与名物档案',
    compare_context: '多史家视角对照',
    boundary_check: '演进与适用边界',
    sparring_dossier: '学术交锋档案',
    counterexample_lab: '反例与现实检验',
    method_audit: '方法与样本局限',
    craft_notes: '工艺流派考据',
    pin: '钉住旁批',
    unpin: '移除旁批',
  };

  RD.modelDefaults = { model: 'deepseek-v4.1-flash', effort: 'high' };
  RD.modelOptions = ['deepseek-v4.1-flash', 'glm-5.3-flash'];
  RD.modelSettings = () => {
    let model = RD.modelDefaults.model;
    let effort = RD.modelDefaults.effort;
    try {
      model = localStorage.getItem('reading-model') || model;
      effort = localStorage.getItem('reading-effort') || effort;
    } catch (e) {}
    if (!['max', 'high', 'medium', 'low'].includes(effort)) effort = RD.modelDefaults.effort;
    return { model: String(model || RD.modelDefaults.model).trim(), effort };
  };
  RD.modelSettingsHtml = () => {
    const settings = RD.modelSettings();
    const efforts = ['max', 'high', 'medium', 'low'].map((value) =>
      '<option value="' + value + '"' + (settings.effort === value ? ' selected' : '') + '>' + value + '</option>'
    ).join('');
    const options = RD.modelOptions.map((value) => '<option value="' + RD.esc(value) + '"></option>').join('');
    return '<div class="model-row">' +
      '<label class="model-field"><span>模型</span><input id="model-input" list="model-options" value="' +
        RD.esc(settings.model) + '" spellcheck="false" autocomplete="off"></label>' +
      '<label class="model-field effort"><span>推理</span><select id="effort-select">' + efforts + '</select></label>' +
      '<datalist id="model-options">' + options + '</datalist></div>' +
      '<p class="model-note">默认 ' + RD.esc(RD.modelDefaults.model) + ' · ' + RD.esc(RD.modelDefaults.effort) +
        '；修改后用于伴读与知识地图。</p>';
  };
  RD.bindModelSettings = () => {
    const model = RD.mount('model-input');
    const effort = RD.mount('effort-select');
    if (!model || !effort) return;
    const save = () => {
      try {
        localStorage.setItem('reading-model', model.value.trim() || RD.modelDefaults.model);
        localStorage.setItem('reading-effort', effort.value);
      } catch (e) {}
    };
    model.addEventListener('change', save);
    model.addEventListener('blur', save);
    effort.addEventListener('change', save);
  };

  RD.elapsed = (ms) => {
    const total = Number(ms);
    if (!(total > 0)) return '';
    if (total < 1000) return total + ' ms';
    const seconds = Math.round(total / 1000);
    if (seconds < 60) return seconds + ' 秒';
    const minutes = Math.floor(seconds / 60);
    const rest = seconds % 60;
    if (minutes < 60) return minutes + ' 分' + (rest ? ' ' + rest + ' 秒' : '');
    return Math.floor(minutes / 60) + ' 小时 ' + (minutes % 60) + ' 分';
  };

  RD.renderMarkdown = (text) => {
    const lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
    const out = [];
    let inList = false;
    const flushList = () => {
      if (inList) { out.push('</ul>'); inList = false; }
    };
    const inline = (s) => {
      let html = RD.esc(s);
      html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
      html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      html = html.replace(/\[(原文|补充|举例)\]/g, (_, tag) => {
        const cls = tag === '原文' ? 'tag-src' : 'tag-ext';
        return '<span class="src-tag ' + cls + '">[' + tag + ']</span>';
      });
      return html;
    };
    for (const raw of lines) {
      const line = raw.trimEnd();
      if (!line.trim()) {
        flushList();
        continue;
      }
      if (/^###\s+/.test(line)) {
        flushList();
        out.push('<h4>' + inline(line.replace(/^###\s+/, '')) + '</h4>');
      } else if (/^##?\s+/.test(line)) {
        flushList();
        out.push('<h3>' + inline(line.replace(/^##?\s+/, '')) + '</h3>');
      } else if (/^[-*•]\s+/.test(line.trim())) {
        if (!inList) { out.push('<ul>'); inList = true; }
        out.push('<li>' + inline(line.trim().replace(/^[-*•]\s+/, '')) + '</li>');
      } else if (/^\d+\.\s+/.test(line.trim())) {
        if (!inList) { out.push('<ul>'); inList = true; }
        out.push('<li>' + inline(line.trim()) + '</li>');
      } else {
        flushList();
        out.push('<p>' + inline(line.trim()) + '</p>');
      }
    }
    flushList();
    return out.join('');
  };

  RD.path = () => location.pathname.replace(/\/+$/, '');
  RD.bookHref = (uid) => '/book/' + encodeURIComponent(uid);
  RD.chapterHref = (uid, n, blockId) => {
    const base = '/chapter/' + encodeURIComponent(uid) + '/' + String(n).padStart(2, '0');
    return blockId ? base + '#blk-' + encodeURIComponent(blockId) : base;
  };

  RD.night = () => {
    if (typeof window.__banduSyncTheme === 'function') {
      window.__banduSyncTheme();
      return;
    }
    const KEY = 'reading-night';
    let on = false;
    try { on = localStorage.getItem(KEY) === '1'; } catch (e) {}
    const sync = () => {
      document.body.classList.toggle('night', on);
      const btn = document.getElementById('night');
      if (btn) btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    };
    sync();
    const btn = document.getElementById('night');
    if (btn) btn.addEventListener('click', () => {
      on = !on;
      try { localStorage.setItem(KEY, on ? '1' : '0'); } catch (e) {}
      sync();
    });
  };

  RD.mount = (id) => document.getElementById(id);

  RD.ready = (fn) => {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  };
})();
