# book-corpus 验证地图

这份地图是 book-corpus 用户可见行为的验证源：先读本索引，再按对应功能文件开跑。
它只写本仓库现在真实能跑的命令，不写「应该有」的工具。

## 基线前置

所有命令都在 git 仓库根执行（先 `cd "$(git rev-parse --show-toplevel)"`），工作区根用一次性的。

```
ROOT=/tmp/bc-verify-$RUN_ID
mkdir -p "$ROOT/run"
export BOOK_CORPUS_ENV="$ROOT/run/env"   # init 写这里，不碰仓库 .env
unset BOOKS_ROOT BOOK_CORPUS_DATA        # find_root() 先看环境变量
uv run --frozen book-corpus init "$ROOT" --no-pdf-venv
uv run --frozen book-corpus sample --root "$ROOT"
uv run --frozen book-corpus doctor --root "$ROOT"
```

- `init` 约 0.2 秒，先打印「工作区建好」，再把 `BOOKS_ROOT` 记到 `$BOOK_CORPUS_ENV`（未设时写仓库 `.env`，会污染本仓），随后两行 venv 未建警告（`--no-pdf-venv` 的预期结果）、概念表、书目库与检索索引。
- `sample` 装内置样书：阿Q正传 `B0001`，9 章，检索索引 123 段，`book_concept` 3 条。字数两处口径不同：`sample` 报 22881 字（含标题等），分层读 `/api/shelf` 报 21362 正文字符。
- `doctor` 只读，逐行 `ok`/`warn`。允许的 `warn` 只有两类：**环境**（PDF 环境、文档环境）与**云端 key 缺失**（配置·MinerU / PaddleOCR / MiMo）。落在工作区、清单、账、库上的 `warn` 才算问题，先解释再开跑。
- 端口用本轮私有的一套：演示 18765、分层读 18771、日课 18772、首页 18773（另 18774 / 18775 验 503 与拉起）、操作台 18770。
- 8765 / 8771 / 8772 / 8773 是操作员的常驻实例，跑在操作员的真实库 `~/书库` 上（`apps/reading/server.py --root` 默认值就是它）。永不驱、永不杀。
- 需要「有内容」才成立的验证，手写 `$ROOT/日课/<date>.json`（契约见 [daily-lessons.md](./daily-lessons.md)）。`book-corpus daily --dry-run` 打选书、模型/思考/工具、`----- 提示词 -----` 与提示词四块，不落盘，不能当 seed。
- embed 要 llama-server 与 GGUF 模型，软链进 `$ROOT/工作区/中间态/embed/`，用私有 18780；见 [embed.md](./embed.md)。

## 驱动约定

- 每个服务由本轮自己起、自己按 PID 杀，不按进程名杀。
- 服务端渲染页（首页、日课、操作台）用 `curl` 取 HTML 断言稳定标记；接口一律 `curl` 取 JSON 断言字段。分层读章页与记录页是客户端渲染，`curl` 只证明模板/js 在，用户可见状态必须用 headless Chrome `--dump-dom` 取渲染后的 DOM，并先剥掉 `<script>` 源码（`--dump-dom` 也会带出模板字符串）；点击、键盘才用 CDP 脚本。具体命令见 [layered-reads.md](./layered-reads.md)。
- CDP 脚本默认找 `google-chrome-stable`；本机有（2026-11-14 实测 155.0.8059.79），不设 `CHROME_BIN` 也能跑。`/usr/bin/chromium` 与 `/usr/bin/microsoft-edge-stable` 都不存在；要显式指定时用 `/usr/bin/google-chrome-stable` 或 `/usr/bin/microsoft-edge`。缺了它脚本死在 launch，不是页面问题。
- 命令逐字复制，不改引号、不改参数。
- 证据写 `artifacts/verify/$RUN_ID/<feature>/`；清理只删实例与临时根，不删证据。
- 五个服务（首页 8773 / 分层读 8771 / 日课 8772 / 操作台 8770 / 演示页 8765）都只认 `127.0.0.1` / `localhost` 的 Host 头，别的 Host 回 403。演示页直到 2026-11-12 晚才补上这道检查（`apps/demo/server.py` 的 `ALLOWED_HOST`），旧脚本里「演示页不校验 Host」的说法已作废。

## 证明与跳过

- 2026-11-16 分层读局部复核 `RUN_ID=20261116-layered`：书架/书目/书页/章页/记录页走 curl + `--dump-dom`，并用 CDP 点了知识地图、模型设置与伴读抽屉；短章门控、stub 生成/缓存、SSE 图解/讲人话、旁批钉住/移除、Host 403 与退役路由 404 都有证据，`CONSOLE ERROR 0`。真模型未跑，模型分支由 `scripts/mock_reading_model.py` 本地 stub 驱动；容量 422 未 live 演练。证据在 `artifacts/verify/20261116-layered/layered-reads/`。下一轮跑完请就地更新这一行。
- 2026-11-14 的 `20261114-mt2` 是旧口径：当时还有书级读法、三档/五层读本与 `/reader/<uid>/<n>/depth`，这些界面在 `ce30e6a` / `2b8bf2d` 已退役，不能按原记录复跑，只作历史。`20261114-lr-preflight` 的「章页是客户端渲染」仍然成立。
- 2026-11-15 书库检索局部复核 `RUN_ID=20261115-search-update`：找一段补 SSE / 暂停续答 / 混合检索（真答 34–42 秒、暂停 `partial_text` 3–5 字、resume `done.ok=true`；`shot.py` 截到流式半截正文与「暂停」按钮、`CONSOLE ERROR 0`、`overflow=False`）；CLI 补 `search`/`query` 词法与混合 (`hybrid, vec_hits=34`)、降级 `bm25_only`、书卡触发。证据在 `artifacts/verify/20261115-search-update/`。候选书多选与暂停按钮点击仍记 `ui-unverified`：样书只有 1 本，多选需 ≥2 本候选；暂停点击已由 API 层 SSE+pause+resume 证明。
- 2026-11-13 的 `20261113-mt` 已随 `artifacts/` 轮转清掉；旧轮次证据不保证还在，别把历史 RUN_ID 当可复核链接。

- 一次证明要同时留下动作与结果状态，不能只有最终画面。
- 只验了接口没走页面时记 `ui-unverified`，不算 UI 通过。
- 缺前置就报失败和原因，不把整条路径降级成「别处跑通了」。
- 已知红就是红，见下节。

## 本次已知红（2026-11-14 复核）

- `tests/manual/ops/shot_ops.py` 现在两个断点都绿：末行 `SHOT PASS`、`CONSOLE ERROR 0`、`overflow=false`。2026-11-12 晚修好了三处脚本与产品口径不一致（进度勾选 3→5、`[data-step]` 9→8 且接受「N 步全过」、「停止」按钮改成不在 DOM 里留死节点），并补上 `ledger` 页签，八个页签全通。旧记录里的 `SHOT FAIL` 不要再当预期值。
- 该脚本默认用 `google-chrome-stable` 就能跑（本机有，2026-11-14 实测），必须带 `PYTHONPATH=$PWD/tests/acceptance/ops`；裸命令死在 launch（找不到浏览器）或 `ModuleNotFoundError: No module named 'check_ops'`。
- `shot_ops.py` 偶发单页 `Runtime.evaluate: {'code': -32000, 'message': 'Inspected target navigated or closed'}`（2026-11-14 首次跑在导入页遇到）；原样重跑一次即 `SHOT PASS`。先重跑，再判产品红。失败留下的 `/tmp/shot-*` 配置目录要手工清。
- `verification/chapter_path/check_paths.py` 用 `uv run --frozen python verification/chapter_path/check_paths.py`，不要传 `--root`：脚本自己造临时根，也不读 `sys.argv`。裸 `python3` 会 `ModuleNotFoundError: No module named 'bookcorpus'`。输出 `checked 128 disk states; 24 reachable by the model`，`128` = 4 种状态 × 32 个布尔组合，`24` 是 `reachable()` 认为可服务的组合，别拿 128 当可服务数。
- 演示页的会话库在仓库内 `apps/demo/缓存/sessions/`，不随 `BOOKS_ROOT` 隔离；新实例照样列出操作员的会话（2026-11-14 为 11 条，随使用变化）。见 [find-a-passage.md](./find-a-passage.md) 的 Gotchas。
- `/classic` 自 `de5e792` 起发 `stateless:true`，不建会话（`apps/demo/server.py:873`），旧记录里的「经典页 turn 永远 pending」已作废。经典页的 turn 不进历史、不能导出，这是设计；要验导出或历史必须走 `/` 的会话页。
- `tests/manual/demo/shot.py` 只等 25 秒（`tests/manual/demo/shot.py:233`）。单本回答 2026-11-15 实测 34–42 秒，流式期间脚本会先报 `answer ready=False cites=0`，但正文已在上屏；不能把 `ready=False` 当失败，慢回答另轮询 `/api/session` 或看 SSE 的 `done`。
- 分层读已经没有读法、三档/五层、读本页或工单系统；`/reader/...`、`/api/depth/...`、`/api/jobs` 等退役路由一律 404。`--no-annotate` 只是兼容开关，不再关任何生成；模型只在划词伴读与知识地图生成时按需调用。离线可验书架、书目、书页、章页、容量/短章门控、缓存地图、旁批与日志；真生成与真伴读用 `scripts/mock_reading_model.py` 的本地 stub，或明确记 `ui-unverified`。没有环境 key 也不等于离线：`ModelClient` 还会读 `~/.pi/agent/auth.json` 的 opencode-go 登录。

## 功能入口契约

每个功能文件以 H1 标题 + 一段用户可见行为说明开头，之后严格四个 H2，顺序固定：

1. `Sub-features`：短 id + 一行行为。
2. `How to get to it (user POV)`：所有用户入口。
3. `Driving it with <harness>`：首行 `Preconditions:`，其余带标签的条目把「用户动作」对「逐字命令 + 可观察结果」。
4. `Gotchas`：会浪费或作废一次验证的坑。

## 功能

- [书库首页](./library-home.md)：8773 的三张入口卡、最新一期、拉起与 503。
- [找一段](./find-a-passage.md)：8765 的多轮会话、候选书、思考等级、五阶段经典页。
- [分层读](./layered-reads.md)：8771 的书架、书目、书页、全章正文工作台、按需知识地图与术语、划词伴读与旁批、操作日志，以及短章门控、容量预检与缓存/过期。
- [往期日课](./daily-lessons.md)：8772 的日课列表、单期、坏文件跳过。
- [管理](./manage-library.md)：8770 的八个页签、导入、拆章、维护、账表、删书、日志。
- [书库检索（CLI）](./search-query.md)：`book-corpus search` / `query` 的段检索、书级规划、混合检索、书卡与降级。
- [全库 embedding](./embed.md)：CLI 的 `book-corpus embed`，切块、断点续跑、单写者锁、状态与校验；没有浏览器面。

## 未映射

除 embed 与 search-query 外的 CLI 面还没有功能文件，以 `book-corpus list` 为准；操作员最常用的几条是
`book-corpus run <script>`、`verify`、`library`、`index`、`catalog`、`numbers`、
`stats`、`daily`、`status`、`ingest`、`audit_state`，以及演示页的运行记录 CLI
`python3 apps/demo/runlog.py {list,timeline,bundle,replay,prune,doctor}`。它们没有浏览器面，驱法和上面五个不同，需要另一层 harness。
[embed.md](./embed.md) 是 CLI 面写法的一份样板：一次性根、私有端口、stdout/stderr 与退出码当断言。
v1 的 `remap`、`migrate` 已不在 `book-corpus list` 里，别再按旧文档找它们。
