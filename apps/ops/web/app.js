'use strict';

const TABS = [
  ['overview', '总览'], ['import', '新书导入'], ['split', '电子书'],
  ['pdf', 'PDF'], ['maintain', '维护'], ['ledger', '账表'], ['retire', '删书'],
  ['logs', '日志'],
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
  ['catalog', '更新书目总表', '重出 Excel 与 CSV。'],
  ['db', '重建书目数据库', '从每本书的记录重算索引库。'],
  ['doc', '重出阅读版页面', '只有阅读版有改动时才需要。'],
];
const FINISH_DEFAULT = ['db', 'index', 'topics', 'awards', 'audit', 'verify', 'catalog'];
const SETTLE_ROUTES = ['OCR', '本地抽文本', '不转-记图册', '日文暂缓'];
const JOB_WORD = {
  queued: '排队', running: '在跑', awaiting_review: '待确认', succeeded: '完成',
  failed: '失败', cancelled: '取消', cancelling: '停止中', orphaned: '失联',
};
const KIND_WORD = {
  'import.scan': '识别新书', 'import.apply': '入库', 'split.run': '拆章',
  'split.inspect': '查看问题',
  'pdf.triage': 'PDF 分诊', 'pdf.confirm': '确认处理方式', 'pdf.run': 'PDF 转写',
  'docs.run': '文档转 md',
  maintain: '收尾', 'delete.dry-run': '删书预览', 'delete.apply': '删书',
};
const STEP_WORD = { pending: '等待', ok: '通过', fail: '失败', running: '在跑', unknown: '退出码未知' };
const SPLIT_WORD = {
  landed: '已入库', replaced: '已替换', accepted: '已受理',
  'skipped-has-md': '已有正文', 'staged-green': '已过检未入库',
  staged: '未复核', 'staged-review': '待复核', 'no-stage': '暂存区空',
  'split-failed': '拆章失败', 'convert-failed': '转格式失败', error: '异常',
  pending_review: '待复核',
};
const ACTION_WORD = {
  add: '新增', replace: '换源', delete: '删除', review: '待核',
  conflict: '冲突', skip: '跳过',
};
const STATUS_WORD = {
  new: ['新书', 'ok'], replace: ['换源', 'half'], in_library: ['已在库', 'miss'],
  batch_dup: ['批次重复', 'miss'], same_format: ['同格式已在库', 'half'],
  weaker: ['格式更低', 'half'], fuzzy: ['书名待核', 'half'],
  candidates: ['多本同名', 'half'],
};
const RETENTION_WORD = { keep: '保留', review: '待定', drop: '不留' };
const PDF_ROUTE_WORD = {
  'OCR': '扫描识别', '本地抽文本': '直接抽取', '不转-记图册': '存档·图册',
  '人工确认': '待人工判断', '日文暂缓': '存档·非中英文',
};
const PICK_TAG = [['convert', '可转'], ['gallery', '存档·图册'], ['ja', '存档·非中英文'],
  ['untriaged', '未分诊']];
// 状态带按「先能转的，再要人动手的」排；清单筛选条沿用上面的处置顺序
const STRIP_TAG = ['convert', 'untriaged', 'gallery', 'ja'];
const PICK_SORT = [['pages', '页数多到少'], ['pages-asc', '页数少到多'], ['uid', '编号']];
const PICK_WORD = {
  convert: '可转', gallery: '存档·图册', ja: '存档·非中英文', untriaged: '未分诊',
};
// 进度词来自服务端 pdf_books_index：landed/running/failed/deferred/staged/todo
const PICK_PROGRESS = {
  todo: ['未开始', 'out'], running: ['在跑', 'half'], landed: ['已落地', 'ok'],
  failed: ['失败', 'miss'], deferred: ['暂缓·待额度', 'half'],
  staged: ['待确认', 'half'],
};
// 清单页的进度筛选只管这三档：已有正文的书不会出现在待转清单里
const PICK_PROGRESS_FILTER = [['todo', '未开始'], ['running', '在跑'], ['failed', '失败'],
  ['staged', '待确认'], ['deferred', '暂缓']];
const SOURCE_WORD = { move: '移动', hardlink: '硬链接' };
// 结构检查的报警键换成普通说法
const VERIFY_WORD = {
  identical: '重复段落', 'broken link': '断链', 'missing image': '缺图',
  'orphan image': '多余图片', 'no H1': '章名缺失',
};
const EBOOK = /^(epub|mobi|azw3|azw)$/;

const S = {
  ledger: null,
  ledgerQ: '',
  ledgerPendingOnly: false,
  ledgerEdit: null,
  ledgerPick: new Set(),
  tab: 'overview',
  payload: null, status: {}, health: null,
  jobs: [], pending: [], inbox: [], pdf: null,
  plan: null, planJob: null, planDirty: false, legacyPlan: false,
  batch: null, splitBooks: [],
  review: null, reviewJob: null,
  retireHits: [], retireDrop: [], retireQuery: '', preview: null,
  logJob: null, logDetail: null, follow: true, importDone: null, importDocsJob: '',
  docs: null, docsNone: false, docsOpen: false, docsSig: '',
  logOpenId: null, logScroll: 0, logStick: true, logRowsHtml: '',
  logStepsFp: '', logCmdFp: '',
  overviewHtml: '',
  filter: '', selected: new Set(), busy: false,
  replaceMd: false, splitWorkers: 4,
  pdfLimit: 0, pdfLimitFrom: -1, pdfWorkers: 3, triageSeen: null, pdfToday: null,
  pdfUntriaged: null,
  books: [], bookCounts: null, pick: new Set(), pickQ: '',
  pickTags: new Set(['convert', 'untriaged', 'gallery', 'ja']),
  pickProgress: new Set(['todo', 'running', 'failed', 'staged', 'deferred']), pickRecheck: false,
  pickSubject: '', pickSort: 'pages', settle: null,
  maintainSteps: new Set(FINISH_DEFAULT),
};

// 轮询没有「超时失败」这回事：到上限只是降频继续看，别把在跑的任务当没跑
const WATCH_TICK = 700;
const WATCH_LOOPS = 900;
const MAINTAIN_LOOPS = 1200;

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
  [/\b(add|replace|delete|review|conflict|skip) (\d+)/g, (match, word, count) => ACTION_WORD[word] + ' ' + count],
  [/\s+\/\s+(?=(?:新增|换源|删除|待核|冲突|跳过)\s*\d)/g, '，'],
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
  const convertible = st.convertible || null;
  return {
    books: dig(st, 'books', 0),
    readable: dig(st, 'md_done', 0),
    chapters: dig(st, 'md_chapters', 0),
    // 待转口径在服务端：无 md 的书里去掉已定存档的。缺口径时退回旧公式
    toConvert: convertible != null ? convertible
      : (pending.pdf || 0) + (pending.txt || 0) + (pending.djvu || 0),
    archive: st.archive || null,
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

// 体检条只给条数，具体哪几条由状态原文拆出来，和条上的数同源
function statusIssues() {
  const st = S.status || {};
  const rows = [];
  dig(st, 'problems', []).forEach((raw) => {
    rows.push({ level: 'fail', text: String(raw) });
  });
  dig(st, 'audit.items', []).forEach((raw) => {
    const text = String(raw);
    if (/^…/.test(text)) {
      rows.push({ level: 'note', text: text });
      return;
    }
    const hit = /^\s*(fail|warn)\s*[:：]?\s*(.*)$/i.exec(text);
    if (hit) rows.push({ level: hit[1].toLowerCase(), text: hit[2] });
    else rows.push({ level: 'warn', text: text });
  });
  return { at: dig(st, 'audit.at', ''), rows: rows };
}

function issueList(info) {
  if (!statsReadable()) return '<p class="hint">状态读不到，刷新一下试试。</p>';
  if (!info.rows.length) {
    return info.at
      ? '<p class="pass">体检全部通过。</p>'
      : '<p class="hint">还没有体检记录，跑一次收尾就会有。</p>';
  }
  return `<ul class="issues">${info.rows.map((row) => `<li class="issue">
    ${row.level === 'note' ? ''
    : `<span class="pill ${row.level === 'fail' ? 'miss' : 'half'}">${
      row.level === 'fail' ? '需处理' : '提醒'}</span>`}
    <span class="issue-text${row.level === 'note' ? ' hint' : ''}">${esc(row.text)}</span>
  </li>`).join('')}</ul>`;
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
    c.archive ? ['存档', num(c.archive.books)] : null,
    ['索引', c.lagBooks ? '落后 ' + num(c.lagBooks) + ' 本' : '已同步'],
    ['收件箱', num(c.inbox)],
  ].filter(Boolean);
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
    maintain: viewMaintain, ledger: viewLedger, retire: viewRetire, logs: viewLogs,
  };
  // 整页重画会丢掉日志框的滚动位置：画之前先记下，paintJobDetail 再落回去
  const logPre = document.querySelector('#job-log');
  if (logPre) {
    S.logScroll = logPre.scrollTop;
    S.logStick = logNearBottom(logPre);
  }
  const html = (map[S.tab] || viewOverview)();
  S.overviewHtml = S.tab === 'overview' ? html : '';
  view.innerHTML = html;
  afterRender();
}

function afterRender() {
  wireDrop();
  if (S.tab === 'logs' && S.logJob && S.logJob.id) openLog(S.logJob.id, true);
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
function nextPlanUid() {
  let max = 0;
  (S.plan || []).forEach((it) => {
    const m = /^B(\d+)$/.exec(String(it.uid || ''));
    if (m) max = Math.max(max, parseInt(m[1], 10));
  });
  return 'B' + String(max + 1).padStart(4, '0');
}

function planRow(item, index) {
  const st = STATUS_WORD[item.status] || ['', ''];
  const isDel = item.action === 'delete';
  const isRev = item.action === 'review';
  const isAdd = item.action === 'add';
  const isRep = item.action === 'replace';
  const note = String(item.note || '');
  const low = !isDel && !isRev && item.classify
    && (item.classify.needs_review || item.classify.conf1 < 0.45
      || note.indexOf('需人工确认') >= 0);
  const subjects = Array.isArray(item.subjects) ? item.subjects.join('/') : (item.subjects || '');
  const libSubjects = Array.isArray(item.target_subjects)
    ? item.target_subjects.join('/') : (item.target_subjects || '');
  const title = isAdd ? item.title : (isDel && !item.target_title ? '—' : (item.target_title || item.title || ''));
  const author = isAdd ? (item.author || '') : (isDel && !item.target_title ? '—' : (item.target_author || item.author || ''));
  const cls = isAdd ? (subjects || '其他') : (libSubjects || '其他');
  const rowClass = isDel ? ' class="del"' : (isRev ? ' class="review"' : (low ? ' class="low"' : ''));
  return `<tr data-row="${index}"${rowClass}>
    <td><select data-field="action" data-idx="${index}">
      ${isRev ? '<option value="review" selected>待核</option>' : ''}
      ${['add', 'replace', 'delete'].map((a) => `<option value="${a}"${a === item.action ? ' selected' : ''}>${ACTION_WORD[a]}</option>`).join('')}
    </select></td>
    <td class="wrap"><span class="${isDel ? 'strike' : ''}">${esc(item.file)}</span></td>
    <td class="nowrap">${esc(item.format || '')}<span class="small"> · ${fmtBytes(item.bytes)}</span></td>
    <td><input type="text" data-field="title" data-idx="${index}" value="${esc(title)}" size="18"${isAdd ? '' : ' readonly'}></td>
    <td class="wrap small">${esc(author || '—')}</td>
    <td>${isAdd
      ? `<input type="text" data-field="subjects" data-idx="${index}" value="${esc(subjects)}" size="12" placeholder="一级/二级">`
      : esc(cls || '—')}</td>
    <td>${(isDel || isRev) ? '—' : `<select data-field="retention" data-idx="${index}">
      ${['keep', 'review', 'drop'].map((r) => `<option value="${r}"${r === (item.retention || 'keep') ? ' selected' : ''}>${RETENTION_WORD[r]}</option>`).join('')}
    </select>`}</td>
    <td>${isAdd
      ? `<input type="text" data-field="uid" data-idx="${index}" value="${esc(item.uid || '')}" size="6">`
      : (isRep
        ? `<input type="text" data-field="uid" data-idx="${index}" value="${esc(item.uid || '')}" size="6" required>`
        : esc(item.uid || '—'))}</td>
    <td class="wrap small">${st[0] ? `<span class="pill ${st[1]}">${esc(st[0])}</span> ` : ''}${esc(note)}</td>
  </tr>`;
}

function planBlockers(plan) {
  const out = [];
  plan.forEach((it) => {
    const name = it.file || '（无文件）';
    if (it.action === 'review') out.push(name + '：待核，先选新增/换源/删除');
    else if (it.action === 'replace' && !String(it.uid || '').trim()) out.push(name + '：缺书号');
    if (it.action !== 'delete' && !String(it.title || '').trim()
      && !String(it.target_title || '').trim()) out.push(name + '：缺书名');
  });
  return out;
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
      <div class="drop-sub">EPUB、MOBI、AZW3、AZW、PDF、TXT、MD、DOCX、XLSX、XLS、PPTX，单个文件不超过 2 GB</div>
      <div class="drop-sub">不收 .doc / .ppt / .csv / .djvu（转不出可用的 md）</div>
    </div>
    <input type="file" id="up-file" class="visually-hidden" multiple
      accept=".epub,.pdf,.mobi,.azw3,.azw,.txt,.md,.docx,.xlsx,.xls,.pptx">
    <div class="uploading" id="up-box" hidden>
      <div class="progress"><i id="up-bar"></i></div>
      <p class="hint" id="up-note"></p>
    </div>
  </div>`;
}

function viewImport() {
  const inbox = S.inbox || [];
  const plan = S.plan;
  const bad = plan ? planBlockers(plan) : [];
  const html = `<h1>新书导入</h1>
    <p class="lead">收件箱里的文件先核对，再决定哪本进书库、哪本换源、哪本删掉。</p>
    ${S.importDone ? `<div class="banner"><span>已入库，接下来按格式去拆章或分诊。${S.importDocsJob ? '已自动接转文档 md。' : ''}</span>
      <button class="btn sm" data-act="goto" data-tab="split">去拆章</button>
      <button class="btn sm" data-act="goto" data-tab="pdf">去分诊 PDF</button>
      ${S.importDocsJob ? '<button class="btn sm" data-act="docs-goto">看转 md 进度</button>' : ''}</div>` : ''}
    ${S.legacyPlan ? `<div class="banner bad"><span>这份清单是旧版（冲突/跳过），按钮对不上，不能直接入库。重新生成一份再核对。</span>
      <button class="btn sm" data-write data-act="import-scan">重新生成</button></div>` : ''}
    <div class="grid c2 section">
      ${dropCard()}
      ${inboxCard(inbox, !!plan)}
    </div>
    <div class="section card">
      <div class="card-head"><h2>${esc(planHead())}</h2>
        <span class="row tight">
          <button class="btn sm" data-act="plan-defaults" ${plan ? '' : 'disabled'}>采用建议分类</button>
          <button class="btn sm${plan && !bad.length ? ' primary' : ''}" data-write data-act="import-apply" ${plan && !bad.length ? '' : 'disabled'}>确认入库</button>
        </span></div>
      ${plan ? planTable(plan) : '<p class="empty">收件箱里有书时，点「生成入库清单」。</p>'}
    </div>
    <div class="section" data-live="docs" id="docs-slot">${docsCard()}</div>`;
  // 刚整页画过，让 2.5 秒的重绘别白写一遍
  S.docsSig = docsSig();
  return html;
}

function planTable(plan) {
  const counts = plan.reduce((acc, row) => {
    acc[row.action] = (acc[row.action] || 0) + 1;
    return acc;
  }, {});
  const summary = ['add', 'replace', 'delete', 'review']
    .map((key) => ACTION_WORD[key] + ' ' + num(counts[key] || 0)).join('，');
  const bad = planBlockers(plan);
  const sample = bad.slice(0, 3).join('；');
  return `<p class="hint">${esc(summary)}</p>
    <div class="legend">
      <span><b>新增</b> 收件箱 → 书库：建一本新书</span>
      <span><b>换源</b> 收件箱 → 书库：只换已有书的主文件，书号不变</span>
      <span><b>删除</b> 收件箱 → 回收站：书库不动，可恢复</span>
      <span><b>待核</b> 先不动：决定后再入库</span>
    </div>
    ${bad.length ? `<div class="banner bad"><span>还有 ${num(bad.length)} 行没定：${esc(sample)}${bad.length > 3 ? ' …' : ''}</span></div>` : ''}
    <div class="tw"><table class="plan-table">
    <colgroup><col style="width:88px"><col style="width:23%"><col style="width:94px"><col style="width:17%">
      <col style="width:9%"><col style="width:12%"><col style="width:88px"><col style="width:72px"><col></colgroup>
    <thead><tr class="group"><th></th><th colspan="2">收件箱</th><th colspan="5">书库（入库后）</th><th></th></tr>
      <tr><th>动作</th><th>文件</th><th>格式 / 大小</th><th>书名</th><th>作者</th>
      <th>分类</th><th>长期保留</th><th>编号</th><th>说明</th></tr></thead>
    <tbody>${plan.map(planRow).join('')}</tbody></table></div>
    <p class="hint">「长期保留」说的是这本书在书库里长期怎么处理：选「不留」也照常入库，之后在删书页顶部的「不留的书」里处理。</p>
    <p class="hint">删除只把收件箱文件移进回收站，书库里的书和正文都不动。</p>`;
}

// ---- 文档转 md ---------------------------------------------------
// docx/xlsx/xls/pptx/txt 在入库后自动转成 md，md 原样复制。report.json 只有跑完才有，
// 跑的过程中只有 progress.json（每本更新一次）。
const DOC_FMT = /^(txt|md|docx|xlsx|xls|pptx)$/i;
const DOCS_STATE = {
  queued: ['排队', 'half'], running: ['转换中', 'half'],
  succeeded: ['完成', 'ok'], failed: ['失败', 'miss'],
  cancelled: ['已停', 'miss'], orphaned: ['失联', 'miss'],
};
const DOCS_ITEM = {
  done: ['完成', 'ok'], skipped: ['跳过', 'half'], failed: ['失败', 'miss'],
};
const DOCS_REASON_WORD = {
  'too-large': '文件太大', 'timeout': '转换超时',
  'convert-error': '转换出错', 'empty-output': '转出来是空的',
  'source-missing': '源文件不见了', 'source-changed': '源文件改过，要重转',
  'venv-missing': '转换环境没装好', 'unsupported': '格式不支持',
};
const DOCS_KEY = 'bookcorpus.docs-job';
const DOCS_DEAD = new Set();   // 存下的任务已经不在了：别再反复试同一个 id

function docsJobCached() {
  try { return sessionStorage.getItem(DOCS_KEY) || ''; } catch (err) { return ''; }
}

function docsJobRemember(id) {
  try {
    if (id) sessionStorage.setItem(DOCS_KEY, id);
    else sessionStorage.removeItem(DOCS_KEY);
  } catch (err) {
    /* 隐私模式不给存：这次会话就靠任务列表里最近一条 */
  }
}

// 库里还有多少 doc 格式没转 md：没有 docs 任务时，这张卡靠它决定露不露面
function docsBackfill() {
  return (S.pending || []).filter((row) => DOC_FMT.test(String(row.format || ''))).length;
}

function docsTitle(uid, items) {
  const hit = (items || []).find((it) => it.uid === uid);
  if (hit && hit.title) return hit.title;
  const row = (S.pending || []).find((r) => r.uid === uid);
  return (row && row.title) || '';
}

function docsReason(item) {
  const code = String(item.reason || '');
  const word = DOCS_REASON_WORD[code] || code;
  const error = String(item.error || '');
  if (!word) return error || '—';
  return error ? word + '：' + error : word;
}

async function loadDocs() {
  // 有缓存就按缓存取；没有就让服务端挑「最近一次真转过书的」，别拿补转 0 本的空任务顶掉明细
  const target = docsJobCached();
  if (!target && S.docsNone) return;   // 已确认没有 docs 任务，别每 2.5 秒打一次 404
  try {
    const body = await api('/api/docs/report' + (target ? '?job_id=' + encodeURIComponent(target) : ''));
    S.docs = body;
    S.docsNone = false;
    if (body && body.job_id) docsJobRemember(body.job_id);
  } catch (err) {
    S.docs = null;
    if (target) {
      // 存下的任务已经不在了：丢掉缓存，下一跳按任务列表或最近一次再来
      DOCS_DEAD.add(target);
      docsJobRemember('');
      return;
    }
    S.docsNone = true;
  }
}

function docsJobSet(id) {
  docsJobRemember(id);
  DOCS_DEAD.delete(String(id || ''));
  S.docsNone = false;
  S.docs = null;
  S.docsOpen = false;
}

// 局部重绘用的指纹：数据没变就不碰 DOM，不抢焦点
function docsSig() {
  const body = S.docs || {};
  const rep = body.report || null;
  const job = body.job_id ? (S.jobs || []).find((row) => row.id === body.job_id) : null;
  return JSON.stringify([
    body.job_id || '', body.state || '', body.progress || null,
    rep ? [rep.summary || {}, rep.scope || {}, rep.items || []] : null,
    docsBackfill(), job ? job.summary || '' : '', S.docsOpen,
  ]);
}

function paintDocs() {
  const slot = document.querySelector('[data-live="docs"]');
  if (!slot) return;
  const sig = docsSig();
  if (sig === S.docsSig) return;
  S.docsSig = sig;
  slot.innerHTML = docsCard();
}

function docsCard() {
  const body = S.docs;
  const pending = docsBackfill();
  if (!body && !pending) return '';   // 没任务也没待补转：整卡不出现
  const rep = (body && body.report) || null;
  const sum = (rep && rep.summary) || {};
  const scope = (rep && rep.scope) || {};
  const prog = (body && body.progress) || {};
  const items = (rep && Array.isArray(rep.items)) ? rep.items : [];
  const state = (body && body.state) || '';
  const stateWord = body ? (DOCS_STATE[state] || ['—', 'half']) : ['待补转', 'half'];
  const job = (body && body.job_id) ? (S.jobs || []).find((row) => row.id === body.job_id) : null;
  const running = state === 'queued' || state === 'running';
  const total = Number(sum.total || prog.total || 0);
  const finished = rep
    ? ['done', 'skipped', 'failed'].reduce((n, key) => n + (Number(sum[key]) || 0), 0)
    : Number(prog.done || 0);
  const share = total ? Math.min(100, Math.round(finished / total * 100)) : 0;
  const current = String(prog.current || '');
  const failed = items.filter((it) => it.status === 'failed');
  const nfail = Number(sum.failed) || 0;
  const nskip = Number(sum.skipped) || 0;
  const nnew = Number(scope.new) || 0;
  const nback = Number(scope.backfill) || 0;

  const bits = ['本次新书 ' + num(nnew)];
  if (nback) bits.push('库内补转 ' + num(nback));
  bits.push('跳过 ' + num(nskip), '失败 ' + num(nfail));

  const bar = total
    ? `<div class="progress-label"><span>${esc(stateWord[0])} ${num(finished)} / ${num(total)} 本</span>
        <span>${share}%</span></div>
      <div class="progress"><i style="width:${share}%"></i></div>`
    : (running ? '<p class="hint">已排队，等前面的任务结束就开始。</p>' : '');
  const now = current
    ? `<p class="hint">正在转 <span class="mono">${esc(current)}</span>${docsTitle(current, items) ? ' · ' + esc(docsTitle(current, items)) : ''}</p>`
    : '';
  const scopeLine = rep
    ? `<p class="hint">${esc(bits.join(' · '))}</p>`
    : (pending ? `<p class="hint">库里还有 ${num(pending)} 本 doc 格式没转 md。</p>` : '');
  const badBanner = state === 'failed' && !rep
    ? `<div class="banner bad"><span>转换没过：${esc(summaryText(job && job.summary) || '看任务日志')}</span>
        <button class="btn sm" data-act="goto" data-tab="logs">看日志</button></div>`
    : '';
  const failTable = failed.length ? `<div class="tw"><table>
      <thead><tr><th>编号</th><th>书名</th><th>格式</th><th>原因</th><th></th></tr></thead>
      <tbody>${failed.map((it) => `<tr>
        <td class="mono">${esc(it.uid)}</td>
        <td class="wrap">${esc(it.title || docsTitle(it.uid, items) || '—')}</td>
        <td>${esc(it.format || '')}</td>
        <td class="wrap small">${esc(docsReason(it))}</td>
        <td><button class="btn sm" data-write data-act="docs-retry" data-uid="${esc(it.uid)}">重试</button></td>
      </tr>`).join('')}</tbody></table></div>` : '';
  const rows = items.length ? `<details class="adv" data-docs="items"${S.docsOpen ? ' open' : ''}>
      <summary>逐本明细（${num(items.length)} 本）</summary>
      <div class="tw"><table>
        <thead><tr><th>编号</th><th>书名</th><th>格式</th><th>状态</th>
          <th class="num">耗时</th><th class="num">字数</th><th>说明</th></tr></thead>
        <tbody>${items.map((it) => {
          const st = DOCS_ITEM[it.status] || [it.status || '—', 'half'];
          const why = it.status === 'failed' ? docsReason(it) : (it.md ? 'md 已落地' : '');
          return `<tr>
            <td class="mono">${esc(it.uid)}</td>
            <td class="wrap">${esc(it.title || '—')}</td>
            <td>${esc(it.format || '')}</td>
            <td><span class="pill ${st[1]}">${esc(st[0])}</span></td>
            <td class="num">${it.elapsed_ms ? (Number(it.elapsed_ms) / 1000).toFixed(1) + ' 秒' : '—'}</td>
            <td class="num">${it.chars ? num(it.chars) : '—'}</td>
            <td class="wrap small">${esc(why)}</td>
          </tr>`;
        }).join('')}</tbody></table></div>
    </details>` : '';

  return `<div class="card" id="docs-card">
    <div class="card-head"><h2>文档转 md <span class="pill ${stateWord[1]}">${esc(stateWord[0])}</span></h2>
      <span class="row tight">
        ${body && body.job_id ? `<span class="hint mono">${esc(body.job_id)}</span>` : ''}
        <button class="btn sm" data-act="docs-refresh">刷新</button>
      </span></div>
    <p class="hint">TXT / DOCX / XLSX / XLS / PPTX 入库后转成 md，源文件是 md 的原样复制。</p>
    ${badBanner}
    ${bar}
    ${now}
    ${scopeLine}
    ${failTable}
    ${rows}
    <div class="toolbar">
      <button class="btn" data-write data-act="docs-backfill">补转库里遗漏的</button>
      <span class="hint">把书库里 doc 格式还没转 md 的书整体补一遍，已经转好的不动。</span>
    </div>
  </div>`;
}

async function submitDocs(payload, note) {
  try {
    const body = await post('/api/docs/run', payload || {});
    docsJobSet(body.job_id);
    toast(note || ('已排队：' + body.job_id));
    await loadDocs();
    await loadJobs();
    paintDocs();
    watchJob(body.job_id, afterJob).then(async (detail) => {
      await loadDocs();
      paintDocs();
      if (detail && detail.job) toast(jobNote(detail.job), jobBad(detail.job));
    }).catch(() => {});
  } catch (err) {
    toast(err.message, true);   // 409「有写任务在跑」原样给出来
  }
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
  // 暂缓（额度用尽）跟失败、未开跑是三回事，分开数
  const paused = S.books.filter((row) => row.progress === 'deferred').length;
  const staged = S.books.filter((row) => row.progress === 'staged').length;
  const note = {
    convert: '已确认处置，可以直接转',
    untriaged: '还没看过，先分诊',
    gallery: recheck ? '其中 ' + num(recheck) + ' 本标了复看' : '图册不转文字',
    ja: stuck ? num(stuck) + ' 本上次失败' : '日文原版暂缓处理',
  };
  const extra = [];
  if (paused) extra.push(num(paused) + ' 本暂缓·待额度');
  if (staged) extra.push(num(staged) + ' 本待确认');
  return `<div class="strip">${STRIP_TAG.map((key) => `<a class="strip-cell" href="#pdf-list" data-act="pdf-jump" data-tag="${esc(key)}">
    <div class="strip-name">${esc(PICK_WORD[key])}</div>
    <div class="strip-read">${num(S.bookCounts[key] || 0)} 本 · ${num(pages[key] || 0)} 页</div>
    <div class="note">${esc(note[key])}</div>
  </a>`).join('')}</div>${extra.length
    ? `<p class="hint">清单里另有 ${esc(extra.join('、'))}，不算未开跑。</p>` : ''}`;
}

function quotaLine(batch) {
  const today = S.pdfToday || { used: 0, cap: 20000 };
  const part = batch.partition || {};
  const day = /(\d{4}-\d{2}-\d{2})$/.exec(String(batch.name || ''));
  const archived = !!batch.name && Number(batch.worklist) > 0;
  const old = archived
    ? `${esc(day ? day[1] : batch.name)} 已封存 · ${num(batch.worklist)} 本里 ${num(part.landed)} 落地、${num(part.left)} 未落地（${num(part.failed)} 失败 / ${num(part.staged)} 待复核 / ${num(part.deferred)} 暂缓·待额度 / ${num(part.untouched)} 未开跑）`
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
  if (c.gallery) bits.push('存档·图册 ' + num(c.gallery) + ' 本');
  if (c.ja) bits.push('存档·非中英文 ' + num(c.ja) + ' 本');
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
    if (row.disposition === 'untriaged' && row.suggestion && !row.suggestion.manual) {
      const conf = row.suggestion.confidence === null
        || row.suggestion.confidence === undefined ? ''
        : ' ' + Number(row.suggestion.confidence).toFixed(2);
      marks.push(`<span class="hint">建议 ${esc(routeWord(row.suggestion.route))}${esc(conf)}</span>`);
    }
    // 模型判不了的书带 manual: true：不当可确认的建议，也不该再进分诊队列
    if (row.suggestion && row.suggestion.manual) {
      marks.push(`<span class="hint" title="${esc((row.suggestion || {}).reason || '')}">模型：需人工判断</span>`);
    }
    if (row.route) marks.push(`<span class="hint">${esc(routeWord(row.route))}</span>`);
    if (row.recheck) marks.push('<span class="hint">复看</span>');
    const pages = row.pages === null || row.pages === undefined ? '—' : num(row.pages);
    const note = pickNote(row);
    const act = row.disposition === 'untriaged' && row.suggestion && !row.suggestion.manual
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
  if (row.progress === 'deferred') return '额度用尽，下一批接着跑';
  if (row.suggestion && row.suggestion.manual) {
    return '需人工判断' + (row.suggestion.reason ? '：' + row.suggestion.reason : '');
  }
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
  // 需人工判断的没有可照发的 route，不能进「按建议确认」
  const suggests = S.books.filter((row) => S.pick.has(row.uid) && row.suggestion
    && !row.suggestion.manual).length;
  return `<b>已选 ${num(c.total)} 本</b>
    <span class="hint">可转 ${num(c.convert)} · 存档·图册 ${num(c.gallery)} · 存档·非中英文 ${num(c.ja)} · 未分诊 ${num(c.untriaged)}</span>
    <button class="btn sm" data-act="pick-suggest-all"${suggests ? '' : ' disabled'}>按建议确认 ${num(suggests)} 本</button>
    <button class="btn sm" data-act="pick-all-convert"${converts ? '' : ' disabled'}>全选可转的</button>
    <button class="btn sm" data-act="pick-clear"${c.total ? '' : ' disabled'}>清空</button>
    <span class="spacer"></span>
    <label class="check" title="按全书合计，含文本层抽取、OCR 与强制转的存档书">最多转 <input type="number" id="pdf-limit" value="${S.pdfLimit}" min="0" style="width:64px"> 本（合计）</label>
    <label class="check">同时处理 <input type="number" id="pdf-workers" value="${S.pdfWorkers || 3}" min="1" max="8" style="width:64px"> 本</label>
    <button class="btn primary" data-write data-act="pdf-run"${c.total ? '' : ' disabled'}>开始转写</button>`;
}

function viewPdfList() {
  const counts = S.bookCounts || { convert: 0, gallery: 0, ja: 0, untriaged: 0 };
  const total = (counts.convert || 0) + (counts.gallery || 0) + (counts.ja || 0)
    + (counts.untriaged || 0);
  const rows = pickRows();
  const lead = '还没有正文的 PDF 都在这里，共 ' + num(total) + ' 本：可转 '
    + num(counts.convert || 0) + ' 本、存档·图册 ' + num(counts.gallery || 0) + ' 本、存档·非中英文 '
    + num(counts.ja || 0) + ' 本、未分诊 ' + num(counts.untriaged || 0) + ' 本。存档的书按政策不转不删。';
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

// 关键字边打边筛：只重画表格与计数，整页 render 会丢焦点。
// 上限是全书合计（文本层、OCR、强制转的存档书都占名额），勾选变了就跟着改
function syncPdfLimit() {
  const total = pickCounts().total;
  if (total === S.pdfLimitFrom) return;
  S.pdfLimitFrom = total;
  S.pdfLimit = total;
}

function paintPick() {
  syncPdfLimit();
  const rows = pickRows();
  const body = $('#pick-body');
  if (body) body.innerHTML = pickRowHtml(rows);
  const bar = $('#pick-bar');
  if (bar) bar.innerHTML = pickBar();
  const note = $('#pick-note');
  if (note) note.textContent = '当前显示 ' + num(rows.length) + ' 本。';
}

// 进度条只在任务真在跑时出现：任务记录是唯一来源，刷新页面也还在
function liveTriage() {
  const rows = S.jobs.filter((row) => row.kind === 'pdf.triage'
    && /^批量分诊/.test(row.title || '')
    && (row.state === 'queued' || row.state === 'running'));
  return rows.reduce((best, row) => (!best || (row.seq || 0) > (best.seq || 0)
    ? row : best), null);
}

function paintTriageProgress() {
  const box = $('#triage-progress');
  if (box) box.innerHTML = triageProgress();
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
  const job = liveTriage();
  if (!job) return '';
  const steps = job.steps || [];
  const done = steps.filter((s) => s.state === 'ok' || s.state === 'fail').length;
  const pct = steps.length ? Math.min(100, Math.round(done / steps.length * 100)) : 0;
  const word = job.state === 'queued' ? '排队' : '分诊中';
  return `<div class="progress-label"><span>${word} ${num(done)} / ${num(steps.length)} 本</span><span>${pct}%</span></div>
    <div class="progress"><i style="width:${pct}%"></i></div>`;
}

function triageCard() {
  const split = S.pdfUntriaged;
  const untriaged = S.books.filter((row) => row.disposition === 'untriaged');
  const total = split ? split.books : untriaged.length;
  const have = untriaged.filter((row) => row.suggestion).length;
  // 模型判不了的书带 manual: true：也算「出过建议」，不再送分诊，不能进 todo
  const manual = untriaged.filter((row) => row.suggestion && row.suggestion.manual);
  const todo = Math.max(0, total - have);
  const reasons = [];
  manual.forEach((row) => {
    const why = String((row.suggestion || {}).reason || '').trim();
    if (why && reasons.indexOf(why) < 0 && reasons.length < 3) reasons.push(why);
  });
  const manualLine = manual.length
    ? '其中 ' + num(manual.length) + ' 本模型：需人工判断'
      + (reasons.length ? '（' + reasons.join('；') + '）' : '')
      + '，这些不再送分诊，请在清单里逐本定处置。'
    : '';
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
  const auto = have - manual.length;
  const second = have
    ? (auto ? '其中 ' + num(auto) + ' 本已经出了建议，等你在清单里确认。' : '') + manualLine
    : '分诊只看指标与三张缩略图，不出正文。出的建议在清单页逐行确认，也可以一次按建议确认。';
  return `<div class="card">
    <h2>批量分诊</h2>
    <p class="small">${esc(lead)}</p>
    <p class="hint">${esc(second)}</p>
    <button class="btn primary" data-act="pdf-triage-batch"${todo && !liveTriage() ? '' : ' disabled'}>开始分诊 ${num(todo)} 本</button>
    <div id="triage-progress" style="margin-top:12px">${triageProgress()}</div>
  </div>`;
}

function runCard() {
  return `<div class="card">
    <h2>确认后转 md</h2>
    ${pdfPickSummary()}
    <label class="field"><span>最多转几本（合计）</span>
      <input type="number" id="pdf-limit" value="${S.pdfLimit}" min="0"></label>
    <label class="field"><span>同时处理</span>
      <input type="number" id="pdf-workers" value="${S.pdfWorkers || 3}" min="1" max="8"></label>
    <button class="btn primary" data-write data-act="pdf-run">开始转写</button>
    <p class="hint">最多转几本按全书合计：文本层抽取、OCR 与强制转的存档书都占名额，填 0 表示不限。存档的书（图册、非中英文）默认不动，要转得逐本点会再问一次；未分诊的书只送去分诊，不出正文。</p>
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
  const info = statusIssues();
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
    <div class="section card" id="status-issues">
      <h2>体检提醒</h2>
      ${issueList(info)}
      ${info.at ? `<p class="hint">体检于 ${esc(fmtTime(info.at).slice(5))}</p>` : ''}
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
        <p><a class="btn sm" href="/api/file?path=${encodeURIComponent('build/书目总表.xlsx')}">书目总表（Excel）</a>
           <a class="btn sm" href="/api/file?path=${encodeURIComponent('build/书目总表.csv')}">书目总表（CSV）</a>
           <a class="btn sm" href="/api/file?path=${encodeURIComponent('tables/分类预填.csv')}">分类预填表</a></p>
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
  const drop = S.retireDrop || [];
  return `<h1>删书</h1>
    <p class="lead">删书是把书移出流程。原书留在冷宫，阅读用的 Markdown、页面和索引记录会删掉。</p>
    <div class="section card">
      <div class="card-head"><h2>不留的书 <span class="hint">${num(drop.length)} 本</span></h2>
        <button class="btn sm" data-act="retire-drop-refresh">刷新</button></div>
      <p class="hint">入库或账表里标成「不留」的书，书还在库里，只是等着移出。账表刚改完要先跑维护，这里才看得到。</p>
      ${drop.length ? `<div class="tw"><table><thead><tr><th>编号</th><th>书名</th>
        <th>分类</th><th class="num">章</th><th class="num">源文件</th><th class="num">奖项</th><th></th></tr></thead>
        <tbody>${drop.map((b) => `<tr>
          <td class="mono">${esc(b.uid)}</td>
          <td class="wrap">${esc(b.title)}</td>
          <td class="wrap small">${esc([b.subject1, b.subject2].filter(Boolean).join('/') || '其他')}</td>
          <td class="num">${esc(b.md && b.md.chapters !== undefined ? b.md.chapters : 0)}</td>
          <td class="num">${(b.sources || []).length}</td>
          <td class="num">${(b.awards || []).length}</td>
          <td><button class="btn sm" data-act="retire-preview" data-uid="${esc(b.uid)}">预览移出</button></td>
        </tr>`).join('')}</tbody></table></div>` : '<p class="empty">没有标记为不留的书。入库时选「不留」，或在账表改保留再跑维护，这里就会出现。</p>'}
    </div>
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
          <td class="wrap small">${esc([b.subject1, b.subject2].filter(Boolean).join('/') || '其他')}</td>
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
// ---- 账表 -----------------------------------------------------------
async function loadLedger() {
  try {
    S.ledger = await api('/api/ledger');
  } catch (err) {
    S.ledger = null;
    toast(err.message, true);
  }
}

const LEDGER_ROW_FIELDS = [
  ['uid', '编号', 'readonly'], ['一级', '一级', ''],
  ['二级', '二级', ''], ['作者层', '作者层', 'narrow'],
  ['保留', '保留', 'select'], ['正式书名', '正式书名', ''],
  ['备注', '备注', ''],
];
const LEDGER_DEL = { main: '概念', broad: '宽词', action: '动作词' };
const LEDGER_CONCEPT_FIELDS = {
  main: [['key', '键', ''], ['aliases', '同义词（逗号分隔）', ''], ['exclude', '排除词（逗号分隔，「无」表示不排）', ''], ['note', '说明', '']],
  broad: [['key', '键', ''], ['aliases', '同义词', ''], ['exclude', '排除词', ''], ['note', '说明', '']],
  // 动作词行没有 key，只认 term（lib_common 的 动作词 表就是 term/kind/note）
  action: [['term', '词', ''], ['kind', ' 类型（动作/事件…）', ''], ['note', '说明', '']],
};

const LEDGER_PENDING = '待复核';

function ledgerPending(row) {
  return String((row && row.备注) || '').includes(LEDGER_PENDING);
}

function ledgerRowFiltered() {
  let rows = (S.ledger && S.ledger.ledger) || [];
  if (S.ledgerPendingOnly) rows = rows.filter(ledgerPending);
  const q = (S.ledgerQ || '').trim().toLowerCase();
  if (!q) return rows;
  return rows.filter((r) => [r.uid, r.正式书名, r.一级, r.二级, r.备注]
    .some((v) => (v || '').toLowerCase().includes(q)));
}

function ledgerRowHtml(row) {
  const editing = S.ledgerEdit && S.ledgerEdit.type === 'row' && S.ledgerEdit.uid === row.uid;
  if (!editing) {
    return `<tr>
      <td><input type="checkbox" data-act="ledger-pick" value="${esc(row.uid)}"${S.ledgerPick.has(row.uid) ? ' checked' : ''}></td>
      <td class="num">${esc(row.uid)}</td>
      <td>${esc(row.一级)}</td>
      <td>${esc(row.二级)}</td>
      <td class="num">${esc(row.作者层)}</td>
      <td>${esc(RETENTION_WORD[row.保留] || row.保留)}</td>
      <td class="wrap">${esc(row.正式书名)}</td>
      <td class="wrap small">${esc(row.备注)}</td>
      <td class="nowrap">${ledgerPending(row)
    ? `<button class="btn sm primary" data-write data-act="ledger-row-confirm" data-uid="${esc(row.uid)}">同意</button> ` : ''}<button class="btn sm" data-act="ledger-row-edit" data-uid="${esc(row.uid)}">改</button>
        <button class="btn sm miss" data-write data-act="ledger-row-delete" data-uid="${esc(row.uid)}">移出</button></td>
    </tr>`;
  }
  const f = (name, label) => {
    const v = row[name] == null ? '' : String(row[name]);
    if (name === '保留') {
      return `<select id="led-f-保留">${
        ['keep', 'review', 'drop'].map((k) => `<option value="${k}"${v === k ? ' selected' : ''}>${RETENTION_WORD[k]}</option>`).join('')}</select>`;
    }
    return `<input id="led-f-${esc(name)}" value="${esc(v)}"${name === 'uid' ? ' readonly' : ''}>`;
  };
  return `<tr class="editing">
    <td></td>
    <td>${f('uid')}</td><td>${f('一级')}</td><td>${f('二级')}</td>
    <td>${f('作者层')}</td><td>${f('保留')}</td>
    <td>${f('正式书名')}</td><td class="wrap">${f('备注')}</td>
    <td class="nowrap"><button class="btn sm primary" data-write data-act="ledger-row-save" data-uid="${esc(row.uid)}">存</button>
      <button class="btn sm" data-act="ledger-edit-clear">取消</button></td>
  </tr>`;
}

function ledgerConceptHtml(which, row, force) {
  const fields = LEDGER_CONCEPT_FIELDS[which];
  const keyOf = (r) => (which === 'action' ? r.term : r.key);
  const edit = !!force || (S.ledgerEdit && S.ledgerEdit.type === 'concept'
    && S.ledgerEdit.which === which && String(S.ledgerEdit.key)
      === String(keyOf(row) == null ? '' : keyOf(row)));
  if (!edit) {
    return `<tr>
      ${(which === 'action')
    ? `<td class="num">${esc(row.term)}</td><td>${esc(row.kind)}</td><td class="wrap small">${esc(row.note)}</td>`
    : `<td class="num">${esc(row.key)}</td><td class="wrap">${esc(row.aliases)}</td><td class="wrap small">${esc(row.exclude)}</td><td class="wrap small">${esc(row.note)}</td>`}
      <td class="nowrap"><button class="btn sm" data-act="ledger-concept-edit" data-which="${which}" data-key="${esc(keyOf(row) || '')}">改</button>
        <button class="btn sm miss" data-write data-act="ledger-concept-delete" data-which="${which}" data-key="${esc(keyOf(row) || '')}">删</button></td>
    </tr>`;
  }
  const val = which === 'action'
    ? { term: row.term || '', kind: row.kind || '', note: row.note || '' }
    : { key: row.key || '', aliases: row.aliases || '', exclude: row.exclude || '', note: row.note || '' };
  const isNew = !!(S.ledgerEdit && S.ledgerEdit.isNew);
  const readonly = (name) => (isNew || !(name === 'key' || name === 'term') ? '' : ' readonly');
  const inputs = fields.map(([name, label]) =>
    `<td><input id="led-c-${esc(name)}" value="${esc(val[name] || '')}"${readonly(name)} title="${esc(label.trim())}"></td>`);
  return `<tr class="editing">${inputs.join('')}
    <td class="nowrap"><button class="btn sm primary" data-write data-act="ledger-concept-save" data-which="${which}">存</button>
      <button class="btn sm" data-act="ledger-edit-clear">取消</button></td>
  </tr>`;
}

function viewLedger() {
  const L = S.ledger;
  if (!L) {
    return `<div class="list"><p>账表还没加载。</p></div>`;
  }
  const rows = ledgerRowFiltered();
  const pendingRows = rows.filter(ledgerPending);
  const picked = pendingRows.filter((r) => S.ledgerPick.has(r.uid)).length;
  const allPicked = pendingRows.length > 0 && picked === pendingRows.length;
  const shown = rows.length !== L.ledger.length
    ? `<p class="hint">筛选到 ${num(rows.length)} / ${num(L.ledger.length)} 行。</p>` : '';
  const pick = (list, which) => {
    // 「加一条」没有真行可选：先插一行空的带输入框的表单，不然存按钮读不到 #led-c-*
    const adding = !!(S.ledgerEdit && S.ledgerEdit.type === 'concept'
      && S.ledgerEdit.isNew && S.ledgerEdit.which === which);
    const body = (adding ? [null].concat(list) : list)
      .map((row) => (row === null
        ? ledgerConceptHtml(which, {}, true)
        : ledgerConceptHtml(which, row))).join('');
    return `
    <div class="card">
      <div class="card-head"><h2>${LEDGER_DEL[which]}（${num(list.length)}）</h2>
        <span class="row tight">
          <button class="btn sm" data-act="ledger-concept-edit" data-which="${which}" data-key="">加一条</button>
          <button class="btn sm" data-act="ledger-load">刷新</button></span></div>
      <div class="tw"><table class="ledger-table">
        <thead><tr>${which === 'action'
    ? '<th>词</th><th>类型</th><th>说明</th>'
    : '<th>键</th><th>同义词</th><th>排除词</th><th>说明</th>'}
          <th style="width:120px"></th></tr></thead>
        <tbody>${body}</tbody>
      </table></div>
    </div>`;
  };
  return `
    <div class="list">
      <div class="section card">
        <div class="card-head"><h2>分类表（${num(L.ledger.length)} 行 · ${esc(L.ledger_db || 'tables/ledger.db')}）</h2>
          <span class="row tight">
            <input id="ledger-q" value="${esc(S.ledgerQ || '')}" placeholder="编号 / 书名 / 分类 / 备注">
            <label class="check"><input type="checkbox" data-act="ledger-pending-only"${S.ledgerPendingOnly ? ' checked' : ''}> 只看待复核</label>
            <label class="check"><input type="checkbox" data-act="ledger-pick-all"${allPicked ? ' checked' : ''}${pendingRows.length ? '' : ' disabled'}> 全选待复核</label>
            <button class="btn sm primary" data-write data-act="ledger-confirm-pick"${picked ? '' : ' disabled'}>同意选中 ${num(picked)} 本</button>
            <button class="btn sm" data-act="ledger-load">刷新</button></span></div>
        ${shown}
        <div class="tw"><table class="ledger-table">
          <thead><tr><th style="width:28px"></th><th>编号</th><th>一级</th><th>二级</th><th>作者层</th><th>保留</th><th>正式书名</th><th>备注</th><th style="width:170px"></th></tr></thead>
          <tbody>${rows.length ? rows.map(ledgerRowHtml).join('')
    : `<tr><td colspan="9" class="hint">${S.ledgerPendingOnly ? '没有备注带「待复核」的行了。' : '没有匹配的行。'}</td></tr>`}</tbody>
        </table></div>
        <p class="hint">编号行是入库给的，加书走「新书导入」；这里改的是已有行——顺序、保留与分类。改完人账，去「维护」跑一次收尾，书目库与总表才会跟上。备注里带「待复核」的行多一个「同意」：点了把这三个字改成「分类确认 日期」，其余照旧；表头「只看待复核」一勾，已确认的行就不在表里了。</p>
      </div>
      ${pick(L.concepts || [], 'main')}
      ${pick(L.broad || [], 'broad')}
      ${pick(L.actions || [], 'action')}
      <p class="hint">词表改键或加词只影响之后跑的检索与索引；要影响已建剖面，得在「维护」里重建索引（--concepts 步骤）。</p>
    </div>`;
}

function logRows() {
  return S.jobs.filter((j) => !S.filter || j.state === S.filter);
}

function logRowsHtml(rows) {
  return rows.length ? rows.map((j) => `<tr>
        <td class="nowrap">${esc(fmtTime(j.created))}</td>
        <td class="nowrap">${esc(KIND_WORD[j.kind] || j.kind)}</td>
        <td class="wrap">${esc(summaryText(j.title))}</td>
        <td><span class="pill">${esc(JOB_WORD[j.state] || j.state)}</span></td>
        <td class="num">${j.started && j.ended ? esc(seconds(j.started, j.ended)) : '—'}</td>
        <td class="wrap small">${esc(summaryText(j.summary))}</td>
        <td><button class="btn sm" data-act="job-open" data-id="${esc(j.id)}">详情</button></td>
      </tr>`).join('') : '<tr><td colspan="7" class="empty">没有任务。</td></tr>';
}

function viewLogs() {
  const rows = logRows();
  const states = ['', 'queued', 'running', 'awaiting_review', 'succeeded', 'failed',
    'cancelled', 'orphaned'];
  const body = logRowsHtml(rows);
  S.logRowsHtml = body;
  return `<h1>日志</h1>
    <p class="lead">每个任务按自己的检查判定成败，日志里的密钥会隐藏。</p>
    <div class="section card">
      <div class="toolbar">
        <label class="check">状态 <select id="log-filter">
          ${states.map((s) => `<option value="${s}"${s === S.filter ? ' selected' : ''}>${s ? JOB_WORD[s] : '全部'}</option>`).join('')}
        </select></label>
        <button class="btn sm" data-act="jobs-refresh">刷新</button>
        <span class="hint" id="log-count">共 ${num(rows.length)} 个任务</span>
      </div>
      <div class="tw"><table><thead><tr><th>创建</th><th>类型</th><th>标题</th><th>状态</th>
        <th class="num">耗时</th><th>摘要</th><th></th></tr></thead>
      <tbody id="log-rows">${body}</tbody></table></div>
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

function stepsHtml(detail) {
  const steps = (detail && detail.steps) || [];
  return steps.length ? steps.map((s) => `<tr><td class="num">${s.seq + 1}</td>
        <td>${esc(s.name)}</td><td>${esc(STEP_WORD[s.state] || s.state)}</td>
        <td class="wrap small">${esc(stepNote(s.detail))}</td>
        <td class="nowrap">${esc(fmtTime(s.started))}</td></tr>`).join('')
    : '<tr><td colspan="5" class="empty">没有步骤</td></tr>';
}

function commandsHtml(detail) {
  const command = ((detail && detail.commands) || []).map((c) => (c.argv || []).join(' ')).join('\n');
  return `<pre class="log">${esc(command || '没有记录')}</pre>`;
}

function jobDetail() {
  const job = S.logJob;
  if (!job) return '<p class="empty">点上面表格里的「详情」，这里会显示步骤和日志。</p>';
  const detail = S.logDetail || {};
  const running = ['queued', 'running'].indexOf(job.state) >= 0;
  return `<div class="card-head"><h2>${esc(summaryText(job.title))}</h2>
      <span class="row tight">
        <span class="pill" id="job-state">${esc(JOB_WORD[job.state] || job.state)}</span>
        <button class="btn sm" id="job-cancel" data-act="job-cancel" data-id="${esc(job.id)}" style="${running ? '' : 'display:none'}">停止</button>
        <button class="btn sm" data-write data-act="job-rerun" data-id="${esc(job.id)}">重跑</button>
        <a class="btn sm" href="/api/file?path=${encodeURIComponent('工作区/运维/jobs/' + job.id + '/log.txt')}">下载日志</a>
      </span></div>
    <p class="hint" id="job-meta">任务号 ${esc(job.id)} · ${esc(fmtTime(job.started))} → ${esc(fmtTime(job.ended))}</p>
    <p class="small" id="job-summary">${esc(summaryText(job.summary))}</p>
    <details class="adv" id="job-commands">
      <summary>运行命令</summary>
      ${commandsHtml(detail)}
    </details>
    <div class="tw" id="job-steps"><table><thead><tr><th>#</th><th>步骤</th><th>状态</th><th>说明</th><th>开始</th></tr></thead>
      <tbody>${stepsHtml(detail)}</tbody></table></div>
    <h3 style="margin-top:14px">日志尾部 200 行</h3>
    <pre class="log" id="job-log" data-painted="0">${esc(detail.log_tail || '')}</pre>`;
}

// ---- 日志页就地刷新 -------------------------------------------------
// 轮询每 2.5 秒跑一次：只改文本与步骤表，不整块换 #job-detail 的 innerHTML，
// 否则选中的文字、滚到一半的日志每两秒被重置一次（task-1 F3/F7）。
function logNearBottom(pre) {
  return pre.scrollHeight - pre.scrollTop - pre.clientHeight < 8;
}

function paintJobDetail() {
  const job = S.logJob;
  const box = document.querySelector('#job-detail');
  if (!job || !box) return;
  const detail = S.logDetail || {};
  let pre = box.querySelector('#job-log');
  const fresh = !pre || pre.dataset.painted !== '1';
  if (fresh || S.logOpenId !== job.id) {
    const switched = S.logOpenId !== job.id;
    box.className = 'section card';
    box.innerHTML = jobDetail();
    pre = box.querySelector('#job-log');
    if (!pre) return;
    if (switched) S.logScroll = 0;
    S.logOpenId = job.id;
    S.logStepsFp = '';
    S.logCmdFp = '';
    S.follow = switched ? true : S.logStick;
    pre.dataset.painted = '1';
    pre.onscroll = () => { S.follow = logNearBottom(pre); };
    pre.textContent = detail.log_tail || '';
    pre.scrollTop = S.follow ? pre.scrollHeight : (S.logScroll || 0);
  }
  // 结构已经在了：状态、摘要、步骤、命令、日志尾部一律就地改
  const stateEl = box.querySelector('#job-state');
  const word = JOB_WORD[job.state] || job.state;
  if (stateEl && stateEl.textContent !== word) stateEl.textContent = word;
  const cancel = box.querySelector('#job-cancel');
  if (cancel) {
    const running = ['queued', 'running'].indexOf(job.state) >= 0;
    cancel.style.display = running ? '' : 'none';
  }
  const meta = box.querySelector('#job-meta');
  if (meta) {
    const text = '任务号 ' + job.id + ' · ' + fmtTime(job.started) + ' → ' + fmtTime(job.ended);
    if (meta.textContent !== text) meta.textContent = text;
  }
  const sumEl = box.querySelector('#job-summary');
  if (sumEl) {
    const text = summaryText(job.summary);
    if (sumEl.textContent !== text) sumEl.textContent = text;
  }
  const steps = box.querySelector('#job-steps');
  const stepsBody = stepsHtml(detail);
  if (steps && S.logStepsFp !== stepsBody) {
    S.logStepsFp = stepsBody;
    const tb = steps.querySelector('tbody');
    if (tb) tb.innerHTML = stepsBody;
  }
  const cmdBox = box.querySelector('#job-commands');
  const cmdBody = commandsHtml(detail);
  if (cmdBox && S.logCmdFp !== cmdBody) {
    S.logCmdFp = cmdBody;
    cmdBox.innerHTML = '<summary>运行命令</summary>' + cmdBody;
  }
  if (pre) {
    const stick = S.follow && logNearBottom(pre);
    const top = pre.scrollTop;
    const text = detail.log_tail || '';
    if (pre.textContent !== text) pre.textContent = text;
    pre.scrollTop = stick ? pre.scrollHeight : top;
  }
}

function paintLogRows() {
  const tb = document.querySelector('#log-rows');
  if (!tb) return;
  const rows = logRows();
  const html = logRowsHtml(rows);
  if (html !== S.logRowsHtml) {
    S.logRowsHtml = html;
    tb.innerHTML = html;
  }
  const count = document.querySelector('#log-count');
  if (count) count.textContent = '共 ' + num(rows.length) + ' 个任务';
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
  if (S.tab !== 'logs') return;
  const sel = S.logJob && S.logJob.id
    ? S.jobs.filter((row) => row.id === S.logJob.id)[0] : null;
  if (sel && S.logJob.state && sel.state !== S.logJob.state) {
    // 选中的任务换了状态：先把新状态落到缓存，再把表格与详情一起重画，
    // 否则拉详情失败时每 2.5 秒都会因为「状态不同」重画一次
    S.logJob = Object.assign({}, S.logJob, {
      state: sel.state, summary: sel.summary, started: sel.started, ended: sel.ended,
    });
    render();
    return;
  }
  paintLogRows();
  if (!sel && S.logJob && S.logJob.id) await openLog(S.logJob.id, true);
  else if (sel && ['queued', 'running'].indexOf(sel.state) >= 0) await openLog(sel.id, true);
}

async function loadPending() {
  try {
    const body = await api('/api/pending');
    S.pending = body.items || [];
  } catch (err) {
    S.pending = [];
  }
  // 与 PDF 勾选（S.pick）同口径：书不在待拆清单里了，勾选也跟着去掉，
  // 不然拆章会把过期 uid 发进新批次
  const known = new Set(S.pending.map((row) => row.uid));
  Array.from(S.selected).forEach((uid) => {
    if (!known.has(uid)) S.selected.delete(uid);
  });
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
  S.plan = normalizePlan(body);
  S.planJob = jobId;
  S.planDirty = false;
}

function normSubjects(value) {
  const parts = Array.isArray(value) ? value : String(value || '').split(/[\/,，]/);
  const out = [];
  parts.forEach((part) => String(part).split(/[\/,，]/).forEach((s) => {
    s = s.trim();
    if (s && out.indexOf(s) < 0) out.push(s);
  }));
  return out.length ? out : ['其他'];
}

function normalizePlan(plan) {
  (plan || []).forEach((it) => {
    it.subjects = normSubjects(it.subjects);
    it.target_subjects = normSubjects(it.target_subjects);
  });
  return plan;
}

async function loadRetireDrop() {
  try {
    const body = await api('/api/retire/drop');
    S.retireDrop = body.books || [];
  } catch (err) {
    S.retireDrop = [];
  }
}

async function loadLatestPlan() {
  if (S.plan || S.legacyPlan) return;
  try {
    const body = await api('/api/import/latest');
    if (body && body.legacy) {
      S.legacyPlan = true;
      S.planJob = body.job_id || null;
      return;
    }
    if (body && body.plan) {
      S.legacyPlan = false;
      S.plan = normalizePlan(body.plan);
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
    await loadDocs();
  } else if (S.tab === 'split') {
    await Promise.all([loadStatus(), loadJobs(), loadPending()]);
    await loadSplitBooks();
  } else if (S.tab === 'pdf') {
    await Promise.all([loadJobs(), loadPdf(), loadPdfBooks()]);
  } else if (S.tab === 'pdf-list') {
    await Promise.all([loadJobs(), loadPdfBooks()]);
  } else if (S.tab === 'ledger') {
    await loadLedger();
  } else if (S.tab === 'maintain') {
    await Promise.all([loadStatus(), loadJobs()]);
  } else if (S.tab === 'retire') {
    await Promise.all([loadStatus(), loadJobs(), loadRetireDrop()]);
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
  if (S.tab === 'logs' && S.logJob) openLog(S.logJob.id);
}

// ---- 任务轮询 -----------------------------------------------------
async function openLog(jobId, quiet) {
  try {
    const body = await api('/api/jobs/' + encodeURIComponent(jobId));
    if (!body || !body.job) return;
    S.logJob = body.job;
    S.logDetail = body;
    if (S.logOpenId !== body.job.id) S.logOpenId = body.job.id;
    paintJobDetail();
  } catch (err) {
    if (!quiet) toast(err.message, true);
  }
}

async function watchJob(jobId, done, maxLoops) {
  const cap = maxLoops || WATCH_LOOPS;
  let misses = 0;
  for (let i = 0; i < cap; i += 1) {
    let body;
    try {
      body = await api('/api/jobs/' + encodeURIComponent(jobId));
    } catch (err) {
      // 单次失败（服务重启/网络抖动）不算完：连续 10 次（约 7 秒）都不通才放弃
      misses += 1;
      if (misses >= 10) return null;
      await new Promise((resolve) => setTimeout(resolve, WATCH_TICK));
      continue;
    }
    misses = 0;
    if (done && done(body.job)) return body;
    await new Promise((resolve) => setTimeout(resolve, WATCH_TICK));
  }
  // 到上限不等于失败：任务可能还在跑，降频接着看，别让调用方拿 null 当「跑完了」
  toast('任务仍在跑，稍后刷新');
  keepWatching(jobId, done);
  return null;
}

function keepWatching(jobId, done) {
  let left = 240;                       // 15 秒 × 240 ≈ 1 小时
  const tick = async () => {
    if (left <= 0) return;
    left -= 1;
    let body = null;
    try {
      body = await api('/api/jobs/' + encodeURIComponent(jobId));
    } catch (err) {
      body = null;
    }
    if (body && done && done(body.job)) {
      toast(jobNote(body.job), jobBad(body.job));
      await Promise.all([loadJobs(), loadPdf(), loadPdfBooks(), loadPending()]);
      render();
      return;
    }
    if (body) {
      await loadJobs();
      render();
    }
    setTimeout(tick, 15000);
  };
  setTimeout(tick, 15000);
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
    const detail = await watchJob(body.job_id, afterJob, MAINTAIN_LOOPS);
    if (detail) toast(jobNote(detail.job), jobBad(detail.job));
    // 没结束就只轻量刷一遍：深度状态要跑全库扫描，任务还在写文件时没意义
    if (!detail) {
      await loadJobs();
      render();
      return;
    }
    await Promise.all([loadStatus(true), loadJobs(), loadPending()]);
    render();
  } catch (err) {
    toast(err.message, true);
  } finally {
    setBusy(false);
  }
}

// ---- 动作 ---------------------------------------------------------
function paintLedgerPick() {
  const rows = ledgerRowFiltered().filter(ledgerPending);
  const picked = rows.filter((r) => S.ledgerPick.has(r.uid)).length;
  const btn = document.querySelector('[data-act="ledger-confirm-pick"]');
  if (btn) {
    btn.textContent = '同意选中 ' + num(picked) + ' 本';
    btn.disabled = !picked;
  }
  const all = document.querySelector('[data-act="ledger-pick-all"]');
  if (all) all.checked = rows.length > 0 && picked === rows.length;
}

async function confirmLedgerRows(uids) {
  setBusy(true);
  try {
    const body = await post('/api/ledger/rows/confirm', { uids: uids });
    const n = body.confirmed || 0;
    const skipped = (body.skipped || []).length;
    uids.forEach((uid) => S.ledgerPick.delete(uid));
    await loadLedger();
    render();
    toast(n
      ? '同意了 ' + n + ' 行' + (skipped ? '，跳过 ' + skipped + ' 行（没有「待复核」或已不在账上）' : '')
      : '这些行没有「待复核」，没改');
  } finally {
    setBusy(false);
  }
}

const actions = {
  // ---- 账表 ----
  async 'ledger-load'() {
    await loadLedger();
    S.ledgerEdit = null;
    render();
  },

  async 'ledger-row-edit'(el) {
    S.ledgerEdit = { type: 'row', uid: el.dataset.uid };
    render();
  },

  async 'ledger-row-save'(el) {
    const uid = el.dataset.uid;
    const val = (id) => (document.getElementById(id) || {}).value;
    const row = {
      uid: uid,
      一级: val('led-f-一级'),
      二级: val('led-f-二级'),
      作者层: val('led-f-作者层'),
      保留: val('led-f-保留'),
      正式书名: val('led-f-正式书名'),
      备注: val('led-f-备注'),
    };
    try {
      await post('/api/ledger/row', { row });
      S.ledgerEdit = null;
      await loadLedger();
      render();
      toast('已存 ' + uid);
    } finally {
      setBusy(false);
    }
  },

  async 'ledger-pick'(el) {
    if (el.checked) S.ledgerPick.add(el.value);
    else S.ledgerPick.delete(el.value);
    paintLedgerPick();
  },

  async 'ledger-pending-only'(el) {
    S.ledgerPendingOnly = !!el.checked;
    render();
  },

  async 'ledger-pick-all'(el) {
    const rows = ledgerRowFiltered().filter(ledgerPending);
    if (el.checked) rows.forEach((r) => S.ledgerPick.add(r.uid));
    else rows.forEach((r) => S.ledgerPick.delete(r.uid));
    render();
  },

  async 'ledger-row-confirm'(el) {
    await confirmLedgerRows([el.dataset.uid]);
  },

  async 'ledger-confirm-pick'() {
    const uids = ledgerRowFiltered()
      .filter((r) => S.ledgerPick.has(r.uid) && ledgerPending(r))
      .map((r) => r.uid);
    if (!uids.length) { toast('先勾选待复核的行', true); return; }
    await confirmLedgerRows(uids);
  },

  async 'ledger-row-delete'(el) {
    const uid = el.dataset.uid;
    if (!window.confirm('把 ' + uid + ' 从人账里移出？这句只删账行，不动书目录；删书请走「删书」。')) return;
    try {
      const body = await post('/api/ledger/rows/delete', { uids: [uid] });
      toast('删了 ' + (body.deleted || 0) + ' 行');
      await loadLedger();
      render();
    } finally {
      setBusy(false);
    }
  },

  async 'ledger-concept-edit'(el) {
    const which = el.dataset.which || 'main';
    const key = el.dataset.key || '';
    S.ledgerEdit = { type: 'concept', which: which, key: key, isNew: key === '' };
    render();
  },

  async 'ledger-concept-save'(el) {
    const which = el.dataset.which || 'main';
    const val = (id) => (document.getElementById(id) || {}).value || '';
    const isNew = S.ledgerEdit && S.ledgerEdit.isNew;
    const key = isNew
      ? (which === 'action' ? val('led-c-term') : val('led-c-key'))
      : String(S.ledgerEdit.key);
    if (!key.trim()) { toast(which === 'action' ? '词不能为空' : '键不能为空', true); return; }
    const row = which === 'action'
      ? { term: key, kind: val('led-c-kind'), note: val('led-c-note') }
      : { key: key, aliases: val('led-c-aliases'), exclude: val('led-c-exclude'), note: val('led-c-note') };
    try {
      await post('/api/ledger/concept', { which: which, row: row });
      S.ledgerEdit = null;
      await loadLedger();
      render();
      toast('已存 ' + LEDGER_DEL[which] + ' ' + key);
    } finally {
      setBusy(false);
    }
  },

  async 'ledger-concept-delete'(el) {
    const which = el.dataset.which || 'main';
    const key = el.dataset.key || '';
    if (!window.confirm('删掉 ' + LEDGER_DEL[which] + ' 里的「' + key + '」？概念剖面里已落地的行要在维护里重建才会清掉。')) return;
    try {
      const body = await post('/api/ledger/concept/delete', { which: which, key: key });
      toast('删了 ' + ((body.deleted || 0) ? '1' : '0') + ' 条');
      await loadLedger();
      render();
    } finally {
      setBusy(false);
    }
  },

  async 'ledger-edit-clear'() {
    S.ledgerEdit = null;
    render();
  },

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
      const detail = await watchJob(body.job_id, afterJob, 600);
      if (detail && detail.job.state === 'awaiting_review') {
        S.planJob = body.job_id;
        await loadPlan(body.job_id);
        S.legacyPlan = false;
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
    const bad = planBlockers(S.plan);
    if (bad.length) {
      toast('还有 ' + bad.length + ' 行没定，先处理', true);
      return;
    }
    const count = (action) => S.plan.filter((it) => it.action === action).length;
    const adds = count('add');
    const reps = count('replace');
    const dels = count('delete');
    const bytes = S.plan.filter((it) => it.action === 'delete')
      .reduce((sum, it) => sum + (Number(it.bytes) || 0), 0);
    const msg = '确认入库？\n\n新增 ' + adds + ' 本，换源 ' + reps + ' 本，删除 '
      + dels + ' 个收件箱文件（' + fmtBytes(bytes) + '，移到回收站，可恢复）。\n\n'
      + '只动收件箱里的文件和书库记录，不会删书库里的书。';
    if (!window.confirm(msg)) return;
    S.importDocsJob = '';
    setBusy(true);
    try {
      await post('/api/import/plan', { job_id: S.planJob, items: S.plan });
      const body = await post('/api/import/apply',
        { plan_path: '工作区/运维/jobs/' + S.planJob + '/plan.json' });
      toast('开始入库，完成后会提示');
      // 入库要 ingest + 重建 DB + 书目表，全库重建不止两分钟，上限放宽到 15 分钟
      const detail = await watchJob(body.job_id, afterJob, 1300);
      if (detail && detail.job.state === 'succeeded') {
        toast('入库完成');
        S.importDone = true;
        S.plan = null;
        S.planJob = null;
        S.legacyPlan = false;
        // 入库任务跑完会自动接一个 docs.run：切到它，卡片上直接看进度
        if (detail.docs_job) {
          S.importDocsJob = String(detail.docs_job);
          docsJobSet(S.importDocsJob);
        }
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

  async 'docs-refresh'() {
    S.docsNone = false;
    await loadDocs();
    await loadJobs();
    paintDocs();
  },

  async 'docs-retry'(el) {
    const uid = String((el && el.dataset && el.dataset.uid) || '').trim();
    if (!uid) {
      toast('缺书号，重试不了', true);
      return;
    }
    await submitDocs({ uids: [uid] }, '已单独重排：' + uid);
  },

  async 'docs-backfill'() {
    await submitDocs({}, '开始补转库里遗漏的 doc 格式');
  },

  async 'docs-goto'() {
    const card = document.querySelector('#docs-card');
    if (card) card.scrollIntoView({ block: 'start', behavior: 'smooth' });
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

  // 明细 = 所有硬失败的书（不限定处置：存档的、可转的、未分诊的都可能失败）
  async 'pdf-detail'() {
    const bad = S.books.filter((row) => row.progress === 'failed').length;
    S.pickTags = new Set();
    // 一本失败都没有就别筛：筛出来空表比不筛更糟
    S.pickProgress = new Set(bad ? ['failed'] : []);
    S.pickRecheck = false;
    if (!bad) toast('现在没有失败的书');
    if (location.hash !== '#pdf-list') location.hash = '#pdf-list';
    else await go('pdf-list');
  },

  async 'pdf-jump'(el) {
    const tag = el.dataset.tag;
    S.pickTags = new Set(tag ? [tag] : ['convert', 'untriaged', 'gallery', 'ja']);
    S.pickProgress = new Set(['todo', 'running', 'failed', 'staged', 'deferred']);
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
    if (row.suggestion.manual) {
      toast('这本模型判不了，得自己定处置', true);
      return;
    }
    const items = [{ uid: row.uid, route: row.suggestion.route }];
    await applySettle(items, settleAsk(items));
  },

  async 'pick-suggest-all'() {
    const items = S.books.filter((row) => S.pick.has(row.uid) && row.suggestion
      && !row.suggestion.manual)
      .map((row) => ({ uid: row.uid, route: row.suggestion.route }));
    if (!items.length) {
      toast('选中的书里没有可直接确认的建议', true);
      return;
    }
    await applySettle(items, settleAsk(items));
  },

  async 'pick-refresh'() {
    await loadPdfBooks();
    render();
  },

  async 'pdf-triage-batch'(el) {
    const uids = S.books.filter((row) => row.disposition === 'untriaged' && !row.suggestion)
      .map((row) => row.uid);
    if (!uids.length) {
      toast('没有还要分诊的书', true);
      return;
    }
    if (el) el.disabled = true;
    try {
      const body = await post('/api/pdf/triage-batch', { uids });
      S.triageSeen = body.job_id || null;
      await loadJobs();
    } catch (err) {
      toast(err.message, true);
    }
    render();
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
      const ask = '这 ' + num(gallery.length + japanese.length) + ' 本原定不转：存档·图册 '
        + num(gallery.length) + ' 本、存档·非中英文 ' + num(japanese.length) + ' 本，合计 '
        + num(pages) + ' 页。改成按扫描识别转？'
        + (limit ? '（最多转 ' + num(limit) + ' 本是全书合计，强制转的也占名额）' : '');
      if (!window.confirm(ask)) return;
      force = true;
    }
    setBusy(true);
    try {
      const body = await post('/api/pdf/run', { uids: picked, limit, workers, force });
      S.pick.clear();
      syncPdfLimit();
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

  async 'retire-drop-refresh'() {
    await loadRetireDrop();
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
        uid, reason, plan_hash: el.dataset.hash, confirm_uid: typed, confirm: true,
      });
      const detail = await watchJob(body.job_id, afterJob);
      if (detail) toast(summaryText(detail.job.summary) || detail.job.state,
        detail.job.state !== 'succeeded');
      S.preview = null;
      $('#modal').innerHTML = '';
      await Promise.all([loadStatus(), loadJobs(), loadPending(), loadRetireDrop()]);
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
    // 换了任务就要从头画：不重置的话 paintJobDetail 会以为还在看上一个
    if (S.logOpenId !== el.dataset.id) S.logOpenId = null;
    if (location.hash !== '#logs') location.hash = '#logs';
    await loadJobs();
    render();
    await openLog(el.dataset.id);
  },

  async 'job-cancel'(el) {
    try {
      await post('/api/jobs/' + encodeURIComponent(el.dataset.id) + '/cancel');
      toast('已发停止，等 10 秒收尾');
      // 立刻刷一遍列表与详情：不然页面停在「停止中」，服务端已经收完了也不显示
      await loadJobs();
      render();
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
  if (!el) return;
  if (el.id === 'ledger-q') {
    S.ledgerQ = el.value;
    const tbody = document.querySelector('#view .ledger-table tbody');
    const rows = ledgerRowFiltered();
    if (tbody && tbody.closest('.card')) {
      // 只刷分类表那一段正文，不整页重绘（不打断输入）
      tbody.innerHTML = rows.map(ledgerRowHtml).join('');
    }
    return;
  }
  if (el.id !== 'pick-q') return;
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
    if (field === 'action') {
      const wasAdd = item.action === 'add';
      item.action = el.value;
      if (el.value === 'add') {
        item.status = 'new';
        item.uid = nextPlanUid();
      } else if (el.value === 'replace') {
        item.status = 'replace';
        const t = String(item.target || '');
        item.uid = t && !t.includes(',') ? t : (wasAdd ? '' : (item.uid || ''));
      } else if (el.value === 'delete') {
        item.status = 'in_library';
        const t = String(item.target || '');
        item.uid = wasAdd ? '' : (t && !t.includes(',') ? t : (item.uid || ''));
      } else item.status = 'fuzzy';
      S.planDirty = true;
      render();
      return;
    }
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

// <details> 的展开状态记在 S.docsOpen：局部重绘不能把用户展开的明细收回去
document.addEventListener('toggle', (ev) => {
  const el = ev.target;
  if (el && el.dataset && el.dataset.docs === 'items') S.docsOpen = !!el.open;
}, true);

function uploadFiles(files) {
  const list = Array.from(files || []);
  const input = $('#up-file');
  if (input) input.value = '';
  if (!list.length) return;
  const box = $('#up-box');
  const note = $('#up-note');
  const bar = $('#up-bar');
  if (box) box.hidden = false;
  if (bar) bar.style.width = '0';
  const setBar = (share) => { if (bar) bar.style.width = Math.max(0, Math.min(100, share)) + '%'; };
  const bad = [];   // 没上传成功的：服务端的中文原因攒起来最后一起看
  let index = 0;
  const next = () => {
    if (index >= list.length) {
      setBar(100);
      if (note) {
        note.textContent = bad.length
          ? (bad.length + ' 个没上传成功：' + bad.join('；'))
          : '上传完成，收件箱里可以看到。';
      }
      setTimeout(() => {
        if (bar) bar.style.width = '0';
        if (box) box.hidden = true;
        if (note) note.textContent = '';
      }, bad.length ? 6000 : 1800);
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
        const why = body.error || ('上传失败 HTTP ' + xhr.status);
        bad.push(file.name + '：' + why);
        toast(why, true);
      }
      next();
    };
    xhr.onerror = () => {
      bad.push(file.name + '：上传断了');
      toast('上传断了', true);
      next();
    };
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
      if (S.tab === 'pdf') {
        const live = liveTriage();
        if (live) {
          S.triageSeen = live.id;
          paintTriageProgress();
        } else if (S.triageSeen) {
          const job = S.jobs.find((row) => row.id === S.triageSeen);
          if (!job) return;
          const made = (job.steps || []).filter((s) => s.state === 'ok').length;
          const ok = job.state === 'awaiting_review' || job.state === 'succeeded';
          S.triageSeen = null;
          await Promise.all([loadPdfBooks(), loadPdf()]);
          render();
          toast(ok ? (made ? num(made) + ' 本出了建议，去清单确认'
            : '分诊跑完了，但没有模型建议') : '分诊没过，去日志看', !ok || !made);
        }
      }
      if (S.tab === 'import') {
        // docs 卡片：跑着就 2.5 秒刷一次；没任务时 loadDocs 自己会短路，不会每跳打 404
        await loadDocs();
        paintDocs();
      }
      if (S.tab === 'overview') {
        // 只在内容真变了才写：无条件写会把焦点、选区、滚动位置每 2.5 秒清一次
        const html = viewOverview();
        if (html !== S.overviewHtml) {
          S.overviewHtml = html;
          const view = document.querySelector('#view');
          if (view) view.innerHTML = html;
        }
      }
    } catch (err) {
      /* 轮询失败不打扰页面，状态条会显示缺 */
    }
  }, 2500);
  setInterval(loadHealth, 12000);
}

boot();
