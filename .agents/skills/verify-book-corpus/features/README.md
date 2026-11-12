# book-corpus 验证地图

这份地图是 book-corpus 用户可见行为的验证源：先读本索引，再按对应功能文件开跑。
它只写本仓库现在真实能跑的命令，不写「应该有」的工具。

## 基线前置

所有命令都在 git 仓库根执行（先 `cd "$(git rev-parse --show-toplevel)"`），工作区根用一次性的。

```
ROOT=/tmp/bc-verify-$RUN_ID
uv run --frozen book-corpus init "$ROOT" --no-pdf-venv
uv run --frozen book-corpus sample --root "$ROOT"
uv run --frozen book-corpus doctor --root "$ROOT"
```

- `init` 约 0.2 秒，打印「工作区建好」「书目库：空库已建好」「检索索引：空库已建好」；随后两行 venv 未建警告是 `--no-pdf-venv` 的预期结果。
- `sample` 装内置样书：阿Q正传 `B0001`，9 章，检索索引 123 段，`book_concept` 3 条。字数两处口径不同：`sample` 报 22881 字（含标题等），分层读 `/api/shelf` 报 21362 正文字符。
- `doctor` 只读，逐行 `ok`/`warn`。允许的 `warn` 只有两类：**环境**（PDF 环境、文档环境）与**云端 key 缺失**（配置·MinerU / PaddleOCR / MiMo）。落在工作区、清单、账、库上的 `warn` 才算问题，先解释再开跑。
- 端口用本轮私有的一套：演示 18765、分层读 18771、日课 18772、首页 18773（另 18774 / 18775 验 503 与拉起）、操作台 18770。
- 8765 / 8771 / 8772 / 8773 是操作员的常驻实例，跑在操作员的真实库 `~/书库` 上（`apps/reading/server.py --root` 默认值就是它；`~/文档/book-corpus-data` 是旧路径，本机不存在）。永不驱、永不杀。
- 需要「有内容」才成立的验证，手写 `$ROOT/日课/<date>.json`（契约见 [daily-lessons.md](./daily-lessons.md)）。`book-corpus daily --dry-run` 只把提示词打到 stdout，不落盘，不能当 seed。

## 驱动约定

- 每个服务由本轮自己起、自己按 PID 杀，不按进程名杀。
- 页面用 `curl` 取 HTML 断言稳定标记，接口用 `curl` 取 JSON 断言字段；只有点击、键盘这类交互才用 headless Chrome + CDP 脚本。
- CDP 脚本默认找 `google-chrome-stable`；本机没有，用 `CHROME_BIN=/usr/bin/chromium`（或 `/usr/bin/microsoft-edge-stable`）。缺了它脚本死在 launch，不是页面问题。
- 命令逐字复制，不改引号、不改参数。
- 证据写 `artifacts/verify/$RUN_ID/<feature>/`；清理只删实例与临时根，不删证据。
- 五个服务（首页 8773 / 分层读 8771 / 日课 8772 / 操作台 8770 / 演示页 8765）都只认 `127.0.0.1` / `localhost` 的 Host 头，别的 Host 回 403。演示页直到 2026-11-12 晚才补上这道检查（`apps/demo/server.py` 的 `ALLOWED_HOST`），旧脚本里「演示页不校验 Host」的说法已作废。

## 证明与跳过

- 上次全绿：`2026-11-12` 一轮 `RUN_ID=20261112-mt`，五个功能全驱过，证据在 `artifacts/verify/20261112-mt/`，结论见该目录 `README.md`。下一轮跑完请就地更新这一行。

- 一次证明要同时留下动作与结果状态，不能只有最终画面。
- 只验了接口没走页面时记 `ui-unverified`，不算 UI 通过。
- 缺前置就报失败和原因，不把整条路径降级成「别处跑通了」。
- 已知红就是红，见下节。

## 本次已知红（2026-11-12）

- `tests/manual/ops/shot_ops.py` 现在两个断点都绿：末行 `SHOT PASS`、`CONSOLE ERROR 0`、`overflow=false`。2026-11-12 晚修好了三处脚本与产品口径不一致（进度勾选 3→5、`[data-step]` 9→8 且接受「N 步全过」、「停止」按钮改成不在 DOM 里留死节点），并补上 `ledger` 页签，八个页签全通。旧记录里的 `SHOT FAIL` 不要再当预期值。
- 该脚本必须先设 `CHROME_BIN=/usr/bin/chromium`（本机没有 `google-chrome-stable`），并带 `PYTHONPATH=$PWD/tests/acceptance/ops`；裸命令分别死在 launch 与 `ModuleNotFoundError: No module named 'check_ops'`。
- `verification/chapter_path/check_paths.py` 必须 `uv run --frozen python ... --root "$ROOT"`，裸 `python3` 会 `ModuleNotFoundError: No module named 'bookcorpus'`。
- 演示页的会话库在仓库内 `apps/demo/缓存/sessions/`，不随 `BOOKS_ROOT` 隔离；新实例照样列出操作员的会话（本轮 11 条）。见 [find-a-passage.md](./find-a-passage.md) 的 Gotchas。
- 分层读的生成必然打模型，`--no-annotate` 只关标注；离线只能验到任务被受理（202）。**202 之后的状态不再是 `failed`**：P1–P4 之后实测 `state done`、`progress 100`、`elapsed_ms 201521`、`layers_ready [model, skim]`、`path depth`、读本页 `/reader/<uid>/<n>/depth` 200（2026-11-12 19:11 一轮，证据 `artifacts/verify/20261112-mt/layered-reads/`）。没有模型 key 时才会失败，那时 `state failed` 带 `error_kind: transport_closed` 这类分类，仍不是裸英文串。

## 功能入口契约

每个功能文件以 H1 标题 + 一段用户可见行为说明开头，之后严格四个 H2，顺序固定：

1. `Sub-features`：短 id + 一行行为。
2. `How to get to it (user POV)`：所有用户入口。
3. `Driving it with <harness>`：首行 `Preconditions:`，其余带标签的条目把「用户动作」对「逐字命令 + 可观察结果」。
4. `Gotchas`：会浪费或作废一次验证的坑。

## 功能

- [书库首页](./library-home.md)：8773 的三张入口卡、最新一期、拉起与 503。
- [找一段](./find-a-passage.md)：8765 的多轮会话、候选书、思考等级、五阶段经典页。
- [分层读](./layered-reads.md)：8771 的书架、书目、书级读法、三档生成、五层深度与读本页。
- [往期日课](./daily-lessons.md)：8772 的日课列表、单期、坏文件跳过。
- [管理](./manage-library.md)：8770 的八个页签、导入、拆章、维护、账表、删书、日志。

## 未映射

CLI 面还没有功能文件，以 `book-corpus list` 为准；操作员最常用的几条是
`book-corpus run <script>`、`verify`、`library`、`index`、`search`、`query`、`catalog`、`numbers`、
`stats`、`daily`、`status`、`ingest`、`audit_state`。它们没有浏览器面，驱法和上面五个不同，需要另一层 harness。
v1 的 `remap`、`migrate` 已不在 `book-corpus list` 里，别再按旧文档找它们。
