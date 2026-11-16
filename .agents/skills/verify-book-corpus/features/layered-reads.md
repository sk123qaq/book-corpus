# 分层读（正文伴读工作台与操作日志）

分层读阅读页采用**正文伴读 + 顶部工具台 + 划词工具栏 + 右侧旁批与伴读抽屉**结构，已退役旧版三档/五层读本、工单队列与视觉读本（`edition`）：

- 章页 `/chapter/<uid>/<n>` 默认直接呈现章节 **100% 完整原文**（按段落 `B01`, `B02`, … 锚点渲染）；
- 顶部常驻工具栏：`知识地图`（带短章门槛预检 `chapter_map_gate`）、`核心术语`、`旁批` 开关、`模型设置`、`伴读抽屉`；
- 正文划词或选中段落浮现工具栏：解释、白话/译文、背景补充、逻辑拆解、图解，点击走 `POST /api/assist`（SSE 流式输出），支持「钉选为旁批」（`POST /api/assist/pin` / `DELETE /api/assist/pin`）；
- 记录页 `/records` 仅保留**各章节伴读与知识地图操作日志**（`ops_log.jsonl`，只记用户实际触发的动作、模型、状态与回跳 `#blk-<id>` 锚点）。

## Sub-features

- `read-shelf` 书架 `/`：可读的书、章节数、字数、伴读与知识地图活动统计（`/api/shelf`）。
- `read-book` 书页 `/book/<uid>`：书目信息、章节目录与各章操作计数、伴读入口（`/api/book/<uid>`，不再设置或暴露「默认读法」）。
- `read-chapter` 章页 `/chapter/<uid>/<n>`：完整原文正文、顶部工具台（`#btn-map` / `#btn-terms` / `#btn-toggle-pins` / `#btn-model-box` / `#btn-drawer`）、划词浮动工具栏（`#selection-bar`）、伴读抽屉（`#assist-drawer`）与段落旁批。
- `read-preflight` 知识地图预检：`/api/chapter/<uid>/<n>` 中的 `preflight.chapter_map` 给出 `supported`、`reason` 与 `stats`（`blocks` / `han_chars` / `chunks` 等）；短章（< 800 汉字且 < 5 块）禁用地图生成并给原因提示。
- `read-assist` 流式伴读与旁批：`POST /api/assist` 返回 SSE 事件流（`meta` / `delta` / `done` / `error`），可选 `action`：`diagram` / `explain` / `translate` / `critique` / `ask`；`POST /api/assist/pin` 将结果钉选成某段落旁批并持久化到 `assist_notes.json`。
- `read-records` 操作日志 `/records`：`GET /api/records` 返回所有操作日志（`logs` / `chapters` / `summary`），每条包含 `uid`、`n`、`action`、`block_ids`、`model`、`status`、`error`、`ts`，支持按状态筛选并回跳 `/chapter/<uid>/<n>#blk-<block_id>`。

## Driving it with curl + headless Chrome + pytest

Preconditions:

- 工作区按 [README](./README.md) 建好，`apps/reading/server.py` 启动在 `18771`。
- `google-chrome-stable` 可用；章页和记录页是客户端渲染，用户可见状态以剥掉 `<script>` 后的 `--dump-dom` 为准。

```bash
CHROME=google-chrome-stable
DOM="$ROOT/run/chapter-dom.html"
"$CHROME" --headless=new --disable-gpu --no-sandbox \
  --user-data-dir="$ROOT/run/chrome-chapter" --virtual-time-budget=8000 \
  --dump-dom http://127.0.0.1:18771/chapter/B0001/01 > "$DOM"
python3 - "$DOM" <<'PY'
import re, sys
html = open(sys.argv[1], encoding="utf-8").read()
body = re.sub(r"<script\b.*?</script>", "", html, flags=re.S | re.I)
for pat in ('id="btn-map"', 'id="btn-terms"', 'id="assist-drawer"', 'id="selection-bar"', 'blk-B01'):
    print(pat, body.count(pat))
assert "工单" not in body
PY
```

```bash
curl -s http://127.0.0.1:18771/api/chapter/B0001/01 \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['blocks']), d['preflight']['chapter_map']['supported'])"

curl -s http://127.0.0.1:18771/api/records \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(list(d.keys()))"
# ['logs', 'chapters', 'summary']
```
