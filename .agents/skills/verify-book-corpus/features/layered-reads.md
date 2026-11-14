# 分层读（读本与深读层）

分层读把一章原文生成五层：`CH`/`A1 图文总览`/`A1′ 速览`/`A2 标准读本`/`A3 深读`，面板、速览、标准层共用一份章节模型。页面上的读法（叙事 `narrative`、论证 `argument`、实用 `practical`、资料 `reference`）是生成与编译的输入；2026-11-13 起，读法改了以后，盘上旧读法生成的层会整体过期，章节路由退回原文，深度页 404。书页可以直接设本书默认读法，但只写账，不生成。

## Sub-features

- `read-shelf` 书架 `/`：可读的书、章节数、字数、最近在读。
- `read-book` 书页 `/book/<uid>`：书目信息、章节表、**本书默认读法**。按钮文案 `设本书默认`（已有默认时 `更新本书默认`），只写人账，不提交生成；页面上没有「生成读本」。
- `read-chapter` 章页 `/reader/<uid>/<n>`：五层读本、读法条（跟随本书默认 / 章级覆写 / 放弃覆写）、生成入口、任务记录。没有书级默认时，生成区显示 `先选一个读法 →`。
- `read-stale` 读法改了：深度层过期。章节路由退回 `none`，理由「读法已改，深度页过期」，`tiers.*.stale` 全为 `true`，`/reader/<uid>/<n>/depth` 404；把读法改回原值时，若盘上 `layer_mode` 与它一致，旧层恢复可用。
- `read-profile` 读法闸门：没有书级默认读法时，深度生成返回 `409 reading_profile_required`。
- `read-quality` 速览质量：按读法分支。叙事只要 `scenes` 且不超上限；其余读法要 `claims`，正文占比 15%–25%，最多自动重试一次。
- `read-generation` 读法确认后的生成：书页只设默认；章页主按钮「生成读本」走 `POST /api/jobs`（三档；新章在深度默认开启且没有旧 manifest 时会被服务端改写成五层 depth 任务），章页下半的逐层面板走 `POST /api/depth`（只补缺层，无覆盖确认，202 + 轮询）。
- `read-records` 生成记录 `/records`：队列与历史工单，加上磁盘上有 `manifest.json` 的章读本（`/api/records` 返回 `jobs` + `packs`）。
- `read-legacy` 旧链接：`/reader/...?tier=research` 302 到深读档（URL 保留 `retired=research`）；**若该章还有旧根 `chapter.html`**，页顶注入退役提示。新 depth 包只有 `depth/chapter.html`，同 URL 会 404「这一章还没有生成读本」，深读页走 `/reader/<uid>/<n>/depth`。研究导出 410。
- `read-formal` 形式验收：单章磁盘状态检查与读法/深度层可达性。

## How to get to it (user POV)

- 浏览器打开 `http://127.0.0.1:18771/`，书架列出可读的书；点书进 `/book/<uid>`，点章进 `/reader/<uid>/<n>`。
- 在书页选一个读法，点 `设本书默认`：只记默认，不出读本；按钮变成 `更新本书默认`，旁边文案仍是「只设本书默认，不生成；章级覆写在章页设置。」
- 回章页：已生成过的层直接显示；没有生成的层按需点生成。没有书级默认时，生成区显示 `先选一个读法 →`，点它回书页选读法。
- 章页读法条可以「跟随本书默认」、给本章单独覆写，或放弃覆写；有覆写或待确认覆写时才出现「放弃章级覆写」。
- 改读法后回章页：顶部提示「读法已改，深度页过期」，深读页打不开（404），不是坏链；把读法改回原值即可恢复原来的层。
- 从旧链接进研究档：地址跳到 `?tier=deep&retired=research`；若该章还留着旧根 `chapter.html`，页顶出现「研读档已退役，已切换到深读档。」。新生成的深读包直接走 `/reader/<uid>/<n>/depth`，旧 URL 会是 404。

## Driving it with curl + pytest

Preconditions:

- 工作区按 [README](./README.md) 建好，五种服务按技能首页起好。
- 阅读服务由本轮起：`uv run --frozen python apps/reading/server.py --root $ROOT --port 18771 --no-annotate --quiet`，就绪行 `http://127.0.0.1:18771`。`init` + `sample` 之后书架应有 1 本书 `B0001`、9 章。
- `python3 -m bookcorpus.reading.depth.cli generate` 现在要求 `--mode`，或已有本书/本章读法账；否则报 `generate 需要 --mode，或先为本书/章写读法账`。不想打模型就别跑 CLI，走 HTTP 的逐层任务。

**页面入口与生成控件。**

```bash
curl -s http://127.0.0.1:18771/ | grep -c 'id="shelf"'          # 1
curl -s http://127.0.0.1:18771/book/B0001 | grep -c 'id="book-mode-set"'   # 1，书页可设默认
curl -s http://127.0.0.1:18771/book/B0001 | grep -c '生成读本'            # 0，书页不生成
curl -s http://127.0.0.1:18771/reader/B0001/01 | grep -c 'id="mode-root"'  # 1
curl -s http://127.0.0.1:18771/reader/B0001/01 | grep -c 'id="mode-follow"' # 1
curl -s http://127.0.0.1:18771/reader/B0001/01 | grep -c 'id="mode-reset"'  # 0 或 1；有覆写才有
curl -s http://127.0.0.1:18771/reader/B0001/01 | grep -c 'mode-confirm'     # 0 或 1；待确认才有
```

`book-corpus sample` 不写读法账，所以新工作区的章页生成按钮是 `先选一个读法 →`（`title="先选一个读法，设为本书默认"`），不是 `按「叙事」生成读本 →`。后者是模板字符串，只有已确认读法时才渲染成按钮。

**还没有书级读法：建议 + 409。**

```bash
curl -s http://127.0.0.1:18771/api/chapter/B0001/01 \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['path'], d['fallback_reason']); print(d['reading']['effective_mode'], d['reading']['effective_source'])"
# none 尚未生成
# narrative rule
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

**逐层面板：202 进队列，轮询到 done。**（打模型，约 3 分钟；`--no-annotate` 只关注释，模型照打。这是章页下半的「生成所选层」，不是主按钮「生成读本」。）

```bash
curl -s -X POST http://127.0.0.1:18771/api/depth/B0001/01 \
  -H 'Content-Type: application/json' -d '{"layers":["skim"]}' -w '\nHTTP %{http_code}\n'
# {"id":"r-<12hex>","kind":"depth","uid":"B0001","n":"01","layers":["model","skim"],
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
# kind claims ratio 0.2392 ok True
# errors []
```

`audit_skim` 的 `mode` 不传时按 `payload['kind']` 推断；叙事读法只查 `scenes` 与上限（`body_max`，没有 15% 下限），其余读法要 `claims` 且比例落在 15%–25%。`kind` 与读法不匹配时错误是 `narrative 读法的速览 kind 必须是 scenes` 或 `<mode> 读法的速览 kind 必须是 claims`；比例越界时质量闸门最多自动重试一次。

**记录页。**

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18771/records   # 200
curl -s http://127.0.0.1:18771/api/records | python3 -m json.tool | head -40
```

记录页分 `全部 / 运行中 / 完成 / 失败` 四档（`apps/reading/web/records.html:69-74`）；失败与取消都在「失败」档，详情里带 `error_kind` 的中文名。id 形如 `r-<12hex>`，不是 `r-1`。

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

重定向只在查询串恰好是 `tier=research` 时触发；`?tier=research&x=1` 不跳，直接 404。退役提示只由 `_serve_reader` 注入，前提是章目录根上还有旧读本 `chapter.html`；新 depth 包把 `chapter.html` 放在 `depth/` 下，`/reader/<uid>/<n>?tier=deep&retired=research` 会 404。要验提示本身，按 `tests/test_reading_server.py:183-188` 往 `工作区/阅读/<uid>/ch/<n>/chapter.html` 写一个 `<html><body>旧读本</body></html>`，再取该 URL，`grep -c '研读档已退役，已切换到深读档。'` 为 1。`/api/export` 研究档一律 410 `legacy 研究导出已退役；深读档在 /reader/<uid>/<n>/depth`；已保存的导出文件还能直接打开。

**旧三档路径（已有 manifest 的旧包；本技能不默认跑）。** 主按钮「生成读本」发 `POST /api/jobs`，body 形如 `{"uid":"B0001","n":1,"tiers":["skim"]}`。新章在深度默认开启且没有旧 manifest 时，服务端会把请求改写成五层 depth 任务，请求里的 `tiers` 直接丢掉；旧包才走三档 `submit`。已有档位再提交返回 `409`：

```json
{"error":"该档位已有读本","code":"pack_exists","tiers":["skim"]}
```

页面收到 `pack_exists` 先弹确认，确认后才带 `overwrite:true` 重发。逐层面板走 `/api/depth`，**没有**覆盖确认，只补缺失/过期层。

生成是异步的：POST 返回 `202` + 任务 JSON，`state` 从 `queued` 经 `running` 到 `done`/`failed`。中断或失败会把现场写到 `工作区/阅读/failures/<job-id>-<layer>.json`（`src/bookcorpus/reading/failures.py:40-80`），不放进 `depth/`，避免下一次 `save_depth` 整目录替换时被带走。

**形式验收。**

```bash
uv run --frozen python verification/chapter_path/check_paths.py --root "$ROOT"
# checked 128 disk states; 24 reachable by the model; P1/P2 无违规
```

`128/24` 是 `sample` 的 8 种磁盘状态 × 16 种读法组合的结果，不是可随手改的常数；换样本或读法表时会变。

## Gotchas

- `POST /api/depth` 或 `/api/jobs` 在书页还没设默认读法时返回 `409 reading_profile_required`，不是 500；新工作区必须先 `PUT /api/book/<uid>/reading`。
- 章接口没有顶层 `book_mode`；书级默认在 `reading.book_mode`，规则建议在 `reading.suggestion`（`effective_mode` 只是展示值）。
- `id="mode-reset"` 只在有章级覆写（或待确认覆写）时出现；新工作区 grep 到 0 是对的，把它当无条件元素会误报。
- 新工作区章页生成按钮是 `先选一个读法 →`，不是 `按「…」生成读本 →`；后者是模板字符串，别用静态 grep 证明按钮已渲染。
- 书页的 `设本书默认` 只写账，不生成；`grep -c '生成读本'` 仍应为 0。不要把「能点」当成「已出读本」。
- 改读法后深读页 404、`path=none`、`tiers.*.stale=true` 都是正确行为，不是 pack 损坏；把读法改回原值就恢复（前提是 `layer_mode` 与改回的值一致）。
- `?tier=research` 的 302 目标现在带 `retired=research`；只断言 `tier=deep` 会漏掉退役标记。重定向只匹配精确查询串，`?tier=research&x=1` 不跳。
- 退役提示不是重定向的必然结果：它只注入旧根 `chapter.html`。新 depth 包下 `/reader/<uid>/<n>?tier=deep&retired=research` 是 404，深读页是 `/reader/<uid>/<n>/depth`；别把 404 当重定向坏了。
- `layers/skim.json` 是编译后的页面层（`sections`/`scenes`），模型稿 `depth/skim.json` 才有模型原始结构；质量断言读错文件会得到 `KeyError`。
- 比例闸门的分母是原文汉字数（`source_han`），不是文件字节数；`audit_skim` 的 `ratio` 才是闸门值，拿 `os.path.getsize` 算会偏。
- 记录 id 形如 `r-<12hex>`，不是固定 `r-1`；新工作区第一次生成后记录页才有一条。
- 叙事速览没有 15% 下限，只查 `scenes` 与上限；把 15%–25% 套到叙事会误判合格稿。
- `generate` CLI 的 `--mode` 是新增必填（或先写读法账）；只传 `--root/--uid/--n` 会报 `generate 需要 --mode，或先为本书/章写读法账`。
- `sample` 的 9 章都是 2 KB 上下的小章，生成一份 skim 约 3 分钟；超时先看任务记录的 `stage`/`phase` 与 `工作区/阅读/failures/`，不要重复提交。
