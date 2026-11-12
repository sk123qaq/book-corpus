# 管理

操作台把书库的八类维护动作收在一个页面：总览、导入、把电子书拆成正文、PDF 出正文、维护、
账表、删书、看日志。页面只负责排队，真正干活的是 `src/bookcorpus/scripts/` 下的脚本。

## Sub-features

- `ops-overview` 总览：书目数、待分诊、收件箱、体检提醒、一键收尾。
- `ops-import` 新书导入：扫描、计划、执行、上传到 `books/_收件箱/`。
- `ops-split` 电子书出正文：拆章计划、逐本状态、批量执行。
- `ops-pdf` PDF 出正文：PDF 书目列表、分诊、队列、执行；`pdf-list` 是这一页的隐藏子页签。
- `ops-maintain` 维护：六个勾选项（主题/奖项/审计/校验/书目/库）加一键收尾（八步）。
- `ops-ledger` 账表：人账 `tables/ledger.db` 的浏览与改行。
- `ops-retire` 删书：dry-run 与 apply 两步。
- `ops-logs` 日志：待确认的批次、取消与重跑。
- `ops-jobs` 任务队列：每个任务一份 log、命令、PID、结果。

## How to get to it (user POV)

- 浏览器打开 `http://127.0.0.1:18770/`，标题是「书库操作台」。
- 点左侧竖排导航的八个页签切换（不是顶部横排）。
- 在「新书导入」选文件上传，或「电子书出正文」勾几本再执行。
- 在「日志」看待确认的批次，取消或重跑。

## Driving it with tests/acceptance/ops/check_ops.py + shot_ops.py

Preconditions:

- 工作区按 [README](./README.md) 建好；`check_ops.py` 自己造临时根，不需要样书。
- 操作台起在本轮端口：`python3 apps/ops/server.py --root $ROOT --port 18770`，启动行是 `统一操作台：http://127.0.0.1:18770  root=<ROOT>`；`--port 0` 会自己挑一个空闲端口并打印实际值。
- CDP 脚本要一个 Chrome 系浏览器：默认找 `google-chrome-stable`，没有就设 `CHROME_BIN`（本机可用 `CHROME_BIN=/usr/bin/chromium`，也有 `/usr/bin/microsoft-edge-stable`）。找不到时报的是 `FileNotFoundError: [Errno 2] No such file or directory: 'google-chrome-stable'`，不是页面问题。
- 只对临时根操作：上传写 `books/_收件箱/`，任务写 `jobs/<id>/`，状态写 `ops.db`。绝不指向 `book-corpus-data`。

- **离线全量。** `python3 tests/acceptance/ops/check_ops.py` 逐项打「过/败」，末两行是 `168 项检查，失败 0 项` 与 `ALL PASS`（项数随功能增加，别把 168 当常量）。
- **页面骨架。** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18770/` 输出 `200`；标题 `书库操作台`；HTML 只有约 900 字节，是 JS 壳，页签文字不在里面。
- **接口在。** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18770/api/status` 输出 `200`；`/api/jobs`、`/api/inbox`、`/api/pending`、`/api/ledger`、`/api/pdf/books` 都在；`/api/maintain` 只收 POST，GET 是 `404`。
- **状态的形状。** `/api/status` 顶层有 `state`（空库里是 `degraded`）、`stale`（`true`）、`status.index.extra_books`、`status.audit.stale`、`status.health`（临时根里是 `{}`）。`extra_books` 是磁盘有、索引没有的书，别把它读成书目总数。
- **八个页签都点得开。** `PYTHONPATH=$PWD/tests/acceptance/ops CHROME_BIN=/usr/bin/chromium python3 tests/manual/ops/shot_ops.py` 自己起临时根服务和 headless Chrome，把总览、新书导入、电子书出正文、PDF 出正文、维护、删书、日志逐页点一遍，出 1440x900 与 390x844 两张图。`TABS` 的来源是 `apps/ops/web/app.js` 的八个条目（含 `ledger` 账表），脚本本身只走七个页签，**账表**没有被它覆盖。
- **已知红（两个断点都红）。** 同一命令的末行是 `SHOT FAIL`，exit `1`；1440x900 与 390x844 都报 `页签或页面检查没过：pdf-list-progress、maintain-product、log-cancel-hidden`，各 `CONSOLE ERROR 2`（两条 404），`overflow=false`。截图照样出（`ops/总览-1440x900.png`、`-390x844.png`）。这三项是脚本断言与产品口径不一致（脚本按旧口径数进度档、维护步数），不是页面塌了：同一次运行里其余交互（导入计划、勾选、改行、删书弹窗、PDF 清单筛选、日志待确认）都通过。
- **上传。** 在「新书导入」选一个文件上传。`curl -s http://127.0.0.1:18770/api/inbox` 里出现该文件，磁盘上落在 `$ROOT/books/_收件箱/`。
- **删书两步。** 先 dry-run 再 apply。`curl -s -X POST http://127.0.0.1:18770/api/delete/dry-run ...` 只出计划，`apply` 才动树；本次未跑（会删临时根里的书）。

## Gotchas

- 页面是 JS 壳，`curl` 拿到的 HTML 只有约 900 字节，搜不到页签文字；页签断言要靠 CDP，不是 `grep`。
- `shot_ops.py` 的裸命令会 `ModuleNotFoundError: No module named 'check_ops'`，必须带 `PYTHONPATH=$PWD/tests/acceptance/ops`。
- 没有 `google-chrome-stable` 时脚本死在 launch，报 `FileNotFoundError`；设 `CHROME_BIN` 指到 `chromium` 或 `microsoft-edge-stable` 即可，别去装 Chrome。
- 两个断点现在都红，末行 `SHOT FAIL`、exit 1。别把它当「移动端不通过」写进报告，也别为了让脚本变绿去改页面：红的是脚本口径。
- `shot_ops.py` 不覆盖账表页签（`apps/ops/web/app.js` 有八个页签，脚本走七个）；账表要验就自己用 CDP 点，或直接验 `/api/ledger`。
- `check_ops.py` 与 `shot_ops.py` 用的都是临时根 + stub 脚本；真实队列会调 `src/bookcorpus/scripts/` 里的东西，用临时根跑才安全。
- 上传是唯一往库里写文件的入口，目标固定是 `_收件箱/`；其余动作只改账本与 `jobs/`。
- 同一份工作区同时只能有一个写者（`.writer.lock`）；两个验证进程并行跑会互相等锁，看起来像卡住。
- `--port 0` 时实际端口只在启动行里，不要假设 8770。
