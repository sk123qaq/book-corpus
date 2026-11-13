---
name: depth-swap
description: 用 TLA+ 与现场脚本检查 `Store.save_depth` 换入 `depth/` 的崩溃窗口：两次 `os.replace` 之间断电，已生成的读本会不会从读者视角消失。改动 `src/bookcorpus/reading/store.py` 的换目录、恢复或读路径，或 `ReadingService` 的启动流程后跑它。
---

# depth-swap：换目录窗口里的可读性

`Store.save_depth` 用两次 rename 换掉整包 `depth/`。第一次把旧包改名成
`.depth-bak-<8hex>/`，第二次把新包 rename 进来。中间那一瞬 `depth/` 不存在，而
`depth_state`、`chapter_route`、`/api/chapter`、`/reader` 都只认 `depth/`。如果在窗口里硬崩溃，
旧包还在备份里，但读者拿到的是「尚未生成」，直到下一次 `save_depth` 触发
`_heal_swap_crash` 才恢复。

模型问两件事：**崩在窗口里，用户还能不能打开一个读本**（`CrashKeepsReadable`，答案是
不能，窗口本身锁在这一条里），以及**重启时 `heal_pending_swaps` 能不能把账还清**
（`BootedClean`，2026-11-13 修后通过）。`ReadingService.__init__` 在构造 `Store` 之后调
`self.store.heal_pending_swaps()`，把 `.depth-bak-*` 放回 `depth/`、清掉没换成的 stage。

## 什么时候用

- 改了 `src/bookcorpus/reading/store.py` 里的 `_swap_dir`、`_heal_swap_crash`、
  `_recover_dir`、`save_depth`、`depth_state`、`chapter_route`。
- 有人报告「读本生成过，崩了一次/断电之后不见了」，或者「重启后深读 404」。
- 想改读路径（例如在 `depth_state` 里顺手 heal）之前，先看这里会红哪一条。

不适用：任务队列、并发提交、内容门槛、HTTP 层。那些看 `verification/chapter_path/` 与
`tests/test_reading_*.py`。

## 跑法

两条检查，缺一条都不算跑过。读 TLC 的结果（通过或违反）时用 pstack 的 `/statechart`：它把
违反展开成一条最短路径的中文步骤，与下面 Record 的行号对得上。`/statechart` 的命令与路径
随发行版变化，不要抄进本文或脚本，按它的技能说明现场取。

1. 模型，通过组：

```text
command: tlc
spec:    verification/depth_swap/DepthSwap.tla
config:  verification/depth_swap/DepthSwap.cfg
options: ["-cleanup", "-noGenerateSpecTE", "-noGenerateSpecTEBin", "-workers", "1"]
```

预期：`passed`，退出 0，`36 states generated, 17 distinct states found`，深度 8，最大出度 3。

2. 模型，反例组：

```text
command: tlc
spec:    verification/depth_swap/DepthSwap.tla
config:  verification/depth_swap/DepthSwapRecover.cfg
options: ["-cleanup", "-noGenerateSpecTE", "-noGenerateSpecTEBin", "-workers", "1"]
```

预期：`invariant-violated`，退出 12，`14 states generated, 8 distinct states found`，深度 5，
`Invariant CrashKeepsReadable is violated.`，五步路径
`Init → Boot → SaveBegins → MoveTargetToBackup → Crash`。这条**红才是对**：它证明模型
能抓到窗口。若它变绿，先查是不是有人把 `crashed ⇒ target ≠ None` 放宽了。

3. 现场脚本：

```bash
cd <repo>
uv run --frozen python verification/depth_swap/check_swap.py
```

预期：退出 0，末行
`states: 8 | conformance: PASS | crash-window: CONFIRMED | heal: PASS | startup-heal: PASS`。
它只用 `tempfile.mkdtemp` 与进程内 monkeypatch，不建库、不连服务器、不碰真实工作区。

## Record

- 区域：`src/bookcorpus/reading/store.py` 的 `_swap_dir`（`:165-172`）、
  `_heal_swap_crash`（`:216-223`）、`_chapter_dirs`（`:225-234`）、
  `heal_pending_swaps`（`:236-275`）、`save_depth`（`:277-294`，`_swap_dir` 在 `:290`）、
  `depth_state`（`:311-345`）、`chapter_route`（`:359-378`）；调用点
  `src/bookcorpus/reading/service.py:508`。
- sync commit：`0f2c3a4` 之后的工作树（模型与启动自愈同一批改动，尚未提交时）。提交后把
  这里的 hash 换成新提交。
- 工具：TLC `2026.11.06.222357`，运行时 Eclipse Adoptium JDK `21.0.13`。jar 由
  `verification_exec` 的 `tlc` 命令按 `pstack.json:tla.tla2toolsJar` 解析，本文件不写机器路径。
  jar 的解析来源与 Java 大版本以 `/setup-pstack status` 或 `/poteto status` 报的 `tlc` 字段为准。
- 命令与选项：见上，两 cfg 共用 `-cleanup -noGenerateSpecTE -noGenerateSpecTEBin -workers 1`。
- 边界：单章、单进程、单次 `save_depth`；只模拟目录名与存在性，不模拟包内容；不建模
  `legacy` 分支；保存进行中的空窗（`target = none`、未崩）由 `NeverLoseReadable` 描述，
  不进任何 cfg。`Boot` 只在 `~booted` 时可用，`Crash` 把 `booted` 打回 FALSE，所以每次硬崩溃
  后必须先重启（走 `Boot`）才能再保存。
- 排除：并发保存与 `_submit_lock`（另测）、其它位置的 SIGKILL、文件系统 `os.replace` 语义、
  整包 `Store.save()` 的 `.bak-` 换目录（`_heal_swap_crash` 不认这个前缀，是另一个未建模的洞）。
- 现状（2026-11-13 修后）：`CrashKeepsReadable` 仍**违反**，这是窗口本身，不再当成待修
  bug；`BootedClean` 通过，启动自愈把账还清。产品层表现仍存在：崩溃后、重启前，
  `/api/chapter/<uid>/<n>` 的 `path=none` 与 `/reader/<uid>/<n>/depth` 的 404。若把
  `ReadingService.__init__` 里的 `heal_pending_swaps()` 删掉，现场脚本的 `startup-heal`
  段会红——那是回归，不是设计。
- sync commit 只在「在新提交上重跑过三条检查、并确认模型仍描述这份代码」之后才向前移，
  移的时候把日期一起改。

## 红了怎么读

先看反例最后一步是哪个动作：

- `MoveTargetToBackup` 之后 `target = none`、`crashed = FALSE`：窗口存在，但还没崩。这是
  `NeverLoseReadable` 的地盘，不一定是新问题。
- `Crash` 之后 `target = none`：崩溃把窗口冻住，读者在重启后仍然打不开。这是本检查要报的那条。
- `Boot` 之后 `pending` 仍为真（`BootedClean` 红）：自愈没跑、没找到备份或放回失败，
  先查 `store.py:236-275` 与 `service.py:508` 的调用点。
- `Boot` 之后 `target = none` 但 `pending = false`：只可能是源文本来就没有包，
  检查 `hadReady` 与 `Boot` 的 `pending' = (target' = None)` 写错没有。
- 现场脚本 `(b) 备份里有旧 chapter.html` 失败：换目录把旧包删了，是数据丢失，不是可读性问题。

## 维护

代码一动，先跑上面三条，再看 `git diff` 是否碰到 Record 里的行。改模型时保持三条不变式
原名、不弱化；`NeverLoseReadable` 留在模块里但不要塞进 cfg（它在第一次 rename 就红，会把
崩溃叙事盖掉）。

每次维护都要做两次真破坏，证明检查还能判错：

1. 数据丢失：把 `store.py:169` 的 `os.replace(target, backup)` 临时改成 `shutil.rmtree(target)`。
   预期 `[FAIL] (b) 备份里有旧 chapter.html（None）`、`CRASH-WINDOW: NOT CONFIRMED`、退出 1。
2. 语义漂移：在 `depth_state` 开头加
   `self._heal_swap_crash(self.pack_dir(uid, n), os.path.join(self.pack_dir(uid, n), "depth"))`。
   预期 `conformance: FAIL`、退出 1（模型里读者只看 `target`，读路径偷偷搬备份对不上）。
3. 启动自愈回归：在 `src/bookcorpus/reading/store.py` 的 `heal_pending_swaps` 开头插
   `return {"healed": [], "cleaned": [], "errors": []}`。预期现场脚本 `STARTUP-HEAL: FAIL`、
   末行 `startup-heal: FAIL`、退出 1；同时 `pytest -q tests/test_reading_depth.py -k "heal or crashed_swap"`
   会一起红，证明服务接线也有断言。三个破坏各做一次，做完都还原。

两次都 `git checkout -- src/bookcorpus/reading/store.py` 还原，再跑一遍确认回到全绿，并在
报告里写清破坏点、预期红、实际红、还原后的状态。

## 坑

- 第二条 cfg 红是设计，不是失败。把它当 CI 红去「修绿」就毁了这条检查。
- `verification_exec` 里 spec/config 必须是仓库相对路径；绝对路径与自造 jar 都会被拒。
- `-workers` 大于 1 时反例路径可能换形状，报告里的四步就对不上。
- 反例会留下 `DepthSwap_TTrace_*.tla/.bin`，删掉，别提交。
- 现场脚本靠 `.depth-` 前缀认 stage、靠 `.depth-bak-` 前缀认备份；改前缀会让注入点静默
  不命中（它有条 `注入点命中` 的断言，能兜住）。
- `chapter_route` 还会比 `source_sha256`，手工造 state 时 sha 不对会被判 stale，读出来仍是
  `none`，别误当成窗口问题。
