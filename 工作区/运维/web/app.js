'use strict';

const TABS = [
  ['overview', '总览'], ['import', '新书导入'], ['split', '电子书'],
  ['pdf', 'PDF'], ['maintain', '维护'], ['retire', '删书'], ['logs', '日志'],
];
// pdf-list 是 PDF 页的子页，不进左侧导航，但能用 #pdf-list 直接打开
const EXTRA_TABS = ['pdf-list'];
const ALL_TABS = TABS.map(([key]) => key).concat(EXTRA_TABS);

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
const SETTLE_ROUTES = ['OCR', '本地抽文本', '不转-记图册', '日文暂缓'];
const JOB_WORD = {
  queued: '排队', running: '在跑', awaiting_review: '待确认', succeeded: '完成',
  failed: '失败', cancelled: '取消', orphaned: '失联',
};
const KIND_WORD = {
  'import.scan': '识别新书', 'import.apply': '入库', 'split.run': '拆章',
  'split.inspect': '查看问题',
  'pdf.triage': 'PDF 分诊', 'pdf.confirm': '确认处理方式', 'pdf.run': 'PDF 转写',
  maintain: '收尾', 'delete.dry-run': '删书预览', 'delete.apply': '删书',
};
const STEP_WORD = { pending: '等待', ok: '通过', fail: '失败', running: '在跑' };
const SPLIT_WORD = {
  landed: '已入库', replaced: '已替换', accepted: '已受理',
  'skipped-has-md': '已有正文', 'staged-green': '已过检未入库',
  staged: '未复核', 'staged-review': '待复核', 'no-stage': '暂存区空',
  'split-failed': '拆章失败', 'convert-failed': '转格式失败', error: '异常',
  pending_review: '待复核',
};
const ACTION_WORD = { add: '新增', replace: '换源', conflict: '冲突', skip: '跳过' };
const RETENTION_WORD = { keep: '保留', review: '待定', drop: '不留' };
const PDF_ROUTE_WORD = {
  'OCR': '扫描识别', '本地抽文本': '直接抽取', '不转-记图册': '只记图册',
  '人工确认': '待人工判断', '日文暂缓': '日文暂缓',
};
const PICK_TAG = [['convert', '可转'], ['gallery', '只记图册'], ['ja', '日文暂缓'],
  ['untriaged', '未分诊']];
// 状态带按「先能转的，再要人动手的」排；清单筛选条沿用上面的处置顺序
const STRIP_TAG = ['convert', 'untriaged', 'gallery', 'ja'];
const PICK_SORT = [['pages', '页数多到少'], ['pages-asc', '页数少到多'], ['uid', '编号']];
const PICK_WORD = {
  convert: '可转', gallery: '只记图册', ja: '日文暂缓', untriaged: '未分诊',
};
const PICK_PROGRESS = {
  todo: ['未开始', 'out'], running: ['在跑', 'half'], landed: ['已落地', 'ok'],
  failed: ['失败', 'miss'],
};
// 清单页的进度筛选只管这三档：已有正文的书不会出现在待转清单里
const PICK_PROGRESS_FILTER = [['todo', '未开始'], ['running', '在跑'], ['failed', '失败']];
const SOURCE_WORD = { move: '移动', hardlink: '硬链接' };
// 结构检查的报警键换成普通说法
const VERIFY_WORD = {
  identical: '重复段落', 'broken link': '断链', 'missing image': '缺图',
  'orphan image': '多余图片', 'no H1': '章名缺失',
};
const EBOOK = /^(epub|mobi|azw3|azw)$/;

const S = {
  tab: 'overview',
  payload: null, status: {}, health: null,
  jobs: [], pending: [], inbox: [], pdf: null,
  plan: null, planJob: null, planDirty: false,
  batch: null, splitBooks: [],
  review: null, reviewJob: null,
  retireHits: [], retireQuery: '', preview: null,
  logJob: null, logDetail: null, follow: true, importDone: null,
  filter: '', selected: new Set(), busy: false,
  replaceMd: false, splitWorkers: 4,
  pdfLimit: 0, pdfWorkers: 3, pdfTriage: null, pdfToday: null, pdfUntriaged: null,
  books: [], bookCounts: null, pick: new Set(), pickQ: '',
  pickTags: new Set(['convert', 'untriaged', 'gallery', 'ja']),
  pickProgress: new Set(['todo', 'running', 'failed']), pickRecheck: false,
  pickSubject: '', pickSort: 'pages', settle: null,
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

function status(cls, label) {
  return `<span class="status ${esc(cls)}">${esc(label)}</span>`;
}

function pct(value, digits) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return (n * 100).toFixed(digits === undefined ? 1 : digits) + '%';
}

function routeWord(route) {
  return PDF_ROUTE_WORD[route] || route || '';
}

function baseName(path) {
  const text = String(path || '');
  const parts = text.split('/');
  return parts[parts.length - 1] || text;
}

function checkWord(rcValue) {
  if (rcValue === null || rcValue === undefined || rcValue === '') return '—';
  const n = Number(rcValue);
  if (!Number.isFinite(n)) return '—';
  return n === 0 ? '<span class="pass">通过</span>' : '<span class="fail">失败</span>';
}

function stepNote(text) {
  const m = /^rc=(-?\d+)$/.exec(String(text || ''));
  if (!m) return text || '';
  return m[1] === '0' ? '' : '退出码 ' + m[1];
}

// 任务摘要里夹着机械写法，页面上换成普通说法。状态词的词表就是 SPLIT_WORD，加状态不用改这里。
const SPLIT_COUNT = new RegExp(
  '\\b(' + Object.keys(SPLIT_WORD).sort((a, b) => b.length - a.length).join('|')
    + ') (\\d+)', 'g');
const SUMMARY_FIX = [
  [/\s*\d{8}-\d{6}-[0-9a-f]{4}\s*/g, ' '],
  [/conflicts=(\d+)[，,]?\s*未匹配 (\d+) 条/g, (match, conflict, miss) => {
    const head = conflict === '0' ? '没有冲突' : conflict + ' 行冲突';
    const tail = miss === '0' ? '书目与文件全部对上' : miss + ' 条书目对不上文件';
    return head + '，' + tail;
  }],
  [/plan_hash\s*与现库不一致/g, '校验码与现库不一致'],
  [/，?\s*plan_hash\s+[^\s，,；;]+(?=[；;，,]|$)/g, ''],
  [/快照不可用，不能应用/g, '回滚副本不可用，不能移出'],
  [/快照就绪/g, '回滚副本就绪'],
  [/，?\s*md (\d+) 章/g, '，Markdown $1 章'],
  [SPLIT_COUNT,
    (match, word, count) => SPLIT_WORD[word] + ' ' + count + ' 本'],
  [/\b(add|replace|conflict|skip) (\d+)/g, (match, word, count) => ACTION_WORD[word] + ' ' + count],
  [/\s+\/\s+(?=(?:新增|换源|冲突|跳过)\s*\d)/g, '，'],
  [/已确认 route=([^\s，,）)]+)/g, (match, name) => '已确认处理方式：' + routeWord(name)],
  [/→\s*(OCR|本地抽文本|不转-记图册|人工确认|日文暂缓)/g, (match, name) => '→ ' + routeWord(name)],
  [/route=([^\s，,）)]+)/g, (match, name) => '处理方式 ' + routeWord(name)],
  [/rc=(-?\d+)/g, (match, code) => '退出码 ' + code],
];

function summaryText(text) {
  let out = String(text === null || text === undefined ? '' : text);
  for (const [pattern, repl] of SUMMARY_FIX) out = out.replace(pattern, repl);
  return out.replace(/\s{2,}/g, ' ').replace(/\s+([，。；：])/g, '$1').trim();
}

function planHead() {
  const id = String(S.planJob || '');
  if (!/^\d{8}-\d{6}-/.test(id)) return '入库清单';
  return '入库清单 · ' + id.slice(9, 11) + ':' + id.slice(11, 13) + ':' + id.slice(13, 15);
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
  const on = S.tab === 'pdf-list' ? 'pdf' : S.tab;
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
    return `<a href="#${key}" class="${on === key ? 'on' : ''}">${esc(label)}${badge}</a>`;
  }).join('');
}

function bookCounts() {
  const st = S.status || {};
  const pending = st.pending || {};
  const lagChapters = dig(st, 'index.lag_chapters', 0);
  return {
    books: dig(st, 'books', 0),
    readable: dig(st, 'md_done', 0),
    chapters: dig(st, 'md_chapters', 0),
    toConvert: (pending.pdf || 0) + (pending.txt || 0) + (pending.djvu || 0),
    inbox: dig(st, 'inbox.files', 0),
    ledgerRows: dig(st, 'ledger.rows', 0),
    awards: dig(st, 'views.awards', 0),
    topics: dig(st, 'views.topics', 0),
    broken: dig(st, 'views.broken', 0),
    lagBooks: dig(st, 'index.lag_books', 0),
    lagChapters: lagChapters,
    warn: dig(st, 'audit.warn', 0),
    fail: dig(st, 'audit.fail', 0),
    unmatched: dig(st, 'catalog.unmatched', 0),
    catalogAt: dig(st, 'catalog.mtime', ''),
    awaiting: dig(S.payload, 'state_counts.awaiting_review', 0),
  };
}

function statsReadable() {
  return !(S.payload && S.payload.error);
}

function ebookCount() {
  return S.pending.filter((row) => EBOOK.test(row.format)).length;
}

function renderStats() {
  const c = bookCounts();
  const items = [
    ['藏书', num(c.books)],
    ['可读', num(c.readable)],
    ['待转', num(c.toConvert)],
    ['索引', c.lagBooks ? '落后 ' + num(c.lagBooks) + ' 本' : '已同步'],
    ['收件箱', num(c.inbox)],
  ];
  let html = items.map(([label, value]) => `<span class="stat">${esc(label)}<b>${esc(value)}</b></span>`).join('');
  if (!statsReadable()) {
    const detail = (S.payload && S.payload.error) || '';
    html += `<span class="stat warn" title="${esc(detail)}">状态<b>读不到</b></span>`;
  }
  const ops = (S.payload && S.payload.ops) || {};
  if (ops.writer) html += '<span class="stat warn">写任务<b>在跑</b></span>';
  $('#stats').innerHTML = html;
}

function renderSearch() {
  const health = S.health || {};
  const dot = $('#search-dot');
  dot.className = 'dot ' + (health.running ? 'on' : 'off');
  dot.title = health.running ? '阅读搜索在跑' : '阅读搜索未启动';
  $('#search-btn').textContent = '打开阅读搜索';
  $('#search-btn').title = 'http://127.0.0.1:8765/';
  $('#search-btn').hidden = !health.running;
  $('#search-start').textContent = '启动阅读搜索';
  $('#search-start').title = 'http://127.0.0.1:8765/';
  $('#search-start').hidden = !!health.running;
}

// ---- 视图骨架 -----------------------------------------------------
function render() {
  renderNav();
  renderStats();
  renderSearch();
  const view = $('#view');
  const map = {
    overview: viewOverview, import: viewImport, split: viewSplit, pdf: viewPdf,
    'pdf-list': viewPdfList,
    maintain: viewMaintain, retire: viewRetire, logs: viewLogs,
  };
  view.innerHTML = (map[S.tab] || viewOverview)();
  afterRender();
}

function afterRender() {
  wireDrop();
  if (S.tab === 'logs' && S.logJob && S.logJob.id) openLog(S.logJob.id);
}

function wireDrop() {
  const drop = document.querySelector('#view .drop');
  if (!drop) return;
  const stop = (ev) => { ev.preventDefault(); ev.stopPropagation(); };
  drop.addEventListener('dragover', (ev) => { stop(ev); drop.classList.add('over'); });
  drop.addEventListener('dragenter', (ev) => { stop(ev); drop.classList.add('over'); });
  drop.addEventListener('dragleave', (ev) => { stop(ev); drop.classList.remove('over'); });
  drop.addEventListener('drop', (ev) => {
    stop(ev);
    drop.classList.remove('over');
    if (ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files.length) {
      uploadFiles(ev.dataTransfer.files);
    }
  });
}

// ---- 总览 ---------------------------------------------------------
function todoItems(c) {
  const items = [];
  if (c.fail || c.broken) {
    items.push({
      title: '有 ' + num(c.fail + c.broken) + ' 处需要处理',
      note: '收尾会走一遍检查，先把这几处修好。', href: '#maintain', label: '去维护',
    });
  }
  if (c.unmatched) {
    items.push({
      title: num(c.unmatched) + ' 条书目对不上文件',
      note: '重出书目总表后就会消失。', href: '#maintain', label: '去维护',
    });
  }
  if (c.inbox) {
    items.push({
      title: '收件箱有 ' + num(c.inbox) + ' 本新书',
      note: '核对书名和分类之后入库。', href: '#import', label: '去导入',
    });
  }
  if (c.awaiting) {
    items.push({
      title: num(c.awaiting) + ' 个任务待确认',
      note: '有结果等人看过才算完。', href: '#logs', label: '去确认',
    });
  }
  const ebooks = ebookCount();
  if (ebooks) {
    items.push({
      title: '有 ' + num(ebooks) + ' 本电子书待拆章',
      note: '拆成按章的 Markdown 才能搜索。', href: '#split', label: '去拆章',
    });
  }
  if (c.toConvert) {
    items.push({
      title: num(c.toConvert) + ' 本还没有正文',
      note: '先看建议，确认处理方式再转。', href: '#pdf', label: '去分诊',
    });
  }
  if (c.lagBooks) {
    items.push({
      title: '检索索引落后 ' + num(c.lagBooks) + ' 本',
      note: '这些书的新章节还搜不到。', href: '#maintain', label: '去维护',
    });
  }
  if (c.warn) {
    items.push({
      title: '体检有 ' + num(c.warn) + ' 条提醒',
      note: '不影响平时用，收尾时会再检查。', href: '#maintain', label: '去维护',
    });
  }
  return items;
}

function verdictText(c, todos) {
  if (!statsReadable()) return '暂时读不到书库状态';
  if (c.fail || c.broken) return '有几处需要处理';
  if (todos.length) return '库可用，有 ' + num(todos.length) + ' 件事待办';
  return '库是干净的';
}

function heroNote(c) {
  if (!statsReadable()) return '刷新一下试试；读不到的原因在顶栏提示里。';
  const tail = c.lagBooks
    ? '索引落后 ' + num(c.lagBooks) + ' 本。'
    : '检索索引已同步。';
  return num(c.books) + ' 本书，' + num(c.readable) + ' 本可读，共 '
    + num(c.chapters) + ' 章。' + tail;
}

function heroAction(c) {
  if (c.inbox) return ['导入新书', '#import'];
  if (c.awaiting) return ['查看待确认', '#logs'];
  if (c.toConvert) return ['继续出正文', ebookCount() ? '#split' : '#pdf'];
  if (c.lagBooks || c.warn || c.unmatched) return ['去维护', '#maintain'];
  return null;
}

function hero(c, todos) {
  const action = heroAction(c);
  return `<div class="hero">
    <h1>总览</h1>
    <p class="verdict">${esc(verdictText(c, todos))}</p>
    <p class="hero-note">${esc(heroNote(c))}</p>
    <div class="hero-actions">
      ${action ? `<a class="btn primary" href="${action[1]}">${esc(action[0])}</a>` : ''}
      <button class="btn" data-act="status-refresh">刷新状态</button>
    </div>
  </div>`;
}

function pipeline(c) {
  const ledgerGap = Number(c.ledgerRows) - Number(c.books);
  const cells = [
    {
      name: '收书', href: '#import',
      state: c.inbox ? 'half' : 'ok', word: c.inbox ? '待处理' : '正常',
      read: c.inbox ? num(c.inbox) + ' 本待处理' : '收件箱是空的',
    },
    {
      name: '入账', href: '#import',
      state: ledgerGap ? 'miss' : 'ok', word: ledgerGap ? '需核对' : '正常',
      read: ledgerGap ? '差 ' + num(Math.abs(ledgerGap)) + ' 本' : num(c.books) + ' 本已登记',
    },
    {
      name: '上架', href: '#maintain',
      state: c.broken ? 'miss' : 'ok', word: c.broken ? '需处理' : '正常',
      read: c.broken
        ? num(c.broken) + ' 条入口失效'
        : '奖项 ' + num(c.awards) + ' · 主题 ' + num(c.topics),
    },
    {
      name: '出正文', href: c.toConvert ? '#pdf' : '#split',
      state: c.toConvert ? 'half' : 'ok', word: c.toConvert ? '待处理' : '正常',
      read: num(c.readable) + ' 本可读，' + num(c.toConvert) + ' 本待转',
    },
    {
      name: '可检索', href: '#maintain',
      state: c.lagBooks ? 'half' : 'ok', word: c.lagBooks ? '待处理' : '正常',
      read: c.lagBooks ? '落后 ' + num(c.lagBooks) + ' 本' : num(c.chapters) + ' 章已索引',
    },
    {
      name: '体检', href: '#maintain',
      state: c.fail ? 'miss' : (c.warn ? 'half' : 'ok'),
      word: c.fail ? '需处理' : (c.warn ? '待处理' : '正常'),
      read: c.fail ? num(c.fail) + ' 项失败' : (c.warn ? num(c.warn) + ' 条提醒' : '全部通过'),
    },
  ];
  return `<div class="pipeline">${cells.map((cell) => `<a class="pipe-cell" href="${cell.href}">
    ${status(cell.state, cell.word)}
    <div class="pipe-name">${esc(cell.name)}</div>
    <div class="pipe-read">${esc(cell.read)}</div>
  </a>`).join('')}</div>`;
}

function nextSteps(todos) {
  if (!todos.length) return '<p class="empty">没有待办，书库是干净的。</p>';
  return todos.slice(0, 4).map((item) => `<div class="rowline">
    <div class="rl-main">
      <div class="rl-title">${esc(item.title)}</div>
      <div class="rl-note">${esc(item.note)}</div>
    </div>
    <a class="btn sm" href="${item.href}">${esc(item.label)}</a>
  </div>`).join('');
}

function jobLine(job) {
  const state = job.state;
  return `<div class="jobline">
    <span class="t">${esc(fmtTime(job.created))}</span>
    <span class="pill">${esc(JOB_WORD[state] || state)}</span>
    <span class="jl-title">${esc(summaryText(job.title))}</span>
    <span class="jl-sum small">${esc(summaryText(job.summary))}</span>
    <a class="btn sm" href="#logs" data-act="job-open" data-id="${esc(job.id)}">详情</a>
  </div>`;
}

function viewOverview() {
  const c = bookCounts();
  const todos = todoItems(c);
  const jobs = S.jobs.slice(0, 4);
  return `${hero(c, todos)}
    <div class="section">${pipeline(c)}</div>
    <div class="grid c2 section">
      <div class="card"><h2>下一步</h2>${nextSteps(todos)}</div>
      <div class="card"><h2>最近任务</h2>${jobs.length ? jobs.map(jobLine).join('') : '<p class="empty">还没有任务。</p>'}
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
      ${['add', 'replace', 'conflict', 'skip'].map((a) => `<option value="${a}"${a === item.action ? ' selected' : ''}>${ACTION_WORD[a]}</option>`).join('')}
    </select></td>
    <td class="wrap">${esc(item.file)}</td>
    <td><input type="text" data-field="title" data-idx="${index}" value="${esc(item.title)}" size="22"></td>
    <td>${esc(item.author || '')}</td>
    <td>${esc(item.format || '')}</td>
    <td class="num">${fmtBytes(item.bytes)}</td>
    <td><input type="text" data-field="subjects" data-idx="${index}" value="${esc(subjects)}" size="16" placeholder="一级/二级"></td>
    <td><select data-field="retention" data-idx="${index}">
      ${['keep', 'review', 'drop'].map((r) => `<option value="${r}"${r === item.retention ? ' selected' : ''}>${RETENTION_WORD[r]}</option>`).join('')}
    </select></td>
    <td><input type="text" data-field="uid" data-idx="${index}" value="${esc(item.uid || '')}" size="7"></td>
    <td class="wrap small">${esc(item.note || '')}</td>
  </tr>`;
}

function inboxCard(inbox, hasPlan) {
  return `<div class="card">
    <div class="card-head"><h2>收件箱 <span class="hint">${num(inbox.length)} 本</span></h2>
      <button class="btn sm" data-act="inbox-refresh">刷新</button></div>
    ${inbox.length ? `<div class="tw"><table>
      <thead><tr><th>文件</th><th>格式</th><th class="num">大小</th><th>放进来的时间</th></tr></thead>
      <tbody>${inbox.map((f) => `<tr><td class="wrap">${esc(f.name)}</td>
        <td>${esc(f.format)}</td><td class="num">${fmtBytes(f.bytes)}</td>
        <td class="nowrap">${esc(fmtTime(f.mtime))}</td></tr>`).join('')}</tbody></table></div>`
    : '<p class="empty">收件箱是空的。把新书拖到上面，它就会出现在这里。</p>'}
    <div class="toolbar">
      <button class="btn${hasPlan ? '' : ' primary'}" data-write data-act="import-scan">生成入库清单</button>
      <span class="hint">会识别书名、作者和分类建议。</span>
    </div>
  </div>`;
}

function dropCard() {
  return `<div class="card">
    <h2>投递新书</h2>
    <div class="drop" data-act="pick-files" tabindex="0" role="button">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>
      </svg>
      <div class="drop-title">拖文件到这里，或点击选择</div>
      <div class="drop-sub">EPUB、MOBI、AZW3、PDF、TXT、DJVU，单个文件不超过 2 GB</div>
    </div>
    <input type="file" id="up-file" class="visually-hidden" multiple>
    <div class="uploading" id="up-box" hidden>
      <div class="progress"><i id="up-bar"></i></div>
      <p class="hint" id="up-note"></p>
    </div>
  </div>`;
}

function viewImport() {
  const inbox = S.inbox || [];
  const plan = S.plan;
  return `<h1>新书导入</h1>
    <p class="lead">把文件拖进来，核对书名和分类，再确认入库。</p>
    ${S.importDone ? `<div class="banner"><span>已入库，接下来按格式去拆章或分诊。</span>
      <button class="btn sm" data-act="goto" data-tab="split">去拆章</button>
      <button class="btn sm" data-act="goto" data-tab="pdf">去分诊 PDF</button></div>` : ''}
    <div class="grid c2 section">
      ${dropCard()}
      ${inboxCard(inbox, !!plan)}
    </div>
    <div class="section card">
      <div class="card-head"><h2>${esc(planHead())}</h2>
        <span class="row tight">
          <button class="btn sm" data-act="plan-defaults" ${plan ? '' : 'disabled'}>采用建议分类</button>
          <button class="btn sm${plan ? ' primary' : ''}" data-write data-act="import-apply" ${plan ? '' : 'disabled'}>确认入库</button>
        </span></div>
      ${plan ? planTable(plan) : '<p class="empty">收件箱里有书时，点「生成入库清单」。</p>'}
    </div>`;
}

function planTable(plan) {
  const counts = plan.reduce((acc, row) => {
    acc[row.action] = (acc[row.action] || 0) + 1;
    return acc;
  }, {});
  const summary = ['add', 'replace', 'conflict', 'skip']
    .map((key) => ACTION_WORD[key] + ' ' + num(counts[key] || 0)).join('，');
  return `<p class="hint">${esc(summary)}</p>
    <div class="tw"><table class="plan-table">
    <colgroup><col style="width:92px"><col style="width:16%"><col style="width:14%"><col style="width:10%">
      <col style="width:52px"><col style="width:84px"><col style="width:11%"><col style="width:78px">
      <col style="width:82px"><col></colgroup>
    <thead><tr><th>动作</th><th>文件</th><th>书名</th><th>作者</th><th>格式</th><th class="num">大小</th>
      <th>分类</th><th>保留</th><th>编号</th><th>说明</th></tr></thead>
    <tbody>${plan.map(planRow).join('')}</tbody></table></div>
    <p class="hint">黄色行建议再核对一遍。</p>
    <p class="hint">确认后会更新书目与检索索引，接着就可以去拆章或分诊。</p>`;
}

// ---- 电子书 -------------------------------------------------------
function viewSplit() {
  const ebooks = S.pending.filter((r) => EBOOK.test(r.format));
  const allPicked = ebooks.length > 0 && ebooks.every((r) => S.selected.has(r.uid));
  return `<h1>电子书出正文</h1>
    <p class="lead">把电子书拆成按章的 Markdown。选书后一次跑完，失败的书可以单独重跑。</p>
    <div class="section card">
      <div class="toolbar">
        <span class="hint">待拆 ${num(ebooks.length)} 本</span>
        <span class="row tight">
          <label class="check"><input type="checkbox" data-act="split-all"${allPicked ? ' checked' : ''}> 全选</label>
          <button class="btn primary" data-write data-act="split-run">开始拆章</button>
        </span>
      </div>
      <details class="adv">
        <summary>更多选项</summary>
        <label class="check"><input type="checkbox" id="split-replace"${S.replaceMd ? ' checked' : ''}> 重拆已有 Markdown</label>
        <p class="hint">现有 Markdown 会换成新版本，旧版留在批次目录里。</p>
        <label class="check">同时处理 <input type="number" id="split-workers" value="${S.splitWorkers || 4}" min="1" max="16" style="width:64px"> 本</label>
      </details>
      ${ebooks.length ? `<div class="tw"><table><thead><tr><th></th><th>编号</th><th>书名</th><th>格式</th><th class="num">大小</th><th>源文件</th></tr></thead>
      <tbody>${ebooks.map((row) => `<tr>
        <td><input type="checkbox" data-act="split-pick" value="${esc(row.uid)}"${S.selected.has(row.uid) ? ' checked' : ''}></td>
        <td class="mono">${esc(row.uid)}</td>
        <td class="wrap">${esc(row.title)}</td>
        <td>${esc(row.format || '')}</td>
        <td class="num">${fmtBytes(row.bytes)}</td>
        <td class="wrap small" title="${esc(row.file || '')}">${esc(baseName(row.file))}</td>
      </tr>`).join('')}</tbody></table></div>` : '<p class="empty">没有待拆的电子书。</p>'}
    </div>
    <div class="section card" data-live="split-batch">${batchCard()}</div>
    ${S.review ? `<div class="section card">${reviewPanel()}</div>` : ''}
    <div class="section card"><h2>最近的拆章任务</h2>${recentJobs('split.run')}</div>`;
}

function batchCard() {
  return `<div class="card-head"><h2>${S.batch ? '批次 ' + esc(S.batch) : '还没有批次'}</h2>
      <button class="btn sm" data-act="split-refresh"${S.batch ? '' : ' disabled'}>刷新状态</button></div>
    ${splitBooks()}`;
}

const INSPECT_STATUS = ['staged', 'staged-review', 'split-failed', 'convert-failed', 'error'];

function splitBooks() {
  if (!S.batch) return '<p class="empty">还没开批次。选书后点「开始拆章」。</p>';
  if (!S.splitBooks.length) return '<p class="empty">批次刚开，还没有单本记录。跑起来后点「刷新状态」。</p>';
  return `<div class="tw"><table><thead><tr><th>编号</th><th>状态</th><th>结构检查</th><th>内容检查</th>
    <th class="num">文本量</th><th class="num">章</th><th class="num">修复</th><th>落地位置</th><th>原 Markdown</th><th></th></tr></thead>
    <tbody>${S.splitBooks.map((b) => `<tr>
      <td class="mono">${esc(b.uid)}</td>
      <td><span class="pill">${esc(SPLIT_WORD[b.status] || b.status || '—')}</span></td>
      <td>${checkWord(b.verify_rc)}</td>
      <td>${checkWord(b.audit_rc)}</td>
      <td class="num">${b.audit_ratio ? pct(b.audit_ratio, 1) : '—'}</td>
      <td class="num">${esc(b.chapters === null || b.chapters === undefined ? '—' : b.chapters)}</td>
      <td class="num">${esc(b.fix || 0)}</td>
      <td class="wrap small">${esc(b.dest || '')}</td>
      <td class="wrap small" title="${esc(b.previous || '')}">${esc(baseName(b.previous))}</td>
      <td class="row tight">
        <button class="btn sm" data-act="split-one" data-uid="${esc(b.uid)}">重拆这本</button>
        <button class="btn sm" data-act="split-recheck" data-uid="${esc(b.uid)}">重新检查</button>
        ${INSPECT_STATUS.indexOf(b.status) >= 0 ? `<button class="btn sm" data-act="split-inspect" data-uid="${esc(b.uid)}">查看问题</button>` : ''}
      </td>
    </tr>`).join('')}</tbody></table></div>
    <p class="hint">重拆这本会从头再拆一遍；重新检查只复核暂存区里已有的正文，过关就直接进库；查看问题列出这本书差在哪。</p>`;
}

function reviewPanel() {
  const row = (Array.isArray(S.review) ? S.review[0] : null) || {};
  const uncovered = Array.isArray(row.uncovered) ? row.uncovered : [];
  const covered = uncovered.reduce((sum, item) => sum + (Number(item[0]) || 0), 0);
  const excluded = row.excluded || {};
  const alarms = row.verify || {};
  const stat = (label, value) => `<span class="k">${esc(label)}</span><span class="v">${esc(value)}</span>`;
  const alarmRows = Object.keys(alarms)
    .map((key) => stat(VERIFY_WORD[key] || key, num((alarms[key] || []).length) + ' 处'));
  const reasonRows = Object.keys(excluded)
    .map((key) => stat(key, num(excluded[key]) + ' 页'));
  return `<div class="card-head"><h2>${esc(row.title || row.uid || '这本书')}</h2>
      <button class="btn sm" data-act="review-close">关闭</button></div>
    <p class="small">状态 <span class="pill">${esc(SPLIT_WORD[row.status] || row.status || '—')}</span>
      · 审计比 ${esc(row.ratio === null || row.ratio === undefined ? '—' : pct(row.ratio, 1))}</p>
    <div class="metrics">
      ${stat('正文字数', num(row.text_chars || 0))}
      ${stat('图片路径字数', num(row.image_path_chars || 0))}
      ${stat('跨文件重复片段', num(row.dup_shingles || 0))}
      ${stat('自链', num(row.self_links || 0))}
    </div>
    <p class="small">原文没收录的页 ${num(uncovered.length)} 个，合计 ${num(covered)} 字${uncovered.length
      ? '；前几个：' + uncovered.slice(0, 5).map((item) => esc(baseName(item[1]))).join('、') : ''}</p>
    <div class="metrics">${reasonRows.join('') || stat('未收录原因', '没有')}</div>
    <div class="metrics">${alarmRows.join('') || stat('结构报警', '没有')}</div>
    <p class="hint">差在卷首卷尾或图版的，多半用章节映射调整后再重拆；确认本来就该少，就写受理原因后放进库。</p>
    <p class="hint">文本量低于原文 88% 或高于 115% 才会被标出。</p>`;
}

function recentJobs(kind, limit = 6) {
  const rows = S.jobs.filter((j) => j.kind === kind).slice(0, limit);
  if (!rows.length) return '<p class="empty">还没有这类任务。</p>';
  return rows.map(jobLine).join('');
}

// 两类合成一份，空态只出一句
function recentPdfJobs(limit = 3) {
  const rows = S.jobs.filter((j) => j.kind === 'pdf.triage' || j.kind === 'pdf.run')
    .slice(0, limit * 2);
  if (!rows.length) return '<p class="empty">还没有这类任务。</p>';
  return rows.map(jobLine).join('');
}

// ---- PDF ----------------------------------------------------------
// 队列条读的是待转清单本身，不读分诊账：分诊账里扫描识别 411 本中有 399 本已经落地
function queueStrip() {
  if (!S.bookCounts) return '<p class="empty">清单还没读出来，点「刷新」再试。</p>';
  const pages = {};
  S.books.forEach((row) => {
    pages[row.disposition] = (pages[row.disposition] || 0) + (Number(row.pages) || 0);
  });
  const recheck = S.books.filter((row) => row.recheck).length;
  const stuck = S.books.filter((row) => row.disposition === 'ja'
    && row.progress === 'failed').length;
  const note = {
    convert: '已确认处置，可以直接转',
    untriaged: '还没看过，先分诊',
    gallery: recheck ? '其中 ' + num(recheck) + ' 本标了复看' : '图册不转文字',
    ja: stuck ? num(stuck) + ' 本上次失败' : '日文原版暂缓处理',
  };
  return `<div class="strip">${STRIP_TAG.map((key) => `<a class="strip-cell" href="#pdf-list" data-act="pdf-jump" data-tag="${esc(key)}">
    <div class="strip-name">${esc(PICK_WORD[key])}</div>
    <div class="strip-read">${num(S.bookCounts[key] || 0)} 本 · ${num(pages[key] || 0)} 页</div>
    <div class="note">${esc(note[key])}</div>
  </a>`).join('')}</div>`;
}

function quotaLine(batch) {
  const today = S.pdfToday || { used: 0, cap: 20000 };
  const part = batch.partition || {};
  const day = /(\d{4}-\d{2}-\d{2})$/.exec(String(batch.name || ''));
  const archived = !!batch.name && Number(batch.worklist) > 0;
  const old = archived
    ? `${esc(day ? day[1] : batch.name)} 已封存 · ${num(batch.worklist)} 本里 ${num(part.landed)} 落地、${num(part.left)} 未落地（${num(part.failed)} 失败 / ${num(part.staged)} 待复核 / ${num(part.untouched)} 未开跑）`
    : '还没封存过批次。';
  return `<div class="toolbar" style="margin-top:10px">
    <span class="small">今天额度 <b>${num(today.used)}</b> / ${num(today.cap)} 页</span>
    <span class="spacer"></span>
    <span class="hint">${old}</span>
    <button class="btn sm" data-act="pdf-detail"${archived ? '' : ' disabled'}>看明细</button>
  </div>`;
}

function pickCounts() {
  const counts = { total: 0, convert: 0, gallery: 0, ja: 0, untriaged: 0 };
  S.books.forEach((row) => {
    if (!S.pick.has(row.uid)) return;
    counts.total += 1;
    if (counts[row.disposition] !== undefined) counts[row.disposition] += 1;
  });
  return counts;
}

function pickBreakdown() {
  const c = pickCounts();
  if (!c.total) return '还没有选书';
  const bits = ['已选 ' + num(c.total) + ' 本'];
  if (c.convert) bits.push('可转 ' + num(c.convert) + ' 本');
  if (c.gallery) bits.push('只记图册 ' + num(c.gallery) + ' 本');
  if (c.ja) bits.push('日文暂缓 ' + num(c.ja) + ' 本');
  if (c.untriaged) bits.push('未分诊 ' + num(c.untriaged) + ' 本');
  return bits.join(' · ');
}

function pdfPickSummary() {
  const note = S.bookCounts
    ? '清单里共 ' + num(S.books.length) + ' 本待转的 PDF。'
    : '清单还没读，点下面进去看。';
  return `<p class="small"><b>${esc(pickBreakdown())}</b></p>
    <p class="hint">${esc(note)}</p>
    <button class="btn" data-act="pdf-pick-open">去清单选书</button>`;
}

function pickRowHtml(rows) {
  if (!rows.length) {
    return `<tr><td class="empty" colspan="8">${S.books.length
      ? '没有符合条件的书。' : '清单是空的：现在没有待转的 PDF。'}</td></tr>`;
  }
  return rows.map((row) => {
    const tag = `<span class="tag tag-${esc(row.disposition)}">${esc(PICK_WORD[row.disposition] || row.disposition)}</span>`;
    const marks = [];
    if (row.disposition === 'untriaged' && row.suggestion) {
      const conf = row.suggestion.confidence === null
        || row.suggestion.confidence === undefined ? ''
        : ' ' + Number(row.suggestion.confidence).toFixed(2);
      marks.push(`<span class="hint">建议 ${esc(routeWord(row.suggestion.route))}${esc(conf)}</span>`);
    }
    if (row.route) marks.push(`<span class="hint">${esc(routeWord(row.route))}</span>`);
    if (row.recheck) marks.push('<span class="hint">复看</span>');
    const pages = row.pages === null || row.pages === undefined ? '—' : num(row.pages);
    const note = pickNote(row);
    const act = row.disposition === 'untriaged' && row.suggestion
      ? `<button class="btn sm" data-act="pick-suggest" data-uid="${esc(row.uid)}">按建议确认</button>`
      : `<button class="btn sm" data-act="pick-settle" data-uid="${esc(row.uid)}">改处置</button>`;
    return `<tr>
      <td><input type="checkbox" data-act="pick-one" value="${esc(row.uid)}"${S.pick.has(row.uid) ? ' checked' : ''}></td>
      <td class="mono">${esc(row.uid)}</td>
      <td class="wrap">${esc(row.title || '')}</td>
      <td>${tag}${marks.join(' ')}</td>
      <td>${pickProgress(row)}</td>
      <td class="num">${pages}</td>
      <td class="small clip" title="${esc(note)}">${esc(note)}</td>
      <td>${act}</td>
    </tr>`;
  }).join('');
}

function pickNote(row) {
  if (row.disposition === 'gallery' && row.why) return row.why;
  if (row.progress === 'failed' && row.last) return '上次 ' + row.last;
  if (row.suggestion && row.suggestion.reason) return '分诊建议：' + row.suggestion.reason;
  return '';
}

function pickProgress(row) {
  const word = PICK_PROGRESS[row.progress] || [row.progress, 'out'];
  return `<span class="status ${word[1]}">${esc(word[0])}</span>`;
}

function pickRows() {
  const q = String(S.pickQ || '').trim().toLowerCase();
  const rows = S.books.filter((row) => {
    if (S.pickTags.size && !S.pickTags.has(row.disposition)) return false;
    if (PICK_PROGRESS_FILTER.some(([key]) => key === row.progress)
      && !S.pickProgress.has(row.progress)) return false;
    if (S.pickRecheck && !row.recheck) return false;
    if (S.pickSubject && row.subject1 !== S.pickSubject) return false;
    if (!q) return true;
    return String(row.uid || '').toLowerCase().indexOf(q) >= 0
      || String(row.title || '').toLowerCase().indexOf(q) >= 0;
  });
  const byUid = (a, b) => String(a.uid).localeCompare(String(b.uid));
  if (S.pickSort === 'uid') return rows.slice().sort(byUid);
  const sign = S.pickSort === 'pages-asc' ? 1 : -1;
  return rows.slice().sort((a, b) => (sign * ((Number(a.pages) || 0) - (Number(b.pages) || 0)))
    || byUid(a, b));
}

function pickSubjects() {
  const out = [];
  S.books.forEach((row) => {
    if (row.subject1 && out.indexOf(row.subject1) < 0) out.push(row.subject1);
  });
  return out.sort();
}

function pickToolbar() {
  return `<div class="toolbar">
    <input type="text" id="pick-q" placeholder="编号或书名" size="14" value="${esc(S.pickQ)}">
    ${PICK_TAG.map(([key, label]) => `<label class="check"><input type="checkbox" data-act="pick-tag" value="${key}"${S.pickTags.has(key) ? ' checked' : ''}>${esc(label)}</label>`).join('')}
    <select id="pick-subject">
      <option value="">全部一级分类</option>
      ${pickSubjects().map((name) => `<option value="${esc(name)}"${name === S.pickSubject ? ' selected' : ''}>${esc(name)}</option>`).join('')}
    </select>
    <select id="pick-sort">
      ${PICK_SORT.map(([key, label]) => `<option value="${key}"${key === S.pickSort ? ' selected' : ''}>${esc(label)}</option>`).join('')}
    </select>
    <span class="spacer"></span>
    <button class="btn sm" data-act="pick-refresh">刷新</button>
    <a class="btn sm" href="#pdf" data-act="goto" data-tab="pdf">回 PDF 页</a>
  </div>`;
}

function pickFilterBar() {
  return `<div class="toolbar">
    <span class="small muted">进度</span>
    ${PICK_PROGRESS_FILTER.map(([key, label]) => `<label class="check"><input type="checkbox" data-act="pick-progress" value="${key}"${S.pickProgress.has(key) ? ' checked' : ''}>${esc(label)}</label>`).join('')}
    <span class="small muted">标记</span>
    <label class="check"><input type="checkbox" data-act="pick-recheck"${S.pickRecheck ? ' checked' : ''}>只看复看</label>
    <span class="hint">复看是当初标了复看的图册，还没复核完</span>
  </div>`;
}

function pickBar() {
  const c = pickCounts();
  const converts = S.books.filter((row) => row.disposition === 'convert').length;
  const suggests = S.books.filter((row) => S.pick.has(row.uid) && row.suggestion).length;
  return `<b>已选 ${num(c.total)} 本</b>
    <span class="hint">可转 ${num(c.convert)} · 只记图册 ${num(c.gallery)} · 日文暂缓 ${num(c.ja)} · 未分诊 ${num(c.untriaged)}</span>
    <button class="btn sm" data-act="pick-suggest-all"${suggests ? '' : ' disabled'}>按建议确认 ${num(suggests)} 本</button>
    <button class="btn sm" data-act="pick-all-convert"${converts ? '' : ' disabled'}>全选可转的</button>
    <button class="btn sm" data-act="pick-clear"${c.total ? '' : ' disabled'}>清空</button>
    <span class="spacer"></span>
    <label class="check">最多转 <input type="number" id="pdf-limit" value="${S.pdfLimit}" min="0" style="width:64px"> 本</label>
    <label class="check">同时处理 <input type="number" id="pdf-workers" value="${S.pdfWorkers || 3}" min="1" max="8" style="width:64px"> 本</label>
    <button class="btn primary" data-write data-act="pdf-run"${c.total ? '' : ' disabled'}>开始转写</button>`;
}

function viewPdfList() {
  const counts = S.bookCounts || { convert: 0, gallery: 0, ja: 0, untriaged: 0 };
  const total = (counts.convert || 0) + (counts.gallery || 0) + (counts.ja || 0)
    + (counts.untriaged || 0);
  const rows = pickRows();
  const lead = '还没有正文的 PDF 都在这里，共 ' + num(total) + ' 本：可转 '
    + num(counts.convert || 0) + ' 本、只记图册 ' + num(counts.gallery || 0) + ' 本、日文暂缓 '
    + num(counts.ja || 0) + ' 本、未分诊 ' + num(counts.untriaged || 0) + ' 本。';
  return `<h1>待转 PDF 清单</h1>
    <p class="lead">${esc(lead)}</p>
    <div class="section card">
      ${pickToolbar()}
      ${pickFilterBar()}
      <div class="tw"><table class="pick-table">
        <colgroup><col style="width:34px"><col style="width:76px"><col><col style="width:232px">
          <col style="width:86px"><col style="width:76px"><col style="width:210px"><col style="width:104px"></colgroup>
        <thead><tr><th></th><th>编号</th><th>书名</th><th>处置</th><th>进度</th>
          <th class="num">页数</th><th>备注</th><th></th></tr></thead>
        <tbody id="pick-body">${pickRowHtml(rows)}</tbody>
      </table></div>
      <p class="hint" id="pick-note">当前显示 ${num(rows.length)} 本。</p>
      <div class="pick-gap"></div>
      <div class="pick-bar" id="pick-bar">${pickBar()}</div>
    </div>`;
}

// 关键字边打边筛：只重画表格与计数，整页 render 会丢焦点
function paintPick() {
  const rows = pickRows();
  const body = $('#pick-body');
  if (body) body.innerHTML = pickRowHtml(rows);
  const bar = $('#pick-bar');
  if (bar) bar.innerHTML = pickBar();
  const note = $('#pick-note');
  if (note) note.textContent = '当前显示 ' + num(rows.length) + ' 本。';
}

function paintTriageProgress() {
  const box = $('#triage-progress');
  if (box) box.innerHTML = triageProgress();
}

// 批量任务要按步数出进度，watchJob 只回任务行，这里连 steps 一起拿
async function watchSteps(jobId, onTick) {
  for (let i = 0; i < 240; i += 1) {
    let body;
    try {
      body = await api('/api/jobs/' + encodeURIComponent(jobId));
    } catch (err) {
      return null;
    }
    if (onTick) onTick(body.steps || []);
    if (SETTLED.indexOf(body.job.state) >= 0) return body;
    await new Promise((resolve) => setTimeout(resolve, 700));
  }
  return null;
}

function settlePages(items) {
  const pages = {};
  items.forEach((item) => {
    const row = S.books.find((one) => one.uid === item.uid) || {};
    pages[item.route] = (pages[item.route] || 0) + (Number(row.pages) || 0);
  });
  return Object.keys(pages)
    .map((route) => routeWord(route) + ' ' + num(pages[route]) + ' 页').join('、');
}

function settleAsk(items) {
  return '按分诊建议确认 ' + num(items.length) + ' 本：' + settlePages(items)
    + '。只写台账，不出正文。';
}

// 改处置只写台账，不出正文
async function applySettle(items, ask) {
  if (ask && !window.confirm(ask)) return;
  setBusy(true);
  try {
    const body = await post('/api/pdf/settle', { items });
    S.settle = null;
    $('#modal').innerHTML = '';
    const detail = await watchJob(body.job_id, afterJob);
    if (detail) toast(jobNote(detail.job), jobBad(detail.job));
    await Promise.all([loadJobs(), loadPdfBooks(), loadPdf()]);
    render();
  } catch (err) {
    toast(err.message, true);
  } finally {
    setBusy(false);
  }
}

function triageProgress() {
  const t = S.pdfTriage;
  if (!t) return '';
  const pct = t.total ? Math.min(100, Math.round(t.done / t.total * 100)) : 0;
  return `<div class="progress-label"><span>分诊中 ${num(t.done)} / ${num(t.total)} 本</span><span>${pct}%</span></div>
    <div class="progress"><i style="width:${pct}%"></i></div>`;
}

function triageCard() {
  const split = S.pdfUntriaged;
  const untriaged = S.books.filter((row) => row.disposition === 'untriaged');
  const total = split ? split.books : untriaged.length;
  const have = untriaged.filter((row) => row.suggestion).length;
  const todo = Math.max(0, total - have);
  const bits = [];
  if (split && split.text.books + split.scan.books === split.books) {
    if (split.text.books) {
      bits.push(num(split.text.books) + ' 本文本层 ' + num(split.text.pages) + ' 页会建议直接抽取');
    }
    if (split.scan.books) {
      bits.push(num(split.scan.books) + ' 本扫描件 ' + num(split.scan.pages) + ' 页会建议识别');
    }
  }
  const lead = total
    ? '未分诊 ' + num(total) + ' 本'
      + (bits.length ? '：' + bits.join('，') + '。'
        : '，共 ' + num(split ? split.pages : 0) + ' 页。')
    : '现在没有未分诊的 PDF。';
  const second = have
    ? '其中 ' + num(have) + ' 本已经出了建议，等你在清单里确认。'
    : '分诊只看指标与三张缩略图，不出正文。出的建议在清单页逐行确认，也可以一次按建议确认。';
  return `<div class="card">
    <h2>批量分诊</h2>
    <p class="small">${esc(lead)}</p>
    <p class="hint">${esc(second)}</p>
    <button class="btn primary" data-act="pdf-triage-batch"${todo && !S.pdfTriage ? '' : ' disabled'}>开始分诊 ${num(todo)} 本</button>
    <div id="triage-progress" style="margin-top:12px">${triageProgress()}</div>
  </div>`;
}

function runCard() {
  return `<div class="card">
    <h2>确认后转 md</h2>
    ${pdfPickSummary()}
    <label class="field"><span>最多转几本</span>
      <input type="number" id="pdf-limit" value="${S.pdfLimit}" min="0"></label>
    <label class="field"><span>同时处理</span>
      <input type="number" id="pdf-workers" value="${S.pdfWorkers || 3}" min="1" max="8"></label>
    <button class="btn primary" data-write data-act="pdf-run">开始转写</button>
    <p class="hint">最多转几本填 0 表示不限。勾了图册或日文会再问一次；未分诊的书只送去分诊，不出正文。</p>
  </div>`;
}

function viewPdf() {
  const batch = (S.pdf && S.pdf.batch) || {};
  return `<h1>PDF 出正文</h1>
    <p class="lead">待转的 PDF 先分诊定处置，再从清单里勾书转成 Markdown。</p>
    <div class="section card">
      <div class="card-head"><h2>现在的状态</h2><span class="row tight"><button class="btn sm" data-act="pdf-refresh">刷新</button></span></div>
      ${queueStrip()}
      ${quotaLine(batch)}
    </div>
    <div class="grid c2 section">
      ${triageCard()}
      ${runCard()}
    </div>
    <div class="section card"><h2>最近的分诊与转 md</h2>${recentPdfJobs()}</div>`;
}

function settleModal(row) {
  if (!row) return '';
  const current = row.route || (row.disposition === 'gallery' ? '不转-记图册'
    : (row.disposition === 'ja' ? '日文暂缓' : (row.suggestion || {}).route));
  const pages = row.pages === null || row.pages === undefined ? '' : ' · ' + num(row.pages) + ' 页';
  return `<div class="overlay" data-act="close-modal">
    <div class="modal" data-stop="1">
      <h2>改处置：${esc(row.title || row.uid)}</h2>
      <p class="hint">${esc(row.uid)}${esc(pages)}</p>
      <label class="field"><span>处置</span><select id="settle-route">
        ${SETTLE_ROUTES.map((r) => `<option value="${esc(r)}"${r === current ? ' selected' : ''}>${esc(routeWord(r))}</option>`).join('')}
      </select></label>
      <div class="row">
        <button class="btn primary" data-write data-act="settle-submit" data-uid="${esc(row.uid)}">提交</button>
        <button class="btn" data-act="close-modal">取消</button>
        <span class="hint">改处置只写台账，不出正文。</span>
      </div>
    </div>
  </div>`;
}

// ---- 维护 ---------------------------------------------------------
function stateCell(name, cls, word, note) {
  return `<div class="strip-cell">
    <div class="strip-name">${esc(name)}</div>
    ${status(cls, word)}
    <div class="note">${esc(note)}</div>
  </div>`;
}

function viewMaintain() {
  const c = bookCounts();
  const last = S.jobs.find((j) => j.kind === 'maintain') || null;
  const lastCls = !last ? 'out' : (last.state === 'succeeded' ? 'ok'
    : (last.state === 'failed' || last.state === 'orphaned' ? 'miss' : 'half'));
  const lastWord = !last ? '还没有记录'
    : (JOB_WORD[last.state] || last.state);
  const lastNote = last
    ? fmtTime(last.ended || last.created).slice(5)
    : '还没跑过，点一次就够';
  return `<h1>维护</h1>
    <p class="lead">让书目、检索和浏览入口保持一致。平时点一次「一键收尾」就够。</p>
    <div class="section card">
      <h2>库的状态</h2>
      <div class="strip">
        ${stateCell('检索索引', c.lagBooks ? 'half' : 'ok',
    c.lagBooks ? '需注意' : '正常',
    c.lagBooks ? '落后 ' + num(c.lagBooks) + ' 本'
      + (c.lagChapters ? ' · ' + num(c.lagChapters) + ' 章' : '') : '与书库同步')}
        ${stateCell('浏览入口', c.broken ? 'miss' : 'ok', c.broken ? '需处理' : '正常',
    c.broken ? num(c.broken) + ' 条失效' : '奖项与主题都在')}
        ${stateCell('体检', c.fail ? 'miss' : (c.warn ? 'half' : 'ok'),
    c.fail ? '需处理' : (c.warn ? '需注意' : '正常'),
    c.fail ? num(c.fail) + ' 项失败' : (c.warn ? num(c.warn) + ' 条提醒' : '全部通过'))}
        ${stateCell('书目总表', c.unmatched ? 'half' : 'ok', c.unmatched ? '需注意' : '正常',
    c.unmatched ? num(c.unmatched) + ' 条对不上文件' : '改于 ' + fmtTime(c.catalogAt).slice(5))}
        ${stateCell('上次收尾', lastCls, lastWord, lastNote)}
      </div>
    </div>
    <div class="section card">
      <div class="card-head"><h2>一键收尾</h2>
        <button class="btn primary" data-write data-act="maintain-run-default">开始收尾</button></div>
      <p class="small">一次跑完这四件事：</p>
      <ul class="next">
        <li>重建书目与总表</li>
        <li>更新检索索引</li>
        <li>刷新主题与奖项入口</li>
        <li>体检与抽查</li>
      </ul>
      <p class="hint">大约 8 分钟，任何一步没过就停在那里，修好后再点一次。</p>
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
        <p class="hint">书目总表改于 ${esc(fmtTime(c.catalogAt).slice(5))}，分类预填表只包含新书。</p>
      </div>
      <div class="card"><h2>清理旧记录</h2>
        <p class="hint">删掉 90 天以前或第 200 个之后的运行记录与缩略图，不碰书和账。</p>
        <p><button class="btn sm" data-act="jobs-clean">清理</button></p>
      </div>
    </div>
    <div class="section card"><h2>最近的收尾记录</h2>${recentJobs('maintain', 4)}</div>`;
}

// ---- 删书 ---------------------------------------------------------
function viewRetire() {
  return `<h1>删书</h1>
    <p class="lead">删书是把书移出流程。原书留在冷宫，阅读用的 Markdown、页面和索引记录会删掉。</p>
    <div class="section card">
      <h2>找书</h2>
      <div class="toolbar">
        <input type="text" id="retire-q" placeholder="编号或书名" value="${esc(S.retireQuery)}" size="24">
        <button class="btn" data-act="retire-search">搜书</button>
      </div>
      ${S.retireHits.length ? `<div class="tw"><table><thead><tr><th>编号</th><th>书名</th>
        <th>分类</th><th class="num">章</th><th class="num">源文件</th><th class="num">奖项</th><th></th></tr></thead>
        <tbody>${S.retireHits.map((b) => `<tr>
          <td class="mono">${esc(b.uid)}</td>
          <td class="wrap">${esc(b.title)}</td>
          <td class="wrap small">${esc([b.subject1, b.subject2].filter(Boolean).join('/'))}</td>
          <td class="num">${esc(b.md && b.md.chapters !== undefined ? b.md.chapters : 0)}</td>
          <td class="num">${(b.sources || []).length}</td>
          <td class="num">${(b.awards || []).length}</td>
          <td><button class="btn sm" data-act="retire-preview" data-uid="${esc(b.uid)}">预览移出</button></td>
        </tr>`).join('')}</tbody></table></div>` : '<p class="empty">输入编号或书名，搜索结果显示在这里。</p>'}
    </div>
    <div class="section card"><h2>最近的删书任务</h2>${recentJobs('delete.dry-run', 3)}${recentJobs('delete.apply', 3)}</div>`;
}

function previewModal(p) {
  if (!p) return '';
  const sources = (p.sources || []).map((s) => `<tr>
    <td class="wrap small">${esc(s.path)}</td>
    <td class="mono small">${esc(String(s.sha256 || '').slice(0, 12))}</td>
    <td class="num">${fmtBytes(s.bytes)}</td>
    <td class="num">${esc(s.shared_with || 0)}</td>
    <td>${esc(SOURCE_WORD[s.action] || s.action || '')}</td></tr>`).join('');
  return `<div class="overlay" data-act="close-modal">
    <div class="modal" data-stop="1">
      <h2>移出：${esc(p.title || p.uid)}</h2>
      <div class="tw"><table><tbody>
        <tr><th>书目录</th><td class="wrap">${esc(p.book_dir || p.path || '')}</td></tr>
        <tr><th>会删掉</th><td class="wrap">${esc((p.delete || []).join('、'))}（Markdown ${num(p.md_chapters)} 章 / ${fmtBytes(p.md_bytes)}）</td></tr>
        <tr><th>书目记录</th><td>${p.ledger ? '删掉这一行' : '无'}</td></tr>
        <tr><th>检索扩展</th><td>${num(p.expand_rows)} 行扩展 + ${num(p.index_rows)} 行索引</td></tr>
        <tr><th>奖项</th><td class="wrap">${esc((p.awards || []).map((a) => a.award || a).join('、') || '无')}</td></tr>
        <tr><th>入口</th><td class="wrap">${esc((p.views || []).join('、') || '无')}</td></tr>
        <tr><th>系列文档</th><td class="wrap">${esc((p.series_docs || []).join('、') || '无')}</td></tr>
        <tr><th>移到</th><td class="wrap mono small">${esc(p.cold_path || '')}</td></tr>
        <tr><th>回滚副本</th><td class="wrap small">${p.snapshot_ok ? '<span class="pass">就绪</span>' : '<span class="fail">不可用</span>'} ${esc(p.snapshot || '')}</td></tr>
        <tr><th>校验码</th><td class="mono small">${esc(p.plan_hash || '')}</td></tr>
      </tbody></table></div>
      <h3>源文件</h3>
      <div class="tw"><table><thead><tr><th>路径</th><th>指纹</th><th class="num">大小</th><th class="num">共享本数</th><th>动作</th></tr></thead>
        <tbody>${sources || '<tr><td colspan="5" class="empty">没有源文件</td></tr>'}</tbody></table></div>
      <div class="section">
        <div class="toolbar">
          <input type="text" id="apply-uid" placeholder="再输一遍编号" size="12">
          <input type="text" id="apply-reason" placeholder="理由" size="24" value="移出流程">
        </div>
        <label class="check"><input type="checkbox" id="apply-confirm"> 我确认原书移到冷宫，阅读版与索引记录删除</label>
        <div class="row"><button class="btn primary" data-write data-act="retire-apply" data-uid="${esc(p.uid)}" data-hash="${esc(p.plan_hash || '')}">确认移出</button>
          <button class="btn" data-act="close-modal">取消</button>
          <span class="hint">确认时会再核对一次，对不上就拒绝。</span></div>
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
    <p class="lead">每个任务按自己的检查判定成败，日志里的密钥会隐藏。</p>
    <div class="section card">
      <div class="toolbar">
        <label class="check">状态 <select id="log-filter">
          ${states.map((s) => `<option value="${s}"${s === S.filter ? ' selected' : ''}>${s ? JOB_WORD[s] : '全部'}</option>`).join('')}
        </select></label>
        <button class="btn sm" data-act="jobs-refresh">刷新</button>
        <span class="hint">共 ${num(rows.length)} 个任务</span>
      </div>
      <div class="tw"><table><thead><tr><th>创建</th><th>类型</th><th>标题</th><th>状态</th>
        <th class="num">耗时</th><th>摘要</th><th></th></tr></thead>
      <tbody>${rows.length ? rows.map((j) => `<tr>
        <td class="nowrap">${esc(fmtTime(j.created))}</td>
        <td class="nowrap">${esc(KIND_WORD[j.kind] || j.kind)}</td>
        <td class="wrap">${esc(summaryText(j.title))}</td>
        <td><span class="pill">${esc(JOB_WORD[j.state] || j.state)}</span></td>
        <td class="num">${j.started && j.ended ? esc(seconds(j.started, j.ended)) : '—'}</td>
        <td class="wrap small">${esc(summaryText(j.summary))}</td>
        <td><button class="btn sm" data-act="job-open" data-id="${esc(j.id)}">详情</button></td>
      </tr>`).join('') : '<tr><td colspan="7" class="empty">没有任务。</td></tr>'}</tbody></table></div>
    </div>
    <div class="section${S.logJob ? ' card' : ''}" id="job-detail">${jobDetail()}</div>`;
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
  if (!job) return '<p class="empty">点上面表格里的「详情」，这里会显示步骤和日志。</p>';
  const detail = S.logDetail || {};
  const steps = detail.steps || [];
  const tail = detail.log_tail || '';
  const command = (detail.commands || []).map((c) => (c.argv || []).join(' ')).join('\n');
  return `<div class="card-head"><h2>${esc(summaryText(job.title))}</h2>
      <span class="row tight">
        <span class="pill">${esc(JOB_WORD[job.state] || job.state)}</span>
        ${['queued', 'running'].indexOf(job.state) >= 0
    ? `<button class="btn sm" data-act="job-cancel" data-id="${esc(job.id)}">停止</button>` : ''}
        <button class="btn sm" data-write data-act="job-rerun" data-id="${esc(job.id)}">重跑</button>
        <a class="btn sm" href="/api/file?path=${encodeURIComponent('工作区/运维/jobs/' + job.id + '/log.txt')}">下载日志</a>
      </span></div>
    <p class="hint">任务号 ${esc(job.id)} · ${esc(fmtTime(job.started))} → ${esc(fmtTime(job.ended))}</p>
    <p class="small">${esc(summaryText(job.summary))}</p>
    <details class="adv">
      <summary>运行命令</summary>
      <pre class="log">${esc(command || '没有记录')}</pre>
    </details>
    <div class="tw"><table><thead><tr><th>#</th><th>步骤</th><th>状态</th><th>说明</th><th>开始</th></tr></thead>
      <tbody>${steps.length ? steps.map((s) => `<tr><td class="num">${s.seq + 1}</td>
        <td>${esc(s.name)}</td><td>${esc(STEP_WORD[s.state] || s.state)}</td>
        <td class="wrap small">${esc(stepNote(s.detail))}</td>
        <td class="nowrap">${esc(fmtTime(s.started))}</td></tr>`).join('')
    : '<tr><td colspan="5" class="empty">没有步骤</td></tr>'}</tbody></table></div>
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

async function loadReview(jobId) {
  try {
    S.review = await api('/api/file?path=' + encodeURIComponent(
      '工作区/运维/jobs/' + jobId + '/result.json'));
    S.reviewJob = jobId;
  } catch (err) {
    S.review = null;
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

async function loadPdfBooks() {
  try {
    const body = await api('/api/pdf/books');
    S.books = body.books || [];
    S.bookCounts = body.counts || null;
    S.pdfToday = body.today || null;
    S.pdfUntriaged = body.untriaged || null;
  } catch (err) {
    S.books = [];
    S.bookCounts = null;
    S.pdfToday = null;
    S.pdfUntriaged = null;
    toast(err.message, true);
  }
  const known = new Set(S.books.map((row) => row.uid));
  Array.from(S.pick).forEach((uid) => {
    if (!known.has(uid)) S.pick.delete(uid);
  });
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
    await Promise.all([loadJobs(), loadPdf(), loadPdfBooks()]);
  } else if (S.tab === 'pdf-list') {
    await Promise.all([loadJobs(), loadPdfBooks()]);
  } else if (S.tab === 'maintain' || S.tab === 'retire') {
    await Promise.all([loadStatus(), loadJobs()]);
  } else if (S.tab === 'logs') {
    await loadJobs();
  }
}

async function go(tab) {
  S.tab = ALL_TABS.indexOf(tab) >= 0 ? tab : 'overview';
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
      box.className = 'section card';
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
  return (job.state === 'awaiting_review' ? '待确认：' : '') + (summaryText(job.summary) || job.state);
}

async function runMaintain(steps) {
  if (!steps.length) {
    toast('先勾要跑的步骤', true);
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
  async 'status-refresh'() {
    await loadStatus(true);
    await loadJobs();
    render();
  },

  async 'pick-files'(el) {
    const input = $('#up-file');
    if (input && el !== input) input.click();
  },

  async 'search-open'() {
    await loadHealth();
    if (!S.health || !S.health.running) {
      toast('阅读搜索还没起来，先点「启动阅读搜索」');
      return;
    }
    window.open('http://127.0.0.1:8765/', '_blank', 'noopener');
  },

  async 'search-start'() {
    try {
      const body = await post('/api/search/start');
      if (body.already) toast('阅读搜索已经在跑');
      else if (body.ok) toast('阅读搜索起来了');
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
      toast('开始识别，好了会出现在下面');
      const detail = await watchJob(body.job_id, afterJob);
      if (detail && detail.job.state === 'awaiting_review') {
        S.planJob = body.job_id;
        await loadPlan(body.job_id);
        toast('清单好了，核对后入库');
      } else {
        toast(summaryText(detail && detail.job.summary) || '扫描没过', true);
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
    toast('已把建议分类填回表格，记得点确认入库');
  },

  async 'import-apply'() {
    if (!S.plan || !S.planJob) return;
    const conflicts = S.plan.filter((it) => it.action === 'conflict').length;
    if (conflicts && !window.confirm(conflicts + ' 行冲突不会入库，文件留在收件箱。继续？')) return;
    setBusy(true);
    try {
      await post('/api/import/plan', { job_id: S.planJob, items: S.plan });
      const body = await post('/api/import/apply',
        { plan_path: '工作区/运维/jobs/' + S.planJob + '/plan.json' });
      toast('开始入库，完成后会提示');
      const detail = await watchJob(body.job_id, afterJob);
      if (detail && detail.job.state === 'succeeded') {
        toast('入库完成');
        S.importDone = true;
        S.plan = null;
        S.planJob = null;
      } else {
        toast(summaryText(detail && detail.job.summary) || '入库没过，看日志', true);
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
    if (replace && !window.confirm('重拆会替换现有 Markdown，确认？')) return;
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
      toast('重拆 ' + el.dataset.uid + '：' + body.job_id);
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

  async 'split-recheck'(el) {
    if (!S.batch) return;
    setBusy(true);
    try {
      const body = await post('/api/split/recheck', { uid: el.dataset.uid, batch: S.batch });
      toast('重新检查 ' + el.dataset.uid + '：' + body.job_id);
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

  async 'split-inspect'(el) {
    if (!S.batch) return;
    try {
      const body = await post('/api/split/inspect', { uid: el.dataset.uid, batch: S.batch });
      const detail = await watchJob(body.job_id, afterJob);
      if (detail && detail.job.state === 'succeeded') {
        await loadReview(body.job_id);
      } else {
        toast(summaryText(detail && detail.job.summary) || '没取到这本书的核对证据', true);
      }
      await loadJobs();
      render();
    } catch (err) {
      toast(err.message, true);
    }
  },

  async 'review-close'() {
    S.review = null;
    S.reviewJob = null;
    render();
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
    await Promise.all([loadPdf(), loadPdfBooks()]);
    render();
  },

  async 'pdf-pick-open'() {
    if (location.hash !== '#pdf-list') location.hash = '#pdf-list';
    else await go('pdf-list');
  },

  // 明细就是清单筛成「日文暂缓 + 失败」
  async 'pdf-detail'() {
    S.pickTags = new Set(['ja']);
    S.pickProgress = new Set(['failed']);
    S.pickRecheck = false;
    if (location.hash !== '#pdf-list') location.hash = '#pdf-list';
    else await go('pdf-list');
  },

  async 'pdf-jump'(el) {
    const tag = el.dataset.tag;
    S.pickTags = new Set(tag ? [tag] : ['convert', 'untriaged', 'gallery', 'ja']);
    S.pickProgress = new Set(['todo', 'running', 'failed']);
    S.pickRecheck = false;
    if (location.hash !== '#pdf-list') location.hash = '#pdf-list';
    else await go('pdf-list');
  },

  async 'pick-one'(el) {
    if (el.checked) S.pick.add(el.value);
    else S.pick.delete(el.value);
    paintPick();
  },

  async 'pick-tag'(el) {
    if (el.checked) S.pickTags.add(el.value);
    else S.pickTags.delete(el.value);
    render();
  },

  async 'pick-progress'(el) {
    if (el.checked) S.pickProgress.add(el.value);
    else S.pickProgress.delete(el.value);
    paintPick();
  },

  async 'pick-recheck'(el) {
    S.pickRecheck = el.checked;
    paintPick();
  },

  async 'pick-all-convert'() {
    pickRows().forEach((row) => {
      if (row.disposition === 'convert') S.pick.add(row.uid);
    });
    paintPick();
  },

  async 'pick-clear'() {
    S.pick.clear();
    paintPick();
  },

  async 'pick-settle'(el) {
    const row = S.books.find((item) => item.uid === el.dataset.uid);
    if (!row) return;
    S.settle = row;
    $('#modal').innerHTML = settleModal(row);
  },

  async 'settle-submit'(el) {
    const route = ($('#settle-route') || {}).value;
    if (!route) return;
    await applySettle([{ uid: el.dataset.uid, route }]);
  },

  async 'pick-suggest'(el) {
    const row = S.books.find((item) => item.uid === el.dataset.uid);
    if (!row || !row.suggestion) return;
    const items = [{ uid: row.uid, route: row.suggestion.route }];
    await applySettle(items, settleAsk(items));
  },

  async 'pick-suggest-all'() {
    const items = S.books.filter((row) => S.pick.has(row.uid) && row.suggestion)
      .map((row) => ({ uid: row.uid, route: row.suggestion.route }));
    if (!items.length) {
      toast('选中的书里没有带建议的', true);
      return;
    }
    await applySettle(items, settleAsk(items));
  },

  async 'pick-refresh'() {
    await loadPdfBooks();
    render();
  },

  async 'pdf-triage-batch'() {
    const uids = S.books.filter((row) => row.disposition === 'untriaged' && !row.suggestion)
      .map((row) => row.uid);
    if (!uids.length) {
      toast('没有还要分诊的书', true);
      return;
    }
    setBusy(true);
    S.pdfTriage = { total: uids.length, done: 0 };
    render();
    try {
      const body = await post('/api/pdf/triage-batch', { uids });
      const detail = await watchSteps(body.job_id, (steps) => {
        S.pdfTriage.done = steps.filter((s) => s.state && s.state !== 'pending').length;
        paintTriageProgress();
      });
      const state = detail ? detail.job.state : 'failed';
      await Promise.all([loadJobs(), loadPdfBooks(), loadPdf()]);
      S.pdfTriage = null;
      const made = uids.filter((uid) => {
        const row = S.books.find((item) => item.uid === uid);
        return !!row && !!row.suggestion;
      }).length;
      if (state === 'awaiting_review' || state === 'succeeded') {
        toast(made ? num(made) + ' 本出了建议，去清单确认' : '分诊跑完了，但没有模型建议', !made);
      } else {
        toast('分诊没过，去日志看', true);
      }
      render();
    } catch (err) {
      S.pdfTriage = null;
      render();
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  },

  async 'pdf-run'() {
    const picked = Array.from(S.pick);
    if (!picked.length) {
      toast('先去清单勾要转的书', true);
      return;
    }
    const limit = Number(S.pdfLimit) || 0;
    const workers = Number(S.pdfWorkers) || 3;
    const rows = S.books.filter((row) => S.pick.has(row.uid));
    const gallery = rows.filter((row) => row.disposition === 'gallery');
    const japanese = rows.filter((row) => row.disposition === 'ja');
    let force = false;
    if (gallery.length || japanese.length) {
      const pages = gallery.concat(japanese)
        .reduce((sum, row) => sum + (Number(row.pages) || 0), 0);
      const ask = '这 ' + num(gallery.length + japanese.length) + ' 本原定不转：只记图册 '
        + num(gallery.length) + ' 本、日文暂缓 ' + num(japanese.length) + ' 本，合计 '
        + num(pages) + ' 页。改成按扫描识别转？';
      if (!window.confirm(ask)) return;
      force = true;
    }
    setBusy(true);
    try {
      const body = await post('/api/pdf/run', { uids: picked, limit, workers, force });
      S.pick.clear();
      if (body.job_id) {
        toast('转写已开始，进度看日志');
        const detail = await watchJob(body.job_id, afterJob);
        if (detail) toast(jobNote(detail.job), jobBad(detail.job));
      } else {
        toast('已送去分诊 ' + num(body.triaged) + ' 本');
      }
      await Promise.all([loadJobs(), loadPdf(), loadPdfBooks(), loadPending()]);
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
        toast(summaryText(detail && detail.job.summary) || '预览没过', true);
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
      toast('输入的编号与目标对不上', true);
      return;
    }
    if (!ok) {
      toast('先勾确认框', true);
      return;
    }
    if (!p.snapshot_ok) {
      toast('回滚副本不可用，不能移出', true);
      return;
    }
    setBusy(true);
    try {
      const body = await post('/api/delete/apply', {
        uid, reason, plan_hash: el.dataset.hash, confirm_uid: typed,
      });
      const detail = await watchJob(body.job_id, afterJob);
      if (detail) toast(summaryText(detail.job.summary) || detail.job.state,
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
    S.settle = null;
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

document.addEventListener('keydown', (ev) => {
  if (ev.key !== 'Enter' && ev.key !== ' ') return;
  const el = ev.target.closest && ev.target.closest('[data-act="pick-files"]');
  if (!el) return;
  ev.preventDefault();
  actions['pick-files'](el).catch((err) => toast(err.message, true));
});

// 拖到别处也不让浏览器把文件当页面打开
document.addEventListener('dragover', (ev) => ev.preventDefault());
document.addEventListener('drop', (ev) => ev.preventDefault());

document.addEventListener('input', (ev) => {
  const el = ev.target;
  if (!el || el.id !== 'pick-q') return;
  S.pickQ = el.value;
  paintPick();
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
  if (el.id === 'pdf-limit') {
    S.pdfLimit = Number(el.value) || 0;
    return;
  }
  if (el.id === 'pdf-workers') {
    S.pdfWorkers = Number(el.value) || 0;
    return;
  }
  if (el.id === 'pick-subject') {
    S.pickSubject = el.value;
    render();
    return;
  }
  if (el.id === 'pick-sort') {
    S.pickSort = el.value || 'pages';
    render();
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
  const box = $('#up-box');
  const note = $('#up-note');
  const bar = $('#up-bar');
  if (box) box.hidden = false;
  if (bar) bar.style.width = '0';
  const setBar = (share) => { if (bar) bar.style.width = Math.max(0, Math.min(100, share)) + '%'; };
  let index = 0;
  const next = () => {
    if (index >= list.length) {
      setBar(100);
      if (note) note.textContent = '上传完成，收件箱里可以看到。';
      setTimeout(() => {
        if (bar) bar.style.width = '0';
        if (box) box.hidden = true;
        if (note) note.textContent = '';
      }, 1800);
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
      if (!e.lengthComputable) return;
      setBar((index - 1 + e.loaded / e.total) / list.length * 100);
      if (note) {
        note.textContent = '正在上传 ' + file.name + ' · '
          + fmtBytes(e.loaded) + ' / ' + fmtBytes(e.total);
      }
    };
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText); } catch (err) { body = { ok: false }; }
      if (xhr.status === 200 && body.ok) {
        toast('已放进收件箱：' + body.name);
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
        if (box) box.innerHTML = batchCard();
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
