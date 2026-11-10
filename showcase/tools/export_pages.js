// 把本地跑着的几套页面导出成可以离线点开的静态样例。
//
//   node showcase/tools/export_pages.js
//
// 需要这些服务在本地跑着（都只绑 127.0.0.1）：
//   8771 四读 reading   8773 统一入口 home   8765 检索/问书 demo
// 只导出公有领域的《阿Q正传》（B0001，鲁迅，9 章），其余书一律不进样例。
'use strict';

const fs = require('fs');
const path = require('path');
const PW = '/home/colaqaq/.local/share/mise/installs/playwright/latest/node_modules/playwright';
const { chromium } = require(PW);

const OUT = path.join(__dirname, '..', 'pages');
const ASSETS = path.join(OUT, 'assets');
const READING = 'http://127.0.0.1:8771';
const HOME = 'http://127.0.0.1:8773';
const DEMO = 'http://127.0.0.1:8765';

const BOOK = 'B0001';
const CHAPTERS = ['01', '02', '03', '04', '05', '06', '07', '08', '09'];

// ---------------------------------------------------------------- 路由映射
// 静态样例里哪些路由存在（其余内链一律变成「这一页没有导出」）
const ROUTES = new Map();
const add = (from, to) => ROUTES.set(from, to);

add(READING + '/', 'shelf.html');
add(READING + '/catalog', 'catalog.html');
add(READING + '/book/' + BOOK, 'book-' + BOOK + '.html');
for (const nn of CHAPTERS) {
  add(READING + '/reader/' + BOOK + '/' + nn, 'reader-' + BOOK + '-' + nn + '.html');
  add(READING + '/chapter/' + BOOK + '/' + nn, 'chapter-' + BOOK + '-' + nn + '.html');
}
add(HOME + '/', 'home.html');
add(HOME, 'home.html');
add(DEMO + '/', 'answer.html');
add(DEMO, 'answer.html');

const DEAD_CSS = `
#dead-toast{position:fixed;left:50%;bottom:48px;transform:translate(-50%,12px);z-index:99;
  padding:10px 18px;border-radius:2px;background:#211e1a;color:#f3f0e7;font-size:13px;
  letter-spacing:.04em;opacity:0;transition:opacity .22s ease,transform .22s ease;pointer-events:none}
#dead-toast.on{opacity:1;transform:translate(-50%,0)}
`;

const DEAD_JS = `
(function(){
  var box = document.createElement('div');
  box.id = 'dead-toast';
  document.body.appendChild(box);
  var timer = 0;
  document.addEventListener('click', function(e){
    var a = e.target.closest && e.target.closest('[data-dead]');
    if (!a) return;
    e.preventDefault();
    box.textContent = '静态样例 · ' + (a.getAttribute('data-dead') || '这一页') + '没有导出';
    box.classList.add('on');
    clearTimeout(timer);
    timer = setTimeout(function(){ box.classList.remove('on'); }, 2200);
  }, true);
})();
`;

function rewriteLinks(html, base) {
  // 资产（<link rel=stylesheet>、图片、字体）先落到本地 pages/assets/：必须在导航重写之前，
  // 否则 /assets/app.css 会被当成没导出的页面改写成 data-dead，整站就丢了样式。
  html = html.replace(
    /(href|src)="(?:https?:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?)?\/(assets\/[^"]+|style\.css|session\.css)"/g,
    (whole, attr, p) => attr + '="assets/' + p.replace(/^assets\//, '') + '"',
  );
  // 再把绝对地址换成 path/query 形式方便统一处理
  for (const origin of [READING, HOME, DEMO]) {
    html = html.split('href="' + origin + '"').join('href="' + origin + '/"');
  }
  return html.replace(/href="([^"]+)"/g, (whole, href) => {
    if (/^(#|mailto:|javascript:)/.test(href)) return whole;
    if (href.endsWith('.html') || href.startsWith('assets/') || href.startsWith('#')) return whole;
    let url;
    try {
      url = new URL(href, base);
    } catch (err) {
      return whole;
    }
    const bare = url.origin + url.pathname;
    const hit = ROUTES.get(bare);
    if (hit) return 'href="' + hit + '"';
    if (url.origin === READING || url.origin === HOME || url.origin === DEMO) {
      const label = url.pathname === '/' ? '首页' : '「' + url.pathname + '」';
      return 'href="#" data-dead="' + label.replace(/"/g, '&quot;') + '"';
    }
    return whole; // 外链保持原样
  });
}

// 静态包里没有后端，也没有 /session.js、/common.js 这些外链脚本：
// 一律去掉带 src 的 <script>；内联脚本按需保留（读本页的四档切换就是内联的）。
function stripScripts(html, opts) {
  const keepInline = !!(opts && opts.keepInline);
  return html.replace(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi, (whole, attrs) => {
    if (/\bsrc\s*=/.test(attrs)) return '';
    return keepInline ? whole : '';
  });
}

function injectDeadLink(html) {
  return html
    .replace('</head>', '<style>' + DEAD_CSS + '</style></head>')
    .replace('</body>', '<script>' + DEAD_JS + '</script></body>');
}

// 会话存档里带着检索过程的原文片段（best_text / snippet / text …），其中不少来自有版权的书。
// 静态包只留开头一小截做示意，其余截掉——不把整段别人的正文分发出去。
function scrubTraces(html) {
  return html.replace(/"(best_text|hit_text|snippet|text)"\s*:\s*"((?:[^"\\]|\\.)*)"/g, (whole, key, val) => {
    const plain = val.replace(/\\n/g, ' ').replace(/\\"/g, '"');
    if (plain.length <= 40) return whole;
    return '"' + key + '": "' + plain.slice(0, 36).replace(/"/g, '\\"') + '……（原文不导出）"';
  });
}

// 首页右栏的「今日日课」引当期原文（有版权）：静态包里换成一句说明
async function prepHome(p) {
  await p.evaluate(() => {
    const slip = document.querySelector('aside .slip');
    if (slip) {
      slip.innerHTML =
        '<div class="slip-head"><span>今日日课</span><span class="issue">静态样例</span></div>' +
        '<p class="kicker">当期原文有版权</p><h2>这一栏没有导出</h2>' +
        '<p class="excerpt">日课每天换一本、引当期原文；静态样例只导出公有领域的《阿Q正传》，这一栏留空。</p>';
    }
    const note = document.querySelector('aside .aside-note');
    if (note) note.textContent = '日课在本地实例里每天更新。';
  });
}

async function save(page, url, out, opts) {
  const o = Object.assign({ keepInline: false, settle: 500, prep: null }, opts);
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.waitForTimeout(o.settle);
  if (o.prep) await o.prep(page);
  let html = await page.content();
  html = stripScripts(html, o);
  html = rewriteLinks(html, url);
  html = scrubTraces(html);
  html = injectDeadLink(html);
  // 资产指向本地
  html = html.replace(/(href|src)="\/assets\/([^"]+)"/g, '$1="assets/$2"');
  // 还没出片的章，读本页是「这一章还没有生成读本」的空页：不导出，链到它的地方按死链处理
  if (/^reader-/.test(out) && /还没有生成读本|还没有读本/.test(html)) {
    try { fs.unlinkSync(path.join(OUT, out)); } catch (e) {}
    console.log('  ' + out.padEnd(28) + '跳过（该章未出片）');
    return;
  }
  fs.writeFileSync(path.join(OUT, out), html);
  const size = (fs.statSync(path.join(OUT, out)).size / 1024).toFixed(0);
  console.log('  ' + out.padEnd(28) + size + ' KB');
}

async function grabAssets() {
  fs.mkdirSync(ASSETS, { recursive: true });
  let n = 0;
  for (const name of ['tokens.css', 'app.css', 'pages.css', 'theme.css', 'home.css', 'style.css']) {
    const res = await fetch(READING + '/assets/' + name).catch(() => null);
    const res2 = res && res.ok ? res : await fetch(HOME + '/' + name).catch(() => null);
    const use = res && res.ok ? res : res2;
    if (!use || !use.ok) continue;
    fs.writeFileSync(path.join(ASSETS, name), Buffer.from(await use.arrayBuffer()));
    n++;
  }
  // 问书页的样式表挂在 /session.css（不在 /assets/ 下）
  for (const [url, name] of [[DEMO + '/session.css', 'session.css']]) {
    const res = await fetch(url).catch(() => null);
    if (!res || !res.ok) continue;
    fs.writeFileSync(path.join(ASSETS, name), Buffer.from(await res.arrayBuffer()));
    n++;
  }
  console.log('  assets: ' + n + ' files');
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  console.log('导出静态页面 →', OUT);
  await grabAssets();

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

  // 首页：服务端渲染，无脚本
  await save(page, HOME + '/', 'home.html', { settle: 400, prep: prepHome });

  // 书架 / 书目 / 书页：客户端渲染，保存 DOM 后必须剔除脚本
  await save(page, READING + '/', 'shelf.html', { settle: 1200 });
  await save(page, READING + '/catalog', 'catalog.html', { settle: 900 });
  await save(page, READING + '/book/' + BOOK, 'book-' + BOOK + '.html', {
    settle: 900,
    
  });

  // 阅读页：CSS 与切换逻辑全内联，原样保留（离线仍能四档切换、只看原文）
  for (const nn of CHAPTERS) {
    await save(page, READING + '/reader/' + BOOK + '/' + nn + '?tier=standard', 'reader-' + BOOK + '-' + nn + '.html', {
      settle: 700,
      keepInline: true,
    });
  }
  // 章节工单：
  for (const nn of CHAPTERS) {
    await save(page, READING + '/chapter/' + BOOK + '/' + nn, 'chapter-' + BOOK + '-' + nn + '.html', {
      settle: 800,
      
    });
  }

  // 问书：一个有真实回答的会话（从本地会话存档打开，不再调模型）
  const sid = process.env.SESSION_ID || '';
  if (sid) {
    await save(page, DEMO + '/?session=' + encodeURIComponent(sid), 'answer.html', {
      settle: 1500,
      prep: async (p) => {
        await p.waitForSelector('text=本轮依据', { timeout: 20000 }).catch(() => {});
        await p.evaluate(() => window.scrollTo(0, 0));
      },
    });
  } else {
    console.log('  answer.html 跳过：没有传 SESSION_ID=');
  }

  await browser.close();
  console.log('完成。');
})();
