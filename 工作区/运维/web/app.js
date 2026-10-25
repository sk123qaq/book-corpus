'use strict';

const TABS = [
  ['overview', '总览'], ['import', '新书导入'], ['split', '电子书'],
  ['pdf', 'PDF'], ['maintain', '维护'], ['retire', '删书'], ['logs', '日志'],
];

const MAINTAIN_STEPS = [
  ['index', '更新检索索引', '让新章节能被搜到。最花时间。'],
  ['topics', '刷新主题入口', '重建按主题浏览的链接。'],
  ['awards', '刷新奖项入口', '重建按奖项浏览的链接。'],
  ['audit', '体检', '检查书目、文件与索引是否对得上。'],
  ['verify', '结构验收', '按固定清单检查库的结构是否完整。'],
  ['p1', '抽查搜索效果', '用固定题目跑一遍，确认搜得准。'],
  ['catalog', '更新书目总表', '重出 Excel 与 CSV。'],
  ['db', '重建书目数据库', '从每本书的记录重算索引库。'],
  ['doc', '重出阅读版页面', '只有阅读版有改动时才需要。'],
];
const FINISH_DEFAULT = ['db', 'index', 'topics', 'awards', 'audit', 'verify', 'p1', 'catalog'];
const PDF_ROUTES = ['OCR', '本地抽文本', '不转-记图册', '人工确认', '日文暂缓'];
const JOB_WORD = {
  queued: '排队', running: '在跑', awaiting_review: '待确认', succeeded: '完成',
  failed: '失败', cancelled: '取消', orphaned: '孤儿',
};

const S = {
  tab: 'overview',
  payload: null, status: {}, health: null,
  jobs: [], pending: [], inbox: [], pdf: null,
  plan: null, planJob: null, planDirty: false,
  batch: null, splitBooks: [], triage: null, triageJob: null,
  retireHits: [], retireQuery: '', preview: null,
  logJob: null, logDetail: null, follow: true, importDone: null,
  filter: '', selected: new Set(), busy: false,
  replaceMd: false, splitWorkers: 4,
  triageUid: '', pdfUids: '', pdfLimit: 0, pdfWorkers: 3,
  maintainSteps: new Set(FINISH_DEFAULT),
};

const $ = (sel) => document.querySelector(sel);

function esc(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function dig(obj, path, dflt) {
  let cur = obj;
  for (const key of path.split('.')) {
    if (cur === null || cur === undefined || !(key in cur)) return dflt;
    cur = cur[key];
  }
  return cur === null || cur === undefined ? dflt : cur;
}

function num(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value === undefined || value === null ? '0' : value);
  return n.toLocaleString('en-US');
}

function fmtTime(value) {
  if (!value) return '—';
  const text = String(value).replace('T', ' ');
  return text.slice(0, 16);
}

function fmtBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  if (n < 1024 * 1024 * 1024) return (n / 1048576).toFixed(1) + ' MB';
  return (n / 1073741824).toFixed(2) + ' GB';
}

function word(state) {
  const cls = { ok: '通', half: '半', miss: '缺', out: '外' }[state] || state;
  return `<span class="word ${esc(state)}">${esc(cls)}</span>`;
}

function toast(message, bad) {
  const el = $('#toast');
  el.textContent = message;
  el.className = 'toast on' + (bad ? ' bad' : '');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.className = 'toast'; }, 3800);
}

async function api(path, opts) {
  const res = await fetch(path, Object.assign({ headers: {} }, opts || {}));
  let body = null;
  try {
    body = await res.json();
  } catch (err) {
    body = { ok: false, error: 'HTTP ' + res.status };
  }
  if (!res.ok || body.ok === false) {
    const error = (body && body.error) || ('HTTP ' + res.status);
    throw new Error(error);
  }
  return body;
}

function post(path, payload) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
}

function setBusy(flag) {
  S.busy = flag;
  document.querySelectorAll('[data-write]').forEach((el) => {
    if (el.tagName === 'BUTTON') el.disabled = flag;
  });
}

// ---- 顶栏与导航 ---------------------------------------------------
function renderNav() {
  const counts = (S.payload && S.payload.state_counts) || {};
  $('#nav').innerHTML = TABS.map(([key, label]) => {
    let badge = '';
    if (key === 'logs') {
      const active = (counts.running || 0) + (counts.queued || 0);
      if (active) badge = `<span class="badge">${active}</span>`;
      else if (counts.awaiting_review) badge = `<span class="badge">${counts.awaiting_review}</span>`;
    }
    if (key === 'split' && S.pending.length) {
      const ebooks = S.pending.filter((r) => /^(epub|mobi|azw3|azw)$/.test(r.format)).length;
      if (ebooks) badge = `<span class="badge">${ebooks}</span>`;
    }
    return `<a href="#${key}" class="${S.tab === key ? 'on' : ''}">${esc(label)}${badge}</a>`;
  }).join('');
}

function renderStats() {
  const st = S.status || {};
  const payload = S.payload || {};
  const pending = st.pending || {};
  const lagBooks = dig(st, 'index.lag_books', 0);
  const lagChapters = dig(st, 'index.lag_chapters', 0);
  const items = [
    ['本', num(dig(st, 'books', 0))],
    ['md', num(dig(st, 'md_done', 0))],
    ['待转', num((pending.pdf || 0) + (pending.txt || 0) + (pending.djvu || 0))],
    ['索引落后', num(lagBooks) + (lagChapters ? ' 本 / ' + num(lagChapters) + ' 章' : ' 本')],
    ['收件箱', num(dig(st, 'inbox.files', 0))],
    ['书目表', fmtTime(st.catalog && st.catalog.mtime).slice(5)],
  ];
  let html = items.map(([label, value]) => `<span class="stat">${esc(label)}<b>${value}</b></span>`).join('');
  if (payload.error) {
    html += `<span class="stat warn" title="${esc(payload.error)}">状态：${esc(payload.source || '缺')}</span>`;
  } else if (payload.source === 'fixture') {
    html += '<span class="stat warn">状态来自 fixture</span>';
  }
  const ops = payload.ops || {};
  if (ops.writer) html += '<span class="stat warn">写任务在跑</span>';
  $('#stats').innerHTML = html;
}

function renderSearch() {
  const health = S.health || {};
  const dot = $('#search-dot');
  dot.className = 'dot ' + (health.running ? 'on' : 'off');
  dot.title = health.running ? '8765 在跑' : '8765 未启动';
  $('#search-btn').textContent = health.running ? '搜索 ↗ 8765' : '打开 8765 ↗';
  $('#search-start').style.display = health.running ? 'none' : '';
}

// ---- 视图骨架 -----------------------------------------------------
function render() {
  renderNav();
  renderStats();
  renderSearch();
  const view = $('#view');
  const map = {
    overview: viewOverview, import: viewImport, split: viewSplit, pdf: viewPdf,
    maintain: viewMaintain, retire: viewRetire, logs: viewLogs,
  };
  view.innerHTML = (map[S.tab] || viewOverview)();
  afterRender();
}

function afterRender() {
  if (S.tab === 'logs' && S.logJob && S.logJob.id) openLog(S.logJob.id);
}

// ---- 总览 ---------------------------------------------------------
function stageCards() {
  const st = S.status || {};
  const pending = st.pending || {};
  const inbox = dig(st, 'inbox.files', 0);
  const lag = dig(st, 'index.lag_books', 0);
  const warn = dig(st, 'audit.warn', 0);
  const fail = dig(st, 'audit.fail', 0);
  const awaiting = dig(S.payload, 'state_counts.awaiting_review', 0);
  const stages = [
    ['S1', '进门 收件箱', inbox + ' 本待处理', inbox ? 'half' : 'ok'],
    ['S2', '入账 人账与 DB', '分类表 ' + num(dig(st, 'ledger.rows', 0)) + ' 行', 'ok'],
    ['S3', '落位 目录与视图', '奖项 ' + num(dig(st, 'views.awards', 0)) + ' / 主题 ' + num(dig(st, 'views.topics', 0)),
      dig(st, 'views.broken', 0) ? 'miss' : 'ok'],
    ['S4', '出正文 md', num(dig(st, 'md_done', 0)) + ' 本有 md，待转 ' + num((pending.pdf || 0) + (pending.txt || 0) + (pending.djvu || 0)),
      ((pending.pdf || 0) + (pending.txt || 0) + (pending.djvu || 0)) ? 'half' : 'ok'],
    ['S5', '检索 索引与扩展', lag ? '落后 ' + num(lag) + ' 本' : '索引同步', lag ? 'half' : 'ok'],
    ['S6', '验收 审计', fail ? 'FAIL ' + fail : 'WARN ' + num(warn) + '，FAIL 0', fail ? 'miss' : (warn ? 'half' : 'ok')],
  ];
  if (awaiting) stages[5][3] = 'half';
  return `<div class="track">${stages.map(([tag, name, read, state]) => `
    <div class="stage">
      <div class="tag">${tag} ${word(state)}</div>
      <div class="name">${esc(name)}</div>
      <div class="read">${esc(read)}</div>
    </div>`).join('')}</div>`;
}

function ledgerCards() {
  const st = S.status || {};
  const jobs = (S.payload && S.payload.state_counts) || {};
  const byKey = (key) => jobs[key] || 0;
  return `<div class="grid c3">
    <div class="card">
      <h3>人账 分类表.csv</h3>
      <p class="mono">${num(dig(st, 'ledger.rows', 0))} 行</p>
      <p class="hint">改于 ${esc(fmtTime(dig(st, 'ledger.mtime', '')))}</p>
      <p class="hint">已有行仍手改 CSV，页面只写入库新增行。</p>
    </div>
    <div class="card">
      <h3>产物 DB 与书目表</h3>
      <p class="mono">段落 ${num(dig(st, 'index.paras', 0))} · 章 ${num(dig(st, 'index.chapters', 0))}</p>
      <p class="hint">索引建于 ${esc(fmtTime(dig(st, 'index.built_at', '')))}</p>
      <p class="hint">书目表 ${esc(fmtTime(dig(st, 'catalog.mtime', '')))} · 未匹配 ${num(dig(st, 'catalog.unmatched', 0))}</p>
      <p><a class="btn sm" href="/api/file?path=${encodeURIComponent('目录/书目总表.csv')}">书目总表.csv</a></p>
    </div>
    <div class="card">
      <h3>机账 任务</h3>
      <p class="mono">在跑 ${byKey('running')} · 排队 ${byKey('queued')} · 待确认 ${byKey('awaiting_review')}</p>
      <p class="hint">失败 ${byKey('failed')} · 孤儿 ${byKey('orphaned')}</p>
      <p class="hint">OCR 批次剩 ${num(dig(st, 'ocr.remaining_books', 0))} 本 / ${num(dig(st, 'ocr.remaining_pages', 0))} 页</p>
    </div>
  </div>`;
}

function breakpoints() {
  const st = S.status || {};
  const pending = st.pending || {};
  const lag = dig(st, 'index.lag_books', 0);
  const ocrLeft = dig(st, 'ocr.remaining_books', 0);
  const rows = [
    ['G3', '分类表到目录名', '半', '改分类后目录名要手工 mv，页面只提示不代做。', '#maintain', '维护页'],
    ['G4', '批次范围', (pending.pdf || 0) || S.pending.length ? '半' : '通',
      `待转 ${num((pending.pdf || 0) + (pending.txt || 0) + (pending.djvu || 0))} 本，新批次 worklist 只写一次。`, '#split', '去拆章'],
    ['G5', 'md 到索引', lag ? '半' : '通', lag ? `索引落后 ${num(lag)} 本。` : '索引与库同步。', '#maintain', '去收尾'],
    ['G10', '冷宫移出', '半', '删书按 uid 推导，预览七层，应用要当天快照与计划哈希。', '#retire', '去删书'],
    ['G11', 'OCR 额度', ocrLeft ? '外' : '通', ocrLeft ? `剩 ${num(ocrLeft)} 本没跑，额度每天 20000 页。` : '待转 PDF 已清零。', '#pdf', '去 PDF'],
  ];
  return `<div class="tw"><table>
    <thead><tr><th>断点</th><th>名称</th><th>状态</th><th>现状</th><th></th></tr></thead>
    <tbody>${rows.map(([id, name, state, text, href, label]) => `<tr>
      <td class="mono">${id}</td><td>${esc(name)}</td>
      <td>${word({ '通': 'ok', '半': 'half', '缺': 'miss', '外': 'out' }[state] || 'half')}</td>
      <td class="wrap">${esc(text)}</td>
      <td><a class="btn sm" href="${href}">${esc(label)}</a></td>
    </tr>`).join('')}</tbody></table></div>`;
}

function nextSteps() {
  const st = S.status || {};
  const items = [];
  const inbox = dig(st, 'inbox.files', 0);
  const ebooks = S.pending.filter((r) => /^(epub|mobi|azw3|azw)$/.test(r.format)).length;
  const pdfs = S.pending.filter((r) => r.format === 'pdf').length;
  const pdfSkip = dig(st, 'ocr.skip_books', 0);
  const pdfDefer = dig(st, 'ocr.deferred_books', 0);
  const pdfAction = Math.max(0, pdfs - pdfSkip - pdfDefer);
  if (inbox) items.push(['收件箱 ' + inbox + ' 本待导入', '#import']);
  if (pdfAction) items.push(['PDF 待分诊或待转 ' + pdfAction + ' 本', '#pdf']);
  else if (pdfs) items.push(['PDF 无 md ' + pdfs + ' 本，图册 ' + pdfSkip + '、日文暂缓 ' + pdfDefer, '#pdf']);
  if (ebooks) items.push(['待拆电子书 ' + ebooks + ' 本', '#split']);
  if (dig(st, 'index.lag_books', 0)) items.push(['索引落后 ' + num(dig(st, 'index.lag_books', 0)) + ' 本', '#maintain']);
  if (dig(st, 'audit.warn', 0)) items.push(['审计 WARN ' + dig(st, 'audit.warn', 0) + ' 条，收尾会刷新', '#maintain']);
  const awaiting = (S.payload && S.payload.state_counts && S.payload.state_counts.awaiting_review) || 0;
  if (awaiting) items.push([awaiting + ' 个任务待人工确认', '#logs']);
  if (!items.length) items.push(['没有待办，库是干净的', '#overview']);
  return `<ul class="next">${items.map(([text, href]) => `<li><a href="${href}">${esc(text)}</a></li>`).join('')}</ul>`;
}

function jobLine(job) {
  const state = job.state;
  const cls = state === 'succeeded' ? 'pass' : (state === 'failed' || state === 'orphaned' ? 'fail' : 'muted');
  return `<div class="jobline">
    <span class="t">${esc(fmtTime(job.created))}</span>
    <span class="pill">${esc(JOB_WORD[state] || state)}</span>
    <span>${esc(job.title)}</span>
    <span class="${cls} small">${esc(job.summary || '')}</span>
    <span class="spacer"></span>
    <a class="btn sm" href="#logs" data-act="job-open" data-id="${esc(job.id)}">日志</a>
  </div>`;
}

function viewOverview() {
  const jobs = S.jobs.slice(0, 5);
  return `<h1>总览</h1>
    <p class="lead">读数来自 ops_status.py 与 ops.db。页面不写人账、library.db、book.json、index.db。</p>
    <div class="section">${stageCards()}</div>
    <div class="section">${ledgerCards()}</div>
    <div class="section card"><h2>断点</h2>${breakpoints()}</div>
    <div class="grid c2 section">
      <div class="card"><h2>下一步</h2>${nextSteps()}</div>
      <div class="card"><h2>最近任务</h2>${jobs.length ? jobs.map(jobLine).join('') : '<p class="hint">还没有任务。</p>'}
        <p><a class="btn sm" href="#logs">全部任务</a></p></div>
    </div>`;
}

// ---- 新书导入 -----------------------------------------------------
function planRow(item, index) {
  const low = item.classify && (item.classify.needs_review
    || item.classify.conf1 < 0.45 || item.note.indexOf('需人工确认') >= 0);
  const subjects = Array.isArray(item.subjects) ? item.subjects.join('/') : (item.subjects || '');
  return `<tr data-row="${index}"${low ? ' class="low"' : ''}>
    <td><select data-field="action" data-idx="${index}">
      ${['add', 'replace', 'conflict', 'skip'].map((a) => `<option value="${a}"${a === item.action ? ' selected' : ''}>${a}</option>`).join('')}
    </select></td>
    <td class="wrap">${esc(item.file)}</td>
    <td><input type="text" data-field="title" data-idx="${index}" value="${esc(item.title)}" size="22"></td>
    <td>${esc(item.author || '')}</td>
    <td>${esc(item.format || '')}</td>
    <td class="mono">${fmtBytes(item.bytes)}</td>
    <td class="mono small">${esc(String(item.sha256 || '').slice(0, 10))}</td>
    <td><input type="text" data-field="subjects" data-idx="${index}" value="${esc(subjects)}" size="16"></td>
    <td><select data-field="retention" data-idx="${index}">
      ${['keep', 'review', 'drop'].map((r) => `<option value="${r}"${r === item.retention ? ' selected' : ''}>${r}</option>`).join('')}
    </select></td>
    <td><input type="text" data-field="uid" data-idx="${index}" value="${esc(item.uid || '')}" size="7"></td>
    <td class="wrap small">${esc(item.note || '')}</td>
  </tr>`;
}

function viewImport() {
  const inbox = S.inbox || [];
  const plan = S.plan;
  return `<h1>新书导入</h1>
    ${S.importDone ? `<div class="banner"><span>${esc(S.importDone)}</span>
      <button class="btn sm" data-act="goto" data-tab="split">去拆章</button>
      <button class="btn sm" data-act="goto" data-tab="pdf">去分诊 PDF</button></div>` : ''}
    <p class="lead">上传只落 库/_收件箱/，入库走 ingest.py apply，之后自动 build_library 与 gen_book_catalog。</p>
    <div class="grid c2 section">
      <div class="card">
        <h2>上传</h2>
        <div class="upload">
          <input type="file" id="up-file" multiple>
          <p class="hint">epub / mobi / azw3 / azw / pdf / txt / djvu，单文件上限 2 GB。</p>
          <div class="bar"><i id="up-bar"></i></div>
          <p class="hint" id="up-note"></p>
        </div>
      </div>
      <div class="card">
        <div class="card-head"><h2>收件箱 ${inbox.length} 本</h2>
          <button class="btn sm" data-act="inbox-refresh">刷新</button></div>
        <div class="tw"><table><thead><tr><th>文件</th><th>格式</th><th>大小</th><th>落盘时间</th></tr></thead>
        <tbody>${inbox.length ? inbox.map((f) => `<tr><td class="wrap">${esc(f.name)}</td>
          <td>${esc(f.format)}</td><td class="mono">${fmtBytes(f.bytes)}</td>
          <td class="mono small">${esc(fmtTime(f.mtime))}</td></tr>`).join('')
    : '<tr><td colspan="4" class="hint">收件箱是空的。</td></tr>'}</tbody></table></div>
        <p class="row"><button class="btn primary" data-write data-act="import-scan">扫描收件箱</button>
        <span class="hint">调 ingest.py scan --json，模型预填只给 add 行。</span></p>
      </div>
    </div>
    <div class="section card">
      <div class="card-head"><h2>扫描后的计划${S.planJob ? ' · ' + esc(S.planJob.slice(0, 24)) : ''}</h2>
        <span class="row tight">
          <button class="btn sm" data-act="plan-defaults" ${plan ? '' : 'disabled'}>全部采用预填</button>
          <button class="btn sm primary" data-write data-act="import-apply" ${plan ? '' : 'disabled'}>应用入库</button>
        </span></div>
      ${plan ? planTable(plan) : '<p class="hint">还没有计划。点「扫描收件箱」出计划。</p>'}
    </div>`;
}

function planTable(plan) {
  const counts = plan.reduce((acc, row) => {
    acc[row.action] = (acc[row.action] || 0) + 1;
    return acc;
  }, {});
  return `<p class="hint">add ${counts.add || 0} · replace ${counts.replace || 0} · conflict ${counts.conflict || 0} · skip ${counts.skip || 0}。低置信行标黄。</p>
    <div class="tw"><table>
    <thead><tr><th>动作</th><th>文件</th><th>书名</th><th>作者</th><th>格式</th><th>大小</th>
      <th>sha256</th><th>一级/二级</th><th>保留</th><th>uid</th><th>说明</th></tr></thead>
    <tbody>${plan.map(planRow).join('')}</tbody></table></div>
    <p class="hint">改完点「应用入库」。应用门槛是 conflicts=0 与 未匹配 0 条。</p>`;
}

// ---- 电子书 -------------------------------------------------------
function viewSplit() {
  const ebooks = S.pending.filter((r) => /^(epub|mobi|azw3|azw)$/.test(r.format));
  const allPicked = ebooks.length > 0 && ebooks.every((r) => S.selected.has(r.uid));
  return `<h1>电子书出正文</h1>
    <p class="lead">每次新建 split-ops-&lt;时间戳&gt; 批次，worklist 只写一次。agent 修复只覆盖 verify 与 audit 失败。</p>
    <div class="section card">
      <div class="card-head"><h2>待拆 ${ebooks.length} 本</h2>
        <span class="row tight">
          <label class="check"><input type="checkbox" data-act="split-all"${allPicked ? ' checked' : ''}> 全选</label>
          <label class="check"><input type="checkbox" id="split-replace"${S.replaceMd ? ' checked' : ''}> 重拆已有 md</label>
          <label class="check">并发 <input type="number" id="split-workers" value="${S.splitWorkers || 4}" min="1" max="16" style="width:64px"></label>
          <button class="btn sm primary" data-write data-act="split-run">开始拆章</button>
        </span></div>
      <div class="tw"><table><thead><tr><th></th><th>uid</th><th>书名</th><th>格式</th><th>源大小</th><th>源文件</th></tr></thead>
      <tbody>${ebooks.length ? ebooks.map((row) => `<tr>
        <td><input type="checkbox" data-act="split-pick" value="${esc(row.uid)}"${S.selected.has(row.uid) ? ' checked' : ''}></td>
        <td class="mono">${esc(row.uid)}</td>
        <td class="wrap">${esc(row.title)}</td>
        <td>${esc(row.format || '')}</td>
        <td class="mono">${fmtBytes(row.bytes)}</td>
        <td class="wrap small">${esc(row.file || '')}</td>
      </tr>`).join('') : '<tr><td colspan="6" class="hint">没有待拆的电子书。</td></tr>'}</tbody></table></div>
      <p class="hint">勾「重拆已有 md」会调 --replace，旧 md 由脚本改名为 &lt;stage&gt;.prev-&lt;stamp&gt; 并把路径写进 logs 的 previous 字段。</p>
    </div>
    <div class="section card" data-live="split-batch">
      <div class="card-head"><h2>批次 ${esc(S.batch || '—')}</h2>
        <button class="btn sm" data-act="split-refresh" ${S.batch ? '' : 'disabled'}>刷新单本状态</button></div>
      ${splitBooks()}
    </div>
    <div class="section card"><h2>最近的拆章任务</h2>${recentJobs('split.run')}</div>`;
}

function splitBooks() {
  if (!S.batch) return '<p class="hint">还没开批次。选书后点「开始拆章」。</p>';
  if (!S.splitBooks.length) return '<p class="hint">批次里还没有 logs/&lt;uid&gt;.json，脚本跑起来后刷新。</p>';
  return `<div class="tw"><table><thead><tr><th>uid</th><th>状态</th><th>verify</th><th>audit</th>
    <th>文本量比</th><th>章</th><th>修复</th><th>落地</th><th>旧 md</th><th></th></tr></thead>
    <tbody>${S.splitBooks.map((b) => `<tr>
      <td class="mono">${esc(b.uid)}</td>
      <td><span class="pill">${esc(b.status || '?')}</span></td>
      <td>${b.verify_rc === 0 ? '<span class="pass">0</span>' : esc(b.verify_rc)}</td>
      <td>${b.audit_rc === 0 ? '<span class="pass">0</span>' : esc(b.audit_rc)}</td>
      <td class="mono">${b.audit_ratio ? (b.audit_ratio * 100).toFixed(1) + '%' : '—'}</td>
      <td class="mono">${esc(b.chapters === null || b.chapters === undefined ? '—' : b.chapters)}</td>
      <td class="mono">${esc(b.fix || 0)}</td>
      <td class="wrap small">${esc(b.dest || '')}</td>
      <td class="wrap small">${esc(b.previous || '')}</td>
      <td><button class="btn sm" data-act="split-one" data-uid="${esc(b.uid)}">只重跑这本</button></td>
    </tr>`).join('')}</tbody></table></div>`;
}

function recentJobs(kind, limit = 6) {
  const rows = S.jobs.filter((j) => j.kind === kind).slice(0, limit);
  if (!rows.length) return '<p class="hint">还没有这类任务。</p>';
  return rows.map(jobLine).join('');
}

// ---- PDF ----------------------------------------------------------
function viewPdf() {
  const data = S.pdf;
  const groups = (data && data.groups) || [];
  const batch = (data && data.batch) || {};
  return `<h1>PDF 出正文</h1>
    <p class="lead">新 PDF 先分诊再确认 route，之后建 pdf-ocr-ops-&lt;日期&gt; 批次。旧批次只读。</p>
    <div class="section card">
      <div class="card-head"><h2>队列</h2><button class="btn sm" data-act="pdf-refresh">刷新</button></div>
      <div class="grid c3">
        ${groups.map((g) => `<div class="card tight">
          <div class="hint">${esc(g.route)}</div>
          <div class="mono">${num(g.books)} 本 · ${num(g.pages)} 页</div>
        </div>`).join('')}
      </div>
      <p class="hint">不转-记图册 ${esc(data ? data.skip : '—')} 本；日文暂缓 ${esc(data ? data.deferred : '—')} 本。</p>
    </div>
    <div class="section card">
      <h2>现有批次 ${esc(batch.name || '—')}</h2>
      <p class="mono">worklist ${num(batch.worklist)} · 已落地 ${num(batch.landed)} · 剩 ${num(batch.remaining_books)} 本 / ${num(batch.remaining_pages)} 页 · 日文暂缓 ${num(data ? data.deferred : 0)} 本 · 额度 ${esc(batch.quota || '20000/天')}</p>
      <p class="hint">额度用尽时接口回 403 code 12001，第二天续跑，不重复派发。</p>
    </div>
    <div class="grid c2 section">
      <div class="card">
        <h2>新 PDF 分诊</h2>
        <p class="row tight"><input type="text" id="triage-uid" placeholder="B1234" size="10" value="${esc(S.triageUid)}">
          <button class="btn" data-act="pdf-triage">分诊</button></p>
        <p class="hint">跑 ops_pdf_triage.py：探针、3 张缩略图、模型建议。不写账。</p>
        ${triagePanel()}
      </div>
      <div class="card">
        <h2>确认后转 md</h2>
        <p class="row tight"><input type="text" id="pdf-uids" placeholder="B1234,B1235" size="18" value="${esc(S.pdfUids)}">
          <label class="check">本数 <input type="number" id="pdf-limit" value="${S.pdfLimit}" min="0" style="width:60px"></label>
          <label class="check">并发 <input type="number" id="pdf-workers" value="${S.pdfWorkers || 3}" min="1" max="8" style="width:60px"></label>
          <button class="btn primary" data-write data-act="pdf-run">转 md</button></p>
        <p class="hint">route 从批次 triage_final.csv 读；不转与日文暂缓不会派发。模型建议只进四列，route 由人工确认。</p>
        ${S.pdf && S.pdf.batch && S.pdf.batch.remaining_pages ? '<p class="warn">额度没清，任务会按 quota 停。</p>' : ''}
      </div>
    </div>
    <div class="section card"><h2>最近的分诊与转 md</h2>${recentJobs('pdf.triage', 3)}${recentJobs('pdf.run', 3)}</div>`;
}

function triagePanel() {
  const t = S.triage;
  if (!t) return '<p class="hint">还没有分诊结果。</p>';
  const model = t.model || {};
  const thumbs = (t.thumbs || []).map((rel) => {
    const job = S.triageJob;
    if (!job) return '';
    const path = '工作区/运维/jobs/' + job + '/' + rel;
    return `<img src="/api/file?path=${encodeURIComponent(path)}" alt="缩略图">`;
  }).join('');
  return `<div class="section">
    <h3>${esc(t.title || t.uid)} · ${esc(t.pages || 0)} 页 · ${esc(t.class || '')}</h3>
    <p class="mono small">${Object.entries(t.metrics || {}).map(([k, v]) => esc(k) + ' ' + esc(v)).join(' · ')}</p>
    <div class="thumbs">${thumbs || '<div class="thumb-empty">没有缩略图</div>'}</div>
    <p class="small">模型建议：<b>${esc(model.route || '无')}</b>
      ${model.confidence !== undefined ? '（' + esc(model.confidence) + '）' : ''}
      <span class="hint">${esc(model.reason || '')}</span></p>
    <p class="row tight"><label class="check">route <select id="confirm-route">
      ${PDF_ROUTES.map((r) => `<option${r === model.route ? ' selected' : ''}>${esc(r)}</option>`).join('')}
      </select></label></p>
    <p><input type="text" id="confirm-reason" placeholder="人工理由（写进 review.json）" style="width:100%"></p>
    <p class="row"><button class="btn primary" data-write data-act="pdf-confirm">确认 route</button>
      <span class="hint">写 review/&lt;uid&gt;.json 后调 pdf_apply_review.py。</span></p>
  </div>`;
}

// ---- 维护 ---------------------------------------------------------
function viewMaintain() {
  const st = S.status || {};
  const lag = dig(st, 'index.lag_books', 0);
  const lagCh = dig(st, 'index.lag_chapters', 0);
  const warn = dig(st, 'audit.warn', 0);
  const fail = dig(st, 'audit.fail', 0);
  const broken = dig(st, 'views.broken', 0);
  const unmatched = dig(st, 'catalog.unmatched', 0);
  const auditAt = dig(st, 'audit.at', '');
  const last = S.jobs.find((j) => j.kind === 'maintain') || null;
  const lastWord = !last ? 'out' : (last.state === 'succeeded' ? 'ok'
    : (last.state === 'failed' || last.state === 'orphaned' ? 'miss' : 'half'));
  const lastText = last
    ? (JOB_WORD[last.state] || last.state) + ' · '
      + fmtTime(last.ended || last.created).slice(5)
    : '还没跑过';
  return `<h1>维护</h1>
    <p class="lead">让书目、检索和入口保持一致。平时只需要点一次「一键收尾」。</p>
    <div class="section card">
      <div class="card-head"><h2>库的状态</h2></div>
      <div class="chips">
        <span class="chip">检索索引 ${word(lag ? 'half' : 'ok')}
          ${lag ? '落后 ' + num(lag) + ' 本' + (lagCh ? ' · ' + num(lagCh) + ' 章' : '') : '已同步'}</span>
        <span class="chip">浏览入口 ${word(broken ? 'miss' : 'ok')}
          ${broken ? num(broken) + ' 条失效' : '正常'}</span>
        <span class="chip">体检 ${word(fail ? 'miss' : (warn ? 'half' : 'ok'))}
          ${fail ? num(fail) + ' 项失败' : (warn ? num(warn) + ' 条提醒' : '通过')}${auditAt ? ' · ' + esc(fmtTime(auditAt).slice(5)) : ''}</span>
        <span class="chip">书目表 ${word(unmatched ? 'half' : 'ok')}
          ${unmatched ? '未匹配 ' + num(unmatched) + ' 条' : '改于 ' + esc(fmtTime(dig(st, 'catalog.mtime', '')).slice(5))}</span>
        <span class="chip">上次收尾 ${word(lastWord)} ${esc(lastText)}</span>
      </div>
      <p class="hint">入库、拆章、删书之后，书目与总表会自动更新，不用来这里点。</p>
    </div>
    <div class="section card">
      <div class="card-head"><h2>一键收尾</h2>
        <button class="btn primary" data-write data-act="maintain-run-default">开始收尾</button></div>
      <p class="small">把下面这些一次跑完，任何一步没过就停在那里：</p>
      <ul class="next">
        <li>重建书目数据库，更新书目总表</li>
        <li>更新检索索引，让新章节能搜到</li>
        <li>刷新主题与奖项的浏览入口</li>
        <li>体检、结构验收与搜索抽查</li>
      </ul>
      <p class="hint">索引大约 8 分钟，进度看日志。哪步没过就修好再点一次，整条会从头再跑。</p>
    </div>
    <div class="section card">
      <details class="adv">
        <summary>只想跑其中几步</summary>
        <div id="maintain-steps">${MAINTAIN_STEPS.map(([key, title, text]) => `
          <label class="check" style="display:flex;margin:2px 0">
            <input type="checkbox" data-step="${key}"${S.maintainSteps.has(key) ? ' checked' : ''}>
            <span><span class="small">${esc(title)}</span>
            <span class="hint">${esc(text)}</span></span>
          </label>`).join('')}</div>
        <p class="row"><button class="btn" data-write data-act="maintain-run">跑选中的步骤</button>
          <span class="hint">默认勾的就是「一键收尾」跑的那几步。</span></p>
      </details>
    </div>
    <div class="grid c2 section">
      <div class="card"><h2>下载</h2>
        <p><a class="btn sm" href="/api/file?path=${encodeURIComponent('目录/书目总表.xlsx')}">书目总表（Excel）</a>
           <a class="btn sm" href="/api/file?path=${encodeURIComponent('目录/书目总表.csv')}">书目总表（CSV）</a>
           <a class="btn sm" href="/api/file?path=${encodeURIComponent('目录/分类预填.csv')}">分类预填表</a></p>
        <p class="hint">书目表改于 ${esc(fmtTime(dig(st, 'catalog.mtime', '')))} · 未匹配 ${num(unmatched)} 条。整库重新分类仍在命令行做，页面只给新书的预填表。</p>
      </div>
      <div class="card"><h2>清理旧记录</h2>
        <p class="hint">删掉 90 天以前或第 200 个之后的运行记录与缩略图，不碰书、账和索引。</p>
        <p><button class="btn sm" data-act="jobs-clean">清理</button></p>
      </div>
    </div>
    <div class="section card"><h2>最近的收尾记录</h2>${recentJobs('maintain', 4)}</div>`;
}

// ---- 删书 ---------------------------------------------------------
function viewRetire() {
  return `<h1>删书</h1>
    <p class="lead">删书是移出流程：原书进 库/_冷宫/&lt;书名&gt;/source/，书目录里的 md、html、json 全删。应用要当天快照、计划哈希、输入 uid 一致。</p>
    <div class="section card">
      <p class="row tight"><input type="text" id="retire-q" placeholder="uid 或书名" value="${esc(S.retireQuery)}" size="24">
        <button class="btn" data-act="retire-search">搜书</button></p>
      ${S.retireHits.length ? `<div class="tw"><table><thead><tr><th>uid</th><th>书名</th>
        <th>分类</th><th>md 章</th><th>源</th><th>奖项</th><th></th></tr></thead>
        <tbody>${S.retireHits.map((b) => `<tr>
          <td class="mono">${esc(b.uid)}</td>
          <td class="wrap">${esc(b.title)}</td>
          <td class="wrap small">${esc([b.subject1, b.subject2].filter(Boolean).join('/'))}</td>
          <td class="mono">${esc(b.md && b.md.chapters !== undefined ? b.md.chapters : 0)}</td>
          <td class="mono">${(b.sources || []).length}</td>
          <td class="mono">${(b.awards || []).length}</td>
          <td><button class="btn sm" data-act="retire-preview" data-uid="${esc(b.uid)}">预览移出</button></td>
        </tr>`).join('')}</tbody></table></div>` : '<p class="hint">输入 uid 或书名搜。</p>'}
    </div>
    <div class="section card"><h2>最近的删书任务</h2>${recentJobs('delete.dry-run', 3)}${recentJobs('delete.apply', 3)}</div>`;
}

function previewModal(p) {
  if (!p) return '';
  const sources = (p.sources || []).map((s) => `<tr>
    <td class="wrap small">${esc(s.path)}</td>
    <td class="mono small">${esc(String(s.sha256 || '').slice(0, 12))}</td>
    <td class="mono">${fmtBytes(s.bytes)}</td>
    <td class="mono">${esc(s.shared_with || 0)}</td>
    <td>${esc(s.action)}</td></tr>`).join('');
  return `<div class="overlay" data-act="close-modal">
    <div class="modal" data-stop="1">
      <h2>预览移出：${esc(p.title || p.uid)}</h2>
      <div class="tw"><table><tbody>
        <tr><th>书目录</th><td class="wrap">${esc(p.book_dir || p.path || '')}</td></tr>
        <tr><th>要删的</th><td class="wrap">${esc((p.delete || []).join('、'))}（md ${num(p.md_chapters)} 章 / ${fmtBytes(p.md_bytes)}）</td></tr>
        <tr><th>人账与 DB</th><td>${p.ledger ? '删 1 行' : '无'} · ${p.db ? '重建后消失' : '无'}</td></tr>
        <tr><th>expand</th><td>${num(p.expand_rows)} 行 + 索引 ${num(p.index_rows)} 行</td></tr>
        <tr><th>奖项</th><td class="wrap">${esc((p.awards || []).map((a) => a.award || a).join('、') || '无')}</td></tr>
        <tr><th>视图</th><td class="wrap">${esc((p.views || []).join('、') || '无')}</td></tr>
        <tr><th>系列文档</th><td class="wrap">${esc((p.series_docs || []).join('、') || '无（不自动改）')}</td></tr>
        <tr><th>冷宫目标</th><td class="wrap mono small">${esc(p.cold_path || '')}</td></tr>
        <tr><th>快照</th><td class="wrap mono small">${esc(p.snapshot || '')} · ${p.snapshot_ok ? '<span class="pass">就绪</span>' : '<span class="fail">不可用</span>'}</td></tr>
        <tr><th>计划哈希</th><td class="mono small">${esc(p.plan_hash || '')}</td></tr>
      </tbody></table></div>
      <h3>源文件</h3>
      <div class="tw"><table><thead><tr><th>路径</th><th>sha256</th><th>字节</th><th>共享本数</th><th>动作</th></tr></thead>
        <tbody>${sources || '<tr><td colspan="5" class="hint">没有源文件</td></tr>'}</tbody></table></div>
      <div class="section">
        <p class="row tight"><input type="text" id="apply-uid" placeholder="再输一遍 ${esc(p.uid)}" size="12">
          <input type="text" id="apply-reason" placeholder="理由" size="24" value="移出流程"></p>
        <label class="check"><input type="checkbox" id="apply-confirm"> 我确认原书进冷宫，md、html、json 删除</label>
        <p class="row"><button class="btn primary" data-write data-act="retire-apply" data-uid="${esc(p.uid)}" data-hash="${esc(p.plan_hash || '')}">应用移出</button>
          <button class="btn" data-act="close-modal">取消</button>
          <span class="hint">脚本重算预览，哈希不一致会拒绝。</span></p>
      </div>
    </div>
  </div>`;
}

// ---- 日志 ---------------------------------------------------------
function viewLogs() {
  const rows = S.jobs.filter((j) => !S.filter || j.state === S.filter);
  const states = ['', 'queued', 'running', 'awaiting_review', 'succeeded', 'failed',
    'cancelled', 'orphaned'];
  return `<h1>日志</h1>
    <p class="lead">成功判据看任务自己的门槛，不看退出码 0。日志已脱敏。</p>
    <div class="section card">
      <p class="row tight"><label class="check">状态 <select id="log-filter">
        ${states.map((s) => `<option value="${s}"${s === S.filter ? ' selected' : ''}>${s ? JOB_WORD[s] : '全部'}</option>`).join('')}
      </select></label>
      <button class="btn sm" data-act="jobs-refresh">刷新</button>
      <span class="hint">共 ${rows.length} 个任务</span></p>
      <div class="tw"><table><thead><tr><th>创建</th><th>类型</th><th>标题</th><th>状态</th>
        <th>耗时</th><th>rc</th><th>摘要</th><th></th></tr></thead>
      <tbody>${rows.length ? rows.map((j) => `<tr>
        <td class="mono small">${esc(fmtTime(j.created))}</td>
        <td class="mono small">${esc(j.kind)}</td>
        <td class="wrap">${esc(j.title)}</td>
        <td><span class="pill">${esc(JOB_WORD[j.state] || j.state)}</span></td>
        <td class="mono small">${j.started && j.ended ? esc(seconds(j.started, j.ended)) : '—'}</td>
        <td class="mono small">${j.exit_code === null || j.exit_code === undefined ? '—' : esc(j.exit_code)}</td>
        <td class="wrap small">${esc(j.summary || '')}</td>
        <td><button class="btn sm" data-act="job-open" data-id="${esc(j.id)}">看</button></td>
      </tr>`).join('') : '<tr><td colspan="8" class="hint">没有任务。</td></tr>'}</tbody></table></div>
    </div>
    <div class="section card" id="job-detail">${jobDetail()}</div>`;
}

function seconds(start, end) {
  const a = Date.parse(start);
  const b = Date.parse(end);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return '—';
  const s = Math.max(0, Math.round((b - a) / 1000));
  return s < 60 ? s + ' 秒' : Math.floor(s / 60) + ' 分 ' + (s % 60) + ' 秒';
}

function jobDetail() {
  const job = S.logJob;
  if (!job) return '<p class="hint">点上面表格里的「看」，这里是步骤与日志。</p>';
  const detail = S.logDetail || {};
  const steps = detail.steps || [];
  const tail = detail.log_tail || '';
  return `<div class="card-head"><h2>${esc(job.title)}</h2>
      <span class="row tight">
        <span class="pill">${esc(JOB_WORD[job.state] || job.state)}</span>
        ${['queued', 'running'].indexOf(job.state) >= 0
    ? `<button class="btn sm" data-act="job-cancel" data-id="${esc(job.id)}">停止</button>` : ''}
        <button class="btn sm" data-write data-act="job-rerun" data-id="${esc(job.id)}">重跑</button>
        <a class="btn sm" href="/api/file?path=${encodeURIComponent('工作区/运维/jobs/' + job.id + '/log.txt')}">下载日志</a>
      </span></div>
    <p class="mono small">${esc(job.id)} · ${esc(job.kind)} · ${esc(fmtTime(job.created))} → ${esc(fmtTime(job.ended))}</p>
    <p class="small">${esc(job.summary || '')}</p>
    <p class="tw small mono">${esc((detail.commands || []).map((c) => (c.argv || []).join(' ')).join('\n'))}</p>
    <div class="tw"><table><thead><tr><th>#</th><th>步骤</th><th>状态</th><th>说明</th><th>开始</th></tr></thead>
      <tbody>${steps.length ? steps.map((s) => `<tr><td class="mono">${s.seq + 1}</td>
        <td>${esc(s.name)}</td><td>${esc(s.state)}</td><td class="wrap small">${esc(s.detail || '')}</td>
        <td class="mono small">${esc(fmtTime(s.started))}</td></tr>`).join('')
    : '<tr><td colspan="5" class="hint">没有步骤</td></tr>'}</tbody></table></div>
    <h3 style="margin-top:14px">日志尾部 200 行</h3>
    <pre class="log" id="job-log">${esc(tail)}</pre>`;
}

// ---- 数据加载 -----------------------------------------------------
async function loadStatus(deep) {
  try {
    S.payload = await api('/api/status' + (deep ? '?refresh=1&deep=1' : ''));
    S.status = S.payload.status || {};
  } catch (err) {
    S.payload = { ok: false, error: err.message, status: {} };
    S.status = {};
  }
  renderStats();
  renderNav();
}

async function loadJobs() {
  const body = await api('/api/jobs?limit=200');
  S.jobs = body.jobs || [];
  if (S.payload) S.payload.state_counts = body.counts || {};
  renderNav();
}

async function loadPending() {
  try {
    const body = await api('/api/pending');
    S.pending = body.items || [];
  } catch (err) {
    S.pending = [];
  }
}

async function loadHealth() {
  try {
    S.health = await api('/api/search/health');
  } catch (err) {
    S.health = { running: false };
  }
  renderSearch();
}

async function loadPlan(jobId) {
  const body = await api('/api/file?path=' + encodeURIComponent(
    '工作区/运维/jobs/' + jobId + '/plan.json'));
  S.plan = body;
  S.planJob = jobId;
  S.planDirty = false;
}

async function loadLatestPlan() {
  if (S.plan) return;
  try {
    const body = await api('/api/import/latest');
    if (body && body.plan) {
      S.plan = body.plan;
      S.planJob = body.job_id;
      S.planDirty = false;
    }
  } catch (err) {
    S.plan = null;
  }
}

async function loadTriage(jobId) {
  try {
    S.triage = await api('/api/file?path=' + encodeURIComponent(
      '工作区/运维/jobs/' + jobId + '/result.json'));
    S.triageJob = jobId;
  } catch (err) {
    S.triage = null;
  }
}

function latestBatch() {
  const row = S.jobs.find((j) => j.kind === 'split.run' && j.batch_dir);
  if (!row) return '';
  const parts = String(row.batch_dir).split('/');
  return parts[parts.length - 1] || '';
}

async function loadSplitBooks() {
  if (!S.batch) S.batch = latestBatch() || null;
  if (!S.batch) return;
  try {
    const body = await api('/api/split/books?batch=' + encodeURIComponent(S.batch));
    S.splitBooks = body.books || [];
  } catch (err) {
    S.splitBooks = [];
  }
}

async function loadPdf() {
  try {
    S.pdf = await api('/api/pdf/queue');
  } catch (err) {
    S.pdf = null;
  }
}

async function refreshTab() {
  if (S.tab === 'overview') {
    await Promise.all([loadStatus(), loadJobs(), loadPending()]);
  } else if (S.tab === 'import') {
    S.inbox = (await api('/api/inbox')).items || [];
    await loadLatestPlan();
  } else if (S.tab === 'split') {
    await Promise.all([loadStatus(), loadJobs(), loadPending()]);
    await loadSplitBooks();
  } else if (S.tab === 'pdf') {
    await Promise.all([loadJobs(), loadPdf()]);
    const last = S.jobs.find((j) => j.kind === 'pdf.triage'
      && (j.state === 'awaiting_review' || j.state === 'succeeded'));
    if (!S.triage && last) await loadTriage(last.id);
  } else if (S.tab === 'maintain' || S.tab === 'retire') {
    await Promise.all([loadStatus(), loadJobs()]);
  } else if (S.tab === 'logs') {
    await loadJobs();
  }
}

async function go(tab) {
  S.tab = TABS.some(([key]) => key === tab) ? tab : 'overview';
  try {
    await refreshTab();
  } catch (err) {
    toast(err.message, true);
  }
  render();
  if (S.tab === 'logs' && S.logJob) openLog(S.logJob);
}

// ---- 任务轮询 -----------------------------------------------------
async function openLog(jobId) {
  try {
    const body = await api('/api/jobs/' + encodeURIComponent(jobId));
    S.logJob = body.job;
    S.logDetail = body;
    const box = document.querySelector('#job-detail');
    if (box) {
      box.innerHTML = jobDetail();
      const pre = document.querySelector('#job-log');
      if (pre) pre.scrollTop = pre.scrollHeight;
    }
  } catch (err) {
    toast(err.message, true);
  }
}

async function watchJob(jobId, done) {
  for (let i = 0; i < 240; i += 1) {
    let body;
    try {
      body = await api('/api/jobs/' + encodeURIComponent(jobId));
    } catch (err) {
      return null;
    }
    if (done && done(body.job)) return body;
    await new Promise((resolve) => setTimeout(resolve, 700));
  }
  return null;
}

// awaiting_review 也是终态：进程已经退出，只是产物等人确认。
const SETTLED = ['awaiting_review', 'succeeded', 'failed', 'cancelled', 'orphaned'];

function afterJob(job) {
  return SETTLED.indexOf(job.state) >= 0;
}

function jobBad(job) {
  return !!job && ['failed', 'cancelled', 'orphaned'].indexOf(job.state) >= 0;
}

function jobNote(job) {
  if (!job) return '没有结果';
  return (job.state === 'awaiting_review' ? '待确认：' : '') + (job.summary || job.state);
}

async function runMaintain(steps) {
  if (!steps.length) {
    toast('先勾步骤', true);
    return;
  }
  setBusy(true);
  try {
    const body = await post('/api/maintain', { steps });
    toast('收尾已排队：' + body.job_id);
    const detail = await watchJob(body.job_id, afterJob);
    if (detail) toast(jobNote(detail.job), jobBad(detail.job));
    await Promise.all([loadStatus(true), loadJobs(), loadPending()]);
    render();
  } catch (err) {
    toast(err.message, true);
  } finally {
    setBusy(false);
  }
}

// ---- 动作 ---------------------------------------------------------
const actions = {
  async 'search-open'() {
    await loadHealth();
    if (!S.health || !S.health.running) {
      toast('8765 没起来，先点「启动搜索」');
      return;
    }
    window.open('http://127.0.0.1:8765/', '_blank', 'noopener');
  },

  async 'search-start'() {
    try {
      const body = await post('/api/search/start');
      if (body.already) toast('8765 已经在跑');
      else if (body.ok) toast('8765 起来了');
      else toast(body.error || '起不来', true);
    } catch (err) {
      toast(err.message, true);
    }
    await loadHealth();
  },

  async 'inbox-refresh'() {
    S.inbox = (await api('/api/inbox')).items || [];
    render();
  },

  async 'import-scan'() {
    setBusy(true);
    try {
      const body = await post('/api/import/scan');
      toast('扫描已排队：' + body.job_id);
      const detail = await watchJob(body.job_id, afterJob);
      if (detail && detail.job.state === 'awaiting_review') {
        S.planJob = body.job_id;
        await loadPlan(body.job_id);
        toast('计划待确认');
      } else {
        toast((detail && detail.job.summary) || '扫描没过', true);
      }
      await refreshTab();
      render();
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  },

  async 'plan-defaults'() {
    if (!S.plan) return;
    S.plan.forEach((item) => {
      const cls = item.classify || {};
      const parts = [cls.level1, cls.level2].filter((v) => v && v !== '不细分');
      if (item.action === 'add' && parts.length) item.subjects = parts;
    });
    S.planDirty = true;
    render();
    toast('已把预填写回表格，记得点应用');
  },

  async 'import-apply'() {
    if (!S.plan || !S.planJob) return;
    const conflicts = S.plan.filter((it) => it.action === 'conflict').length;
    if (conflicts && !window.confirm(conflicts + ' 行 conflict 不会入库，文件留在收件箱。继续？')) return;
    setBusy(true);
    try {
      await post('/api/import/plan', { job_id: S.planJob, items: S.plan });
      const body = await post('/api/import/apply',
        { plan_path: '工作区/运维/jobs/' + S.planJob + '/plan.json' });
      toast('入库已排队：' + body.job_id);
      const detail = await watchJob(body.job_id, afterJob);
      if (detail && detail.job.state === 'succeeded') {
        toast('入库完成：' + (detail.job.summary || ''));
        S.importDone = '入库完成：' + (detail.job.summary || '') + '。下一步按格式走拆章或分诊。';
        S.plan = null;
        S.planJob = null;
      } else {
        toast((detail && detail.job.summary) || '入库没过，看日志', true);
      }
      await refreshTab();
      await loadPending();
      render();
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  },

  async 'split-all'(el) {
    const ebooks = S.pending.filter((r) => /^(epub|mobi|azw3|azw)$/.test(r.format));
    if (el.checked) ebooks.forEach((r) => S.selected.add(r.uid));
    else S.selected.clear();
    render();
  },

  async 'split-pick'(el) {
    const uid = el.value;
    if (el.checked) S.selected.add(uid);
    else S.selected.delete(uid);
    render();
  },

  async 'split-run'() {
    const uids = Array.from(S.selected);
    if (!uids.length) {
      toast('先勾书', true);
      return;
    }
    const replace = S.replaceMd;
    const workers = S.splitWorkers || 4;
    if (replace && !window.confirm('重拆会替换现有 md，确认？')) return;
    setBusy(true);
    try {
      const body = await post('/api/split/run', { uids, workers, replace });
      S.batch = body.batch;
      S.selected.clear();
      S.replaceMd = false;
      toast('批次已排队：' + body.batch);
      await loadSplitBooks();
      render();
      watchJob(body.job_id, afterJob).then(async (detail) => {
        if (detail) toast('拆章结束：' + jobNote(detail.job), jobBad(detail.job));
        await loadSplitBooks();
        await loadPending();
        await loadJobs();
        render();
      });
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  },

  async 'split-one'(el) {
    if (!S.batch) return;
    setBusy(true);
    try {
      const body = await post('/api/split/run',
        { uids: [el.dataset.uid], only: el.dataset.uid, batch: S.batch, workers: 1 });
      toast('只重跑 ' + el.dataset.uid + '：' + body.job_id);
      watchJob(body.job_id, afterJob).then(async (detail) => {
        if (detail) toast(jobNote(detail.job), jobBad(detail.job));
        await loadSplitBooks();
        await loadPending();
        await loadJobs();
        render();
      });
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  },

  async 'goto'(el) {
    const tab = el.dataset.tab;
    if (location.hash !== '#' + tab) location.hash = '#' + tab;
    else go(tab);
  },

  async 'split-refresh'() {
    await loadSplitBooks();
    render();
  },

  async 'pdf-refresh'() {
    await loadPdf();
    render();
  },

  async 'pdf-triage'() {
    const uid = (($('#triage-uid') || {}).value || '').trim();
    if (!/^B\d{4}$/.test(uid)) {
      toast('uid 形如 B1234', true);
      return;
    }
    setBusy(true);
    try {
      const body = await post('/api/pdf/triage', { uid });
      S.triageUid = uid;
      toast('分诊已排队：' + body.job_id);
      const detail = await watchJob(body.job_id, afterJob);
      if (detail && detail.job.state === 'awaiting_review') {
        await loadTriage(body.job_id);
        if (S.triage && S.triage.uid) S.triageUid = S.triage.uid;
        toast('分诊完成，看建议再确认');
      } else {
        toast((detail && detail.job.summary) || '分诊没过，看日志', true);
      }
      await loadJobs();
      render();
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  },

  async 'pdf-confirm'() {
    const uid = ((S.triage && S.triage.uid) || S.triageUid || '').trim();
    const route = ($('#confirm-route') || {}).value;
    const reason = (($('#confirm-reason') || {}).value || '').trim();
    if (!/^B\d{4}$/.test(uid)) {
      toast('先把 uuid 填上（B1234）', true);
      return;
    }
    if (!S.triageJob) {
      toast('先跑分诊，拿到 triage.csv 再确认', true);
      return;
    }
    setBusy(true);
    try {
      const body = await post('/api/pdf/confirm', {
        uid, route, reason, verdict: route, job_id: S.triageJob,
        model: (S.triage && S.triage.model) || {},
      });
      toast('已写 review，批次 ' + body.batch);
      const detail = await watchJob(body.job_id, afterJob);
      if (detail && detail.job.state === 'succeeded') {
        S.triage = null;
        S.triageJob = null;
      } else if (detail) {
        toast(detail.job.summary || detail.job.state, true);
      }
      await loadJobs();
      render();
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  },

  async 'pdf-run'() {
    const uids = (($('#pdf-uids') || {}).value || '').split(',').map((s) => s.trim())
      .filter(Boolean);
    if (!uids.length) {
      toast('填要转的 uid', true);
      return;
    }
    const limit = Number(($('#pdf-limit') || {}).value) || 0;
    const workers = Number(($('#pdf-workers') || {}).value) || 3;
    S.pdfUids = uids.join(',');
    S.pdfLimit = limit;
    S.pdfWorkers = workers;
    setBusy(true);
    try {
      const body = await post('/api/pdf/run', { uids, limit, workers });
      toast('已排队：' + body.job_id + '（' + JSON.stringify(body.routes) + '）');
      const detail = await watchJob(body.job_id, afterJob);
      if (detail) toast(jobNote(detail.job), jobBad(detail.job));
      await Promise.all([loadJobs(), loadPdf(), loadPending()]);
      render();
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  },

  async 'maintain-run'() {
    const steps = MAINTAIN_STEPS.map(([key]) => key)
      .filter((key) => S.maintainSteps.has(key));
    await runMaintain(steps);
  },

  async 'maintain-run-default'() {
    await runMaintain(FINISH_DEFAULT.slice());
  },

  async 'jobs-clean'() {
    try {
      const body = await post('/api/jobs/clean');
      toast('清掉 ' + body.removed + ' 个任务');
      await loadJobs();
      render();
    } catch (err) {
      toast(err.message, true);
    }
  },

  async 'jobs-refresh'() {
    await loadJobs();
    render();
  },

  async 'retire-search'() {
    const q = (($('#retire-q') || {}).value || '').trim();
    if (!q) {
      S.retireQuery = '';
      S.retireHits = [];
      render();
      return;
    }
    S.retireQuery = q;
    try {
      const body = await api('/api/retire/search?q=' + encodeURIComponent(q));
      S.retireHits = body.books || [];
    } catch (err) {
      S.retireHits = [];
      toast(err.message, true);
    }
    render();
  },

  async 'retire-preview'(el) {
    const uid = el.dataset.uid;
    setBusy(true);
    try {
      const body = await post('/api/delete/dry-run', { uid, reason: '移出流程' });
      const detail = await watchJob(body.job_id, afterJob);
      if (detail && detail.job.state === 'succeeded') {
        S.preview = await api('/api/file?path=' + encodeURIComponent(
          '工作区/运维/jobs/' + body.job_id + '/dry-run.json'));
        $('#modal').innerHTML = previewModal(S.preview);
      } else {
        toast((detail && detail.job.summary) || '预览没过', true);
      }
      await loadJobs();
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  },

  async 'retire-apply'(el) {
    const p = S.preview || {};
    const uid = el.dataset.uid;
    const typed = (($('#apply-uid') || {}).value || '').trim();
    const reason = (($('#apply-reason') || {}).value || '').trim();
    const ok = $('#apply-confirm') && $('#apply-confirm').checked;
    if (typed !== uid) {
      toast('输入的 uid 与目标不一致', true);
      return;
    }
    if (!ok) {
      toast('先勾确认框', true);
      return;
    }
    if (!p.snapshot_ok) {
      toast('快照不可用，不能应用', true);
      return;
    }
    setBusy(true);
    try {
      const body = await post('/api/delete/apply', {
        uid, reason, plan_hash: el.dataset.hash, confirm_uid: typed,
      });
      const detail = await watchJob(body.job_id, afterJob);
      if (detail) toast(detail.job.summary || detail.job.state,
        detail.job.state !== 'succeeded');
      S.preview = null;
      $('#modal').innerHTML = '';
      await Promise.all([loadStatus(), loadJobs(), loadPending()]);
      render();
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  },

  async 'close-modal'() {
    S.preview = null;
    $('#modal').innerHTML = '';
  },

  async 'job-open'(el) {
    S.tab = 'logs';
    S.logJob = { id: el.dataset.id, title: '载入中', state: 'running', kind: '', created: '' };
    if (location.hash !== '#logs') location.hash = '#logs';
    await loadJobs();
    render();
    await openLog(el.dataset.id);
  },

  async 'job-cancel'(el) {
    try {
      await post('/api/jobs/' + encodeURIComponent(el.dataset.id) + '/cancel');
      toast('已发停止，等 10 秒收尾');
      await openLog(el.dataset.id);
    } catch (err) {
      toast(err.message, true);
    }
  },

  async 'job-rerun'(el) {
    try {
      const body = await post('/api/jobs/' + encodeURIComponent(el.dataset.id) + '/rerun');
      toast('重跑已排队：' + body.job_id);
      await loadJobs();
      render();
    } catch (err) {
      toast(err.message, true);
    }
  },
};

// ---- 事件 ---------------------------------------------------------
document.addEventListener('click', (ev) => {
  const el = ev.target.closest('[data-act]');
  if (!el) return;
  const act = el.dataset.act;
  if (!actions[act]) return;
  // 勾选框与输入框交给 change，preventDefault 会把原生切换撤掉
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) return;
  if (act === 'close-modal' && el !== ev.target) return;
  if (act !== 'close-modal') ev.preventDefault();
  actions[act](el).catch((err) => toast(err.message, true));
});

document.addEventListener('change', (ev) => {
  const el = ev.target;
  if (el.dataset && el.dataset.act) {
    const act = el.dataset.act;
    if (actions[act]) actions[act](el).catch((err) => toast(err.message, true));
    return;
  }
  if (el.id === 'split-replace') {
    S.replaceMd = el.checked;
    return;
  }
  if (el.id === 'split-workers') {
    S.splitWorkers = Number(el.value) || 0;
    return;
  }
  if (el.id === 'triage-uid') {
    S.triageUid = el.value.trim();
    return;
  }
  if (el.id === 'pdf-uids') {
    S.pdfUids = el.value;
    return;
  }
  if (el.id === 'pdf-limit') {
    S.pdfLimit = Number(el.value) || 0;
    return;
  }
  if (el.id === 'pdf-workers') {
    S.pdfWorkers = Number(el.value) || 0;
    return;
  }
  if (el.dataset && el.dataset.step) {
    if (el.checked) S.maintainSteps.add(el.dataset.step);
    else S.maintainSteps.delete(el.dataset.step);
    return;
  }
  if (el.dataset && el.dataset.field && S.plan) {
    const idx = Number(el.dataset.idx);
    const item = S.plan[idx];
    if (!item) return;
    const field = el.dataset.field;
    if (field === 'subjects') {
      item.subjects = el.value.split('/').map((s) => s.trim()).filter(Boolean);
    } else {
      item[field] = el.value;
    }
    S.planDirty = true;
  }
  if (el.id === 'log-filter') {
    S.filter = el.value;
    render();
  }
});


document.addEventListener('change', (ev) => {
  if (ev.target && ev.target.id === 'up-file') uploadFiles(ev.target.files);
});

function uploadFiles(files) {
  const list = Array.from(files || []);
  if (!list.length) return;
  let index = 0;
  const note = $('#up-note');
  const bar = $('#up-bar');
  const next = () => {
    if (index >= list.length) {
      note.textContent = '上传完成';
      setTimeout(() => { if (bar) bar.style.width = '0'; }, 800);
      api('/api/inbox').then((body) => {
        S.inbox = body.items || [];
        render();
      }).catch(() => {});
      return;
    }
    const file = list[index];
    index += 1;
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/import/upload');
    xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && bar) bar.style.width = (e.loaded / e.total * 100) + '%';
      if (note) note.textContent = '上传 ' + file.name + ' ' + fmtBytes(e.loaded);
    };
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText); } catch (err) { body = { ok: false }; }
      if (xhr.status === 200 && body.ok) {
        toast('已落收件箱：' + body.name);
      } else {
        toast(body.error || ('上传失败 HTTP ' + xhr.status), true);
      }
      next();
    };
    xhr.onerror = () => { toast('上传断了', true); next(); };
    xhr.send(file);
  };
  next();
}

window.addEventListener('hashchange', () => {
  const tab = (location.hash || '#overview').slice(1);
  if (tab !== S.tab) go(tab);
});

async function boot() {
  S.tab = (location.hash || '#overview').slice(1) || 'overview';
  try {
    await Promise.all([loadStatus(), loadJobs(), loadPending(), loadHealth()]);
    await refreshTab();
  } catch (err) {
    toast(err.message, true);
  }
  render();
  setInterval(async () => {
    try {
      await loadStatus();
      await loadJobs();
      if (S.tab === 'split') {
        await loadSplitBooks();
        const box = document.querySelector('[data-live="split-batch"]');
        if (box) box.innerHTML = '<div class="card-head"><h2>批次 '
          + esc(S.batch || '—') + '</h2><button class="btn sm" data-act="split-refresh">刷新单本状态</button></div>'
          + splitBooks();
      }
      if (S.tab === 'overview') {
        const view = document.querySelector('#view');
        if (view) view.innerHTML = viewOverview();
      }
      if (S.tab === 'logs' && S.logJob && S.logJob.id) {
        const el = S.logJob;
        if (['queued', 'running'].indexOf(el.state) >= 0) {
          await openLog(el.id);
        }
      }
    } catch (err) {
      /* 轮询失败不打扰页面，状态条会显示缺 */
    }
  }, 2500);
  setInterval(loadHealth, 12000);
}

boot();
