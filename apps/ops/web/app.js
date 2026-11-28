'use strict';

const TABS = [
  ['overview', '总览'],
  ['import', '新书入库'],
  ['ledger', '书目与分类'],
  ['split', '正文生产'],
  ['maintain', '同步与维护'],
  ['logs', '任务与日志'],
];
const NAV_ICONS = {
  overview: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>',
  import: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>',
  ledger: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/></svg>',
  split: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h5"/></svg>',
  maintain: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 16h5v5"/></svg>',
  logs: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h10"/></svg>',
};
const THEME_KEY = 'bookcorpus.ops-theme';
// 子页不进左侧主导航，支持直接用 #hash 跳转并保持兼容
const EXTRA_TABS = ['docs', 'pdf', 'pdf-list', 'manual', 'retire', 'cold'];
const ALL_TABS = TABS.map(([key]) => key).concat(EXTRA_TABS);

const MAINTAIN_STEPS = [
  ['index', '更新检索索引', '让新章节能被搜到。最花时间。'],
  ['awards', '刷新奖项入口', '重建按奖项浏览的链接。'],
  ['audit', '体检', '检查书目、文件与索引是否对得上。'],
  ['verify', '结构验收', '按固定清单检查库的结构是否完整。'],
  ['catalog', '更新书目总表', '重出 Excel 与 CSV。'],
  ['db', '重建书目数据库', '从每本书的记录重算索引库。'],
  ['doc', '重出阅读版页面', '只有阅读版有改动时才需要。'],
];
const FINISH_DEFAULT = ['db', 'index', 'awards', 'audit', 'verify', 'catalog'];
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
  maintain: '系统维护', 'sync.fts': '词法增量同步', 'sync.embed': '向量增量同步',
  'sync.reconcile': '偏差增量同步', 'sync.expand': '主题扩展',
  'embed.compact': '向量库压缩', 'manual.rescan': '扫描手工正文',
  'delete.dry-run': '删书预览', 'delete.apply': '删书',
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
// 清单页的进度筛选五档全开：已有正文的书不会出现在待转清单里
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
  ledgerLimit: 100,
  ledgerEdit: null,
  ledgerPick: new Set(),
  tab: 'overview',
  tabSeq: 0,
  statusAt: 0,
  pollBusy: false,
  pollTimer: null,
  ledgerDebounce: null,
  payload: null, status: {},
  jobs: [], pending: [], inbox: [], inboxIgnored: [], pdf: null,
  plan: null, planJob: null, planDirty: false, legacyPlan: false, nextUid: '',
  batch: null, splitBooks: [],
  review: null, reviewJob: null,
  retireHits: [], retireDrop: [], retireQuery: '', preview: null,
  logJob: null, logDetail: null, follow: true, importDone: null, importDocsJob: '', importSplitJob: '', importSplitBatch: '',
  docs: null, docsNone: false, docsOpen: false, docsSig: '',
  logOpenId: null, logJumpSeq: 0, logSkipAfterRender: false,
  logScroll: 0, logStick: true, logRowsHtml: '',
  logStepsFp: '', logCmdFp: '',
  overviewHtml: '',
  filter: '', selected: new Set(), busy: false,
  replaceMd: false, splitWorkers: 4,
  pdfLimit: 0, pdfLimitFrom: -1, pdfWorkers: 3, pdfReplace: false, triageSeen: null, pdfToday: null,
  pdfUntriaged: null,
  books: [], bookCounts: null, pick: new Set(), pickQ: '',
  pickTags: new Set(['convert', 'untriaged', 'gallery', 'ja']),
  pickProgress: new Set(['todo', 'running', 'failed', 'staged', 'deferred']), pickRecheck: false,
  pickSubject: '', pickSort: 'pages', settle: null, triageInfo: null,
  pdfInlineTag: 'all', pdfInlineQ: '',
  maintainSteps: new Set(FINISH_DEFAULT),
  cold: null, coldError: '', coldRun: null, coldBusy: false, settings: { auto_embed: true, auto_split: true }, services: [],
  reconcileExtra: new Set(),
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

// 快照键：服务端显式 null = 读数未知（缺库/坏库），返回 null 让页面显示「—」，
// 不能落到默认 0；旧 payload 没这个键时才走 legacy 键兜底。
function snapNum(st, key, legacyKey, legacyDflt) {
  if (st && key in st) {
    const v = st[key];
    return v == null ? null : Number(v) || 0;
  }
  return dig(st, legacyKey, legacyDflt);
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
  if (n < 1024) return n + '\u00A0B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + '\u00A0KB';
  if (n < 1024 * 1024 * 1024) return (n / 1048576).toFixed(1) + '\u00A0MB';
  return (n / 1073741824).toFixed(2) + '\u00A0GB';
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
  if (!el) return;
  el.textContent = message;
  el.className = 'toast on' + (bad ? ' bad' : '');
  if (typeof el.showPopover === 'function') {
    try { el.showPopover(); } catch (err) { /* ignore popover state */ }
  }
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    el.className = 'toast';
    if (typeof el.hidePopover === 'function') {
      try { el.hidePopover(); } catch (err) { /* ignore popover state */ }
    }
  }, 3800);
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
  const c = bookCounts();
  const groupOf = (tab) => {
    if (tab === 'retire' || tab === 'cold') return 'ledger';
    if (tab === 'docs' || tab === 'pdf' || tab === 'pdf-list' || tab === 'manual') return 'split';
    return tab;
  };
  const on = groupOf(S.tab);
  $('#nav').innerHTML = TABS.map(([key, label]) => {
    let badge = '';
    if (key === 'import' && c.inbox) {
      badge = `<span class="badge">${c.inbox}</span>`;
    } else if (key === 'split' && c.toConvert) {
      badge = `<span class="badge">${c.toConvert}</span>`;
    } else if (key === 'maintain') {
      const delta = (S.status && S.status.sync_delta) || null;
      if (delta && delta.has_drift) badge = '<span class="badge">!</span>';
    } else if (key === 'logs') {
      const active = (counts.running || 0) + (counts.queued || 0);
      if (active) badge = `<span class="badge">${active}</span>`;
      else if (counts.awaiting_review) badge = `<span class="badge">${counts.awaiting_review}</span>`;
    }
    return `<a href="#${key}" class="${on === key ? 'on' : ''}"${on === key ? ' aria-current="page"' : ''}>${NAV_ICONS[key] || ''}<span class="nav-label">${esc(label)}</span>${badge}</a>`;
  }).join('');
}

// 三态由服务端给（ok / degraded / unknown）；老 payload 没有 state 时按 error 退化，保持兼容
function parseHash(raw) {
  const clean = String(raw || '').replace(/^#/, '');
  const qIdx = clean.indexOf('?');
  const tabRaw = (qIdx >= 0 ? clean.slice(0, qIdx) : clean) || 'overview';
  const tab = ALL_TABS.indexOf(tabRaw) >= 0 ? tabRaw : 'overview';
  const params = {};
  if (qIdx >= 0) {
    new URLSearchParams(clean.slice(qIdx + 1)).forEach((val, key) => {
      params[key] = val;
    });
  }
  return { tab, params };
}

function applyHashParams(tab, params) {
  if (tab === 'logs') {
    if (params && Object.prototype.hasOwnProperty.call(params, 'state')) {
      S.filter = params.state || '';
    } else {
      S.filter = '';
    }
  }
}

function statusState() {
  const p = S.payload || {};
  if (p.state === 'ok' || p.state === 'degraded' || p.state === 'unknown') return p.state;
  return p.error ? 'unknown' : 'ok';
}

function bookCounts() {
  const st = S.status || {};
  const pending = st.pending || {};
  const lagChapters = dig(st, 'index.lag_chapters', 0);
  const lagBooksRaw = dig(st, 'index.lag_books', 0);
  const lagChaptersRaw = dig(st, 'index.lag_chapters', 0);
  const convertible = st.convertible != null ? st.convertible : null;
  const cg = st.convertible_groups || null;
  // 缺库时服务端把计数填 0，但带 ok:false：0 不能当真实读数，页面要显示「—/未知」
  const libKnown = dig(st, 'library.ok', true) !== false;
  const indexKnown = dig(st, 'index.ok', true) !== false;
  const viewsKnown = dig(st, 'views.ok', true) !== false;
  const catalogKnown = st.catalog ? st.catalog.unmatched != null : true;
  const ebooksToConvert = cg ? (Number(cg.ebook) || 0) : ebookCount();
  const docsToConvert = cg ? (Number(cg.doc) || 0) : docsBackfill();
  const otherToConvert = cg ? (Number(cg.other) || 0) : 0;
  const pdfsToConvert = cg
    ? (Number(cg.pdf) || 0)
    : Math.max(0, (convertible != null ? convertible : 0) - ebooksToConvert - docsToConvert - otherToConvert);
  const readable = snapNum(st, 'books_readable', 'md_indexable', 0);
  const mdDone = st.md_done != null ? Number(st.md_done) : null;
  const zeroChapterDone = st.zero_chapter_done != null
    ? Math.max(0, Number(st.zero_chapter_done) || 0)
    : (st.books_readable != null && mdDone != null ? Math.max(0, mdDone - Number(st.books_readable)) : 0);
  return {
    books: snapNum(st, 'books', 'books', 0),
    // 可读=快照 books_readable：md 目录存在且章数>0；不再用 md_done（会把 0 章 md（图片书）算成可读）。
    // 旧 payload（无 books_readable）退 md_indexable，口径相同；显式 null（未知）保留，页面显示「—」。
    readable,
    mdDone,
    zeroChapterDone,
    chapters: snapNum(st, 'stats_chapters', 'md_chapters', 0),
    builtAt: dig(st, 'stats_built_at', ''),
    // 待转口径在服务端：无 md 的书里去掉已定存档的。缺口径时退回旧公式
    toConvert: convertible != null ? convertible
      : (pending.pdf || 0) + (pending.txt || 0) + (pending.djvu || 0),
    ebooksToConvert,
    docsToConvert,
    pdfsToConvert,
    otherToConvert,
    archive: st.archive || null,
    inbox: dig(st, 'inbox.files', 0),
    inboxIgnored: dig(st, 'inbox.ignored_count', 0),
    ledgerRows: dig(st, 'ledger.rows', 0),
    ledgerStale: !!dig(st, 'ledger.stale', false),
    prefillReady: !!dig(st, 'prefill_ready', false),
    awards: dig(st, 'views.awards', 0),
    broken: dig(st, 'views.broken', 0),
    lagBooks: Math.max(0, lagBooksRaw),
    lagChapters: Math.max(0, lagChaptersRaw),
    indexExtra: Math.max(0, -lagBooksRaw),
    indexExtraChapters: Math.max(0, -lagChaptersRaw),
    indexedChapters: dig(st, 'index.chapters', 0),
    filteredChapters: dig(st, 'index.filtered_chapters', 0),
    emptyBooks: dig(st, 'index.empty_books', 0),
    warn: dig(st, 'audit.warn', 0),
    fail: dig(st, 'audit.fail', 0),
    unmatched: dig(st, 'catalog.unmatched', 0),
    catalogAt: dig(st, 'catalog.mtime', ''),
    awaiting: dig(S.payload, 'state_counts.awaiting_review', 0),
    libKnown: libKnown,
    indexKnown: indexKnown,
    viewsKnown: viewsKnown,
    catalogKnown: catalogKnown,
    auditRan: dig(st, 'audit.ran', !!dig(st, 'audit.at', '')) !== false,
    auditStale: !!dig(st, 'audit.stale', false),
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
  return {
    at: dig(st, 'audit.at', ''),
    ran: dig(st, 'audit.ran', !!dig(st, 'audit.at', '')) !== false,
    stale: !!dig(st, 'audit.stale', false),
    rows: rows,
  };
}

function issueList(info) {
  const auditRefreshing = !!dig(S.payload, 'ops.audit_refreshing', false);
  const refreshingNote = auditRefreshing ? '<p class="hint">后台正在自动刷新体检结果，稍候自动更新…</p>' : '';
  const list = info.rows.length ? `<ul class="issues">${info.rows.map((row) => `<li class="issue">
    ${row.level === 'note' ? ''
    : `<span class="pill ${row.level === 'fail' ? 'miss' : 'half'}">${
      row.level === 'fail' ? '需处理' : '提醒'}</span>`}
    <span class="issue-text${row.level === 'note' ? ' hint' : ''}">${esc(row.text)}</span>
  </li>`).join('')}</ul>` : '';
  if (statusState() === 'unknown') {
    return (list || '<p class="hint">状态读不到。</p>')
      + '<p class="hint">点「刷新状态」重试；读数未知时不能当没有问题。</p>' + refreshingNote;
  }
  if (!info.rows.length) {
    if (info.stale) {
      return (auditRefreshing
        ? refreshingNote
        : '<p class="hint">体检结果已过期：最近一次入库或索引更新晚于体检，重跑收尾后再判断。</p>');
    }
    return (info.at && statusState() === 'ok'
      ? '<p class="pass">体检全部通过。</p>'
      : '<p class="hint">体检没有报问题，但状态还不完整。</p>') + refreshingNote;
  }
  const expired = info.at && info.stale && !auditRefreshing
    ? '<p class="hint">体检结果已过期：最近一次入库或索引更新晚于体检，重跑收尾后再判断。</p>'
    : '';
  return info.at ? list + expired + refreshingNote
    : list + '<p class="hint">体检未运行：上面是从书库现状读出来的问题，还没有体检记录。</p>' + refreshingNote;
}

function statsReadable() {
  return !(S.payload && S.payload.error);
}

function ebookCount() {
  return S.pending.filter((row) => EBOOK.test(row.format)).length;
}

function formatStampTime(raw) {
  const text = String(raw || '').trim();
  if (!text) return '';
  const match = /(\d{2}:\d{2})(?::\d{2})?/.exec(text);
  return match ? match[1] : text;
}

function renderStats() {
  const c = bookCounts();
  const items = [
    ['藏书', c.libKnown && c.books != null ? num(c.books) : '—', ''],
    ['可读', c.libKnown && c.readable != null ? num(c.readable) : '—', c.libKnown && c.readable != null ? 'ok' : ''],
    ['待转', c.libKnown ? num(c.toConvert) : '—', c.libKnown && c.toConvert ? 'warn' : ''],
    ['索引', !c.indexKnown ? '未知'
      : (c.lagBooks ? '落后 ' + num(c.lagBooks) + ' 本'
        : (statusState() === 'ok' ? '已同步' : '没有落后')),
      !c.indexKnown || c.lagBooks ? 'warn' : (statusState() === 'ok' ? 'ok' : '')],
    c.inbox ? ['收件箱', num(c.inbox), 'warn'] : null,
  ].filter(Boolean);
  let html = items.map(([label, value, cls]) => `<li class="stat${cls ? ' ' + cls : ''}">${esc(label)}<b>${esc(value)}</b></li>`).join('');
  if (!statsReadable()) {
    const detail = (S.payload && S.payload.error) || '';
    html += `<li class="stat warn" title="${esc(detail)}">状态<b>读不到</b></li>`;
  }
  const ops = (S.payload && S.payload.ops) || {};
  if (ops.writer) html += '<li class="stat warn">写任务<b>在跑</b></li>';
  $('#stats').innerHTML = html;

  const builtAt = c.builtAt || (S.payload && S.payload.stats_built_at) || '';
  const stampEl = $('#stats-stamp');
  const stampTime = $('#stamp-time');
  const stampDiv = $('#stamp-divider');
  if (stampEl && stampTime) {
    if (builtAt) {
      stampTime.textContent = formatStampTime(builtAt);
      stampEl.title = '统计于 ' + builtAt + '（来自统一统计基准，与各应用同源）';
      stampEl.hidden = false;
      if (stampDiv) stampDiv.hidden = false;
    } else {
      stampEl.hidden = true;
      if (stampDiv) stampDiv.hidden = true;
    }
  }
}

function openModal(html) {
  const host = $('#modal');
  if (!host) return;
  const existing = host.querySelector('dialog.modal[open]');
  if (existing && html) {
    const tmp = document.createElement('div');
    tmp.innerHTML = html;
    const nextBody = tmp.querySelector('.dlg-body');
    const curBody = existing.querySelector('.dlg-body');
    if (nextBody && curBody) {
      curBody.innerHTML = nextBody.innerHTML;
      return;
    }
  }
  host.innerHTML = html || '';
  const dlg = host.querySelector('dialog.modal');
  if (!dlg) return;
  dlg.addEventListener('close', () => {
    dlg.returnValue = '';
    S.preview = null;
    S.settle = null;
    S.triageInfo = null;
    if (host.contains(dlg)) host.innerHTML = '';
  }, { once: true });
  if (typeof dlg.showModal === 'function' && !dlg.open) {
    dlg.showModal();
  }
}

function closeModal() {
  const host = $('#modal');
  if (!host) return;
  const dlg = host.querySelector('dialog.modal');
  if (dlg && dlg.open && typeof dlg.close === 'function') {
    dlg.close('');
  } else {
    S.preview = null;
    S.settle = null;
    S.triageInfo = null;
    host.innerHTML = '';
  }
}

// ---- 视图骨架 -----------------------------------------------------
function render() {
  renderNav();
  renderStats();
  const view = $('#view');
  const map = {
    overview: viewOverview,
    import: viewImport,
    split: viewSplit,
    docs: viewDocs,
    pdf: viewPdf,
    'pdf-list': viewPdfList,
    manual: viewManual,
    maintain: viewMaintain,
    ledger: viewLedger,
    retire: viewRetire,
    cold: viewCold,
    logs: viewLogs,
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

function scrollTabIntoView(el) {
  if (!el || typeof el.scrollIntoView !== 'function') return;
  try {
    el.scrollIntoView({ block: 'nearest', inline: 'nearest', container: 'nearest' });
  } catch (err) {
    el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }
}

function wireTrackLinkage() {
  const board = document.querySelector('#view .track-board');
  if (!board || board.dataset.linkedWired === '1') return;
  board.dataset.linkedWired = '1';
  const setStage = (stage, active) => {
    if (!stage) return;
    const node = board.querySelector(`.track-stage[data-stage="${stage}"]`);
    if (node && node.classList) node.classList.toggle('is-linked', active);
  };
  board.addEventListener('mouseover', (ev) => {
    const link = ev.target.closest && ev.target.closest('[data-link-stage]');
    if (link) setStage(link.dataset.linkStage, true);
  });
  board.addEventListener('mouseout', (ev) => {
    const link = ev.target.closest && ev.target.closest('[data-link-stage]');
    if (link && (!ev.relatedTarget || !link.contains(ev.relatedTarget))) {
      setStage(link.dataset.linkStage, false);
    }
  });
  board.addEventListener('focusin', (ev) => {
    const link = ev.target.closest && ev.target.closest('[data-link-stage]');
    if (link) setStage(link.dataset.linkStage, true);
  });
  board.addEventListener('focusout', (ev) => {
    const link = ev.target.closest && ev.target.closest('[data-link-stage]');
    if (link && (!ev.relatedTarget || !link.contains(ev.relatedTarget))) {
      setStage(link.dataset.linkStage, false);
    }
  });
}

function afterRender() {
  wireDrop();
  wireTrackLinkage();
  scrollTabIntoView(document.querySelector('#nav a.on'));
  scrollTabIntoView(document.querySelector('#view .subtabs a.on'));
  if (S.logSkipAfterRender) {
    S.logSkipAfterRender = false;
    return;
  }
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
const SVC_DESC = {
  home: '全库书架与统一入口',
  search: '自然语言检索与段落引用',
  reading: '逐章正文阅读与批注',
  daily: '每日推荐一章只读版',
};

function trackAlerts(c) {
  const items = [];
  if (statusState() === 'unknown') {
    items.push({
      stage: 'audit', miss: true,
      title: '暂时读不到书库状态',
      note: '书目库或检索索引未就绪时计数与体检不可信',
      href: '#maintain', label: '去维护',
    });
  } else if (!c.libKnown || !c.indexKnown || !c.catalogKnown || !c.viewsKnown) {
    const lack = [];
    if (!c.libKnown) lack.push('书目库');
    if (!c.indexKnown) lack.push('检索索引');
    if (!c.catalogKnown) lack.push('书目总表');
    if (!c.viewsKnown) lack.push('浏览入口（奖项）');
    items.push({
      stage: 'views', miss: true,
      title: '书库状态不完整',
      note: '缺 ' + lack.join('、'),
      href: '#maintain', label: '去维护',
    });
  }
  if (statusState() !== 'unknown' && !c.auditRan) {
    items.push({
      stage: 'audit', miss: true,
      title: '还没有做过体检',
      note: '跑一次收尾后总览才算完整',
      href: '#maintain', label: '去维护',
    });
  } else if (c.auditStale) {
    items.push({
      stage: 'audit',
      title: '体检结果已过期',
      note: '入库或索引晚于上次体检',
      href: '#maintain', label: '去维护',
    });
  }
  if (c.fail || c.broken) {
    items.push({
      stage: c.broken && !c.fail ? 'views' : 'audit', miss: true,
      title: '有 ' + num(c.fail + c.broken) + ' 处需处理',
      note: '先修好异常项再体检',
      href: '#maintain', label: '去维护',
    });
  }
  if (c.unmatched) {
    items.push({
      stage: 'views',
      title: num(c.unmatched) + ' 条书目对不上文件',
      note: '重出书目总表即可对齐',
      href: '#maintain', label: '去维护',
    });
  }
  if (c.awaiting) {
    items.push({
      stage: 'split',
      title: num(c.awaiting) + ' 个任务待确认',
      note: '产物停在暂存区等核对',
      href: '#logs?state=awaiting_review', label: '去确认',
    });
  }
  if (c.inbox) {
    items.push({
      stage: 'inbox',
      title: '核对收件箱 ' + num(c.inbox) + ' 本新书',
      note: '核对书名与分类后入库',
      href: '#import', label: '去入库',
    });
  }
  if (c.inboxIgnored) {
    items.push({
      stage: 'inbox',
      title: '收件箱有 ' + num(c.inboxIgnored) + ' 个不支持格式',
      href: '#import', label: '去查看',
    });
  }
  if (c.ledgerStale) {
    items.push({
      stage: 'ledger',
      title: '分类账有修改待同步',
      href: '#maintain', label: '去同步',
    });
  }
  const ebooks = c.ebooksToConvert || ebookCount();
  if (ebooks) {
    items.push({
      stage: 'split',
      title: '拆章 ' + num(ebooks) + ' 本电子书',
      href: '#split', label: '去拆章',
    });
  }
  if (c.pdfsToConvert) {
    items.push({
      stage: 'split',
      title: '待转 PDF ' + num(c.pdfsToConvert) + ' 本',
      note: '政策存档书已自动跳过',
      href: '#pdf', label: '去处理',
    });
  }
  if (c.docsToConvert) {
    items.push({
      stage: 'split',
      title: '补转文档 ' + num(c.docsToConvert) + ' 本',
      href: '#docs', label: '去转文档',
    });
  }
  if (c.otherToConvert) {
    items.push({
      stage: 'split',
      title: num(c.otherToConvert) + ' 本手工格式待转换',
      href: '#manual', label: '去查看',
    });
  }
  if (c.lagBooks) {
    items.push({
      stage: 'index',
      title: '检索索引落后 ' + num(c.lagBooks) + ' 本',
      href: '#maintain', label: '去维护',
    });
  }
  if (c.indexExtra) {
    items.push({
      stage: 'index',
      title: '检索索引比书库多 ' + num(c.indexExtra) + ' 本',
      href: '#maintain', label: '去维护',
    });
  }
  if (c.warn) {
    items.push({
      stage: 'audit',
      title: '体检有 ' + num(c.warn) + ' 条提醒',
      note: '可在维护页查看明细',
      href: '#maintain', label: '去查看',
    });
  }
  return items;
}

function verdictText(c, alerts) {
  if (statusState() === 'unknown' || !statsReadable()) return '暂时读不到书库状态';
  if (!c.libKnown || !c.indexKnown || !c.auditRan || statusState() !== 'ok') {
    if (c.fail || c.broken) return '有几处需要处理';
    return '状态不完整，先补齐再判断';
  }
  if (c.fail || c.broken) return '有几处需要处理';
  if (!alerts.length) return '库是干净的';
  return '书库整体运转正常，有 ' + num(alerts.length) + ' 项待关注';
}

function hero(c, alerts) {
  const books = Number(c.books) || 0;
  const readable = Number(c.readable) || 0;
  const convertible = Number(c.toConvert) || 0;
  const archBooks = c.archive ? (Number(c.archive.books) || 0) : 0;

  const knownPct = c.libKnown && books > 0;
  const readyShare = knownPct ? Math.min(100, Math.max(0, (readable / books) * 100)) : 0;
  const warnShare = knownPct ? Math.min(100 - readyShare, Math.max(0, (convertible / books) * 100)) : 0;
  const archShare = knownPct ? Math.min(100 - readyShare - warnShare, Math.max(0, (archBooks / books) * 100)) : 0;
  const pctLabel = knownPct ? readyShare.toFixed(1) + '%' : '—';

  const indexSub = !c.indexKnown
    ? '检索索引未就绪'
    : (c.filteredChapters
      ? '已滤除前后页与空章 ' + num(c.filteredChapters) + ' 章'
      : (c.lagChapters ? '索引落后 ' + num(c.lagChapters) + ' 章' : '全书正文均已覆盖'));
  const archSub = c.archive
    ? '图册 ' + num(c.archive.gallery || 0) + ' · 非中英文 ' + num(c.archive.ja || 0) + '（不转不删）'
    : '暂无存档明细';

  const ledgerItems = [
    `<div class="ledger-item">
      <dt class="ledger-label">全文索引</dt>
      <dd class="ledger-val">${c.indexKnown ? num(c.indexedChapters) : '—'} <span class="ledger-unit">/ ${c.libKnown && c.chapters != null ? num(c.chapters) : '—'} 章</span></dd>
      <dd class="ledger-sub">${esc(indexSub)}</dd>
    </div>`,
    `<div class="ledger-item">
      <dt class="ledger-label">政策存档</dt>
      <dd class="ledger-val">${c.archive ? num(archBooks) : '—'} <span class="ledger-unit">本</span></dd>
      <dd class="ledger-sub">${esc(archSub)}</dd>
    </div>`,
  ];
  if (c.libKnown && c.zeroChapterDone > 0) {
    ledgerItems.push(`<div class="ledger-item">
      <dt class="ledger-label">无正文绘本</dt>
      <dd class="ledger-val">${num(c.zeroChapterDone)} <span class="ledger-unit">本</span></dd>
      <dd class="ledger-sub">已拆 0 章（无正文可检索）</dd>
    </div>`);
  }

  return `<div class="page-head">
      <h1 id="main-heading">总览</h1>
    </div>
    <section class="hero-card section" aria-labelledby="hero-verdict">
      <div class="hero-main">
        <h2 class="verdict" id="hero-verdict">${esc(verdictText(c, alerts))}</h2>
        <dl class="hero-ledger${ledgerItems.length >= 3 ? ' cols-3' : ''}">${ledgerItems.join('')}</dl>
      </div>
      <div class="hero-meter">
        <div class="meter-top">
          <span class="meter-title">正文可读率</span>
          <span class="meter-pct">${esc(pctLabel)}</span>
        </div>
        <div class="meter-bar" role="img" aria-label="正文可读率 ${esc(pctLabel)}">
          <div class="meter-seg meter-fill-ok" style="width:${readyShare.toFixed(1)}%"></div>
          <div class="meter-seg meter-fill-warn" style="width:${warnShare.toFixed(1)}%"></div>
          <div class="meter-seg meter-fill-arch" style="width:${archShare.toFixed(1)}%"></div>
        </div>
        <div class="meter-legend">
          <span class="legend-item"><span class="swatch ok" aria-hidden="true"></span>已有正文 <span class="sub-num">${esc(pctLabel)}</span></span>
          <span class="legend-item"><span class="swatch warn" aria-hidden="true"></span>待转 <span class="sub-num">${esc(knownPct ? warnShare.toFixed(1) + '%' : '—')}</span></span>
          <span class="legend-item"><span class="swatch arch" aria-hidden="true"></span>政策存档 <span class="sub-num">${esc(knownPct ? archShare.toFixed(1) + '%' : '—')}</span></span>
        </div>
      </div>
    </section>`;
}

function pipeline(c, alerts) {
  const unknown = statusState() === 'unknown';
  const ledgerGap = Number(c.ledgerRows) - Number(c.books);
  const cells = [
    {
      key: 'inbox', name: '收书', href: '#import',
      state: unknown ? 'miss' : (c.inbox ? 'half' : 'ok'),
      word: unknown ? '未知' : (c.inbox ? '待核对 ' + num(c.inbox) : '正常'),
    },
    {
      key: 'ledger', name: '入账', href: c.ledgerStale ? '#maintain' : '#ledger',
      state: !c.libKnown ? 'miss' : (ledgerGap ? 'miss' : (c.ledgerStale ? 'half' : 'ok')),
      word: !c.libKnown ? '未知' : (ledgerGap ? '需核对' : (c.ledgerStale ? '需同步' : '正常')),
    },
    {
      key: 'views', name: '上架', href: '#maintain',
      state: !c.viewsKnown ? 'miss' : (c.broken ? 'miss' : 'ok'),
      word: !c.viewsKnown ? '未知' : (c.broken ? '需处理' : '正常'),
    },
    {
      key: 'split', name: '正文生产',
      href: (c.ebooksToConvert || ebookCount()) ? '#split'
        : (c.pdfsToConvert ? '#pdf' : (c.docsToConvert ? '#docs' : '#split')),
      state: !c.libKnown ? 'miss' : (c.toConvert ? 'half' : 'ok'),
      word: !c.libKnown ? '未知' : (c.toConvert ? '待转 ' + num(c.toConvert) : '正常'),
    },
    {
      key: 'index', name: '可检索', href: '#maintain',
      state: !c.indexKnown ? 'miss' : (c.lagBooks || c.lagChapters || c.indexExtra || c.indexExtraChapters ? 'half' : 'ok'),
      word: !c.indexKnown ? '未知' : (c.lagBooks || c.lagChapters ? '待处理' : (c.indexExtra || c.indexExtraChapters ? '需重建' : '正常')),
    },
    {
      // 体检记录本身没问题也不能替整个书库担保：state 非 ok 时降成「需注意」
      key: 'audit', name: '体检', href: '#maintain',
      state: !c.auditRan ? 'miss'
        : (c.auditStale ? 'half' : (c.fail ? 'miss' : (c.warn ? 'half' : (statusState() === 'ok' ? 'ok' : 'half')))),
      word: !c.auditRan ? '未运行'
        : (c.auditStale ? '已过期' : (c.fail ? '需处理' : (c.warn ? num(c.warn) + ' 条提醒' : (statusState() === 'ok' ? '正常' : '需注意')))),
    },
  ];
  const arrowSvg = '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 8.5 8.5 3.5"/><path d="M4.2 3.5H8.5v4.3"/></svg>';
  return `<section class="section" aria-labelledby="sec-pipeline">
    <div class="section-head">
      <h2 id="sec-pipeline">流水线六环节</h2>
    </div>
    <div class="track-board">
      <div class="track-grid">${cells.map((cell, idx) => {
        const stepNo = String(idx + 1).padStart(2, '0');
        const stateCls = cell.state === 'miss' ? ' is-miss' : (cell.state === 'half' ? ' is-warn' : '');
        return `<a class="track-stage${stateCls}" href="${cell.href}" data-stage="${esc(cell.key)}">
          <span class="track-watermark" aria-hidden="true">${stepNo}</span>
          <span class="pipe-cue" aria-hidden="true">${arrowSvg}</span>
          <div class="track-rail" aria-hidden="true"><span class="track-node"></span></div>
          <div class="track-title"><span class="track-step-num" aria-hidden="true">${stepNo}</span>${esc(cell.name)}</div>
          ${status(cell.state, cell.word)}
        </a>`;
      }).join('')}</div>
      <div class="track-alerts"${alerts.length ? '' : ' hidden'}>
        ${alerts.slice(0, 6).map((a) => `<a class="track-alert${a.miss ? ' is-miss' : ''}" href="${a.href}" data-link-stage="${esc(a.stage)}">
          <span class="alert-dot" aria-hidden="true"></span>
          <span><strong>${esc(a.title)}</strong>${a.note ? ' · ' + esc(a.note) : ''}</span>
          <span class="alert-act">${esc(a.label)} →</span>
        </a>`).join('')}
      </div>
    </div>
  </section>`;
}

function subtabsBar(items, active) {
  return `<nav class="subtabs" aria-label="子视图切换">${items.map(([key, label, count]) => `<a class="subtab${active === key ? ' on' : ''}" href="#${key}"${active === key ? ' aria-current="page"' : ''}>${esc(label)}${count ? `<span class="badge">${num(count)}</span>` : ''}</a>`).join('')}</nav>`;
}

function catalogSubtabs(active) {
  const coldCount = dig(S.status, 'sync_delta.cold_count', 0);
  const dropCount = (S.retireDrop || []).length;
  return subtabsBar([
    ['ledger', '分类与全库台账', 0],
    ['retire', '待退库与删书', dropCount],
    ['cold', '退库台账与冷宫', coldCount],
  ], active);
}

function contentSubtabs(active) {
  const c = bookCounts();
  return subtabsBar([
    ['split', '电子书', c.ebooksToConvert],
    ['docs', '文档转正文', c.docsToConvert],
    ['pdf', 'PDF 分诊与转写', c.pdfsToConvert],
    ['manual', '手工格式', c.otherToConvert],
  ], active);
}

function reviewHref(job) {
  if (!job || job.state !== 'awaiting_review') return '';
  if (job.kind === 'split.run') return '#split';
  if (job.kind === 'pdf.run') return '#pdf-list';
  if (job.kind === 'import.scan') return '#import';
  if (job.kind === 'pdf.triage') return '#pdf-list';
  return '';
}

function jobReviewHint(job) {
  if (!job) return '';
  if (job.kind === 'split.run') {
    return '该拆章批次有待复核书目（已暂存）。可去「正文生产」查看问题、受理或重拆，也可在核对后直接人工收口：';
  }
  if (job.kind === 'pdf.run') {
    return '该 PDF 批次有待复核书目（已暂存）。可去「待转 PDF 清单」受理或改处置，也可在核对后直接人工收口：';
  }
  return '该任务停在「待确认」（例如服务重启接管导致退出码未知）。请核对下方步骤与日志后人工收口或重跑：';
}

function jobReviewBox(job) {
  if (!job || job.state !== 'awaiting_review') return '';
  const rhref = reviewHref(job);
  const jumpLabel = job.kind === 'split.run' ? '去正文生产处理' : (job.kind === 'pdf.run' ? '去 PDF 清单处理' : '去对应页面处理');
  return `<div class="review-box" id="job-review-box">
    <p class="small">${esc(jobReviewHint(job))}</p>
    <div class="toolbar" style="margin-bottom:0">
      ${rhref ? `<button class="btn sm" data-act="job-handle" data-id="${esc(job.id)}">${esc(jumpLabel)}</button>` : ''}
      <input type="text" id="job-resolve-note" placeholder="处理说明 / 确认备注（可选）" size="28">
      <button class="btn sm primary" data-act="job-resolve" data-id="${esc(job.id)}" data-res="succeeded">确认已处理</button>
      <button class="btn sm" data-act="job-resolve" data-id="${esc(job.id)}" data-res="failed">标为失败</button>
      <button class="btn sm" data-act="job-resolve" data-id="${esc(job.id)}" data-res="dismissed">忽略关闭</button>
    </div>
  </div>`;
}

function jobLine(job) {
  const state = job.state;
  const rhref = reviewHref(job);
  return `<div class="jobline">
    <span class="t">${esc(fmtTime(job.created))}</span>
    <span class="pill">${esc(JOB_WORD[state] || state)}</span>
    <span class="jl-title">${esc(summaryText(job.title))}</span>
    <span class="jl-sum small">${esc(summaryText(job.summary))}</span>
    <span class="row tight">
      ${rhref ? `<a class="btn sm primary" href="${rhref}">去处理</a>` : ''}
      <a class="btn sm" href="#logs" data-act="job-open" data-id="${esc(job.id)}">详情</a>
    </span>
  </div>`;
}

function servicesCard() {
  const list = S.services || [];
  if (!list.length) return '';
  return `<section aria-labelledby="sec-apps">
    <div class="section-head">
      <h2 id="sec-apps">书库应用入口</h2>
      <button type="button" class="btn compact" data-act="services-refresh">刷新</button>
    </div>
    <div class="svc-bar">${list.map((svc) => {
      const desc = SVC_DESC[svc.key] || svc.name || '';
      const cls = svc.running ? 'is-on' : 'is-off';
      return `<div class="svc-item ${cls}">
        <div class="svc-info">
          <div class="svc-title-row">
            <span class="svc-name">${esc(svc.name)}</span>
            <span class="svc-state ${svc.running ? 'on' : 'off'}"><span class="svc-dot ${svc.running ? 'on' : 'off'}" aria-hidden="true"></span>${svc.running ? '运行中' : '未启动'}</span>
          </div>
          <div class="svc-sub">${esc(desc)}</div>
        </div>
        <div class="svc-actions">
          ${svc.running
            ? `<a class="btn compact svc-open" href="${esc(svc.url)}" target="_blank" rel="noopener">打开 ↗</a>`
            : `<button type="button" class="btn compact svc-start" data-act="service-start" data-key="${esc(svc.key)}">▶ 启动</button>`}
        </div>
      </div>`;
    }).join('')}</div>
  </section>`;
}

function viewOverview() {
  const c = bookCounts();
  const alerts = trackAlerts(c);
  return `${hero(c, alerts)}
    ${pipeline(c, alerts)}
    ${servicesCard()}`;
}

// ---- 新书导入 -----------------------------------------------------
function nextPlanUid() {
  let max = 0;
  if (S.nextUid) {
    const nm = /^B(\d+)$/.exec(String(S.nextUid));
    if (nm) max = Math.max(max, parseInt(nm[1], 10) - 1);
  }
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
    <td data-label="动作"><select data-field="action" data-idx="${index}" aria-label="第 ${index + 1} 行动作">
      ${isRev ? '<option value="review" selected>待核</option>' : ''}
      ${['add', 'replace', 'delete'].map((a) => `<option value="${a}"${a === item.action ? ' selected' : ''}>${ACTION_WORD[a]}</option>`).join('')}
    </select></td>
    <td data-label="原文件" class="wrap"><span class="${isDel ? 'strike' : ''}">${esc(item.file)}</span></td>
    <td data-label="格式与体积" class="nowrap"><span>${esc(item.format || '')}<span class="small"> · ${fmtBytes(item.bytes)}</span></span></td>
    <td data-label="正式书名"><input type="text" data-field="title" data-idx="${index}" aria-label="第 ${index + 1} 行书名" value="${esc(title)}" size="18" style="min-width:130px"${isAdd ? '' : ' readonly'}></td>
    <td data-label="作者" class="wrap small">${esc(author || '—')}</td>
    <td data-label="分类">${isAdd
      ? `<input type="text" data-field="subjects" data-idx="${index}" aria-label="第 ${index + 1} 行分类" value="${esc(subjects)}" size="12" style="min-width:96px" placeholder="一级/二级">`
      : esc(cls || '—')}</td>
    <td data-label="长期保留">${(isDel || isRev) ? '—' : `<select data-field="retention" data-idx="${index}" aria-label="第 ${index + 1} 行保留策略">
      ${['keep', 'review', 'drop'].map((r) => `<option value="${r}"${r === (item.retention || 'keep') ? ' selected' : ''}>${RETENTION_WORD[r]}</option>`).join('')}
    </select>`}</td>
    <td data-label="编号">${isAdd
      ? `<input type="text" data-field="uid" data-idx="${index}" aria-label="第 ${index + 1} 行编号" value="${esc(item.uid || '')}" size="6">`
      : (isRep
        ? `<input type="text" data-field="uid" data-idx="${index}" aria-label="第 ${index + 1} 行编号" value="${esc(item.uid || '')}" size="6" required>`
        : esc(item.uid || '—'))}</td>
    <td data-label="说明" class="wrap small"><span>${st[0] ? `<span class="pill ${st[1]}">${esc(st[0])}</span> ` : ''}${esc(note)}</span></td>
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
  const ignored = S.inboxIgnored || [];
  const ignoredHtml = ignored.length
    ? `<p class="hint" style="margin-top:10px">另有 ${num(ignored.length)} 个不支持格式文件（${ignored.slice(0, 6).map((f) => esc(f.name)).join('、')}${ignored.length > 6 ? ' 等' : ''}），暂不自动入库。</p>`
    : '';
  return `<section class="card section" aria-label="收件箱清单">
    <div class="card-head">
      <div style="display:flex;align-items:center;gap:8px">
        <h2>收件箱</h2>
        <span class="pill ${inbox.length ? 'half' : 'ok'}">${num(inbox.length)} 本</span>
      </div>
      <div class="row tight">
        <button type="button" class="btn sm" data-act="inbox-refresh">刷新</button>
        <button type="button" class="btn sm${inbox.length && !hasPlan ? ' primary' : ''}" data-write data-act="import-scan"${inbox.length ? '' : ' disabled'}>生成入库清单</button>
      </div>
    </div>
    ${inbox.length ? `<div class="tw table-wrap stack-mobile" tabindex="0" role="region" aria-label="收件箱文件表"><table>
      <thead><tr><th>文件</th><th>格式</th><th class="num">大小</th><th>放进来的时间</th></tr></thead>
      <tbody>${inbox.map((f) => `<tr><td data-label="文件" class="wrap">${esc(f.name)}</td>
        <td data-label="格式" class="mono" translate="no">${esc(f.format)}</td><td data-label="大小" class="num">${fmtBytes(f.bytes)}</td>
        <td data-label="放入时间" class="nowrap small muted">${esc(fmtTime(f.mtime))}</td></tr>`).join('')}</tbody></table></div>`
    : `<div class="empty-state"><div>
        <div class="empty-state-title">收件箱当前是空的</div>
        <div class="empty-state-sub">点击上方「选择文件」或直接把电子书拖进投递条，即可准备入库。</div>
      </div></div>`}
    ${ignoredHtml}
  </section>`;
}

function dropCard() {
  return `<section class="section" aria-label="投递新书">
    <button type="button" class="drop drop-bar" data-act="pick-files" aria-label="拖文件到这里，或点击选择新书">
      <span class="drop-bar-left">
        <span class="drop-icon" aria-hidden="true">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>
          </svg>
        </span>
        <span>
          <strong style="display:block;font-size:14px">投递新书到收件箱</strong>
          <span class="small muted">支持 EPUB、MOBI、AZW3、AZW、PDF、TXT、MD、DOCX、XLSX、XLS、PPTX，单文件不超过 2&nbsp;GB（不收 .doc / .ppt / .csv / .djvu）</span>
        </span>
      </span>
      <span class="btn sm primary">选择文件</span>
    </button>
    <input type="file" id="up-file" class="visually-hidden" multiple
      accept=".epub,.pdf,.mobi,.azw3,.azw,.txt,.md,.docx,.xlsx,.xls,.pptx">
    <div class="uploading" id="up-box" hidden>
      <div class="progress"><i id="up-bar"></i></div>
      <p class="hint" id="up-note"></p>
    </div>
  </section>`;
}

function viewImport() {
  const inbox = S.inbox || [];
  const plan = S.plan;
  const bad = plan ? planBlockers(plan) : [];
  const docCount = (inbox || []).filter((f) => DOC_FMT.test(String(f.format || ''))).length + docsBackfill();
  const html = `<div class="page-head">
      <div>
        <h1>新书入库</h1>
        <p class="page-lead">上传新书到收件箱，核对书名与分类建议后一键入库。</p>
      </div>
    </div>
    ${S.importDone ? `<div class="banner ok"><span>已入库${S.importSplitJob || S.importDocsJob ? '。' : '，接下来按格式去拆章或分诊。'}${S.importSplitJob ? '已自动接力电子书拆章（完成后自动更新伴读与检索）。' : ''}${S.importDocsJob ? '已自动接转文档 md。' : ''}</span>
      <div class="row tight">
        ${S.importSplitJob
          ? '<button class="btn sm primary" data-act="split-goto">查看拆章进度 →</button>'
          : '<button class="btn sm" data-act="goto" data-tab="split">去拆章</button>'}
        <button class="btn sm" data-act="goto" data-tab="pdf">去分诊 PDF</button>
        ${S.importDocsJob ? '<button class="btn sm" data-act="docs-goto">查看文档转换 →</button>' : ''}
      </div></div>`
    : (docCount > 0 ? `<div class="banner ok"><span>后台提示：新入库的文本或 Office 文档会自动转为章节正文，可在「文档转正文」查看进度。</span>
      <a class="btn sm" href="#docs">查看文档转换 →</a></div>` : '')}
    ${S.legacyPlan ? `<div class="banner bad"><span>这份清单已过期（存在旧版格式冲突），请重新生成后再核对入库。</span>
      <button class="btn sm" data-write data-act="import-scan">重新生成</button></div>` : ''}
    ${dropCard()}
    ${inboxCard(inbox, !!plan)}
    <section class="card" aria-label="入库核对清单">
      <div class="card-head">
        <h2>${esc(planHead())}</h2>
        <div class="row tight">
          <button type="button" class="btn sm" data-act="plan-defaults" ${plan && plan.length ? '' : 'disabled'}>采用建议分类</button>
          <button type="button" class="btn sm${plan && plan.length && !bad.length ? ' primary' : ''}" data-write data-act="import-apply" ${plan && plan.length && !bad.length ? '' : 'disabled'}>确认入库</button>
        </div>
      </div>
      ${plan && plan.length ? planTable(plan) : `<div class="empty-state"><div>
        <div class="empty-state-title">还没有待核对的入库清单</div>
        <div class="empty-state-sub">收件箱放进新书后点「生成入库清单」，系统会自动识别书名、作者与分类建议。</div>
      </div></div>`}
    </section>`;
  S.docsSig = docsSig();
  return html;
}

function planTable(plan) {
  const counts = plan.reduce((acc, row) => {
    acc[row.action] = (acc[row.action] || 0) + 1;
    return acc;
  }, {});
  const summary = ['add', 'replace', 'delete', 'review']
    .map((key) => ACTION_WORD[key] + ' ' + num(counts[key] || 0)).join(' · ');
  const bad = planBlockers(plan);
  const sample = bad.slice(0, 3).join('；');
  return `<p class="hint" style="margin-bottom:8px">${esc(summary)}</p>
    <div class="legend">
      <span><b>新增</b> 收件箱 → 书库：建一本新书</span>
      <span><b>换源</b> 收件箱 → 书库：只换已有书主文件，书号不变</span>
      <span><b>删除</b> 收件箱 → 回收站：书库不动，可恢复</span>
      <span><b>待核</b> 先不动：决定后再入库</span>
    </div>
    ${bad.length ? `<div class="banner bad"><span>还有 ${num(bad.length)} 行没定：${esc(sample)}${bad.length > 3 ? ' …' : ''}</span></div>` : ''}
    <div class="tw table-wrap stack-mobile" tabindex="0" role="region" aria-label="入库核对表"><table class="plan-table">
    <colgroup><col style="width:88px"><col style="width:22%"><col style="width:94px"><col style="width:18%">
      <col style="width:10%"><col style="width:13%"><col style="width:88px"><col style="width:74px"><col></colgroup>
    <thead><tr class="group"><th></th><th colspan="2">收件箱</th><th colspan="5">书库（入库后）</th><th></th></tr>
      <tr><th>动作</th><th>文件</th><th>格式 / 大小</th><th>正式书名</th><th>作者</th>
      <th>分类</th><th>长期保留</th><th>编号</th><th>说明</th></tr></thead>
    <tbody>${plan.map(planRow).join('')}</tbody></table></div>
    <p class="hint" style="margin-top:10px">「不留」同样照常入库，之后可在「书目与分类 · 待退库与删书」统一处理；删除只把收件箱文件移进回收站。</p>`;
}

// ---- 文档转正文 ---------------------------------------------------
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

// 库里还有多少 doc 格式没转正文：没有 docs 任务时，这张卡靠它决定露不露面
function docsBackfill() {
  return (S.pending || []).filter((row) => DOC_FMT.test(String(row.format || ''))).length;
}

function docsPendingRows() {
  return (S.pending || []).filter((row) => DOC_FMT.test(String(row.format || '')));
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
    if (body && body.job_id) {
      S.docs = body;
      S.docsNone = false;
      docsJobRemember(body.job_id);
    } else {
      // 服务端说「一轮都没跑过」：当没有明细，别再每 2.5 秒打一次。
      S.docs = null;
      S.docsNone = true;
    }
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
  slot.innerHTML = docsCard() || docsIdleView(docsPendingRows());
}

function docsPendingSection(pendingRows) {
  return `<section class="card" aria-label="文档转换明细">
    ${pendingRows.length ? `
      <div class="card-head">
        <h2>待转文档列表</h2>
        <span class="pill half">${num(pendingRows.length)} 本</span>
      </div>
      <div class="tw table-wrap stack-mobile" tabindex="0" role="region" aria-label="待转文档表">
        <table>
          <thead><tr><th>编号</th><th>书名</th><th>分类</th><th>格式</th><th class="num">大小</th></tr></thead>
          <tbody>${pendingRows.map((r) => `<tr>
            <td data-label="编号" class="mono" translate="no">${esc(r.uid)}</td>
            <td data-label="书名" class="wrap">${esc(r.title)}</td>
            <td data-label="分类" class="wrap small muted">${esc([r.subject1, r.subject2].filter(Boolean).join(' · ') || '其他')}</td>
            <td data-label="格式" class="mono">${esc(r.format)}</td>
            <td data-label="大小" class="num">${fmtBytes(r.bytes)}</td>
          </tr>`).join('')}</tbody>
        </table>
      </div>`
    : `<div class="empty-state">
        <div>
          <div class="empty-state-title">全部文档类书目都已转为正文</div>
          <div class="empty-state-sub">新入库文档会自动接力转换，一般无需手工介入。</div>
        </div>
      </div>`}
  </section>`;
}

function docsIdleView(pendingRows) {
  return `<section class="card section" aria-label="文档转正文状态">
      <div class="card-head">
        <div>
          <h2>文档转正文</h2>
          <p class="small muted">TXT、DOCX、XLSX、PPTX 入库后会自动转换；这里用于补转历史遗漏或核对转换结果。</p>
        </div>
        <div class="row tight">
          <button type="button" class="btn sm" data-act="docs-refresh">刷新</button>
          <button type="button" class="btn sm primary" data-write data-act="docs-backfill">扫描并补转遗漏</button>
        </div>
      </div>
      <div class="strip">
        <div class="strip-cell"><div class="strip-name">待补转文档</div><div class="strip-read">${num(pendingRows.length)} 本</div><div class="strip-note">${pendingRows.length ? '等待转换' : '当前无遗漏'}</div></div>
        <div class="strip-cell"><div class="strip-name">最近一批完成</div><div class="strip-read">0 本</div><div class="strip-note">暂无近期批次</div></div>
        <div class="strip-cell"><div class="strip-name">失败待重试</div><div class="strip-read">0 本</div><div class="strip-note">无异常文件</div></div>
        <div class="strip-cell"><div class="strip-name">整体状态</div><div class="strip-read"><span class="status ${pendingRows.length ? 'half' : 'ok'}">${pendingRows.length ? '待处理' : '全部就绪'}</span></div><div class="strip-note">自动同步检索已启用</div></div>
      </div>
    </section>
    ${docsPendingSection(pendingRows)}`;
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
  const ndone = Number(sum.done || 0) || items.filter((it) => it.status === 'done').length;
  const nfail = Number(sum.failed) || failed.length;
  const nskip = Number(sum.skipped) || 0;
  const nnew = Number(scope.new) || 0;
  const nback = Number(scope.backfill) || 0;
  const pendingRows = docsPendingRows();

  const bits = ['本次新书 ' + num(nnew)];
  if (nback) bits.push('库内补转 ' + num(nback));
  bits.push('跳过 ' + num(nskip), '失败 ' + num(nfail));

  const bar = total
    ? `<div class="progress-label" style="margin-top:12px"><span>${esc(stateWord[0])} ${num(finished)} / ${num(total)} 本</span>
        <span>${share}%</span></div>
      <div class="progress"><i style="width:${share}%"></i></div>`
    : (running ? '<p class="hint" style="margin-top:10px">已排队，等前面的任务结束就开始。</p>' : '');
  const now = current
    ? `<p class="hint" style="margin-top:8px">正在转 <span class="mono">${esc(current)}</span>${docsTitle(current, items) ? ' · ' + esc(docsTitle(current, items)) : ''}</p>`
    : '';
  const scopeLine = rep
    ? `<p class="hint" style="margin-top:10px">${esc(bits.join(' · '))}</p>`
    : '';
  const badBanner = state === 'failed' && !rep
    ? `<div class="banner bad" style="margin-top:12px"><span>转换没过：${esc(summaryText(job && job.summary) || '看任务日志')}</span>
        ${body && body.job_id
    ? `<button class="btn sm" data-act="job-open" data-id="${esc(body.job_id)}">看日志</button>`
    : '<button class="btn sm" data-act="goto" data-tab="logs">看日志</button>'}</div>`
    : '';
  const failTable = failed.length ? `<div class="tw table-wrap stack-mobile" style="margin-top:12px" tabindex="0" role="region" aria-label="失败待重试列表"><table>
      <thead><tr><th>编号</th><th>书名</th><th>格式</th><th>原因</th><th>操作</th></tr></thead>
      <tbody>${failed.map((it) => `<tr>
        <td data-label="编号" class="mono" translate="no">${esc(it.uid)}</td>
        <td data-label="书名" class="wrap">${esc(it.title || docsTitle(it.uid, items) || '—')}</td>
        <td data-label="格式" class="mono">${esc(it.format || '')}</td>
        <td data-label="原因" class="wrap small">${esc(docsReason(it))}</td>
        <td data-label="操作"><button class="btn sm" data-write data-act="docs-retry" data-uid="${esc(it.uid)}">重试</button></td>
      </tr>`).join('')}</tbody></table></div>` : '';
  const rows = items.length ? `<details class="adv" style="margin-top:14px" data-docs="items"${S.docsOpen ? ' open' : ''}>
      <summary>查看最近一次转换明细（${num(items.length)} 本）</summary>
      <div class="tw table-wrap stack-mobile" style="margin-top:10px" tabindex="0" role="region" aria-label="最近转换明细表"><table>
        <thead><tr><th>编号</th><th>书名</th><th>格式</th><th>状态</th>
          <th class="num">耗时</th><th class="num">字数</th><th>说明</th></tr></thead>
        <tbody>${items.map((it) => {
          const st = DOCS_ITEM[it.status] || [it.status || '—', 'half'];
          const why = it.status === 'failed' ? docsReason(it) : (it.md ? '已转为正文' : '');
          return `<tr>
            <td data-label="编号" class="mono" translate="no">${esc(it.uid)}</td>
            <td data-label="书名" class="wrap">${esc(it.title || '—')}</td>
            <td data-label="格式" class="mono">${esc(it.format || '')}</td>
            <td data-label="状态"><span class="pill ${st[1]}">${esc(st[0])}</span></td>
            <td data-label="耗时" class="num">${it.elapsed_ms ? (Number(it.elapsed_ms) / 1000).toFixed(1) + ' 秒' : '—'}</td>
            <td data-label="字数" class="num">${it.chars ? num(it.chars) : '—'}</td>
            <td data-label="说明" class="wrap small muted">${esc(why)}</td>
          </tr>`;
        }).join('')}</tbody></table></div>
    </details>` : '';

  return `<div id="docs-card">
    <section class="card section" aria-label="文档转正文状态">
      <div class="card-head">
        <div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <h2>文档转正文</h2>
            <span class="pill ${stateWord[1]}">${esc(stateWord[0])}</span>
          </div>
          <p class="small muted">TXT、DOCX、XLSX、PPTX 入库后会自动转换；这里用于补转历史遗漏或核对转换结果。</p>
        </div>
        <div class="row tight">
          <button type="button" class="btn sm" data-act="docs-refresh">刷新</button>
          <button type="button" class="btn sm primary" data-write data-act="docs-backfill">扫描并补转遗漏</button>
        </div>
      </div>
      <div class="strip">
        <div class="strip-cell"><div class="strip-name">待补转文档</div><div class="strip-read">${num(pending)} 本</div><div class="strip-note">${pending ? '等待转换' : '当前无遗漏'}</div></div>
        <div class="strip-cell"><div class="strip-name">最近一批完成</div><div class="strip-read">${num(ndone)} 本</div><div class="strip-note">${nfail ? num(nfail) + ' 本异常' : '全部顺利通过'}</div></div>
        <div class="strip-cell"><div class="strip-name">失败待重试</div><div class="strip-read">${num(nfail)} 本</div><div class="strip-note">${nfail ? '下方可直接重试' : '无异常文件'}</div></div>
        <div class="strip-cell"><div class="strip-name">整体状态</div><div class="strip-read"><span class="status ${running || pending || nfail ? 'half' : 'ok'}">${running ? '转换中' : (pending || nfail ? '待处理' : '全部就绪')}</span></div><div class="strip-note">自动同步检索已启用</div></div>
      </div>
      ${badBanner}
      ${bar}
      ${now}
      ${scopeLine}
      ${failTable}
    </section>
    <section class="card" aria-label="文档转换明细">
      ${pendingRows.length ? `
        <div class="card-head"><h2>待转文档列表</h2><span class="pill half">${num(pendingRows.length)} 本</span></div>
        <div class="tw table-wrap stack-mobile" tabindex="0" role="region" aria-label="待转文档表"><table>
          <thead><tr><th>编号</th><th>书名</th><th>分类</th><th>格式</th><th class="num">大小</th></tr></thead>
          <tbody>${pendingRows.map((r) => `<tr>
            <td data-label="编号" class="mono" translate="no">${esc(r.uid)}</td>
            <td data-label="书名" class="wrap">${esc(r.title)}</td>
            <td data-label="分类" class="wrap small muted">${esc([r.subject1, r.subject2].filter(Boolean).join(' · ') || '其他')}</td>
            <td data-label="格式" class="mono">${esc(r.format)}</td>
            <td data-label="大小" class="num">${fmtBytes(r.bytes)}</td>
          </tr>`).join('')}</tbody></table></div>`
      : `<div class="empty-state">
          <div>
            <div class="empty-state-title">全部文档类书目都已转为正文</div>
            <div class="empty-state-sub">新入库文档会自动接力转换，一般无需手工介入。</div>
          </div>
        </div>`}
      ${rows}
    </section>
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
      await Promise.all([loadDocs(), loadPending(), loadStatus()]);
      paintDocs();
      renderNav();
      renderStats();
      if (detail && detail.job) toast(jobNote(detail.job), jobBad(detail.job));
    }).catch(() => {});
  } catch (err) {
    toast(err.message, true);   // 409「有写任务在跑」原样给出来
  }
}

// ---- 正文生产：电子书 / 文档 / PDF / 手工格式 ----------------------
function viewSplit() {
  const ebooks = S.pending.filter((r) => EBOOK.test(r.format));
  const allPicked = ebooks.length > 0 && ebooks.every((r) => S.selected.has(r.uid));
  const stagedFlag = S.splitBooks && S.splitBooks.some((b) => INSPECT_STATUS.indexOf(b.status) >= 0);
  return `<div class="page-head">
      <div>
        <h1>正文生产</h1>
        <p class="page-lead">把电子书、文档与 PDF 转为章节正文；完成后自动更新书目与检索。</p>
      </div>
    </div>
    ${contentSubtabs('split')}
    <div data-live="split-batch">${batchCard()}</div>
    ${S.review ? `<section class="card section" aria-label="问题复核详情">${reviewPanel()}</section>` : ''}
    <section class="card" aria-label="待拆章节的电子书">
      <div class="card-head">
        <h2>待拆章节的电子书</h2>
        <div class="row tight">
          <span class="pill ${ebooks.length ? 'half' : 'ok'}">${num(ebooks.length)} 本待拆</span>
          ${ebooks.length ? `<label class="check"><input type="checkbox" data-act="split-all"${allPicked ? ' checked' : ''}> 全选</label>
            <button type="button" class="btn sm primary" data-write data-act="split-run">开始拆章 (${num(S.selected.size)})</button>` : ''}
        </div>
      </div>
      ${ebooks.length ? `<details class="adv" style="margin-bottom:10px">
          <summary>更多拆章选项</summary>
          <label class="check"><input type="checkbox" id="split-replace"${S.replaceMd ? ' checked' : ''}> 重拆已有正文（覆盖现有章节）</label>
          <label class="check">同时处理 <input type="number" id="split-workers" value="${S.splitWorkers || 4}" min="1" max="16" style="width:64px"> 本</label>
        </details>
        <div class="tw table-wrap stack-mobile" tabindex="0" role="region" aria-label="待拆电子书表"><table>
          <thead><tr><th style="width:36px">选</th><th>编号</th><th>书名</th><th>格式</th><th class="num">大小</th></tr></thead>
          <tbody>${ebooks.map((row) => `<tr>
            <td data-label="选"><input type="checkbox" aria-label="选择 ${esc(row.title)}" data-act="split-pick" value="${esc(row.uid)}"${S.selected.has(row.uid) ? ' checked' : ''}></td>
            <td data-label="编号" class="mono" translate="no">${esc(row.uid)}</td>
            <td data-label="书名" class="wrap">${esc(row.title)}</td>
            <td data-label="格式" class="mono">${esc(row.format || '')}</td>
            <td data-label="大小" class="num">${fmtBytes(row.bytes)}</td>
          </tr>`).join('')}</tbody></table></div>`
      : `<div class="empty-state"><div>
          <div class="empty-state-title">全部电子书均已完成拆章</div>
          <div class="empty-state-sub">新导入的 EPUB / MOBI / AZW3 会自动进入这里等待切分章节${stagedFlag ? '；当前上方暂存区仍有待复核书目' : ''}。</div>
        </div></div>`}
    </section>`;
}

function batchCard() {
  if (!S.batch) return '';
  return `<section class="card section" aria-label="最近拆章批次产物">
    <div class="card-head">
      <h2>批次核对 · ${esc(S.batch)}</h2>
      <button type="button" class="btn sm" data-act="split-refresh">刷新状态</button>
    </div>
    ${splitBooks()}
  </section>`;
}

const INSPECT_STATUS = ['staged', 'staged-review', 'split-failed', 'convert-failed', 'error'];

function splitBooks() {
  if (!S.batch) return '';
  if (!S.splitBooks.length) return '<p class="empty">批次刚开，还没有单本记录。跑起来后点「刷新状态」。</p>';
  return `<div class="tw table-wrap stack-mobile" tabindex="0" role="region" aria-label="拆章批次记录"><table><thead><tr><th>编号</th><th>状态</th><th>结构检查</th><th>内容检查</th>
    <th class="num">文本量</th><th class="num">章数</th><th class="num">修复</th><th>操作</th></tr></thead>
    <tbody>${S.splitBooks.map((b) => `<tr>
      <td data-label="编号" class="mono" translate="no">${esc(b.uid)}</td>
      <td data-label="状态"><span class="pill ${b.status === 'staged-review' ? 'half' : (b.status === 'landed' || b.status === 'accepted' ? 'ok' : '')}">${esc(SPLIT_WORD[b.status] || b.status || '—')}</span></td>
      <td data-label="结构检查">${checkWord(b.verify_rc)}</td>
      <td data-label="内容检查">${checkWord(b.audit_rc)}</td>
      <td data-label="文本量" class="num">${b.audit_ratio ? pct(b.audit_ratio, 1) : '—'}</td>
      <td data-label="章数" class="num">${esc(b.chapters === null || b.chapters === undefined ? '—' : b.chapters)}</td>
      <td data-label="修复" class="num">${esc(b.fix || 0)}</td>
      <td data-label="操作" class="nowrap">
        <div class="row tight">
          <button type="button" class="btn sm" data-act="split-one" data-uid="${esc(b.uid)}">重拆这本</button>
          <button type="button" class="btn sm" data-act="split-recheck" data-uid="${esc(b.uid)}">重新检查</button>
          ${INSPECT_STATUS.indexOf(b.status) >= 0 ? `<button type="button" class="btn sm primary" data-act="split-inspect" data-uid="${esc(b.uid)}">查看问题</button>` : ''}
        </div>
      </td>
    </tr>`).join('')}</tbody></table></div>
    <p class="hint" style="margin-top:8px">重新检查只复核暂存区里已有的正文，过关后直接入库。</p>`;
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
      <button type="button" class="btn sm" data-act="review-close">关闭</button></div>
    <p class="small">状态 <span class="pill half">${esc(SPLIT_WORD[row.status] || row.status || '—')}</span>
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
    <p class="hint">确认本来就该少（如卷首序言、版权页或插图页），填写受理原因后即可放行入库。</p>
    ${row.uid ? `<div class="toolbar" style="margin-bottom:4px">
      <input type="text" id="split-accept-reason" placeholder="写受理原因（如：卷首序言/版权页无需收录）" size="34">
      <button type="button" class="btn sm primary" data-write data-act="split-accept" data-uid="${esc(row.uid)}">受理并重新检查</button>
    </div>` : ''}
    <p class="hint">文本量低于原文 88% 或高于 115% 才会被标出。</p>`;
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
  const partial = Number(part.partial) || 0;
  const partialNote = partial
    ? `；其中 ${num(partial)} 本有不可读页（已受理，非全书完整）` : '';
  return `<div class="toolbar" style="margin-top:10px">
    <span class="small">今天额度 <b>${num(today.used)}</b> / ${num(today.cap)} 页</span>
    <span class="spacer"></span>
    <span class="hint">${old}${partialNote}</span>
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
    if (row.has_md) marks.push('<span class="hint">已有正文</span>');
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
    <label class="check"><input type="checkbox" id="pdf-replace"${S.pdfReplace ? ' checked' : ''}> 覆盖已有正文</label>
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
  return `<h1>正文生产 · 待转 PDF 清单</h1>
    ${contentSubtabs('pdf')}
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
    closeModal();
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
    <label class="check"><input type="checkbox" id="pdf-replace"${S.pdfReplace ? ' checked' : ''}> 覆盖已有正文（re-run / 强制重转）</label>
    <div style="margin-top:10px"><button class="btn primary" data-write data-act="pdf-run">开始转写</button></div>
    <p class="hint">最多转几本按全书合计：文本层抽取、OCR 与强制转的存档书都占名额，填 0 表示不限。存档的书（图册、非中英文）默认不动，要转得逐本点会再问一次；未分诊的书只送去分诊，不出正文。</p>
  </div>`;
}

function viewDocs() {
  return `<div class="page-head">
      <div>
        <h1>正文生产</h1>
        <p class="page-lead">把电子书、文档与 PDF 转为章节正文；完成后自动更新书目与检索。</p>
      </div>
    </div>
    ${contentSubtabs('docs')}
    <div data-live="docs" id="docs-slot">${docsCard()}${(!S.docs && !docsBackfill()) ? docsIdleView(docsPendingRows()) : ''}</div>`;
}

function viewManual() {
  const autoRe = /^(epub|mobi|azw3|azw|pdf|txt|md|docx|xlsx|xls|pptx)$/i;
  const rows = S.pending.filter((r) => !autoRe.test(String(r.format || '')));
  return `<div class="page-head">
      <div>
        <h1>正文生产</h1>
        <p class="page-lead">把电子书、文档与 PDF 转为章节正文；完成后自动更新书目与检索。</p>
      </div>
    </div>
    ${contentSubtabs('manual')}
    <section class="card" aria-label="手工格式处理">
      <div class="card-head">
        <h2>手工格式书目（djvu / csv）</h2>
        <button type="button" class="btn sm primary" data-write data-act="manual-rescan">扫描落位</button>
      </div>
      ${rows.length ? `<div class="tw table-wrap stack-mobile" tabindex="0" role="region" aria-label="手工格式清单"><table>
        <thead><tr><th>编号</th><th>书名</th><th>格式</th><th>状态</th></tr></thead>
        <tbody>${rows.map((r) => `<tr>
          <td data-label="编号" class="mono" translate="no">${esc(r.uid)}</td>
          <td data-label="书名" class="wrap">${esc(r.title)}</td>
          <td data-label="格式" class="mono">${esc(r.format || '未知')}</td>
          <td data-label="状态" class="small muted">待手工放回章节正文</td>
        </tr>`).join('')}</tbody></table></div>`
      : `<div class="empty-state"><div>
          <div class="empty-state-title">没有需要手工处理的书目</div>
          <div class="empty-state-sub">在外部转好章节正文并放回书目目录后，点击右上角「扫描落位」即可自动入库。</div>
        </div></div>`}
    </section>`;
}

function pdfInlineRows() {
  const q = String(S.pdfInlineQ || '').trim().toLowerCase();
  return (S.books || []).filter((row) => {
    if (S.pdfInlineTag !== 'all' && row.disposition !== S.pdfInlineTag) return false;
    if (!q) return true;
    return String(row.uid || '').toLowerCase().indexOf(q) >= 0
      || String(row.title || '').toLowerCase().indexOf(q) >= 0;
  }).slice(0, 80);
}

function viewPdf() {
  const batch = (S.pdf && S.pdf.batch) || {};
  const today = S.pdfToday || { used: 0, cap: 20000 };
  const cap = Number(today.cap) || 20000;
  const used = Number(today.used) || 0;
  const quotaPct = cap > 0 && used > 0 ? Math.min(100, Math.max(1, Math.round(used / cap * 100))) : 0;
  const counts = S.bookCounts || { convert: 0, untriaged: 0, gallery: 0, ja: 0 };
  const stripMeta = [
    ['convert', '可转', '已定路线，可直接转写'],
    ['untriaged', '未分诊', '等待采样判断'],
    ['gallery', '存档·图册', '政策只存不转'],
    ['ja', '存档·非中英文', '政策只存不转'],
  ];
  const untriaged = S.books.filter((row) => row.disposition === 'untriaged');
  const split = S.pdfUntriaged;
  const totalUntriaged = split ? split.books : untriaged.length;
  const haveSuggest = untriaged.filter((row) => row.suggestion).length;
  const manualSuggest = untriaged.filter((row) => row.suggestion && row.suggestion.manual).length;
  const autoSuggest = Math.max(0, haveSuggest - manualSuggest);
  const todoTriage = Math.max(0, totalUntriaged - haveSuggest);
  const live = liveTriage();
  const part = batch.partition || {};
  const day = /(\d{4}-\d{2}-\d{2})$/.exec(String(batch.name || ''));
  const archived = !!batch.name && Number(batch.worklist) > 0;
  const old = archived
    ? `${esc(day ? day[1] : batch.name)} 已封存 · ${num(batch.worklist)} 本里 ${num(part.landed)} 落地、${num(part.left)} 未落地`
    : '还没封存过批次';
  const rows = pdfInlineRows();

  return `<div class="page-head">
      <div>
        <h1>正文生产</h1>
        <p class="page-lead">把电子书、文档与 PDF 转为章节正文；完成后自动更新书目与检索。</p>
      </div>
    </div>
    ${contentSubtabs('pdf')}
    <section class="card section" aria-label="PDF 队列与今日额度">
      <div class="card-head">
        <h2>PDF 状态概览</h2>
        <div class="row tight">
          <span class="small muted tnum">今天额度 <b>${num(used)}</b> / ${num(cap)}&nbsp;页</span>
          <button type="button" class="btn sm" data-act="pdf-refresh">刷新</button>
        </div>
      </div>
      <div class="strip" style="margin-bottom:12px">
        ${stripMeta.map(([key, label, note]) => `<button type="button" class="strip-cell" data-act="pdf-inline-tag" data-tag="${key}" aria-pressed="${S.pdfInlineTag === key}">
          <span class="strip-name">${esc(label)}</span>
          <span class="strip-read">${num(counts[key] || 0)} 本</span>
          <span class="strip-note">${esc(note)}</span>
        </button>`).join('')}
      </div>
      <div class="progress" aria-label="今日额度使用进度"><i style="width:${quotaPct}%"></i></div>
      <div class="toolbar" style="margin-top:12px;margin-bottom:0">
        <span class="small muted">${old}</span>
        <span class="spacer"></span>
        ${totalUntriaged > 0 || live ? `<button type="button" class="btn sm" data-act="pdf-triage-batch"${todoTriage && !live ? '' : ' disabled'}>开始分诊 ${num(todoTriage)} 本</button>` : `<button type="button" class="btn sm" data-act="pdf-triage-batch" disabled hidden>开始分诊 0 本</button>`}
        <button type="button" class="btn sm" data-act="pdf-detail"${archived ? '' : ' disabled'}>失败明细</button>
      </div>
      ${haveSuggest > 0 ? `<p class="hint" style="margin-top:8px">${autoSuggest ? '其中 ' + num(autoSuggest) + ' 本已经出了建议，等你在清单里确认。' : ''}${manualSuggest ? '另有 ' + num(manualSuggest) + ' 本需人工判断。' : ''}</p>` : ''}
      <div id="triage-progress" style="margin-top:${live ? '10px' : '0'}">${triageProgress()}</div>
    </section>

    <section class="card" aria-label="待转 PDF 清单">
      <div class="card-head">
        <h2>待转 PDF 清单</h2>
        <div class="row tight">
          <button type="button" class="btn sm" data-act="pdf-pick-open">去清单选书</button>
          <button type="button" class="btn sm primary" data-write data-act="pdf-run">开始转写选中 (${num(S.pick.size)})</button>
        </div>
      </div>
      <div class="toolbar">
        <div class="filter-group">
          <label for="pdf-inline-q" class="small muted" style="padding-left:4px">搜索</label>
          <input type="search" id="pdf-inline-q" autocomplete="off" spellcheck="false" value="${esc(S.pdfInlineQ)}" placeholder="编号或书名…" style="width:180px">
        </div>
        <label for="pdf-inline-disp" class="small muted">处置筛选</label>
        <select id="pdf-inline-disp">
          <option value="all"${S.pdfInlineTag === 'all' ? ' selected' : ''}>全部状态</option>
          <option value="convert"${S.pdfInlineTag === 'convert' ? ' selected' : ''}>可转</option>
          <option value="untriaged"${S.pdfInlineTag === 'untriaged' ? ' selected' : ''}>未分诊</option>
          <option value="gallery"${S.pdfInlineTag === 'gallery' ? ' selected' : ''}>存档·图册</option>
          <option value="ja"${S.pdfInlineTag === 'ja' ? ' selected' : ''}>存档·非中英文</option>
        </select>
        ${S.pdfInlineTag !== 'all' ? '<button type="button" class="btn sm" data-act="pdf-inline-reset">显示全部</button>' : ''}
        <span class="spacer"></span>
        <span class="small muted tnum">已选 ${num(S.pick.size)} 本</span>
      </div>
      <details class="adv" style="margin-bottom:12px">
        <summary>转写批处理参数</summary>
        <div class="row" style="margin-top:6px">
          <label class="check">最多转几本（合计） <input type="number" id="pdf-limit" value="${S.pdfLimit}" min="0" style="width:72px"></label>
          <label class="check">同时处理 <input type="number" id="pdf-workers" value="${S.pdfWorkers || 3}" min="1" max="8" style="width:64px"></label>
          <label class="check"><input type="checkbox" id="pdf-replace"${S.pdfReplace ? ' checked' : ''}> 覆盖已有正文</label>
        </div>
      </details>
      <div class="tw table-wrap stack-mobile" tabindex="0" role="region" aria-label="PDF 清单表">
        <table>
          <thead><tr><th style="width:36px">选</th><th>编号</th><th>书名</th><th>处置</th><th class="num">页数</th><th>说明</th><th>操作</th></tr></thead>
          <tbody>
            ${rows.length ? rows.map((row) => {
              const label = row.route ? routeWord(row.route) : (PICK_WORD[row.disposition] || row.disposition);
              const pages = row.pages === null || row.pages === undefined ? '—' : num(row.pages);
              const note = pickNote(row) || '—';
              return `<tr>
                <td data-label="选"><input type="checkbox" aria-label="选择 ${esc(row.title || row.uid)}" data-act="pick-one" value="${esc(row.uid)}"${S.pick.has(row.uid) ? ' checked' : ''}></td>
                <td data-label="编号" class="mono" translate="no">${esc(row.uid)}</td>
                <td data-label="书名" class="wrap">${esc(row.title || '')}</td>
                <td data-label="处置"><span class="pill ${row.disposition === 'convert' ? 'ok' : ''}">${esc(label)}</span></td>
                <td data-label="页数" class="num">${pages}</td>
                <td data-label="说明" class="clip small muted" title="${esc(note)}">${esc(note)}</td>
                <td data-label="操作"><button type="button" class="btn sm" data-act="pick-settle" data-uid="${esc(row.uid)}">改处置</button></td>
              </tr>`;
            }).join('') : '<tr><td colspan="7" class="empty">当前筛选条件下没有 PDF 书目。</td></tr>'}
          </tbody>
        </table>
      </div>
      ${S.books.length > 80 ? `<p class="hint" style="margin-top:8px">当前显示前 80 本，完整筛选与排序请点击右上角「去清单选书」。</p>` : ''}
    </section>`;
}

function triagePreviewHtml(info) {
  if (!info) return '';
  if (info.loading) return '<p class="hint">正在载入分诊指标与缩略图…</p>';
  const res = info.result;
  const thumbList = info.thumbs || [];
  if (!res && !thumbList.length) return '';
  const m = (res && res.metrics) || {};
  const metricBits = [];
  if (m.pages !== undefined) metricBits.push('页数 ' + num(m.pages));
  if (m.text_page_share !== undefined && m.text_page_share !== null) {
    metricBits.push('文字页比 ' + pct(m.text_page_share, 0));
  }
  if (m.image_share !== undefined && m.image_share !== null) {
    metricBits.push('图片占比 ' + pct(m.image_share, 0));
  }
  if (m.ink !== undefined && m.ink !== null) metricBits.push('墨迹 ' + m.ink);
  if (m.img_ppi_median) metricBits.push('图分辨率 ' + num(m.img_ppi_median) + '\u00A0ppi');
  const thumbs = thumbList.map((rel) => `<a href="/api/file?path=${encodeURIComponent(rel)}" target="_blank" rel="noopener"><img class="thumb-frame" src="/api/file?path=${encodeURIComponent(rel)}" alt="采样缩略图" style="max-height:140px"></a>`).join('');
  return `<div class="section" style="margin-bottom:0">
    ${metricBits.length ? `<p class="hint">${esc(metricBits.join(' · '))}</p>` : ''}
    ${thumbs ? `<div class="thumbs">${thumbs}</div>` : ''}
  </div>`;
}

function settleModal(row) {
  if (!row) return '';
  const current = row.route || (row.disposition === 'gallery' ? '不转-记图册'
    : (row.disposition === 'ja' ? '日文暂缓' : (row.suggestion || {}).route));
  const pages = row.pages === null || row.pages === undefined ? '' : ' · ' + num(row.pages) + ' 页';
  const acceptBox = row.progress === 'staged'
    ? `<div>
        <p class="hint">这本书已转写但留在暂存区未过审，若确认可放行，写受理原因后放入待复核入口：</p>
        <div class="toolbar" style="margin-bottom:0">
          <input type="text" id="pdf-accept-reason" placeholder="受理原因（如：扫图版页数吻合）" size="28">
          <button type="button" class="btn sm" data-write data-act="pdf-accept" data-uid="${esc(row.uid)}">写入受理并重审</button>
        </div>
      </div>` : '';
  return `<dialog class="modal" closedby="any" aria-labelledby="settle-dlg-title">
    <div class="dlg-head">
      <div>
        <h2 id="settle-dlg-title">改处置：${esc(row.title || row.uid)}</h2>
        <p class="small muted mono" translate="no">${esc(row.uid)}${esc(pages)}</p>
      </div>
      <button type="button" class="icon-btn" data-act="close-modal" aria-label="关闭弹窗">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
      </button>
    </div>
    <div class="dlg-body">
      ${triagePreviewHtml(S.triageInfo)}
      <label class="field"><span>处置方式</span><select id="settle-route">
        ${SETTLE_ROUTES.map((r) => `<option value="${esc(r)}"${r === current ? ' selected' : ''}>${esc(routeWord(r))}</option>`).join('')}
      </select></label>
      <p class="hint">改处置只写台账，不出正文。</p>
      ${acceptBox}
    </div>
    <div class="dlg-foot">
      <button type="button" class="btn" data-act="close-modal">取消</button>
      <button type="button" class="btn primary" data-write data-act="settle-submit" data-uid="${esc(row.uid)}">提交</button>
    </div>
  </dialog>`;
}

// ---- 同步与维护 ---------------------------------------------------
function maintainCardCell(name, cls, word, note) {
  return `<div class="strip-cell">
    <div class="strip-name">${esc(name)}</div>
    <div class="strip-read">${status(cls, word)}</div>
    <div class="strip-note">${esc(note)}</div>
  </div>`;
}

function viewMaintain() {
  const c = bookCounts();
  const info = statusIssues();
  const delta = (S.status && S.status.sync_delta) || null;
  const fts = (delta && delta.fts) || { added: [], modified: [], removed: [], drift_count: 0 };
  const emb = (delta && delta.embed) || { ok: false, engine_ready: false, added: [], modified: [], orphan: [], chunks: 0, garbage: 0, garbage_ratio: 0, compact_recommended: false };
  const exp = (delta && delta.expand) || { covered_books: 0, uncovered_books: 0 };
  const meta = (delta && delta.meta) || { ledger_stale: c.ledgerStale, catalog_missing: false, views_missing: false };
  const ftsUids = [...(fts.added || []).map((u) => '+' + u), ...(fts.modified || []).map((u) => '~' + u), ...(fts.removed || []).map((u) => '-' + u)];
  const embUids = [...(emb.added || []).map((u) => '+' + u), ...(emb.modified || []).map((u) => '~' + u), ...(emb.orphan || []).map((u) => '-' + u)];
  const embDrift = (emb.added || []).length + (emb.modified || []).length + (emb.orphan || []).length;
  const hasDrift = delta ? !!delta.has_drift : (c.lagBooks > 0 || c.indexExtra > 0 || c.ledgerStale);
  const autoEmbed = !S.settings || S.settings.auto_embed !== false;
  const garbagePct = Math.round((Number(emb.garbage_ratio) || 0) * 100);
  const isOk = statusState() === 'ok';

  // Cell 1: 书目与视图
  let metaCls = 'ok';
  let metaWord = '已对齐';
  let metaNote = num(c.books) + ' 本 · ' + num(c.awards) + ' 条奖项';
  if (!c.libKnown || !c.catalogKnown || !c.viewsKnown) {
    metaCls = 'miss';
    metaWord = '未知';
    const lack = [];
    if (!c.libKnown) lack.push('缺少书目库');
    if (!c.viewsKnown) lack.push('浏览入口缺失');
    if (!c.catalogKnown) lack.push('没有书目总表');
    metaNote = lack.join(' · ');
  } else if (c.broken) {
    metaCls = 'miss';
    metaWord = '需处理';
    metaNote = num(c.broken) + ' 条入口失效';
  } else if (meta.ledger_stale || meta.views_missing || c.unmatched) {
    metaCls = 'half';
    metaWord = '需注意';
    metaNote = meta.ledger_stale ? '台账有新改动待对齐'
      : (c.unmatched ? num(c.unmatched) + ' 条对不上文件' : '缺奖项入口');
  }

  // Cell 2: 全文检索
  const ftsCls = !c.indexKnown ? 'miss' : (c.lagBooks || c.lagChapters || c.indexExtra || c.indexExtraChapters || ftsUids.length ? 'half' : 'ok');
  const ftsWord = !c.indexKnown ? '未知'
    : (c.lagBooks || c.lagChapters ? '需注意'
      : (c.indexExtra || c.indexExtraChapters ? '需重建'
        : (ftsUids.length ? '待增量 ' + num(ftsUids.length) + ' 本'
          : (isOk ? '已同步' : '没有落后'))));
  const ftsNote = !c.indexKnown ? '索引缺失或读不到'
    : (c.lagBooks
      ? '落后 ' + num(c.lagBooks) + ' 本' + (c.lagChapters ? ' · ' + num(c.lagChapters) + ' 章' : '')
      : (c.lagChapters
        ? '落后 ' + num(c.lagChapters) + ' 章'
        : (c.indexExtra
          ? '比书库多 ' + num(c.indexExtra) + ' 本' + (c.indexExtraChapters ? ' · ' + num(c.indexExtraChapters) + ' 章' : '')
          : (c.indexExtraChapters
            ? '比书库多 ' + num(c.indexExtraChapters) + ' 章'
            : num(c.indexedChapters || c.chapters) + ' 章无滞后' + (c.filteredChapters ? '（跳过 ' + num(c.filteredChapters) + ' 非正文）' : '')))));

  // Cell 3: 语义检索
  const embCls = !emb.initialized ? (emb.engine_ready ? 'half' : 'out') : (embDrift || emb.compact_recommended ? 'half' : 'ok');
  const embWord = !emb.engine_ready && !emb.initialized ? '未启用'
    : (!emb.initialized ? '未首次构建'
      : (embDrift ? '待处理 ' + num(embDrift) + ' 本'
        : (isOk ? '已同步' : '正常')));
  const embNote = !emb.initialized
    ? (emb.engine_ready ? '可在高级运维启动首次构建' : '未配置本地向量引擎（不影响全文检索）')
    : (!emb.engine_ready
      ? num(emb.chunks) + ' 语块（未连接本地引擎）'
      : (num(emb.chunks) + ' 语块 · ' + (emb.garbage ? num(emb.garbage) + ' 冗余（' + garbagePct + '%）' : '零冗余')));

  // Cell 4: 体检状态
  const auditCls = !info.ran ? 'miss' : (info.stale ? 'half' : (c.fail ? 'miss' : (c.warn ? 'half' : (isOk ? 'ok' : 'half'))));
  const auditWord = !info.ran ? '未运行'
    : (info.stale ? '已过期' : (c.fail ? '需处理' : (c.warn ? num(c.warn) + ' 条提醒' : (isOk ? '正常' : '需注意'))));
  const auditNote = !info.ran ? '还没有体检记录'
    : (info.stale ? '库或索引比体检新'
      : (c.fail ? num(c.fail) + ' 项失败'
        : (c.warn ? '核心结构通过'
          : (isOk ? '全部通过' : '无失败项'))));

  const issuesSummary = info.rows.length
    ? (isOk && !c.fail && !info.stale
      ? `查看体检提醒（${num(info.rows.length)} 条 · 不影响日常使用）`
      : `查看体检提醒（${num(info.rows.length)} 条）`)
    : (info.stale ? '体检状态已过期，点击查看详情'
      : (!info.ran ? '尚未运行体检，点击查看详情'
        : (isOk ? '查看体检结果（全部通过）' : '查看体检说明')));
  const issuesOpen = c.fail > 0 || info.stale || !info.ran || !isOk;
  const driftUids = [];
  if (ftsUids.length) driftUids.push('全文差集：' + ftsUids.slice(0, 12).join(', ') + (ftsUids.length > 12 ? ` 等 ${num(ftsUids.length)} 本` : ''));
  if (emb.initialized && embUids.length) driftUids.push('向量差集：' + embUids.slice(0, 12).join(', ') + (embUids.length > 12 ? ` 等 ${num(embUids.length)} 本` : ''));

  return `<div class="page-head">
      <div>
        <h1>同步与维护</h1>
        <p class="page-lead">日常变更已自动差量同步；这里用于偏差兜底、主题增强与导出书目总表。</p>
      </div>
    </div>

    <section class="card section" aria-label="同步健康总览">
      <div class="card-head">
        <h2>同步健康总览</h2>
        <div class="row tight">
          <label class="check"><input type="checkbox" data-act="toggle-auto-embed"${autoEmbed ? ' checked' : ''}> 正文落地后自动同步语义索引</label>
          <button type="button" class="btn sm primary" data-write data-act="sync-reconcile">${hasDrift ? '补齐偏差（增量同步）' : '复核并增量对齐'}</button>
        </div>
      </div>
      <div class="strip" style="margin-bottom:12px">
        ${maintainCardCell('书目与视图', metaCls, metaWord, metaNote)}
        ${maintainCardCell('全文检索', ftsCls, ftsWord, ftsNote)}
        ${maintainCardCell('语义检索', embCls, embWord, embNote)}
        ${maintainCardCell('体检状态', auditCls, auditWord, auditNote)}
      </div>
      <div class="toolbar" style="margin-bottom:10px">
        <label class="check"><input type="checkbox" data-reconcile="catalog"${S.reconcileExtra.has('catalog') ? ' checked' : ''}> 对齐时同时更新书目总表（Excel / CSV）</label>
        <label class="check"><input type="checkbox" data-reconcile="doc"${S.reconcileExtra.has('doc') ? ' checked' : ''}> 同时刷新阅读版索引</label>
      </div>
      <details class="adv-issues" id="status-issues"${issuesOpen ? ' open' : ''}>
        <summary style="cursor:pointer;font-size:13px;font-weight:600;color:var(--ink)">${esc(issuesSummary)}</summary>
        <div style="margin-top:8px">
          ${issueList(info)}
          ${driftUids.map((line) => `<p class="hint mono" style="margin-top:4px">${esc(line)}</p>`).join('')}
          ${info.at ? `<p class="hint" style="margin-top:6px">体检于 ${esc(fmtTime(info.at).slice(5))}</p>` : ''}
        </div>
      </details>
    </section>

    <div class="grid c2 section">
      <section class="card" aria-label="主题增强">
        <div class="card-head">
          <h2>章节主题增强（按需）</h2>
          <button type="button" class="btn sm" data-write data-act="sync-expand"${exp.uncovered_books ? '' : ' disabled'}>扩展未覆盖 ${num(exp.uncovered_books)} 本</button>
        </div>
        <p class="small muted">为新书章节生成抽象主题提示，提升概念型提问的召回率；目前已有 ${num(exp.covered_books)} 本书完成覆盖。此项会调用外部模型额度，只在手动点击后运行。</p>
      </section>
      <section class="card" aria-label="导出书目总表">
        <div class="card-head">
          <h2>导出书目总表</h2>
          <span class="small muted">${c.catalogKnown && c.catalogAt ? '更新于 ' + esc(fmtTime(c.catalogAt).slice(5)) : '与当前台账一致'}</span>
        </div>
        <p class="small muted" style="margin-bottom:12px">导出包含每本书的编号、书名、分类与正文就绪状态。</p>
        <div class="row tight">
          <a class="btn sm" href="/api/file?path=${encodeURIComponent('build/书目总表.xlsx')}">导出 Excel 表格</a>
          <a class="btn sm" href="/api/file?path=${encodeURIComponent('build/书目总表.csv')}">导出 CSV 表格</a>
          ${c.prefillReady
            ? `<a class="btn sm" href="/api/file?path=${encodeURIComponent('tables/分类预填.csv')}">新书分类预填表</a>`
            : '<button type="button" class="btn sm" disabled title="当前没有未分类新书的预填表">新书分类预填表</button>'}
        </div>
      </section>
    </div>

    <section class="card" aria-label="高级运维与重建">
      <details class="adv">
        <summary>高级运维与重建（低频 · 仅整理碎片或异常排查时使用）</summary>
        <div class="adv-grid">
          ${!emb.initialized && emb.engine_ready ? `<div class="adv-item">
            <div><b>首次构建全库语义索引</b> <span class="pill half">支持断点续跑</span>
              <p class="hint">从零为全库正文建立语义向量索引（约 6–7 小时），建成后新书自动增量更新。</p></div>
            <button type="button" class="btn sm primary" data-write data-act="adv-action" data-op="embed_init">启动首次构建</button>
          </div>` : ''}
          <div class="adv-item">
            <div><b>压缩冗余语块</b> ${emb.compact_recommended ? '<span class="pill half">建议运行</span>' : ''}
              <p class="hint">回收退库与换源留下的旧向量碎片（当前 ${num(emb.garbage)} 块，占比 ${garbagePct}%），纯本地整理不重跑模型。</p></div>
            <button type="button" class="btn sm" data-write data-act="adv-action" data-op="embed_compact"${emb.garbage ? '' : ' disabled'}>执行压缩</button>
          </div>
          <div class="adv-item">
            <div><b>重建全文检索</b>
              <p class="hint">从零扫描全库重建全文倒排索引，耗时较长，仅在索引异常恢复时使用。</p></div>
            <button type="button" class="btn sm" data-write data-act="adv-action" data-op="fts_rebuild">全量重建</button>
          </div>
          <div class="adv-item">
            <div><b>重算全库语义索引</b>
              <p class="hint">强制重算全库所有章节向量，仅在更换检索模型时使用。</p></div>
            <button type="button" class="btn sm" data-write data-act="adv-action" data-op="embed_rebuild">全量重嵌</button>
          </div>
          <div class="adv-item">
            <div><b>深度体检与结构核查</b>
              <p class="hint">逐书核对源文件、正文与索引一致性，适合批量迁移后使用。</p></div>
            <button type="button" class="btn sm" data-write data-act="adv-action" data-op="deep_verify">运行深度体检</button>
          </div>
          <div class="adv-item">
            <div><b>清理旧任务记录</b>
              <p class="hint">清理超过 90 天或超出最近 200 条的已完成任务日志与临时预览图（跳过 30 天内有缩略图的任务），不影响书目与正文。</p></div>
            <button type="button" class="btn sm" data-act="jobs-clean">清理旧记录</button>
          </div>
        </div>
        <details class="adv" style="margin-top:12px">
          <summary>传统单步维护勾选</summary>
          <div id="maintain-steps">${MAINTAIN_STEPS.map(([key, title, text]) => `
            <label class="check" style="display:flex;margin:2px 0">
              <input type="checkbox" data-step="${key}"${S.maintainSteps.has(key) ? ' checked' : ''}>
              <span><span class="small">${esc(title)}</span>
              <span class="hint">${esc(text)}</span></span>
            </label>`).join('')}</div>
          <p class="row"><button type="button" class="btn sm" data-write data-act="maintain-run">跑选中的步骤</button></p>
        </details>
      </details>
    </section>`;
}

// ---- 删书与冷宫 ---------------------------------------------------
function viewRetire() {
  const drop = S.retireDrop || [];
  const rq = String(S.retireQuery || '').trim();
  const hits = S.retireHits || [];
  return `<div class="page-head">
      <div>
        <h1>书目与分类</h1>
        <p class="page-lead">修改书名、分类或保留策略后即刻生效，并自动同步阅读与检索。</p>
      </div>
    </div>
    ${catalogSubtabs('retire')}
    <section class="card section" aria-label="查找并移出书目">
      <div class="card-head">
        <h2>按编号或书名查找移出</h2>
        <span class="small muted">移出后原书保留在冷宫，阅读版与检索记录同步清理</span>
      </div>
      <div class="toolbar" style="margin-bottom:${hits.length || rq ? '12px' : '0'}">
        <div class="filter-group">
          <label for="retire-q" class="small muted" style="padding-left:4px">查找</label>
          <input type="search" id="retire-q" autocomplete="off" spellcheck="false" value="${esc(S.retireQuery)}" placeholder="输入编号或书名…" style="width:220px">
        </div>
        <button type="button" class="btn sm primary" data-act="retire-search">搜书</button>
      </div>
      ${hits.length ? `<div class="tw table-wrap stack-mobile" tabindex="0" role="region" aria-label="搜书结果表"><table>
        <thead><tr><th>编号</th><th>书名</th><th>分类</th><th class="num">章数</th><th class="num">奖项</th><th>操作</th></tr></thead>
        <tbody>${hits.map((b) => `<tr>
          <td data-label="编号" class="mono" translate="no">${esc(b.uid)}</td>
          <td data-label="书名" class="wrap">${esc(b.title)}</td>
          <td data-label="分类" class="wrap small muted">${esc([b.subject1, b.subject2].filter(Boolean).join(' · ') || '其他')}</td>
          <td data-label="章数" class="num">${esc(b.md && b.md.chapters !== undefined ? b.md.chapters : 0)}</td>
          <td data-label="奖项" class="num">${(b.awards || []).length}</td>
          <td data-label="操作"><button type="button" class="btn sm danger" data-act="retire-preview" data-uid="${esc(b.uid)}">预览移出</button></td>
        </tr>`).join('')}</tbody></table></div>`
      : (rq ? '<p class="small muted">没有找到匹配的书目，请换个编号或关键词。</p>' : '')}
    </section>
    <section class="card" aria-label="已标为不留的书目">
      <div class="card-head">
        <div style="display:flex;align-items:center;gap:8px">
          <h2>已标为“不留”的书目</h2>
          <span class="pill">${num(drop.length)} 本</span>
        </div>
        <button type="button" class="btn sm" data-act="retire-drop-refresh">刷新</button>
      </div>
      ${drop.length ? `<div class="tw table-wrap stack-mobile" tabindex="0" role="region" aria-label="待退库表"><table>
        <thead><tr><th>编号</th><th>书名</th><th>分类</th><th class="num">章数</th><th class="num">奖项</th><th>操作</th></tr></thead>
        <tbody>${drop.map((b) => `<tr>
          <td data-label="编号" class="mono" translate="no">${esc(b.uid)}</td>
          <td data-label="书名" class="wrap">${esc(b.title)}</td>
          <td data-label="分类" class="wrap small muted">${esc([b.subject1, b.subject2].filter(Boolean).join(' · ') || '其他')}</td>
          <td data-label="章数" class="num">${esc(b.md && b.md.chapters !== undefined ? b.md.chapters : 0)}</td>
          <td data-label="奖项" class="num">${(b.awards || []).length}</td>
          <td data-label="操作"><button type="button" class="btn sm danger" data-act="retire-preview" data-uid="${esc(b.uid)}">预览移出</button></td>
        </tr>`).join('')}</tbody></table></div>`
      : `<div class="empty-state"><div>
          <div class="empty-state-title">当前没有标记为“不留”的书</div>
          <div class="empty-state-sub">在全库台账里把某本书的保留策略改为「不留」，它会汇总到这里等待最终确认移出。</div>
        </div></div>`}
    </section>`;
}

function previewModal(p) {
  if (!p) return '';
  const srcList = p.sources || [];
  const sharedCount = srcList.filter((s) => Number(s.shared_with) > 0).length;
  return `<dialog class="modal" closedby="any" aria-labelledby="retire-dlg-title">
    <div class="dlg-head">
      <div>
        <h2 id="retire-dlg-title">移出流程预览：${esc(p.title || p.uid)}</h2>
        <p class="small muted">原书将迁入冷宫只读区保存，正文、阅读版与检索记录同步移除。</p>
      </div>
      <button type="button" class="icon-btn" data-act="close-modal" aria-label="关闭弹窗">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
      </button>
    </div>
    <div class="dlg-body">
      <div class="tw table-wrap" tabindex="0" role="region" aria-label="退库影响明细"><table><tbody>
        <tr><th>书目编号</th><td class="mono" translate="no">${esc(p.uid)}</td></tr>
        <tr><th>将清理内容</th><td class="tnum">已拆 ${num(p.md_chapters)} 章正文（${fmtBytes(p.md_bytes)}）、阅读版与全部检索记录（扩展 ${num(p.expand_rows)} 条 · 词法 ${num(p.index_rows)} 条 · 向量 ${num(p.embed_chunks || 0)} 块）</td></tr>
        <tr><th>源文件处理</th><td>${srcList.length ? num(srcList.length) + ' 个原始文件随书保留在冷宫归档区（' + (sharedCount ? num(sharedCount) + ' 个有合集共享' : '无共享冲突') + '）' : '原始文件随书移入冷宫归档区'}</td></tr>
        <tr><th>奖项与分类</th><td>${p.ledger ? '同步移除分类台账与 ' : ''}${esc((p.awards || []).map((a) => a.award || a).join('、') || '无')}奖项入口记录</td></tr>
      </tbody></table></div>
      <div class="grid c2" style="gap:10px">
        <div>
          <label class="small muted" for="apply-uid" style="display:block;margin-bottom:4px">输入编号确认（<span class="mono" translate="no">${esc(p.uid)}</span>）</label>
          <input type="text" id="apply-uid" placeholder="再输一遍编号" autocomplete="off" spellcheck="false" style="width:100%">
        </div>
        <div>
          <label class="small muted" for="apply-reason" style="display:block;margin-bottom:4px">移出原因</label>
          <input type="text" id="apply-reason" placeholder="移出原因" value="移出流程" autocomplete="off" style="width:100%">
        </div>
      </div>
      <label class="check"><input type="checkbox" id="apply-confirm"> <span>我确认将原书移入冷宫，并清除正文与检索记录</span></label>
      <p class="hint">确认时会再核对一次校验结果，信息不一致则自动拒绝。</p>
    </div>
    <div class="dlg-foot">
      <button type="button" class="btn" data-act="close-modal" autofocus>取消</button>
      <button type="button" class="btn danger" data-write data-act="retire-apply" data-uid="${esc(p.uid)}" data-hash="${esc(p.plan_hash || '')}">确认移出</button>
    </div>
  </dialog>`;
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
  const pending = ledgerPending(row);
  if (!editing) {
    const keepWord = RETENTION_WORD[row.保留] || row.保留;
    const keepCls = row.保留 === 'drop' ? 'miss' : (row.保留 === 'review' ? 'half' : 'ok');
    const cat1 = esc(row.一级 || '未分类');
    const cat2 = row.二级 ? ` · <span class="muted">${esc(row.二级)}</span>` : '';
    return `<tr class="${pending ? 'row-review' : ''}">
      <td data-label="选"><input type="checkbox" aria-label="选择 ${esc(row.正式书名 || row.uid)}" data-act="ledger-pick" value="${esc(row.uid)}"${S.ledgerPick.has(row.uid) ? ' checked' : ''}></td>
      <td data-label="编号" class="mono" translate="no">${esc(row.uid)}</td>
      <td data-label="正式书名" class="wrap">${esc(row.正式书名)}</td>
      <td data-label="分类" class="small"><span>${cat1}${cat2}</span></td>
      <td data-label="作者层" class="num">${esc(row.作者层)}</td>
      <td data-label="保留"><span class="pill ${keepCls}">${esc(keepWord)}</span></td>
      <td data-label="备注" class="clip small muted" title="${esc(row.备注)}">${esc(row.备注)}</td>
      <td data-label="操作" class="nowrap"><span class="row tight">${pending
    ? `<button type="button" class="btn sm primary" data-write data-act="ledger-row-confirm" data-uid="${esc(row.uid)}">同意</button>` : ''}<button type="button" class="btn sm" data-act="ledger-row-edit" data-uid="${esc(row.uid)}" aria-label="修改 ${esc(row.正式书名 || row.uid)}">改</button></span></td>
    </tr>`;
  }
  const f = (name) => {
    const v = row[name] == null ? '' : String(row[name]);
    if (name === '保留') {
      return `<select id="led-f-保留" aria-label="保留策略">${
        ['keep', 'review', 'drop'].map((k) => `<option value="${k}"${v === k ? ' selected' : ''}>${RETENTION_WORD[k]}</option>`).join('')}</select>`;
    }
    return `<input type="text" id="led-f-${esc(name)}" value="${esc(v)}"${name === 'uid' ? ' readonly' : ''} autocomplete="off">`;
  };
  return `<tr class="editing">
    <td></td>
    <td data-label="编号">${f('uid')}</td>
    <td data-label="正式书名">${f('正式书名')}</td>
    <td data-label="分类"><div style="display:flex;gap:4px">${f('一级')}${f('二级')}</div></td>
    <td data-label="作者层">${f('作者层')}</td>
    <td data-label="保留">${f('保留')}</td>
    <td data-label="备注" class="wrap">${f('备注')}</td>
    <td data-label="操作" class="nowrap"><span class="row tight"><button type="button" class="btn sm primary" data-write data-act="ledger-row-save" data-uid="${esc(row.uid)}">保存</button><button type="button" class="btn sm" data-act="ledger-edit-clear">取消</button></span></td>
  </tr>`;
}

const COLD_STATE_UI = {
  in_cold: { word: '在库', mark: '✓', pill: 'ok' },
  missing: { word: '缺失', mark: '✕', pill: 'miss' },
  unregistered: { word: '未登记', mark: '＋', pill: 'half' },
};

function coldStateCell(r) {
  const ui = COLD_STATE_UI[r.state] || COLD_STATE_UI.missing;
  const word = r.backfilled ? `${ui.word} · 补登` : ui.word;
  const checked = r.state === 'missing' && r.last_checked
    ? `<div class="small muted" style="margin-top:4px">核对 ${esc(fmtTime(r.last_checked))}</div>`
    : '';
  return `<span class="pill ${ui.pill}"><span aria-hidden="true">${ui.mark}</span>${esc(word)}</span>${checked}`;
}

function viewCold() {
  const data = S.cold;
  const items = (data && data.items) || [];
  const counts = (data && data.counts) || {};
  const err = S.coldError;
  const run = S.coldRun;
  const busy = S.coldBusy;
  const notices = [];
  if (err) {
    notices.push(`<div class="banner bad"><span>冷宫台账读取失败：${esc(err)}</span><span class="small">检查操作台的书库根是否指向带 books/_冷宫 与 工作区/批次 的目录。</span></div>`);
  }
  if (data && !data.cold_dir_exists) {
    notices.push(`<div class="banner warn"><span>书库目录不存在：没找到 ${esc(data.cold_dir)}。</span><span class="small">台账仍可核对，但实物一律按缺失算。</span></div>`);
  }
  if (data && !data.batches_exists) {
    notices.push('<div class="banner warn"><span>还没有退库批次目录（工作区/批次）。</span><span class="small">刷新会把 books/_冷宫 里的实物补登成第一条台账。</span></div>');
  }
  if (data && (data.errors || []).length) {
    notices.push(`<div class="banner warn"><span>台账有问题：${esc(data.errors.join('；'))}</span></div>`);
  }
  return `<div class="page-head">
      <div>
        <h1>书目与分类</h1>
        <p class="page-lead">修改书名、分类或保留策略后即刻生效，并自动同步阅读与检索。</p>
      </div>
    </div>
    ${catalogSubtabs('cold')}
    <section class="card" aria-label="退库台账与冷宫实物核验">
      <div class="card-head">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <h2>退库台账</h2>
          <span class="pill">${num(items.length)} 条</span>
        </div>
        <div class="row tight">
          <button type="button" class="btn sm" data-act="cold-refresh"${busy ? ' disabled aria-busy="true"' : ''}>${busy ? '核对中…' : '刷新'}</button>
        </div>
      </div>
      <p class="hint">这里显示退库台账；刷新会把台账按 <code>books/_冷宫</code> 实际文件校正：实物在记「在库」，实物没了记「缺失」（行保留），实物有台账没有就补登一行。冷宫与活库完全隔离，不进检索与推荐。</p>
      ${notices.join('')}
      ${data ? `<div class="stats" role="status" aria-label="核验汇总">
        <span class="stat">在库 <b>${num(counts.in_cold)}</b></span>
        <span class="stat">缺失 <b>${num(counts.missing)}</b></span>
        <span class="stat">补登 <b>${num(counts.backfilled)}</b></span>
        <span class="stat">未登记 <b>${num(counts.unregistered)}</b></span>
        ${run ? `<span class="stat ${run.changed ? 'warn' : 'ok'}">本次改动 <b>${num(run.changed)}</b></span>` : ''}
      </div>` : ''}
      ${items.length ? `<div class="tw table-wrap stack-mobile" tabindex="0" role="region" aria-label="退库台账"><table>
        <thead><tr><th>书名</th><th>编号</th><th>实物状态</th><th>批次</th><th>格式与体积</th><th>移出原因</th><th>归档时间</th></tr></thead>
        <tbody>${items.map((r) => `<tr>
          <td data-label="书名" class="wrap">${esc(r.title || r.uid || '—')}</td>
          <td data-label="编号" class="mono" translate="no">${esc(r.uid || '—')}</td>
          <td data-label="实物状态">${coldStateCell(r)}</td>
          <td data-label="批次" class="small muted" translate="no">${esc(r.batch || '—')}</td>
          <td data-label="格式与体积" class="small tnum"><span class="mono">${r.format ? `${esc(r.format)} · ${fmtBytes(r.bytes)}` : '—'}</span></td>
          <td data-label="移出原因" class="wrap small muted">${esc(r.reason || '移出流程')}</td>
          <td data-label="归档时间" class="mono small muted">${r.retired_at ? esc(fmtTime(r.retired_at)) : '—'}</td>
        </tr>`).join('')}</tbody></table></div>`
      : `<div class="empty-state"><div>
          <div class="empty-state-title">台账与冷宫都是空的</div>
          <div class="empty-state-sub">在「待退库与删书」确认移出的书目会记到这里；冷宫里放了没人登记的东西也能被刷新补登。</div>
        </div></div>`}
    </section>`;
}

function viewLedger() {
  const L = S.ledger;
  if (!L) {
    return `<div class="page-head"><div><h1>书目与分类</h1><p class="page-lead">修改书名、分类或保留策略后即刻生效，并自动同步阅读与检索。</p></div></div>
      ${catalogSubtabs('ledger')}<section class="card"><p class="hint">正在载入全库台账…</p></section>`;
  }
  const totalPending = L.ledger.filter(ledgerPending).length;
  const rows = ledgerRowFiltered();
  const visible = rows.slice(0, S.ledgerLimit);
  const pendingRows = rows.filter(ledgerPending);
  const picked = pendingRows.filter((r) => S.ledgerPick.has(r.uid)).length;
  const allPicked = pendingRows.length > 0 && picked === pendingRows.length;
  const stale = S.status && S.status.ledger && S.status.ledger.stale;
  const staleBanner = stale
    ? `<div class="banner warn"><div><b>台账有新改动尚未对齐书目</b><div class="hint">点右侧按钮立即同步元数据与浏览入口。</div></div>
        <button type="button" class="btn sm primary" data-write data-act="ledger-sync">立即同步元数据</button></div>` : '';
  const shown = rows.length !== L.ledger.length
    ? `<p class="hint" id="ledger-shown" style="margin-bottom:8px">筛选到 ${num(rows.length)} / ${num(L.ledger.length)} 本。</p>` : '<p class="hint" id="ledger-shown" hidden></p>';
  const pager = rows.length > visible.length
    ? `<div class="toolbar" id="ledger-pager" style="margin-top:12px;margin-bottom:0">
        <span class="hint">已显示 ${num(visible.length)} / ${num(rows.length)} 本</span>
        <button type="button" class="btn sm" data-act="ledger-more">加载更多 100 行</button>
        <button type="button" class="btn sm" data-act="ledger-show-all">显示全部 ${num(rows.length)} 本</button>
      </div>`
    : '<div class="toolbar" id="ledger-pager" hidden></div>';
  return `<div class="page-head">
      <div>
        <h1>书目与分类</h1>
        <p class="page-lead">修改书名、分类或保留策略后即刻生效，并自动同步阅读与检索。</p>
      </div>
    </div>
    ${catalogSubtabs('ledger')}
    ${staleBanner}
    <section class="card" aria-label="全库书目台账">
      <div class="card-head">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <h2>全库书目台账</h2>
          <span class="pill">${num(L.ledger.length)} 本</span>
          ${totalPending ? `<span class="pill half">${num(totalPending)} 本待复核</span>` : ''}
        </div>
        <button type="button" class="btn sm" data-act="ledger-load">刷新</button>
      </div>
      <div class="toolbar">
        <div class="filter-group">
          <label for="ledger-q" class="small muted" style="padding-left:4px">搜索</label>
          <input type="search" id="ledger-q" autocomplete="off" spellcheck="false" value="${esc(S.ledgerQ || '')}" placeholder="编号、书名、分类或备注…" style="width:210px">
        </div>
        <div class="seg" role="group" aria-label="复核状态筛选">
          <button type="button" class="seg-btn" data-act="ledger-scope" data-scope="all" aria-pressed="${!S.ledgerPendingOnly}">全部书目</button>
          <button type="button" class="seg-btn" data-act="ledger-scope" data-scope="pending" aria-pressed="${!!S.ledgerPendingOnly}">只看待复核</button>
        </div>
        <span class="spacer"></span>
        <button type="button" class="btn sm" data-act="ledger-pick-all-btn"${pendingRows.length ? '' : ' disabled'}>${allPicked ? '取消全选待复核' : '全选当前页待复核'}</button>
        <button type="button" class="btn sm primary" data-write data-act="ledger-confirm-pick"${picked ? '' : ' disabled'}>同意选中 ${num(picked)} 本</button>
      </div>
      ${shown}
      <div class="tw table-wrap stack-mobile" tabindex="0" role="region" aria-label="书目台账表"><table class="ledger-table">
        <thead><tr><th style="width:36px">选</th><th>编号</th><th>正式书名</th><th>分类</th><th class="num">作者层</th><th>保留</th><th>备注</th><th>操作</th></tr></thead>
        <tbody>${visible.length ? visible.map(ledgerRowHtml).join('')
    : `<tr><td colspan="8" class="empty">${S.ledgerPendingOnly ? '没有备注带「待复核」的行了。' : '没有匹配的书目。'}</td></tr>`}</tbody>
      </table></div>
      ${pager}
    </section>`;
}

function logRows() {
  return S.jobs.filter((j) => !S.filter || j.state === S.filter);
}

function jobPillCls(state) {
  if (state === 'succeeded') return 'ok';
  if (state === 'awaiting_review' || state === 'running' || state === 'queued' || state === 'cancelling') return 'half';
  if (state === 'failed' || state === 'orphaned' || state === 'cancelled') return 'miss';
  return '';
}

function logRowsHtml(rows) {
  return rows.length ? rows.map((j) => {
    const rhref = reviewHref(j);
    const selected = !!(S.logJob && S.logJob.id === j.id);
    const pillCls = jobPillCls(j.state);
    return `<tr data-job-id="${esc(j.id)}"${selected ? ' class="active row-active"' : ''}>
        <td data-label="创建" class="nowrap mono small">${esc(fmtTime(j.created))}</td>
        <td data-label="类型" class="nowrap small">${esc(KIND_WORD[j.kind] || j.kind)}</td>
        <td data-label="任务" class="wrap">${esc(summaryText(j.title))}</td>
        <td data-label="状态"><span class="pill ${pillCls}">${esc(JOB_WORD[j.state] || j.state)}</span></td>
        <td data-label="耗时" class="num">${j.started && j.ended ? esc(seconds(j.started, j.ended)) : '—'}</td>
        <td data-label="摘要" class="wrap small muted">${esc(summaryText(j.summary))}</td>
        <td data-label="操作" class="nowrap"><span class="row tight">${rhref ? `<button type="button" class="btn sm primary" data-act="job-handle" data-id="${esc(j.id)}">去处理</button>` : ''}<button type="button" class="btn sm${selected ? ' primary' : ''}" data-act="job-open" data-id="${esc(j.id)}">详情</button></span></td>
      </tr>`;
  }).join('') : '<tr><td colspan="7" class="empty">该状态下暂无任务记录。</td></tr>';
}

function viewLogs() {
  const rows = logRows();
  const states = ['', 'queued', 'running', 'awaiting_review', 'succeeded', 'failed',
    'cancelled', 'orphaned'];
  const quickStates = [['', '全部'], ['awaiting_review', '待确认'], ['running', '在跑'], ['succeeded', '已完成'], ['failed', '失败']];
  const body = logRowsHtml(rows);
  S.logRowsHtml = body;
  return `<div class="page-head">
      <div>
        <h1>任务与日志</h1>
        <p class="page-lead">全库所有后台任务的唯一查看与人工收口中心。</p>
      </div>
    </div>
    <section class="card section" aria-label="任务列表">
      <div class="toolbar">
        <div class="seg" role="group" aria-label="任务状态筛选">
          ${quickStates.map(([k, lbl]) => `<button type="button" class="seg-btn" data-act="log-seg" data-state="${k}" aria-pressed="${S.filter === k}">${esc(lbl)}</button>`).join('')}
        </div>
        <select id="log-filter" aria-label="全部任务状态筛选">
          ${states.map((s) => `<option value="${s}"${s === S.filter ? ' selected' : ''}>${s ? JOB_WORD[s] : '全部状态'}</option>`).join('')}
        </select>
        <button type="button" class="btn sm" data-act="jobs-refresh">刷新</button>
        <span class="spacer"></span>
        <span class="small muted tnum" id="log-count">共 ${num(rows.length)} 个任务</span>
      </div>
      <div class="tw table-wrap stack-mobile" tabindex="0" role="region" aria-label="任务列表"><table>
        <thead><tr><th>创建</th><th>类型</th><th>标题</th><th>状态</th><th class="num">耗时</th><th>摘要</th><th>操作</th></tr></thead>
        <tbody id="log-rows">${body}</tbody>
      </table></div>
    </section>
    <section class="${S.logJob ? 'card' : 'card'}" id="job-detail" aria-label="任务详情">${jobDetail()}</section>`;
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
  return steps.length ? steps.map((s) => {
    const sCls = s.state === 'ok' ? 'ok' : (s.state === 'fail' ? 'miss' : 'half');
    return `<tr><td data-label="#" class="num">${s.seq + 1}</td>
        <td data-label="步骤">${esc(s.name)}</td>
        <td data-label="状态"><span class="pill ${sCls}">${esc(STEP_WORD[s.state] || s.state)}</span></td>
        <td data-label="说明" class="wrap small">${esc(stepNote(s.detail))}</td>
        <td data-label="开始" class="nowrap mono small">${esc(fmtTime(s.started))}</td></tr>`;
  }).join('')
    : '<tr><td colspan="5" class="empty">没有步骤</td></tr>';
}

function commandsHtml(detail) {
  const command = ((detail && detail.commands) || []).map((c) => (c.argv || []).join(' ')).join('\n');
  return `<pre class="log">${esc(command || '没有记录')}</pre>`;
}

function cancelButton(id) {
  return `<button class="btn sm" id="job-cancel" data-act="job-cancel" data-id="${esc(id)}">停止</button>`;
}

function jobDetail() {
  const job = S.logJob;
  if (!job) return '<p class="empty">点上面表格里的「详情」，这里会显示步骤和日志。</p>';
  const detail = S.logDetail || {};
  const running = ['queued', 'running'].indexOf(job.state) >= 0;
  const diagOpen = running || job.state === 'failed' || job.state === 'orphaned';
  const pillCls = jobPillCls(job.state);
  return `<div class="card-head">
      <div>
        <h2>${esc(summaryText(job.title))}</h2>
        <p class="small muted mono" id="job-meta" translate="no">任务号 ${esc(job.id)} · ${esc(fmtTime(job.started))} → ${esc(fmtTime(job.ended))}</p>
      </div>
      <div class="row tight">
        <span class="pill ${pillCls}" id="job-state">${esc(JOB_WORD[job.state] || job.state)}</span>
        ${running ? cancelButton(job.id) : ''}
        <button type="button" class="btn sm" data-write data-act="job-rerun" data-id="${esc(job.id)}">重跑</button>
        <a class="btn sm" href="/api/file?path=${encodeURIComponent('工作区/运维/jobs/' + job.id + '/log.txt')}">下载日志</a>
      </div>
    </div>
    <p class="small" id="job-summary">${esc(summaryText(job.summary))}</p>
    ${jobReviewBox(job)}
    <div class="tw table-wrap stack-mobile" id="job-steps" style="margin-bottom:12px" tabindex="0" role="region" aria-label="任务步骤表"><table>
      <thead><tr><th>#</th><th>步骤</th><th>状态</th><th>说明</th><th>开始</th></tr></thead>
      <tbody>${stepsHtml(detail)}</tbody>
    </table></div>
    <details class="adv" id="job-diag"${diagOpen ? ' open' : ''}>
      <summary>技术诊断：运行命令与日志尾部</summary>
      <div id="job-commands" style="margin-top:8px">${commandsHtml(detail)}</div>
      <h3 style="margin-top:12px;margin-bottom:6px">日志尾部 200 行</h3>
      <pre class="log" id="job-log" data-painted="0">${esc(detail.log_tail || '')}</pre>
    </details>`;
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
    box.className = 'card';
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
  const pillCls = jobPillCls(job.state);
  if (stateEl) {
    if (stateEl.textContent !== word) stateEl.textContent = word;
    stateEl.className = 'pill ' + pillCls;
  }
  const cancel = box.querySelector('#job-cancel');
  if (cancel) {
    const running = ['queued', 'running'].indexOf(job.state) >= 0;
    if (!running) cancel.remove();
  } else if (['queued', 'running'].indexOf(job.state) >= 0) {
    const rerun = box.querySelector('[data-act="job-rerun"]');
    if (rerun) rerun.insertAdjacentHTML('beforebegin', cancelButton(job.id));
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
  const revBox = box.querySelector('#job-review-box');
  if (job.state === 'awaiting_review') {
    if (!revBox && sumEl) sumEl.insertAdjacentHTML('afterend', jobReviewBox(job));
  } else if (revBox) {
    revBox.remove();
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
    cmdBox.innerHTML = cmdBody;
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

function scrollJobDetail(instant) {
  const box = document.querySelector('#job-detail');
  if (!box) return;
  const reduced = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  if (typeof box.scrollIntoView === 'function') {
    box.scrollIntoView({ block: 'start', behavior: (instant || reduced) ? 'auto' : 'smooth' });
  }
  if (box.classList) {
    box.classList.remove('detail-focus');
    void box.offsetWidth;
    box.classList.add('detail-focus');
    clearTimeout(scrollJobDetail.timer);
    scrollJobDetail.timer = setTimeout(() => {
      const cur = document.querySelector('#job-detail');
      if (cur && cur.classList) cur.classList.remove('detail-focus');
    }, 1200);
  }
}

// ---- 数据加载 -----------------------------------------------------
async function loadStatus(deep) {
  try {
    S.payload = await api('/api/status' + (deep ? '?refresh=1&deep=1' : ''));
    S.status = S.payload.status || {};
    S.statusAt = Date.now();
  } catch (err) {
    S.payload = { ok: false, error: err.message, status: {} };
    S.status = {};
  }
  renderStats();
  renderNav();
}

async function loadStatusIfStale(maxAgeMs = 5000) {
  if (S.payload && S.statusAt && Date.now() - S.statusAt < maxAgeMs) return;
  await loadStatus();
}

async function loadJobs(opts) {
  const body = await api('/api/jobs?limit=200');
  S.jobs = body.jobs || [];
  if (S.payload) S.payload.state_counts = body.counts || {};
  renderNav();
  if (S.tab !== 'logs' || (opts && opts.quiet)) return;
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
    S.nextUid = (body && body.next_uid) || S.nextUid || '';
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

async function loadCold() {
  try {
    S.cold = await api('/api/cold');
    S.coldError = '';
  } catch (err) {
    S.cold = null;
    S.coldError = err.message || '未知错误';
  }
}

async function loadSettings() {
  try {
    const body = await api('/api/settings');
    S.settings = {
      auto_embed: body.auto_embed !== false,
      auto_split: body.auto_split !== false,
    };
  } catch (err) {
    S.settings = { auto_embed: true, auto_split: true };
  }
}

async function loadServices() {
  try {
    const body = await api('/api/services');
    S.services = body.services || [];
  } catch (err) {
    S.services = [];
  }
}

async function refreshTab(allowStaleStatus) {
  const statusTask = () => (allowStaleStatus ? loadStatusIfStale() : loadStatus());
  if (S.tab === 'overview') {
    await Promise.all([statusTask(), loadJobs(), loadPending(), loadServices()]);
  } else if (S.tab === 'import') {
    await Promise.all([
      api('/api/inbox').then((inboxBody) => {
        S.inbox = inboxBody.items || [];
        S.inboxIgnored = inboxBody.ignored || [];
      }),
      loadLatestPlan(),
      loadDocs(),
    ]);
  } else if (S.tab === 'split') {
    await Promise.all([
      statusTask(),
      loadJobs(),
      loadPending(),
      S.batch ? loadSplitBooks() : Promise.resolve(),
    ]);
    if (!S.batch) await loadSplitBooks();
  } else if (S.tab === 'docs') {
    await Promise.all([statusTask(), loadJobs(), loadPending(), loadDocs()]);
  } else if (S.tab === 'manual') {
    await Promise.all([statusTask(), loadJobs(), loadPending()]);
  } else if (S.tab === 'pdf') {
    await Promise.all([loadJobs(), loadPdf(), loadPdfBooks()]);
  } else if (S.tab === 'pdf-list') {
    await Promise.all([loadJobs(), loadPdfBooks()]);
  } else if (S.tab === 'ledger') {
    await Promise.all([loadLedger(), statusTask(), loadRetireDrop()]);
  } else if (S.tab === 'maintain') {
    await Promise.all([statusTask(), loadJobs(), loadSettings()]);
  } else if (S.tab === 'retire') {
    await Promise.all([statusTask(), loadJobs(), loadRetireDrop()]);
  } else if (S.tab === 'cold') {
    await Promise.all([statusTask(), loadCold(), loadRetireDrop()]);
  } else if (S.tab === 'logs') {
    await loadJobs();
  }
}

async function go(tab, params) {
  const target = ALL_TABS.indexOf(tab) >= 0 ? tab : 'overview';
  const seq = ++S.tabSeq;
  S.tab = target;
  applyHashParams(target, params || {});
  render();
  try {
    await refreshTab(true);
  } catch (err) {
    toast(err.message, true);
  }
  if (seq !== S.tabSeq) return;
  if (S.tab === 'logs') {
    const rows = logRows();
    if (S.filter === 'awaiting_review') {
      if (rows.length && (!S.logJob || S.logJob.state !== 'awaiting_review')) {
        S.logJob = rows[0];
        S.logOpenId = null;
      }
    } else if (!S.logJob && rows.length) {
      S.logJob = rows[0];
      S.logOpenId = null;
    }
  }
  render();
  if (S.tab === 'logs' && S.logJob) {
    await openLog(S.logJob.id, true);
    if (S.filter === 'awaiting_review' && seq === S.tabSeq && S.tab === 'logs') scrollJobDetail(true);
  }
}

// ---- 任务轮询 -----------------------------------------------------
async function openLog(jobId, quiet) {
  try {
    const body = await api('/api/jobs/' + encodeURIComponent(jobId));
    if (!body || !body.job) return;
    if (S.logJob && S.logJob.id && S.logJob.id !== jobId) return;
    S.logJob = body.job;
    S.logDetail = body;
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
    await Promise.all([loadStatus(steps.indexOf('audit') < 0), loadJobs(), loadPending()]);
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
  const allPicked = rows.length > 0 && picked === rows.length;
  const btn = document.querySelector('[data-act="ledger-confirm-pick"]');
  if (btn) {
    btn.textContent = '同意选中 ' + num(picked) + ' 本';
    btn.disabled = !picked;
  }
  const btnAll = document.querySelector('[data-act="ledger-pick-all-btn"]');
  if (btnAll) {
    btnAll.textContent = allPicked ? '取消全选待复核' : '全选当前页待复核';
    btnAll.disabled = !rows.length;
  }
  const all = document.querySelector('[data-act="ledger-pick-all"]');
  if (all) all.checked = allPicked;
}

async function confirmLedgerRows(uids) {
  setBusy(true);
  try {
    const body = await post('/api/ledger/rows/confirm', { uids: uids });
    const n = body.confirmed || 0;
    const skipped = (body.skipped || []).length;
    uids.forEach((uid) => S.ledgerPick.delete(uid));
    await Promise.all([loadLedger(), loadStatus(true)]);
    render();
    const syncWord = body.synced ? '并已自动同步书目库' : '';
    toast(n
      ? '同意了 ' + n + ' 行' + syncWord + (skipped ? '，跳过 ' + skipped + ' 行（没有「待复核」或已不在账上）' : '')
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
    setBusy(true);
    try {
      const body = await post('/api/ledger/row', { row });
      S.ledgerEdit = null;
      await Promise.all([loadLedger(), loadStatus(true), loadRetireDrop()]);
      render();
      toast(body && body.synced ? ('已保存 ' + uid + ' 并自动同步书目库') : ('已存 ' + uid));
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
    S.ledgerLimit = 100;
    render();
  },

  async 'ledger-scope'(el) {
    S.ledgerPendingOnly = el.dataset.scope === 'pending';
    S.ledgerLimit = 100;
    render();
  },

  async 'ledger-pick-all-btn'() {
    const rows = ledgerRowFiltered().filter(ledgerPending);
    const allPicked = rows.length > 0 && rows.every((r) => S.ledgerPick.has(r.uid));
    if (allPicked) rows.forEach((r) => S.ledgerPick.delete(r.uid));
    else rows.forEach((r) => S.ledgerPick.add(r.uid));
    render();
  },

  async 'log-seg'(el) {
    S.filter = el.dataset.state || '';
    render();
  },

  async 'ledger-more'() {
    S.ledgerLimit += 100;
    render();
  },

  async 'ledger-show-all'() {
    S.ledgerLimit = (S.ledger && S.ledger.ledger ? S.ledger.ledger.length : 10000);
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

  async 'ledger-sync'() {
    await runMaintain(['db', 'catalog']);
    await Promise.all([loadLedger(), loadStatus()]);
    render();
  },

  async 'ledger-edit-clear'() {
    S.ledgerEdit = null;
    render();
  },

  async 'status-refresh'() {
    const btn = document.getElementById('btn-status-refresh');
    if (btn && btn.classList) {
      btn.classList.remove('spin');
      void btn.offsetWidth;
      btn.classList.add('spin');
    }
    await loadStatus(true);
    await loadJobs();
    render();
  },

  async 'pick-files'(el) {
    const input = $('#up-file');
    if (input && el !== input) input.click();
  },

  async 'inbox-refresh'() {
    const body = await api('/api/inbox');
    S.inbox = body.items || [];
    S.inboxIgnored = body.ignored || [];
    render();
  },

  async 'import-scan'() {
    setBusy(true);
    try {
      const body = await post('/api/import/scan');
      toast('开始识别，好了会出现在下面');
      const detail = await watchJob(body.job_id, afterJob, 600);
      if (detail && (detail.job.state === 'awaiting_review' || detail.job.state === 'succeeded')) {
        S.planJob = body.job_id;
        await loadPlan(body.job_id);
        S.legacyPlan = false;
        if (S.plan && S.plan.length) {
          toast('清单好了，核对后入库');
        } else {
          S.plan = null;
          S.planJob = null;
          toast(summaryText(detail.job.summary) || '收件箱里没有可入库的书');
        }
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
    S.importSplitJob = '';
    S.importSplitBatch = '';
    setBusy(true);
    try {
      await post('/api/import/plan', { job_id: S.planJob, items: S.plan });
      const body = await post('/api/import/apply',
        { plan_path: '工作区/运维/jobs/' + S.planJob + '/plan.json' });
      toast('开始入库，完成后会提示');
      // 入库要 ingest + 重建 DB + 书目表，全库重建不止两分钟，上限放宽到 15 分钟
      const detail = await watchJob(body.job_id, afterJob, 1300);
      if (detail && detail.job.state === 'succeeded') {
        toast(detail.split_job ? '入库完成，已自动开始电子书拆章' : '入库完成');
        S.importDone = true;
        S.plan = null;
        S.planJob = null;
        S.legacyPlan = false;
        // 入库任务跑完会自动接 docs.run / split.run：同步状态，横幅上可直接查看进度
        if (detail.docs_job) {
          S.importDocsJob = String(detail.docs_job);
          docsJobSet(S.importDocsJob);
        }
        if (detail.split_job) {
          S.importSplitJob = String(detail.split_job);
          S.importSplitBatch = String(detail.split_batch || '');
          if (S.importSplitBatch) S.batch = S.importSplitBatch;
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
    if (location.hash !== '#docs') location.hash = '#docs';
    else await go('docs');
  },

  async 'split-goto'() {
    if (S.importSplitBatch) S.batch = S.importSplitBatch;
    if (location.hash !== '#split') location.hash = '#split';
    else await go('split');
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
        { uids: [el.dataset.uid], only: el.dataset.uid, batch: S.batch, workers: 1, replace: true });
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

  async 'split-accept'(el) {
    if (!S.batch) return;
    const uid = el.dataset.uid;
    const reason = String((document.getElementById('split-accept-reason') || {}).value || '').trim();
    if (!reason) {
      toast('先写受理原因', true);
      return;
    }
    setBusy(true);
    try {
      await post('/api/split/accept', { uid, batch: S.batch, reason });
      toast('已写受理原因，开始重新检查 ' + uid);
      const body = await post('/api/split/recheck', { uid, batch: S.batch });
      const detail = await watchJob(body.job_id, afterJob);
      if (detail) toast(jobNote(detail.job), jobBad(detail.job));
      S.review = null;
      S.reviewJob = null;
      await Promise.all([loadSplitBooks(), loadPending(), loadJobs(), loadStatus()]);
      render();
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

  async 'pdf-inline-tag'(el) {
    const tag = el.dataset.tag || 'all';
    S.pdfInlineTag = S.pdfInlineTag === tag ? 'all' : tag;
    render();
  },

  async 'pdf-inline-reset'() {
    S.pdfInlineTag = 'all';
    S.pdfInlineQ = '';
    render();
  },

  async 'pick-one'(el) {
    if (el.checked) S.pick.add(el.value);
    else S.pick.delete(el.value);
    if (S.tab === 'pdf-list') {
      paintPick();
    } else {
      syncPdfLimit();
      const lim = document.querySelector('#pdf-limit');
      if (lim) lim.value = String(S.pdfLimit);
      const runBtn = document.querySelector('#view [data-act="pdf-run"]');
      if (runBtn) runBtn.textContent = '开始转写选中 (' + num(S.pick.size) + ')';
      render();
    }
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
    S.triageInfo = { loading: true };
    openModal(settleModal(row));
    try {
      const info = await api('/api/pdf/triage-info?uid=' + encodeURIComponent(row.uid));
      if (S.settle && S.settle.uid === row.uid) {
        S.triageInfo = info;
        openModal(settleModal(row));
      }
    } catch (err) {
      if (S.settle && S.settle.uid === row.uid) {
        S.triageInfo = null;
        openModal(settleModal(row));
      }
    }
  },

  async 'pdf-accept'(el) {
    const uid = el.dataset.uid;
    const reason = String((document.getElementById('pdf-accept-reason') || {}).value || '').trim();
    if (!reason) {
      toast('先写受理原因', true);
      return;
    }
    try {
      await post('/api/pdf/accept', { uid, reason });
      toast('已写受理原因：' + uid + '，请点「开始转写」重新入库复审');
      closeModal();
      await loadPdfBooks();
      render();
    } catch (err) {
      toast(err.message, true);
    }
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
      const body = await post('/api/pdf/run', {
        uids: picked, limit, workers, force, replace: !!S.pdfReplace,
      });
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

  async 'toggle-auto-embed'(el) {
    try {
      const body = await post('/api/settings', { auto_embed: !!el.checked });
      S.settings = {
        auto_embed: body.auto_embed !== false,
        auto_split: body.auto_split !== false,
      };
      toast(S.settings.auto_embed ? '已开启：正文落地后自动增量同步向量库' : '已暂停自动向量同步（可在维护页手动补齐）');
    } catch (err) {
      toast(err.message, true);
    }
  },

  async 'sync-reconcile'() {
    const steps = ['meta', 'fts', 'embed', 'audit'].concat(Array.from(S.reconcileExtra));
    setBusy(true);
    try {
      const body = await post('/api/sync/reconcile', { steps });
      toast('增量同步已排队：' + body.job_id);
      const detail = await watchJob(body.job_id, afterJob, MAINTAIN_LOOPS);
      if (detail) toast(jobNote(detail.job), jobBad(detail.job));
      await Promise.all([loadStatus(true), loadJobs(), loadPending()]);
      render();
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  },

  async 'sync-expand'() {
    if (!window.confirm('对所有未覆盖的章节运行 LLM 主题扩展？此操作会调用外部模型 API 并产生费用。')) return;
    setBusy(true);
    try {
      const body = await post('/api/sync/expand', {});
      toast('主题扩展已排队：' + body.job_id);
      const detail = await watchJob(body.job_id, afterJob, MAINTAIN_LOOPS);
      if (detail) toast(jobNote(detail.job), jobBad(detail.job));
      await Promise.all([loadStatus(true), loadJobs()]);
      render();
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  },

  async 'adv-action'(el) {
    const op = el.dataset.op;
    const asks = {
      embed_init: '启动全库首次向量构建？约需 6–7 小时（支持中途取消与断点续跑）。建成后新书会自动增量补嵌。',
      embed_compact: '执行向量库零推理压缩？约需 10–30 秒，仅重编 active=1 块并回收垃圾空间。',
      fts_rebuild: '全量重建词法索引 index.db？全库约需 40–70 分钟，确认只在数据库损坏或规则升级时运行。',
      embed_rebuild: '强制全量重嵌所有章节向量？全库约需 6–7 小时，确认只在更换模型时运行。',
      deep_verify: '运行深度全库体检（audit --deep）与四箱结构验收（verify_phase）？',
    };
    if (asks[op] && !window.confirm(asks[op])) return;
    setBusy(true);
    try {
      const body = await post('/api/maintain/advanced', { action: op });
      toast('高级任务已排队：' + body.job_id);
      const detail = await watchJob(body.job_id, afterJob, MAINTAIN_LOOPS);
      if (detail) toast(jobNote(detail.job), jobBad(detail.job));
      await Promise.all([loadStatus(true), loadJobs()]);
      render();
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  },

  async 'manual-rescan'() {
    setBusy(true);
    try {
      const body = await post('/api/manual/rescan', {});
      toast('正在扫描手工正文并同步索引：' + body.job_id);
      const detail = await watchJob(body.job_id, afterJob);
      if (detail) toast(jobNote(detail.job), jobBad(detail.job));
      await Promise.all([loadStatus(true), loadJobs(), loadPending()]);
      render();
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  },

  async 'cold-refresh'() {
    if (S.coldBusy) return;
    S.coldBusy = true;
    render();
    try {
      const body = await post('/api/cold/reconcile', {});
      S.cold = body;
      S.coldError = '';
      S.coldRun = { changed: body.changed || 0, backups: body.backups || [] };
      const c = body.counts || {};
      toast(`核对完成：在库 ${c.in_cold || 0}，缺失 ${c.missing || 0}，未登记 ${c.unregistered || 0}，本次改动 ${body.changed || 0}${(body.backups || []).length ? `（备份 ${body.backups.join('、')}）` : ''}`);
    } catch (err) {
      S.coldError = err.message || '校正失败';
      toast(`核对失败：${S.coldError}`, true);
    } finally {
      S.coldBusy = false;
      render();
    }
  },

  async 'services-refresh'() {
    await loadServices();
    render();
  },

  async 'service-start'(el) {
    const key = el.dataset.key;
    try {
      const body = await post('/api/services/start', { key });
      if (body.ok) {
        toast((body.name || key) + (body.already ? ' 已在运行' : ' 已启动'));
      } else {
        toast(body.error || '启动失败', true);
      }
      await loadServices();
      render();
    } catch (err) {
      toast(err.message, true);
    }
  },

  async 'jobs-clean'() {
    try {
      const preview = await post('/api/jobs/clean', { dry_run: true });
      const count = Number(preview.would_remove || 0);
      if (!count) {
        toast('没有可清理的旧任务');
        return;
      }
      const msg = `将永久删除 ${count} 个已完成旧任务记录及日志（规则：超过 ${preview.keep_days} 天或超出最近 ${preview.keep_jobs} 条，跳过 ${preview.thumb_days} 天内有缩略图的任务），不影响书目与正文，确认清理？`;
      if (!window.confirm(msg)) return;
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
        openModal(previewModal(S.preview));
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
    setBusy(true);
    try {
      const body = await post('/api/delete/apply', {
        uid, reason, plan_hash: el.dataset.hash, confirm_uid: typed, confirm: true,
      });
      const detail = await watchJob(body.job_id, afterJob);
      if (detail) toast(summaryText(detail.job.summary) || detail.job.state,
        detail.job.state !== 'succeeded');
      closeModal();
      await Promise.all([loadStatus(), loadJobs(), loadPending(), loadRetireDrop()]);
      render();
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  },

  async 'close-modal'() {
    closeModal();
  },

  async 'theme-toggle'() {
    const isNight = document.documentElement.getAttribute('data-theme') === 'night'
      || document.documentElement.getAttribute('data-theme') === 'dark';
    applyTheme(isNight ? 'light' : 'night', true);
  },

  async 'job-open'(el) {
    const id = String((el && el.dataset && el.dataset.id) || '').trim();
    if (!id) return;
    const seq = ++S.logJumpSeq;
    const fromOtherTab = S.tab !== 'logs';
    if (fromOtherTab) {
      S.filter = '';
      S.tab = 'logs';
    }
    const existing = (S.jobs || []).find((j) => j.id === id);
    S.logJob = existing
      ? Object.assign({}, existing)
      : { id, title: '载入中', state: '', kind: '', created: '' };
    // 换了任务就要从头画：不重置的话 paintJobDetail 会以为还在看上一个
    if (S.logOpenId !== id) {
      S.logOpenId = null;
      S.logDetail = null;
    }
    if (parseHash(location.hash).tab !== 'logs') location.hash = '#logs';
    if (fromOtherTab) {
      await Promise.all([loadJobs({ quiet: true }), openLog(id)]);
      if (seq !== S.logJumpSeq || S.tab !== 'logs') return;
      S.logSkipAfterRender = true;
      render();
      paintJobDetail();
      scrollJobDetail(true);
      return;
    }
    paintLogRows();
    await openLog(id);
    if (seq !== S.logJumpSeq || S.tab !== 'logs') return;
    paintLogRows();
    scrollJobDetail(false);
  },

  async 'job-handle'(el) {
    const id = el.dataset.id;
    const job = (S.jobs || []).find((j) => j.id === id) || (S.logJob && S.logJob.id === id ? S.logJob : null);
    if (!job) return;
    if (job.kind === 'split.run') {
      if (job.batch_dir) S.batch = baseName(job.batch_dir);
      if (location.hash !== '#split') location.hash = '#split';
      else await go('split');
      return;
    }
    if (job.kind === 'pdf.run') {
      S.pickTags = new Set(['convert', 'untriaged', 'gallery', 'ja']);
      S.pickProgress = new Set(['staged']);
      S.pickRecheck = false;
      if (location.hash !== '#pdf-list') location.hash = '#pdf-list';
      else await go('pdf-list');
      return;
    }
    const href = reviewHref(job);
    if (href) {
      if (location.hash !== href) location.hash = href;
      else await go(href.slice(1));
    }
  },

  async 'job-resolve'(el) {
    const id = el.dataset.id;
    const res = el.dataset.res || 'succeeded';
    const note = String((document.getElementById('job-resolve-note') || {}).value || '').trim();
    setBusy(true);
    try {
      const body = await post('/api/jobs/' + encodeURIComponent(id) + '/resolve', { resolution: res, note });
      if (body && body.job) S.logJob = body.job;
      await Promise.all([loadJobs(), loadStatus()]);
      if (S.logJob && S.logJob.id === id) await openLog(id, true);
      render();
      toast('已收口：' + summaryText((body && body.job && body.job.summary) || res));
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
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
  if (act === 'close-modal' && el.classList.contains('overlay') && el !== ev.target) return;
  if (act !== 'close-modal') ev.preventDefault();
  actions[act](el).catch((err) => toast(err.message, true));
});

document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape' && document.querySelector('#modal .modal')) {
    actions['close-modal']();
    return;
  }
  if (ev.key === 'Enter' && ev.target && ev.target.id === 'retire-q') {
    ev.preventDefault();
    actions['retire-search']().catch((err) => toast(err.message, true));
    return;
  }
  if (ev.key !== 'Enter' && ev.key !== ' ') return;
  const el = ev.target.closest && ev.target.closest('[data-act="pick-files"]');
  if (!el) return;
  ev.preventDefault();
  actions['pick-files'](el).catch((err) => toast(err.message, true));
});

// 拖到别处也不让浏览器把文件当页面打开
document.addEventListener('dragover', (ev) => ev.preventDefault());
document.addEventListener('drop', (ev) => ev.preventDefault());

function paintPdfInlineTable() {
  const tb = document.querySelector('#view tbody');
  if (!tb || S.tab !== 'pdf') return;
  const rows = pdfInlineRows();
  tb.innerHTML = rows.length ? rows.map((row) => {
    const label = row.route ? routeWord(row.route) : (PICK_WORD[row.disposition] || row.disposition);
    const pages = row.pages === null || row.pages === undefined ? '—' : num(row.pages);
    const note = pickNote(row) || '—';
    return `<tr>
      <td data-label="选"><input type="checkbox" aria-label="选择 ${esc(row.title || row.uid)}" data-act="pick-one" value="${esc(row.uid)}"${S.pick.has(row.uid) ? ' checked' : ''}></td>
      <td data-label="编号" class="mono" translate="no">${esc(row.uid)}</td>
      <td data-label="书名" class="wrap">${esc(row.title || '')}</td>
      <td data-label="处置"><span class="pill ${row.disposition === 'convert' ? 'ok' : ''}">${esc(label)}</span></td>
      <td data-label="页数" class="num">${pages}</td>
      <td data-label="说明" class="clip small muted" title="${esc(note)}">${esc(note)}</td>
      <td data-label="操作"><button type="button" class="btn sm" data-act="pick-settle" data-uid="${esc(row.uid)}">改处置</button></td>
    </tr>`;
  }).join('') : '<tr><td colspan="7" class="empty">当前筛选条件下没有 PDF 书目。</td></tr>';
}

function paintLedgerTable() {
  const L = S.ledger;
  if (!L) return;
  const tbody = document.querySelector('#view .ledger-table tbody');
  if (!tbody || !tbody.closest('.card')) return;
  const rows = ledgerRowFiltered();
  const visible = rows.slice(0, S.ledgerLimit);
  tbody.innerHTML = visible.length ? visible.map(ledgerRowHtml).join('')
    : `<tr><td colspan="8" class="empty">${S.ledgerPendingOnly ? '没有备注带「待复核」的行了。' : '没有匹配的书目。'}</td></tr>`;
  const shown = document.querySelector('#ledger-shown');
  if (shown) {
    const showFilter = rows.length !== L.ledger.length;
    shown.hidden = !showFilter;
    if (showFilter) shown.textContent = '筛选到 ' + num(rows.length) + ' / ' + num(L.ledger.length) + ' 本。';
  }
  const pager = document.querySelector('#ledger-pager');
  if (pager) {
    if (rows.length > visible.length) {
      pager.hidden = false;
      pager.innerHTML = `<span class="hint">已显示 ${num(visible.length)} / ${num(rows.length)} 本</span>
        <button type="button" class="btn sm" data-act="ledger-more">加载更多 100 行</button>
        <button type="button" class="btn sm" data-act="ledger-show-all">显示全部 ${num(rows.length)} 本</button>`;
    } else {
      pager.hidden = true;
      pager.innerHTML = '';
    }
  }
  paintLedgerPick();
}

document.addEventListener('input', (ev) => {
  const el = ev.target;
  if (!el) return;
  if (el.id === 'ledger-q') {
    S.ledgerQ = el.value;
    S.ledgerLimit = 100;
    clearTimeout(S.ledgerDebounce);
    S.ledgerDebounce = setTimeout(paintLedgerTable, 80);
    return;
  }
  if (el.id === 'pdf-inline-q') {
    S.pdfInlineQ = el.value;
    paintPdfInlineTable();
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
  if (el.id === 'pdf-replace') {
    S.pdfReplace = el.checked;
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
  if (el.dataset && el.dataset.reconcile) {
    if (el.checked) S.reconcileExtra.add(el.dataset.reconcile);
    else S.reconcileExtra.delete(el.dataset.reconcile);
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
  if (el.id === 'pdf-inline-disp') {
    S.pdfInlineTag = el.value || 'all';
    render();
    return;
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
      Promise.all([api('/api/inbox'), loadStatus()]).then(([body]) => {
        S.inbox = body.items || [];
        S.inboxIgnored = body.ignored || [];
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
  const parsed = parseHash(location.hash);
  if (parsed.tab !== S.tab || (parsed.tab === 'logs' && Object.prototype.hasOwnProperty.call(parsed.params, 'state'))) {
    go(parsed.tab, parsed.params);
  }
});

function pollIntervalMs() {
  const counts = (S.payload && S.payload.state_counts) || {};
  const ops = (S.payload && S.payload.ops) || {};
  const active = (counts.running || 0) + (counts.queued || 0);
  if (active > 0 || ops.writer || ops.audit_refreshing || S.triageSeen) return 2500;
  return 10000;
}

async function pollTick() {
  if (document.hidden) return;
  if (S.pollBusy) {
    schedulePoll();
    return;
  }
  S.pollBusy = true;
  try {
    await Promise.all([loadStatus(), loadJobs()]);
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
        if (job) {
          const made = (job.steps || []).filter((s) => s.state === 'ok').length;
          const ok = job.state === 'awaiting_review' || job.state === 'succeeded';
          S.triageSeen = null;
          await Promise.all([loadPdfBooks(), loadPdf()]);
          render();
          toast(ok ? (made ? num(made) + ' 本出了建议，去清单确认'
            : '分诊跑完了，但没有模型建议') : '分诊没过，去日志看', !ok || !made);
        }
      }
    }
    if (S.tab === 'import' || S.tab === 'docs') {
      // docs 卡片：跑着就 2.5 秒刷一次；没任务时 loadDocs 自己会短路，不会每跳打 404
      await loadDocs();
      paintDocs();
    }
    if (S.tab === 'overview') {
      // 只在内容真变了才写：无条件写会把焦点、选区、滚动位置清一次
      const html = viewOverview();
      if (html !== S.overviewHtml) {
        S.overviewHtml = html;
        const view = document.querySelector('#view');
        if (view) {
          view.innerHTML = html;
          wireTrackLinkage();
        }
      }
    }
  } catch (err) {
    /* 轮询失败不打扰页面，状态条会显示缺 */
  } finally {
    S.pollBusy = false;
    schedulePoll();
  }
}

function schedulePoll(delayMs) {
  clearTimeout(S.pollTimer);
  if (document.hidden) return;
  S.pollTimer = setTimeout(pollTick, delayMs === undefined ? pollIntervalMs() : delayMs);
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && !S.pollBusy) schedulePoll(50);
});

async function boot() {
  const parsed = parseHash(location.hash);
  S.tab = parsed.tab;
  applyHashParams(parsed.tab, parsed.params);
  try {
    const baseTasks = [loadStatus(), loadJobs(), loadPending()];
    if (S.tab === 'overview') baseTasks.push(loadServices());
    else if (S.tab === 'import') {
      baseTasks.push(api('/api/inbox').then((body) => {
        S.inbox = body.items || [];
        S.inboxIgnored = body.ignored || [];
      }));
      baseTasks.push(loadLatestPlan(), loadDocs());
    } else if (S.tab === 'docs') baseTasks.push(loadDocs());
    else if (S.tab === 'pdf') baseTasks.push(loadPdf(), loadPdfBooks());
    else if (S.tab === 'pdf-list') baseTasks.push(loadPdfBooks());
    else if (S.tab === 'ledger') baseTasks.push(loadLedger(), loadRetireDrop());
    else if (S.tab === 'maintain') baseTasks.push(loadSettings());
    else if (S.tab === 'retire') baseTasks.push(loadRetireDrop());
    else if (S.tab === 'cold') baseTasks.push(loadCold(), loadRetireDrop());
    await Promise.all(baseTasks);
    if (S.tab === 'split') await loadSplitBooks();
    if (S.tab === 'logs') {
      const rows = logRows();
      if (rows.length && !S.logJob) S.logJob = rows[0];
    }
  } catch (err) {
    toast(err.message, true);
  }
  render();
  schedulePoll();
}

function applyTheme(theme, persist) {
  const night = theme === 'night' || theme === 'dark';
  const doc = document.documentElement;
  if (night) doc.setAttribute('data-theme', 'night');
  else if (typeof doc.setAttribute === 'function') doc.setAttribute('data-theme', 'light');
  const schemeMeta = typeof document.querySelector === 'function'
    ? document.querySelector('meta[name="color-scheme"]')
    : null;
  if (schemeMeta && typeof schemeMeta.setAttribute === 'function') {
    schemeMeta.setAttribute('content', night ? 'dark' : 'light');
  }
  const pressed = String(night);
  const aria = night ? '切换宣纸日间' : '切换墨夜主题';
  const shortLabel = night ? '日间' : '墨夜';
  const btn = document.getElementById('theme-toggle');
  const swap = document.getElementById('theme-icon-swap');
  const labelEl = document.getElementById('theme-label');
  const meta = document.getElementById('meta-theme-color');
  if (btn) {
    btn.setAttribute('aria-pressed', pressed);
    btn.setAttribute('aria-label', aria);
  }
  if (swap && swap.classList) swap.classList.toggle('swapped', night);
  if (labelEl) labelEl.textContent = shortLabel;
  if (meta) meta.setAttribute('content', night ? '#161716' : '#F4F1E8');
  if (persist) {
    try { localStorage.setItem(THEME_KEY, night ? 'night' : 'light'); } catch (err) { /* ignore */ }
  }
}

function initTheme() {
  let saved = '';
  try { saved = localStorage.getItem(THEME_KEY) || ''; } catch (err) { saved = ''; }
  if (!saved && typeof window.matchMedia === 'function') {
    saved = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'night' : 'light';
  }
  applyTheme(saved === 'night' || saved === 'dark' ? 'night' : 'light', false);
  if (typeof window.matchMedia === 'function') {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onMq = (ev) => {
      let cur = '';
      try { cur = localStorage.getItem(THEME_KEY) || ''; } catch (err) { cur = ''; }
      if (!cur) applyTheme(ev.matches ? 'night' : 'light', false);
    };
    if (typeof mq.addEventListener === 'function') mq.addEventListener('change', onMq);
  }
  if (typeof window.addEventListener === 'function') {
    window.addEventListener('storage', (ev) => {
      if (ev && ev.key === THEME_KEY) {
        const next = ev.newValue || 'light';
        applyTheme(next === 'night' || next === 'dark' ? 'night' : 'light', false);
      }
    });
  }
}

initTheme();
boot();
