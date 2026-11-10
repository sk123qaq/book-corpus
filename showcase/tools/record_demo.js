// 录一遍真实操作：打开本地跑着的实例，用假光标真的点，Playwright 原样录屏。
//
//   node showcase/tools/record_demo.js            # 录片段
//   bash showcase/tools/make_video.sh             # 拼成 demo.mp4
//
// 依赖 8771（四读）、8773（入口）、8765（问书）三个本地服务在跑。
// 输出：showcase/tools/.build/clips/*.webm + marks.json（每个片段的时长与关键时间点）
'use strict';

const fs = require('fs');
const path = require('path');
const PW = '/home/colaqaq/.local/share/mise/installs/playwright/latest/node_modules/playwright';
const { chromium } = require(PW);

const BUILD = path.join(__dirname, '.build');
const CLIPS = path.join(BUILD, 'clips');
const SHOTS = path.join(__dirname, '..', 'shots');
const HOME = 'http://127.0.0.1:8773/';
const READING = 'http://127.0.0.1:8771';
const DEMO = 'http://127.0.0.1:8765/';

const VIEW = { width: 1920, height: 1080 };

// 假光标：一道朱红小点 + 点击涟漪（Playwright 不录真实指针）
const CURSOR = () => {
  const boot = () => {
    if (!document.body) return setTimeout(boot, 12);
    if (document.getElementById('__cur')) return;
    const style = document.createElement('style');
    style.textContent = `
      #__cur{position:fixed;left:0;top:0;width:14px;height:14px;border-radius:50%;
        background:#A8442F;box-shadow:0 0 0 3px rgba(243,240,231,.92),0 3px 10px rgba(24,24,23,.35);
        z-index:2147483647;pointer-events:none;opacity:0;
        transform:translate(-50%,-50%);transition:transform .12s linear,opacity .18s linear}
      #__cur.on{opacity:1}
      #__rip{position:fixed;left:0;top:0;width:44px;height:44px;border-radius:50%;
        border:2px solid #A8442F;z-index:2147483646;pointer-events:none;opacity:0;
        transform:translate(-50%,-50%) scale(.15)}
      #__rip.go{animation:__rip .46s ease-out forwards}
      @keyframes __rip{0%{opacity:.85;transform:translate(-50%,-50%) scale(.15)}
        100%{opacity:0;transform:translate(-50%,-50%) scale(1.25)}}
    `;
    document.head.appendChild(style);
    const dot = document.createElement('div');
    dot.id = '__cur';
    document.body.appendChild(dot);
    const rip = document.createElement('div');
    rip.id = '__rip';
    document.body.appendChild(rip);
    const move = (x, y) => {
      dot.style.transform = 'translate(' + x + 'px,' + y + 'px) translate(-50%,-50%)';
      dot.classList.add('on');
    };
    window.addEventListener('mousemove', (e) => move(e.clientX, e.clientY), true);
    window.addEventListener('mousedown', (e) => {
      rip.style.left = e.clientX + 'px';
      rip.style.top = e.clientY + 'px';
      rip.classList.remove('go');
      void rip.offsetWidth;
      rip.classList.add('go');
    }, true);
  };
  boot();
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function spot(page, selector, nth = 0) {
  const loc = page.locator(selector).nth(nth);
  // 滚到视口中线，免得落到吸顶栏底下点不着
  await loc.evaluate((el) => el.scrollIntoView({ block: 'center', inline: 'nearest' })).catch(() => {});
  await sleep(120);
  const box = await loc.boundingBox();
  if (!box) throw new Error('找不到可以点的位置：' + selector);
  return { x: Math.round(box.x + box.width / 2), y: Math.round(box.y + box.height / 2) };
}

async function glide(page, x, y, steps = 26) {
  await page.mouse.move(x, y, { steps });
  await sleep(220);
}

async function hoverTo(page, selector, nth = 0) {
  const p = await spot(page, selector, nth);
  await glide(page, p.x, p.y);
  return p;
}

async function clickAt(page, selector, nth = 0, after = 260) {
  const p = await hoverTo(page, selector, nth);
  await page.mouse.down();
  await sleep(70);
  await page.mouse.up();
  await sleep(after);
  return p;
}

async function wheel(page, x, y, dy, times = 6, gap = 55) {
  await page.mouse.move(x, y, { steps: 6 });
  for (let i = 0; i < times; i++) {
    await page.mouse.wheel(0, dy);
    await sleep(gap);
  }
  await sleep(180);
}

async function still(page, name) {
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, name + '.png') });
}

async function recorder(browser, name, fn) {
  const ctx = await browser.newContext({
    viewport: VIEW,
    deviceScaleFactor: 1,
    recordVideo: { dir: CLIPS, size: VIEW },
  });
  await ctx.addInitScript(CURSOR);
  const page = await ctx.newPage();
  page.on('pageerror', () => {});
  const t0 = Date.now();
  const marks = { name, started: 0 };
  try {
    await fn(page, marks, t0);
  } catch (err) {
    await page.screenshot({ path: path.join(BUILD, 'fail-' + name + '.png') }).catch(() => {});
    await page.close().catch(() => {});
    await ctx.close().catch(() => {});
    throw err;
  }
  const video = page.video();
  await page.close();
  await ctx.close();
  const src = await video.path();
  const out = path.join(CLIPS, name + '.webm');
  fs.renameSync(src, out);
  marks.file = path.basename(out);
  marks.wall = (Date.now() - t0) / 1000;
  console.log('  录好 ' + name.padEnd(10) + marks.wall.toFixed(1) + 's');
  return marks;
}

(async () => {
  fs.mkdirSync(CLIPS, { recursive: true });
  for (const f of fs.readdirSync(CLIPS)) fs.unlinkSync(path.join(CLIPS, f));
  const browser = await chromium.launch();
  const marks = {};
  const at = (marks, key, t0) => {
    marks[key] = (Date.now() - t0) / 1000;
  };

  // ---------------------------------------------------------------- 片头
  marks.title = await recorder(browser, 'card-title', async (page) => {
    await page.goto('file://' + path.join(__dirname, 'cards.html') + '?c=title');
    await page.waitForTimeout(260);
    await sleep(4300);
  });

  // ---------------------------------------------------------------- 入口
  marks.home = await recorder(browser, 'home', async (page) => {
    await page.goto(HOME, { waitUntil: 'load' });
    await page.waitForTimeout(1400);
    await hoverTo(page, '.row[data-key="1"]');
    await sleep(950);
    await hoverTo(page, '.row[data-key="2"]');
    await sleep(950);
    await hoverTo(page, '.row[data-key="3"]');
    await sleep(950);
    await hoverTo(page, 'a.read');
    await sleep(900);
  });

  // ------------------------------------------ 书架 → 书页 → 工单 → 读本
  marks.browse = await recorder(browser, 'browse', async (page, m, t0) => {
    await page.goto(READING + '/', { waitUntil: 'load' });
    await page.waitForTimeout(1800);

    // 书架：扫一遍书脊
    await page.mouse.move(1000, 640, { steps: 18 });
    await sleep(320);
    await page.mouse.move(700, 620, { steps: 26 });
    await sleep(420);
    await page.mouse.move(430, 590, { steps: 24 });
    await sleep(620);
    await still(page, '01-shelf');
    await hoverTo(page, 'a.spine[href="/book/B0001"]');
    await sleep(900);
    at(m, 'shelfToBook', t0);
    await page.mouse.down();
    await sleep(70);
    await page.mouse.up();
    await page.waitForURL(/\/book\/B0001/, { timeout: 15000 });
    await page.waitForTimeout(1700);

    // 书页：章目里选第三章
    await wheel(page, 960, 700, 200, 3, 60);
    await sleep(360);
    await hoverTo(page, '.ch-row[data-n="03"]');
    await sleep(700);
    await still(page, '02-book');
    await clickAt(page, '.ch-row[data-n="03"]', 0, 460);
    at(m, 'bookToChapter', t0);
    const p = await spot(page, '.book-actions a[href*="/chapter/"]');
    await glide(page, p.x, p.y, 20);
    await sleep(520);
    await page.mouse.down();
    await sleep(70);
    await page.mouse.up();
    await page.waitForURL(/\/chapter\/B0001\/03/, { timeout: 15000 });
    await page.waitForTimeout(1500);

    // 工单：切块 + 四档配额
    await still(page, '03-chapter');
    await wheel(page, 900, 700, 150, 2, 70);
    await sleep(560);
    await hoverTo(page, 'a[href="/reader/B0001/03?tier=standard"]');
    await sleep(820);
    at(m, 'chapterToReader', t0);
    await page.mouse.down();
    await sleep(70);
    await page.mouse.up();
    await page.waitForURL(/\/reader\/B0001\/03/, { timeout: 15000 });
    await page.waitForTimeout(1700);

    // 读本：标准本 → 旁注 → 深读 → 只看原文 → 回标准
    await still(page, '04-reader-standard');
    await wheel(page, 1000, 700, 230, 4, 60);
    await sleep(560);
    await still(page, '05-reader-notes');
    await clickAt(page, '[data-tier-tab="deep"]', 0, 900);
    await still(page, '06-reader-deep');
    await wheel(page, 1000, 700, 260, 3, 60);
    await sleep(460);
    await clickAt(page, '#only-btn', 0, 900);
    await still(page, '07-reader-source');
    await wheel(page, 1000, 700, 240, 3, 60);
    await sleep(340);
    await clickAt(page, '#only-btn', 0, 600);
    await clickAt(page, '[data-tier-tab="standard"]', 0, 700);
    await sleep(700);
  });

  // ---------------------------------------------------------------- 问书
  marks.ask = await recorder(browser, 'ask', async (page, m, t0) => {
    await page.goto(DEMO, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1400);
    const q = '《阿Q正传》里，阿Q在未庄的「精神胜利法」具体有哪几种表现？';
    await clickAt(page, '#q', 0, 320);
    await page.keyboard.type(q, { delay: 62 });
    await sleep(650);
    await clickAt(page, '#send', 0, 0);
    at(m, 'sendAt', t0);
    // 真实等真实回答，不快进、不改时长
    const started = Date.now();
    let ready = false;
    while (Date.now() - started < 240000) {
      ready = await page.evaluate(() => {
        const ev = document.querySelector('.evidences .ev');
        const busy = document.querySelector('#send[disabled], #send[aria-busy="true"]');
        return !!ev && !busy;
      });
      if (ready) break;
      await page.waitForTimeout(500);
    }
    if (!ready) throw new Error('回答没等到（超过 4 分钟）');
    at(m, 'answerAt', t0);
    m.elapsed = await page.evaluate(() => {
      const hit = document.body.innerText.match(/回答\s*([0-9.]+)\s*s/);
      return hit ? hit[1] : '';
    });
    await page.waitForTimeout(1600);
    await still(page, '08-answer');
    await wheel(page, 900, 700, 280, 3, 65);
    await sleep(700);
    await hoverTo(page, '.evidences .ev[data-cite="2"]');
    await sleep(900);
    at(m, 'askEnd', t0);
    await page.mouse.down();
    await sleep(70);
    await page.mouse.up();
    await page.waitForTimeout(1700);
    await still(page, '09-citation');
    await wheel(page, 1600, 700, 240, 3, 65);
    await sleep(1500);
  });

  // ---------------------------------------------------------------- 片尾
  marks.outro = await recorder(browser, 'card-outro', async (page) => {
    await page.goto('file://' + path.join(__dirname, 'cards.html') + '?c=outro');
    await page.waitForTimeout(260);
    await sleep(4200);
  });

  await browser.close();
  fs.writeFileSync(path.join(BUILD, 'marks.json'), JSON.stringify(marks, null, 2));
  console.log(JSON.stringify(marks, null, 2));
})();
