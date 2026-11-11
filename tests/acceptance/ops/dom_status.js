#!/usr/bin/env node
// 用 node 的 vm 跑 apps/ops/web/app.js 的渲染函数，不启浏览器。
//
//   node dom_status.js <app.js> < input.json > output.json
//
// input: {"payload": <GET /api/status 的响应>, "status": <payload.status>, "jobs": [], "pending": []}
// output: {"overview": "...", "maintain": "...", "stats": "..."} 三段 HTML。
// fetch 永远不 resolve，boot() 会停在第一次 await，不会去碰真网络。
'use strict';
const fs = require('fs');
const vm = require('vm');

const file = process.argv[2];
if (!file) {
  console.error('用法: node dom_status.js <app.js>');
  process.exit(2);
}
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const src = fs.readFileSync(file, 'utf8');

const elements = new Map();
function makeEl(sel) {
  return {
    sel,
    innerHTML: '',
    textContent: '',
    className: '',
    title: '',
    hidden: false,
    value: '',
    checked: false,
    scrollTop: 0,
    scrollHeight: 0,
    dataset: {},
    style: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {},
    removeEventListener() {},
    appendChild() {},
    removeChild() {},
    setAttribute() {},
    getAttribute() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    closest() { return null; },
    focus() {},
    blur() {},
    click() {},
    insertAdjacentHTML() {},
  };
}

const sandbox = {
  console,
  __input: input,
  document: {
    title: '',
    body: makeEl('body'),
    documentElement: makeEl('html'),
    querySelector(sel) {
      if (!elements.has(sel)) elements.set(sel, makeEl(sel));
      return elements.get(sel);
    },
    querySelectorAll() { return []; },
    getElementById(id) { return sandbox.document.querySelector('#' + id); },
    addEventListener() {},
    createElement() { return makeEl('created'); },
    createTextNode() { return {}; },
  },
  location: { hash: '#overview', href: 'http://127.0.0.1/', reload() {} },
  history: { replaceState() {}, pushState() {} },
  localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
  navigator: { clipboard: { writeText() { return Promise.resolve(); } } },
  // boot() 的第一次 await 永远不回来：用永不 resolve 的 fetch + 空计时器把页面冻住
  fetch: () => new Promise(() => {}),
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
  requestAnimationFrame: () => 0,
  URLSearchParams,
  encodeURIComponent,
  decodeURIComponent,
  alert() {},
  confirm() { return false; },
  addEventListener() {},
  removeEventListener() {},
  scrollTo() {},
  matchMedia() { return { matches: false, addEventListener() {}, removeEventListener() {} }; },
};
sandbox.window = sandbox;

const context = vm.createContext(sandbox);
vm.runInContext(src, context, { filename: file });
vm.runInContext(
  'S.payload = __input.payload; S.status = S.payload.status || {};'
  + 'S.jobs = __input.jobs || []; S.pending = __input.pending || [];',
  context,
);
const out = vm.runInContext(
  'JSON.stringify({ overview: viewOverview(), maintain: viewMaintain(),'
  + ' stats: (renderStats(), document.querySelector("#stats").innerHTML) })',
  context,
);
process.stdout.write(out);
