# 分层读（读本与深读层）

分层读把一章原文生成五层：`CH`/`A1 图文总览`/`A1′ 速览`/`A2 标准读本`/`A3 深读`，面板、速览、标准层共用一份章节模型。页面上的读法（叙事 `narrative`、论证 `argument`、实用 `practical`、资料 `reference`）是生成与编译的输入；2026-11-13 起，读法改了以后，盘上旧读法生成的层会整体过期，章节路由退回原文，深度页 404。书页可以直接设本书默认读法，但只写账，不生成。2026-11-14 起，章页在生成前做容量预检：按块数与分块规划给出预计 token/秒数；超过单章上限时禁用深度面板入口、显示拆分建议；`POST /api/depth` 与主按钮 `POST /api/jobs` 都返回 422 且不创建任务，章页把预检行切到 blocked 并显示内联阻断卡。

## Sub-features

- `read-shelf` 书架 `/`：可读的书、章节数、字数、最近生成的读本（进度固定 `0.0`）。
- `read-book` 书页 `/book/<uid>`：书目信息、章节表、**本书默认读法**。按钮文案 `设本书默认`（已有默认时 `更新本书默认`），只写人账，不提交生成；页面上没有「生成读本」。
- `read-chapter` 章页 `/chapter/<uid>/<n>`：五层读本、读法条（跟随本书默认 / 章级覆写 / 放弃覆写）、生成入口、任务记录。没有书级默认时，主按钮 `#generate` 显示 `先选一个读法 →` 并禁用；已确认读法时渲染成 `按「<mode>」生成读本 →`。逐层深度面板默认全选时 `#depth-generate` 写 `生成全部五层`，取消部分层后写 `生成所选层（保留 N 层）`，一层不选写 `没有要生成的层`；没有读法时点它会 409。
- `read-preflight` 章页容量预检：`/api/chapter` 的 `depth.preflight.stats` 给出 `blocks/chunks/max_chunk_blocks/estimated_output_tokens/estimated_seconds`，同级还有 `warnings` 与 `remediation`；页面渲染成 `本章 N 块 · M 个分块 · 单块最多 K 块 · 预计 T 输出 token · 约 S 秒`，有警告时同一段 `<br>` 后接 `警告：…`。
- `read-preflight-blocked` 超限阻断：命中 5,000 块 / 100 个分块 / 1,000,000 输出 token / 7,200 秒任一硬上限时 `ok=false`、`code=source_too_large`；章页预检行带 `blocked`，显示拆分建议与 `md 目录：books/<uid>/md/`，`#depth-generate`、`#depth-rebuild-model` 和层勾选框全部禁用；`POST /api/depth` 返回 422，不创建任务。有书级读法时，主按钮 `#generate` 不在禁用范围；点它走 `POST /api/jobs`，同样返回 422，章页显示 `#job-mount` 内联卡并把预检行切到 blocked。
- `read-stale` 读法改了：深度层过期。章节路由退回 `none`，理由「读法已改，深度页过期」，`tiers.*.stale` 全为 `true`，`/reader/<uid>/<n>/depth` 404；把读法改回原值时，若盘上 `layer_mode` 与它一致，旧层恢复可用。
- `read-profile` 读法闸门：没有书级默认读法时，深度生成返回 `409 reading_profile_required`。
- `read-quality` 速览质量：按读法分支。叙事只要 `scenes` 且不超上限；其余读法要 `claims`，正文占比 15%–25%，最多自动重试一次。
- `read-generation` 读法确认后的生成：书页只设默认；章页主按钮走 `POST /api/jobs`（三档；深度开关打开、没有旧 `manifest.json`，且 depth 状态是 `missing` 并盘上没有 `chapter.html`/`chapter_model.json`/`ledger.json`，或状态是 `partial`（续跑）时，会被服务端改写成五层 depth 任务），章页下半的逐层面板走 `POST /api/depth`（只补缺层，无覆盖确认，202 + 轮询；先过读法闸门再预检，`POST /api/depth` 的 422 不建任务，409 先于 422；主按钮走同一预检，`POST /api/jobs` 的 422 同样不建任务，并在 `#job-mount` 渲染阻断卡）。
- `read-records` 生成记录 `/records`：队列与历史工单，加上磁盘上有 `manifest.json` 的章读本（`/api/records` 返回 `jobs` + `packs`）。`blocked` 有独立的「已阻断」档与统计；状态徽标「已阻断」，副标题 `已阻断 · 源文件过大`，详情不出重跑按钮，改为显示 remediation。
- `read-legacy` 旧链接：`/reader/...?tier=research` 302 到深读档（URL 保留 `retired=research`）；读本页与深度页在 200 时都会注入退役提示，旧根 `chapter.html` 只决定旧读本页存不存在。新 depth 包只有 `depth/chapter.html`，同 URL 会 404「这一章还没有生成读本」，深读页走 `/reader/<uid>/<n>/depth`。研究导出 410。
- `read-formal` 形式验收：单章磁盘状态检查、读法/深度层可达性，以及预检阈值与 422 契约。

## How to get to it (user POV)

- 浏览器打开 `http://127.0.0.1:18771/`，书架列出可读的书；点书进 `/book/<uid>`，点章进 `/chapter/<uid>/<n>`。
- 在书页选一个读法，点 `设本书默认`：只记默认，不出读本；按钮变成 `更新本书默认`，旁边文案仍是「只设本书默认，不生成；章级覆写在章页设置。」
- 回章页：已生成过的层直接显示；没有生成的层按需点生成。没有书级默认时，主按钮显示 `先选一个读法 →` 并禁用，点它回书页选读法。
- 章页生成区上方先显示预检行：`本章 N 块 · M 个分块 · 单块最多 K 块 · 预计 T 输出 token · 约 S 秒`。大章（超过 1,000 块、20 个分块或 1 小时）在同一段 `<br>` 后接「警告：…」；超过硬上限时预检行变红、下面给出拆分建议和 `md 目录`，深度面板的两个按钮与层勾选框不可点。有书级读法时，主按钮 `#generate` 仍可点；点它在超限章上同样得到 422，页面出现「预检未通过 / 已阻断」内联卡并刷新预检行。主按钮可以直接验；逐层按钮要先在小章页加载后再追加源文件，或直接 `POST /api/depth`。
- 章页读法条可以「跟随本书默认」、给本章单独覆写，或放弃覆写；有覆写时出现「恢复跟随本书」，待确认覆写时出现「确认覆写」与「放弃覆写」。
- 改读法后回章页：顶部提示「读法已改，深度页过期」，深读页打不开（404），不是坏链；把读法改回原值即可恢复原来的层。
- 从旧链接进研究档：地址跳到 `?tier=deep&retired=research`；若该章还留着旧根 `chapter.html`，页顶出现「研读档已退役，已切换到深读档。」。新生成的深读包直接走 `/reader/<uid>/<n>/depth`，旧 URL 会是 404。

## Driving it with curl + headless Chrome + pytest

Preconditions:

- 工作区按 [README](./README.md) 建好，五种服务按技能首页起好。
- 阅读服务由本轮起：`uv run --frozen python apps/reading/server.py --root $ROOT --port 18771 --no-annotate --quiet`，就绪行 `http://127.0.0.1:18771`。`init` + `sample` 之后书架应有 1 本书 `B0001`、9 章。
- `google-chrome-stable` 可用。章页与记录页是客户端渲染，`curl` 只能证明模板/js 存在；用户可见状态用 `--dump-dom` 取渲染后的 DOM，点击、键盘才用 CDP。
- `python3 -m bookcorpus.reading.depth.cli generate` 现在要求 `--mode`，或已有本书/本章读法账；否则报 `generate 需要 --mode，或先为本书/章写读法账`。不想打模型就别跑 CLI，走 HTTP 的逐层任务。

**页面入口与生成控件（Chrome DOM）。**

```bash
CHROME=google-chrome-stable
DOM="$ROOT/run/chapter-dom.html"
"$CHROME" --headless=new --disable-gpu --no-sandbox \
  --user-data-dir="$ROOT/run/chrome-chapter" --virtual-time-budget=8000 \
  --dump-dom http://127.0.0.1:18771/chapter/B0001/01 > "$DOM"
python3 - "$DOM" <<'PY'
import re, sys
html = open(sys.argv[1], encoding='utf-8').read()
body = re.sub(r'<script\b.*?</script>', '', html, flags=re.S | re.I)
for pat in ('id="mode-root"', 'id="mode-follow"', 'id="mode-reset"',
            'id="depth-panel"', 'id="depth-generate"'):
    print(pat, body.count(pat))
PY
# id="mode-root" 1
# id="mode-follow" 1
# id="mode-reset" 0
# id="depth-panel" 1
# id="depth-generate" 1
```

`--dump-dom` 的 HTML 里仍有 `<script>` 源码；直接 `grep` 会把模板字符串算进去（`mode-reset` 会数到 2）。先剥掉 `<script>` 再数，或用 CDP `querySelectorAll`。用户可见状态以剥掉脚本后的 DOM 为准。

**预检：小章放行。**

```bash
curl -s http://127.0.0.1:18771/api/chapter/B0001/01 \
  | python3 -c "import json,sys; p=json.load(sys.stdin)['depth']['preflight']; s=p['stats']; print(p['ok'], p['code'], s['blocks'], s['chunks'], s['max_chunk_blocks'], s['estimated_output_tokens'], s['estimated_seconds'])"
# True None 15 1 15 16800 155
```

章页同一行渲染成 `本章 15 块 · 1 个分块 · 单块最多 15 块 · 预计 16,800 输出 token · 约 155 秒`，没有 `blocked`，`#depth-generate` 没有 `disabled`。

**还没有书级读法：建议 + 409。**

```bash
curl -s http://127.0.0.1:18771/api/chapter/B0001/01 \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['path'], d['fallback_reason']); print(d['reading']['effective_mode'], d['reading']['effective_source'])"
# none 尚未生成
# narrative suggestion
curl -s -X POST http://127.0.0.1:18771/api/depth/B0001/01 \
  -H 'Content-Type: application/json' -d '{"layers":["skim"]}' -w '\nHTTP %{http_code}\n'
# {"error": "这本书还没有读法，请先确认书级默认读法", "code": "reading_profile_required", "uid": "B0001", ...}
# HTTP 409
```

章接口没有顶层 `book_mode` 键；书级默认在 `reading.book_mode`，规则建议在 `reading.suggestion`。

**书页设本书默认。**

```bash
curl -s -X PUT http://127.0.0.1:18771/api/book/B0001/reading \
  -H 'Content-Type: application/json' -d '{"mode":"argument"}'
# {"book_mode": "argument", "panels": [], "chapters": {}}
curl -s -X PUT http://127.0.0.1:18771/api/book/B0001/reading \
  -H 'Content-Type: application/json' -d '{"mode":"bogus"}' -w '\nHTTP %{http_code}\n'
# {"error": "mode 必须是 narrative/argument/practical/reference", "code": "bad_request", ...}
# HTTP 400
curl -s http://127.0.0.1:18771/api/chapter/B0001/01 \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['reading']['book_mode'])"  # argument
```

**预检：超限章阻断（用第 2 章，别动第 1 章的生成流程）。**

```bash
python3 - "$ROOT" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1]) / 'books' / 'B0001' / 'md' / '02_第二章优胜记略.md'
text = p.read_text(encoding='utf-8').rstrip()
p.write_text(text + '\n\n' + '\n\n'.join(f'第 {i} 段。' for i in range(1, 5011)) + '\n', encoding='utf-8')
PY
curl -s http://127.0.0.1:18771/api/chapter/B0001/02 \
  | python3 -c "import json,sys; p=json.load(sys.stdin)['depth']['preflight']; s=p['stats']; print(p['ok'], p['code'], s['blocks'], s['chunks'], s['max_chunk_blocks'], s['estimated_output_tokens'], s['estimated_seconds']); print(p['warnings'])"
# False source_too_large 5034 51 100 4955040 45460
# ['本章 5,034 块，超过 1,000 块；会切成多个分块，预计约 758 分钟。', ...]
```

Chrome 重新取一次 `/chapter/B0001/02`：`class="depth-preflight blocked"`，下面一行 `md 目录：books/B0001/md/`；`#depth-generate` 与 `#depth-rebuild-model` 都带 `disabled=""`，`data-depth-layer="model"` 也是 `checked="" disabled=""`。

```bash
curl -s -X POST http://127.0.0.1:18771/api/depth/B0001/02 \
  -H 'Content-Type: application/json' -d '{"layers":["model"],"model":"fake-model","effort":"max"}' \
  -w '\nHTTP %{http_code}\n'
# {"error":"本章 5,034 块，超过单章上限 5,000 块；...","code":"source_too_large","stats":{...},"remediation":"把 md/ 中的这个文件按章节或每 100 块拆成多个 md，再对每个 md 提交深读。"}
# HTTP 422
curl -s http://127.0.0.1:18771/api/jobs   # {"jobs": []}，422 不建任务
```

主按钮同样走预检。`POST /api/jobs` 在超限章上返回同一个结构化 422，不再断连接：

```bash
curl -s -X POST http://127.0.0.1:18771/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"uid":"B0001","n":2,"tiers":["standard"],"model":"fake-model","effort":"max"}' \
  -w '\nHTTP %{http_code}\n'
# {"error":"本章 5,034 块，超过单章上限 5,000 块；...","code":"source_too_large","stats":{...},"remediation":"把 md/ 中的这个文件按章节或每 100 块拆成多个 md，再对每个 md 提交深读。"}
# HTTP 422
curl -s http://127.0.0.1:18771/api/jobs   # {"jobs": []}，422 不建任务
```

章页用 CDP 点 `#generate`（主按钮）会：预检行变成 `class="depth-preflight blocked"`、深度两个按钮禁用、`#job-mount` 里出现一张 `预检未通过 / 已阻断` 卡（含 `本章 N 块 · M 个分块 · 约 T 输出 token`、remediation、`md 目录：books/<uid>/md/`），同时弹红 toast。主按钮本身仍可点，这是有意的：提交后才拿 422，不要因为 `#generate` 没禁用就判失败。已 blocked 的页面上 `#depth-generate` 是禁用的；要验逐层按钮的 422 分支，必须先加载小章页、再追加源文件、再点它（或直接 `POST /api/depth`）。

**逐层面板：202 进队列，轮询到 done。**（打模型，本轮约 194 秒；`--no-annotate` 只关注释，模型照打。这是章页下半的「生成所选层」，不是主按钮。）

```bash
curl -s -X POST http://127.0.0.1:18771/api/depth/B0001/01 \
  -H 'Content-Type: application/json' -d '{"layers":["skim"]}' -w '\nHTTP %{http_code}\n'
# {"id":"r-<10hex>","kind":"depth","uid":"B0001","n":"01","layers":["model","skim"],
#  "state":"queued","stage":"queued","progress":0,"error":null,...}
# HTTP 202
# 等 3s 后按记录里的 id 轮询：
curl -s http://127.0.0.1:18771/api/jobs/<id>
# 完成时 state=done，progress=100，layers_ready=["model","skim"]，
# steps 结构 done / 面板 pending / 速览 done / 标准 pending / 深读 pending / 渲染 done
```

完成后：

```bash
python3 -c "import json; d=json.load(open('$ROOT/工作区/阅读/B0001/ch/01/depth/state.json')); print(d['schema'], d['layer_mode'])"
# 2 {'skim': 'argument'}
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/reader/B0001/01/depth   # 200
curl -s http://127.0.0.1:18771/api/chapter/B0001/01 \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['path'], d['fallback_reason']); print([(t['key'],t['exists'],t['stale']) for t in d['tiers']])"
# depth None
# [('skim', True, False), ('standard', False, False), ('deep', False, False)]
```

第二条是这次新增的关键账：`state.json.layer_mode` 记下每层生成时的读法；旧 pack 没有 `layer_mode` 的层，在换读法后同样过期。

**读法改了：旧层过期。**（在已有一份可用 skim pack 之后跑。）

```bash
curl -s -X PUT http://127.0.0.1:18771/api/book/B0001/reading \
  -H 'Content-Type: application/json' -d '{"mode":"narrative"}' >/dev/null
curl -s http://127.0.0.1:18771/api/chapter/B0001/01 \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['path'], d['fallback_reason']); print([(t['key'],t['exists'],t['stale']) for t in d['tiers']]); print(d['depth']['state'])"
# none 读法已改，深度页过期
# [('skim', True, True), ('standard', False, True), ('deep', False, True)]
# stale
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/reader/B0001/01/depth   # 404
# 改回原读法，旧层恢复：
curl -s -X PUT http://127.0.0.1:18771/api/book/B0001/reading \
  -H 'Content-Type: application/json' -d '{"mode":"argument"}' >/dev/null
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/reader/B0001/01/depth   # 200
```

**速览质量按读法分支。**

模型产出的 `depth/skim.json` 与编译后的 `layers/skim.json` 不是一回事：前者是模型稿，后者是页面读的编译层（`sections`/`scenes`/`detail_index`，`prompt_version=skim-compiler-v*`）。质量闸门与比例统计读编译层，但校验要用原文 blocks 与 `chapter_model.json`。直接调库里的 `audit_skim`，不要自己拿文件字节数当分母（闸门算的是汉字数）：

```bash
uv run --frozen python - <<'PY'
import json
from bookcorpus.reading.library import Library
from bookcorpus.reading.depth.skim import audit_skim
root = '$ROOT'
base = root + '/工作区/阅读/B0001/ch/01/depth/'
raw = Library(root).read_chapter('B0001', 1)
skim = json.load(open(base + 'layers/skim.json'))
model = json.load(open(base + 'chapter_model.json'))
mode = 'argument'   # 换成实际读法
rep = audit_skim(skim, model, raw['blocks'], mode=mode)
print('kind', skim.get('kind'), 'ratio', rep['stats'].get('ratio'), 'ok', rep['stats'].get('ratio_ok'))
print('errors', rep['errors'])
assert not rep['errors'] and rep['stats']['ratio_ok']
PY
# kind claims ratio 0.2464 ok True（本轮 ch02）
# errors []
```

`audit_skim` 的 `mode` 不传时按 `payload['kind']` 推断；叙事读法只查 `scenes` 与上限（`body_max`，没有 15% 下限），其余读法要 `claims` 且比例落在 15%–25%。`kind` 与读法不匹配时错误是 `narrative 读法的速览 kind 必须是 scenes` 或 `<mode> 读法的速览 kind 必须是 claims`；比例越界时质量闸门最多自动重试一次。

**记录页。**

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/records   # 200
curl -s http://127.0.0.1:18771/api/records | python3 -m json.tool | head -40
```

记录页分 `全部 / 运行中 / 完成 / 已阻断 / 失败` 五档（`apps/reading/web/records.html:69-75`）；失败与取消都在「失败」档。`blocked` 有专用「已阻断」档和统计（`records.html:125-135`），状态徽标「已阻断」，`jobSubtitle`（`records.html:414-420`）写 `已阻断 · 源文件过大`。`source_too_large` 已在 `RD.errorKindName`（`app.js:167-186`）登记中文名；blocked 详情不出 `重跑所选层` / `重建结构模型`（`records.html:218-231`），改为显示 `job.remediation`。id 形如 `r-<10hex>`，不是 `r-1`。

**研究档旧链接。**

```bash
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' \
  'http://127.0.0.1:18771/reader/B0001/01?tier=research'
# 302 http://127.0.0.1:18771/reader/B0001/01?tier=deep&retired=research
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' \
  'http://127.0.0.1:18771/reader/B0001/01/depth?tier=research'
# 302 http://127.0.0.1:18771/reader/B0001/01/depth?tier=deep&retired=research
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/reader/B0001/01/depth   # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/api/export/B0001/01/research  # 410
curl -s -o /dev/null -w '%{http_code}\n' -X GET http://127.0.0.1:18771/api/depth/B0001/01  # 405
```

重定向按 `parse_qs` 解析，`?tier=research&x=1` 也跳，额外参数保留；目标同时带 `tier=deep&retired=research`。退役提示由 `_serve_reader` 与 `_serve_depth` 都注入，条件是查询串里有 `tier=deep&retired=research`；旧根 `chapter.html` 只决定 `/reader/<uid>/<n>?tier=deep&retired=research` 能不能出页面（新 depth 包下该 URL 仍 404）。要验旧读本页的提示，按 `tests/test_reading_server.py:183-188` 往 `工作区/阅读/<uid>/ch/<n>/chapter.html` 写一个 `<html><body>旧读本</body></html>`，再取该 URL，`grep -c '研读档已退役，已切换到深读档。'` 为 1；深度页在 200 时同一查询串也会出现提示。`/api/export` 研究档一律 410 `legacy 研究导出已退役；深读档在 /reader/<uid>/<n>/depth`；已保存的导出文件还能直接打开。

**旧三档路径（已有 manifest 的旧包；本技能不默认跑）。** 主按钮「生成读本」发 `POST /api/jobs`，body 形如 `{"uid":"B0001","n":1,"tiers":["skim"]}`。新章在深度默认开启且没有旧 manifest 时，服务端会把请求改写成五层 depth 任务，请求里的 `tiers` 直接丢掉；旧包才走三档 `submit`。已有档位再提交返回 `409`：

```json
{"error":"该档位已有读本","code":"pack_exists","tiers":["skim"]}
```

页面收到 `pack_exists` 先弹确认，确认后才带 `overwrite:true` 重发。逐层面板走 `/api/depth`，**没有**覆盖确认，只补缺失/过期层。

生成是异步的：POST 返回 `202` + 任务 JSON，`state` 从 `queued` 经 `chunking/annotating/...` 到 `done`/`partial`/`failed`/`cancelled`；源文件在提交后变大时 runner 会记 `blocked`（`src/bookcorpus/reading/jobs.py:376-388`），不写失败现场。中断或失败会把现场写到 `工作区/阅读/failures/<job-id>-<layer>.json`（`src/bookcorpus/reading/failures.py:40-80`），不放进 `depth/`，避免下一次 `save_depth` 整目录替换时被带走。

**形式验收。**

```bash
uv run --frozen pytest tests/test_reading_preflight.py -q
# 17 dots，退出 0
uv run --frozen python verification/chapter_path/check_paths.py
# checked 128 disk states; 24 reachable by the model; P1/P2 无违规
```

脚本自己造临时根，不读 `sys.argv`，传 `--root` 也不生效。`128` = 4 种 depth 状态 × 32 个布尔组合（`html`/`model`/`ledger`/`legacy`/`sha` 各真假），`24` 是 `reachable()` 认为可服务的组合；换 `reachable()` 规则时 `24` 会变。

## Gotchas

- `POST /api/depth` 或 `/api/jobs` 在书页还没设默认读法时返回 `409 reading_profile_required`，不是 500；新工作区必须先 `PUT /api/book/<uid>/reading`。
- 章接口没有顶层 `book_mode`；书级默认在 `reading.book_mode`，规则建议在 `reading.suggestion`（`effective_mode` 只是展示值）。
- 章页是 `/chapter/<uid>/<n>`，不是 `/reader/<uid>/<n>`；`/reader` 是已生成读本的阅读页，没有读本时 404「这一章还没有生成读本」，深读页是 `/reader/<uid>/<n>/depth`。把章页写成 `/reader` 会得到 404，不是页面坏了。
- `id="mode-reset"` 只在有章级覆写（或待确认覆写）时出现；但章页是客户端渲染，`curl | grep -c` 数到 2 是脚本模板里的两个字符串，不是两个按钮。判断渲染态用 `--dump-dom` 后的 DOM，别用 curl 数动态元素。
- 新工作区主按钮 `#generate` 是 `先选一个读法 →` 且 `disabled`，不是 `按「…」生成读本 →`；后者是已确认读法后的渲染。逐层 `#depth-generate` 仍写 `生成全部五层`，没有读法时点它返回 409。
- 书页的 `设本书默认` 只写账，不生成；`--dump-dom` 后的 DOM 里 `生成读本` 仍应为 0。不要把「能点」当成「已出读本」。
- 改读法后深读页 404、`path=none`、`tiers.*.stale=true` 都是正确行为，不是 pack 损坏；把读法改回原值就恢复（前提是 `layer_mode` 与改回的值一致）。
- `?tier=research` 的 302 目标现在带 `retired=research`；只断言 `tier=deep` 会漏掉退役标记。重定向是参数级匹配，`?tier=research&x=1` 也跳，额外参数保留。
- 退役提示不是重定向的必然结果：`_serve_reader` 与 `_serve_depth` 在页面 200 时才注入；新 depth 包没有旧根 `chapter.html`，`/reader/<uid>/<n>?tier=deep&retired=research` 是 404，深读页是 `/reader/<uid>/<n>/depth`；别把 404 当重定向坏了。
- `layers/skim.json` 是编译后的页面层（`sections`/`scenes`），模型稿 `depth/skim.json` 才有模型原始结构；质量断言读错文件会得到 `KeyError`。
- 比例闸门的分母是原文汉字数（`source_han`），不是文件字节数；`audit_skim` 的 `ratio` 才是闸门值，拿 `os.path.getsize` 算会偏。
- 记录 id 形如 `r-<10hex>`，不是固定 `r-1`；新工作区第一次生成后记录页才有一条。
- 叙事速览没有 15% 下限，只查 `scenes` 与上限；把 15%–25% 套到叙事会误判合格稿。
- `generate` CLI 的 `--mode` 是新增必填（或先写读法账）；只传 `--root/--uid/--n` 会报 `generate 需要 --mode，或先为本书/章写读法账`。
- `sample` 的 9 章都是 2 KB 上下的小章，生成一份 skim 本轮约 194 秒；超时先看任务记录的 `stage`/`phase` 与 `工作区/阅读/failures/`，不要重复提交。
- 书架统计来自 `build/阅读/shelf-index.db`，服务启动时增量刷新；重建用 `book-corpus run reading_index --rebuild`，只查状态用 `--status`。索引坏了先看 `.corrupt-*`/`.bak-*`，不是书架没数据。
- 预检只在 `service.depth` 打开时出现在 `/api/chapter`；`--no-annotate` 不关深度，`READING_DEPTH=0` 才关。关掉后章页没有预检行，`POST /api/depth` 返回 400「深读模式已关闭；请按旧路径生成读本」。
- 超限章在书级读法未设时先返回 409 `reading_profile_required`，不是 422；先 `PUT /api/book/<uid>/reading` 才能看到预检 422。`POST /api/depth` 先过读法闸门（`apps/reading/server.py:466`），再在 `submit_depth` 里预检（`src/bookcorpus/reading/service.py:1068`）。
- 422 是提交前闸门，不建任务：`GET /api/jobs` 仍是空，记录页不会出现 blocked 工单。别把「记录页没变化」当预检没生效。
- 422 时章页会先用 payload 更新 `data.depth.preflight` 并重绘预检行，再把「预检未通过 / 已阻断」卡写进重绘后的 `#depth-jobs`（逐层）或 `#job-mount`（主按钮）；卡片含 stats、remediation、`md 目录`，两个深度按钮禁用。旧的「只弹 toast、不刷新预检」行为已修，别按旧缺口判失败。
- 主按钮 `#generate` 本身不禁用；点它走 `POST /api/jobs`，超限时返回结构化 422（`_post_job` 的 `SourceTooLarge` 分支，`apps/reading/server.py:339-447`），不建任务，不再断连接。`/api/jobs` 仍应是空的。
- 记录页 blocked 工单有专用「已阻断」档和统计，副标题 `已阻断 · 源文件过大`；详情不出 `重跑所选层` / `重建结构模型`，改为显示 remediation，`source_too_large` 已登记中文名。422 本身不建任务，所以记录页只会有运行期变成 blocked 的工单。
- 预检硬上限是 5,000 块 / 100 分块 / 1,000,000 输出 token / 7,200 秒；样例短段落约 798 块就先撞 7,200 秒，错误文案是秒数超限，别以为要 5,000 块才阻断。警告线是 1,000 块 / 20 分块 / 1 小时 / 单块 >100；警告不阻断，命中任一硬上限才 `ok=false`。
- 样例第 1 章原有 15 块，追加 5,010 段后 API 报 5,025 块；第 2 章原有 24 块，追加后报 5,034 块。`blocks` 是解析后的块数，不是追加的段数。
- `estimated_seconds` 按 109 token/s 估算（`src/bookcorpus/reading/depth/preflight.py:22`），不是实际耗时；45,460 秒只是预检值，别拿它当任务已经跑了 12.6 小时。
