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

## 驱动约定

- 每个服务由本轮自己起、自己按 PID 杀，不按进程名杀。
- 页面用 `curl` 取 HTML 断言稳定标记，接口用 `curl` 取 JSON 断言字段；只有点击、键盘这类交互才用 headless Chrome + CDP 脚本。
- CDP 脚本默认找 `google-chrome-stable`；本机有（2026-11-14 实测 155.0.8059.79），不设 `CHROME_BIN` 也能跑。`/usr/bin/chromium` 与 `/usr/bin/microsoft-edge-stable` 都不存在；要显式指定时用 `/usr/bin/google-chrome-stable` 或 `/usr/bin/microsoft-edge`。缺了它脚本死在 launch，不是页面问题。
- 命令逐字复制，不改引号、不改参数。
- 证据写 `artifacts/verify/$RUN_ID/<feature>/`；清理只删实例与临时根，不删证据。
- 五个服务（首页 8773 / 分层读 8771 / 日课 8772 / 操作台 8770 / 演示页 8765）都只认 `127.0.0.1` / `localhost` 的 Host 头，别的 Host 回 403。演示页直到 2026-11-12 晚才补上这道检查（`apps/demo/server.py` 的 `ALLOWED_HOST`），旧脚本里「演示页不校验 Host」的说法已作废。

## 证明与跳过

- 上次全绿：`2026-11-14` 一轮 `RUN_ID=20261114-mt2`，五个页面全过（首页/日课/分层读/找一段走 curl + HTML，操作台走 `shot_ops.py` CDP，`check_ops.py` 168 项全过），含一次真生成 `r-260618d0b1`（速览比例 0.2464 过门槛，`elapsed_ms 193976`，章 02 `path depth`、读本页 200）与一次读法切换过期/恢复实测。首页键盘仍记 `ui-unverified`；`check_session_ui.py` 因无 3 轮样本未跑，多轮 UI 不在这次「全绿」里。证据在 `artifacts/verify/20261114-mt2/`。下一轮跑完请就地更新这一行。
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
- `/classic` 会建会话，但它的 `/api/answer` 不带 `session_id`，服务端落到 `answer_once`，所以该会话的 turn 永远停在 `answer_state=pending`、`answer=null`（2026-11-14 实测 `82abddf275fc`，证据 `artifacts/verify/20261114-mt2/find-a-passage/session-pending-82abddf275fc.json`）。这是产品缺口，不是验证脚本问题；别把它当页面坏或会话恢复失败。
- `tests/manual/demo/shot.py` 只等 25 秒（`tests/manual/demo/shot.py:233`）。单本回答今天实测 225 秒，脚本会先报 `answer ready=False cites=0`，服务端仍在跑；慢回答要另轮询 `/api/session`，不能把 `ready=False` 当失败。
- 分层读的生成必然打模型，`--no-annotate` 只关标注；离线只能验到任务被受理（202）。**202 之后的状态不再是 `failed`**：本轮真生成 `r-260618d0b1`（章 02，只勾 `skim`）实测 `state done`、`progress 100`、`elapsed_ms 193976`、`layers_ready [model, skim]`、`state.json status=ready`、`path depth`、读本页 `/reader/<uid>/<n>/depth` 200（2026-11-14，证据 `artifacts/verify/20261114-mt2/layered-reads/`）。盘上 `state.json` 的 `layer_mode` 记下每层生成时的读法；把书级读法改成别的值后，同一章 `path=none`、理由「读法已改，深度页过期」、深度页 404，改回原值即恢复。没有模型 key 时才会失败，那时 `state failed` 带 `error_kind: transport_closed` 这类分类，仍不是裸英文串。

## 功能入口契约

每个功能文件以 H1 标题 + 一段用户可见行为说明开头，之后严格四个 H2，顺序固定：

1. `Sub-features`：短 id + 一行行为。
2. `How to get to it (user POV)`：所有用户入口。
3. `Driving it with <harness>`：首行 `Preconditions:`，其余带标签的条目把「用户动作」对「逐字命令 + 可观察结果」。
4. `Gotchas`：会浪费或作废一次验证的坑。

## 功能

- [书库首页](./library-home.md)：8773 的三张入口卡、最新一期、拉起与 503。
- [找一段](./find-a-passage.md)：8765 的多轮会话、候选书、思考等级、五阶段经典页。
- [分层读](./layered-reads.md)：8771 的书架、书目、书级默认读法（只写账）、读法切换导致的深度层过期、三档生成、五层深度与读本页。
- [往期日课](./daily-lessons.md)：8772 的日课列表、单期、坏文件跳过。
- [管理](./manage-library.md)：8770 的八个页签、导入、拆章、维护、账表、删书、日志。

## 未映射

CLI 面还没有功能文件，以 `book-corpus list` 为准；操作员最常用的几条是
`book-corpus run <script>`、`verify`、`library`、`index`、`search`、`query`、`catalog`、`numbers`、
`stats`、`daily`、`status`、`ingest`、`audit_state`，以及演示页的运行记录 CLI
`python3 apps/demo/runlog.py {list,timeline,bundle,replay,prune,doctor}`。它们没有浏览器面，驱法和上面五个不同，需要另一层 harness。
v1 的 `remap`、`migrate` 已不在 `book-corpus list` 里，别再按旧文档找它们。
