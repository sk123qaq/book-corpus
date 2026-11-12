/* 分层读前端公共层：只读 API，数据全部来自本地服务。 */
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
      error.conflicts = (payload && payload.conflicts) || [];
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

  RD.tierNames = { skim: '速览', standard: '标准', deep: '深读' };
  RD.depthNames = { model: '结构模型', panels: '面板', skim: '速览', standard: '标准', deep: '深读' };
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
        '；可改，按本次工单生效。</p>';
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

  RD.statusName = {
    queued: '排队', chunking: '切块', annotating: '标注', validating: '校验',
    projecting: '组档', rendering: '渲染', done: '完成', partial: '部分通过',
    failed: '失败', cancelled: '已取消'
  };
  RD.activeStates = ['queued', 'chunking', 'annotating', 'validating', 'projecting', 'rendering'];
  RD.jobForTier = (jobs, tier) => (jobs || []).find((job) =>
    (job.tiers || []).includes(tier) && RD.activeStates.includes(job.state)
  ) || null;
  RD.activeTierKeys = (jobs) => {
    const keys = new Set();
    (jobs || []).forEach((job) => {
      if (RD.activeStates.includes(job.state)) (job.tiers || []).forEach((tier) => keys.add(tier));
    });
    return keys;
  };
  RD.jobHint = (job) => {
    if (!job) return '';
    if (job.state === 'cancelled') return '已取消（收尾中）';
    // 失败/完成/部分通过都不是「进行中」，别拼出「失败中」。
    if (!RD.activeStates.includes(job.state)) return RD.statusName[job.state] || job.state;
    return (RD.statusName[job.state] || job.state) + '中';
  };

  RD.jobSteps = (job) => {
    const steps = (job && job.steps) || [];
    return '<ol class="steps">' + steps.map((step) => (
      '<li class="step ' + (step.status === 'done' ? 'done' : step.status === 'run' ? 'run' : step.status === 'failed' ? 'fail' : '') + '">' +
      '<span class="mark">' + (step.status === 'done' ? '✓' : step.status === 'failed' ? '✗' : '') + '</span>' +
      '<span>' + RD.esc(step.name) + '</span></li>'
    )).join('') + '</ol>';
  };

  // 「已用 2 分 17 秒」比毫秒好读，也便于核对六层各花了多久。
  RD.elapsed = (ms) => {
    const total = Number(ms);
    if (!(total > 0)) return '';
    const seconds = Math.round(total / 1000);
    if (seconds < 60) return seconds + ' 秒';
    const minutes = Math.floor(seconds / 60);
    const rest = seconds % 60;
    if (minutes < 60) return minutes + ' 分' + (rest ? ' ' + rest + ' 秒' : '');
    return Math.floor(minutes / 60) + ' 小时 ' + (minutes % 60) + ' 分';
  };

  // 一行摘要：第几层、用时、错误种类。失败时不用展开也能判断停在哪。
  // 页面上不出现裸的英文类别；认不出的原样显示，方便查事件账。
  RD.errorKindName = (kind) => ({
    transport_closed: '对端未回状态行就断开',
    transport_timeout: '读超时',
    transport_reset: '连接被重置',
    transport_truncated: '响应被截断',
    transport_dns: '域名解析失败',
    transport_tls: 'TLS 握手失败',
    transport_refused: '连接被拒',
    rate_limited: '被限流',
    http_5xx: '服务端错误',
    http_4xx: '请求被拒',
    auth: '鉴权失败',
    truncated: '输出被截断',
    protocol: '响应不合协议',
    config: '配置错误',
    restart: '服务重启中断',
    cancelled: '已取消',
    quality: '未过硬门槛',
    unexpected: '未分类异常',
    provider: '提供方失败',
    transport_other: '连接异常',
  })[kind] || kind || '';

  RD.jobMeta = (job) => {
    if (!job) return '';
    const parts = [];
    if (job.kind === 'depth') {
      const order = ['model', 'panels', 'skim', 'standard', 'deep', 'render'];
      const layer = job.failed_layer || job.layer;
      const index = order.indexOf(layer);
      if (index >= 0) {
        parts.push('第 ' + (index + 1) + '/6 层' + (layer === 'render' ? '' : ' · ' + (RD.depthNames[layer] || layer)));
      }
      const ready = (job.layers_ready || []).length;
      if (ready) parts.push('已落 ' + ready + '/5 层');
    }
    const took = RD.elapsed(job.elapsed_ms);
    if (took) parts.push('已用 ' + took);
    if (job.error_kind) parts.push(RD.errorKindName(job.error_kind));
    if (job.attempts) parts.push('尝试 ' + job.attempts + ' 次');
    return parts.join(' · ');
  };

  RD.jobCard = (job) => {
    const state = job.state || 'queued';
    const bad = state === 'failed';
    const names = job.kind === 'depth'
      ? (job.layers || []).map((layer) => RD.depthNames[layer] || layer)
      : (job.tiers || []).map((tier) => RD.tierNames[tier] || tier);
    return '<div class="job-inline open" data-job="' + RD.esc(job.id) + '">' +
      '<div class="job-head"><span class="eyebrow">工单 ' + RD.esc(job.id) + '</span>' +
      '<span class="job-state ' + (bad ? 'bad' : '') + '">' + RD.esc(RD.statusName[state] || state) + '</span></div>' +
      '<div class="job-title">' + RD.esc(job.uid) + '-' + RD.esc(job.n) + ' · ' +
      names.join(' / ') + '</div>' +
      RD.jobSteps(job) +
      (RD.jobMeta(job) ? '<div class="job-meta">' + RD.esc(RD.jobMeta(job)) + '</div>' : '') +
      '<div class="job-progress"><i style="width:' + Number(job.progress || 0) + '%"></i></div>' +
      (job.error ? '<p class="job-error">' + RD.esc(job.error) + '</p>' : '') +
      '</div>';
  };

  RD.pollJob = (jobId, mount, onDone, onTick) => {
    let misses = 0;
    const tick = async () => {
      try {
        const job = await RD.api('/api/jobs/' + encodeURIComponent(jobId));
        misses = 0;
        if (mount) mount.innerHTML = RD.jobCard(job);
        if (onTick) onTick(job);
        if (['done', 'partial', 'failed', 'cancelled'].includes(job.state)) {
          if (onDone) onDone(job);
          return;
        }
      } catch (err) {
        misses += 1;
        if (misses > 6) {
          RD.toast(err.message, true);
          return;
        }
      }
      setTimeout(tick, 700);
    };
    tick();
  };

  RD.tierDots = (tiers) => {
    const keys = ['skim', 'standard', 'deep'];
    return '<span class="tier-sq" title="三档出片状态">' + keys.map((key) => {
      const info = (tiers && tiers[key]) || {};
      const cls = info.exists ? (info.stale ? 'stale' : 'on') : '';
      return '<i class="' + cls + '" data-tier="' + key + '"></i>';
    }).join('') + '</span>';
  };

  RD.path = () => location.pathname.replace(/\/+$/, '');

  RD.bookHref = (uid) => '/book/' + encodeURIComponent(uid);
  RD.chapterHref = (uid, n) => '/chapter/' + encodeURIComponent(uid) + '/' + String(n).padStart(2, '0');
  RD.recordsJobHref = (jobId) => '/records?job=' + encodeURIComponent(jobId);
  RD.readerHref = (uid, n, tier, path) => '/reader/' + encodeURIComponent(uid) + '/' + String(n).padStart(2, '0') +
    (path === 'depth' ? '/depth' : '') + (tier ? '?tier=' + encodeURIComponent(tier) : '');

  RD.night = () => {
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
