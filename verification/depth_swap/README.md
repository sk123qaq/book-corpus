# depth/ 换目录的形式化验证

`Store.save_depth` 用两次 `os.replace` 换掉整包 `depth/`。第二次之前硬崩溃时，`depth/` 不在，
旧包只剩 `.depth-bak-*`；`depth_state` 报 `missing`，`chapter_route` 报 `('none', '尚未生成')`。
重启不自愈，只有下一次 `save_depth` 的 `_heal_swap_crash` 会把备份放回去。
本目录把这段窗口写成 TLA+ 模型，给一个不碰产品代码的现场脚本，并锁住 2026-11-13 的修法：
启动时 `heal_pending_swaps` 把备份放回去。

## 代码里的事实

| 位置 | 行为 |
|---|---|
| `src/bookcorpus/reading/store.py:165-172` | `_swap_dir`：`rmtree` 旧备份，`if exists(target): os.replace(target, backup)`（169），`os.replace(stage, target)`（170），成功后再删备份（171-172） |
| `src/bookcorpus/reading/store.py:277-294` | `save_depth`：`_heal_swap_crash`（284）、建 stage、写文件，最后调 `_swap_dir`（290）；`except Exception` 走 `_recover_dir`（291-293），`SystemExit` 是 `BaseException`，不在这条恢复路径里 |
| `src/bookcorpus/reading/store.py:216-223` | `_heal_swap_crash`：target 不在时把 `sorted(...)` 里第一个 `.depth-bak-*` 换回 target |
| `src/bookcorpus/reading/store.py:284` | `_heal_swap_crash` 的既有调用点，在 `save_depth` 开头 |
| `src/bookcorpus/reading/store.py:225-234` | `_chapter_dirs`：遍历 `阅读/<uid>/ch/<NN>`，扫不到就跳过 |
| `src/bookcorpus/reading/store.py:236-275` | `heal_pending_swaps`：逐章放回 `.depth-bak-*`，清掉 `.depth-<rand>`；target 与 backup 都不在时保留 stage（盘上唯一一份包）；返回 `{"healed", "cleaned", "errors"}` |
| `src/bookcorpus/reading/service.py:505-508` | `ReadingService.__init__` 在 `Store(...)` 之后调 `self.store.heal_pending_swaps()`，结果存 `recovered_swaps` |
| `src/bookcorpus/reading/store.py:311-345` | `depth_state` 只读 `depth/state.json`（`depth_json`，303-309）；`ready` 还要求 `depth/chapter.html` 在盘上（341-343） |
| `src/bookcorpus/reading/store.py:359-378` | `chapter_route`：`ready → ("depth", None)`；没有 manifest 且状态不是 ready/partial → `("none", "尚未生成")` |

`pack_dir` 是 `<layout.work>/阅读/<uid>/ch/<NN>`（`store.py:30-32, 42-44`），不是 `<layout.work>/<uid>/...`。
行号对应 2026-11-13 加了 `heal_pending_swaps` 之后的工作树。

## 模型

`DepthSwap.tla` 的变量：

| 变量 | 取值 | 含义 |
|---|---|---|
| `target` | `None`, `OLD`, `NewPkg` | `depth/`，读者真正打开的包 |
| `backup` | `None`, `OLD` | `.depth-bak-<8hex>/`，只有 `_heal_swap_crash` 与 `Boot` 会读 |
| `stage` | `None`, `NewPkg` | `.depth-<rand>/`，正在写的新包 |
| `crashed` | `BOOLEAN` | 两次 replace 之间断电 |
| `hadReady` | `BOOLEAN` | 保存开始前有可服务的包 |
| `booted` | `BOOLEAN` | 进程过完启动自愈、开始对外服务；`Crash` 把它打回 `FALSE` |
| `pending` | `BOOLEAN` | 崩溃那一刻「该有包却没有」，`Boot` 放回备份或一次保存换完才清 |

动作：`Boot`、`SaveBegins`、`MoveTargetToBackup`、`MoveStageToTarget`、`DropBackup`、`Crash`。
`Boot` 是 `heal_pending_swaps`：`~booted` 时把备份放回 `target`、清 stage、清 `crashed`，
`pending' = (target' = None)`；正常启动也走这一步，只是没有备份可放。
`Crash` 之后 `booted = FALSE`、`crashed = TRUE`，必须先 `Boot` 才能再保存或再服务。

`_swap_dir` 的先后次序写进了前置条件，否则模型会允许代码里不存在的交错：

- `MoveTargetToBackup` 要求 `stage = NewPkg`：`_swap_dir` 只在 `_write_depth_files`
  成功后才被调用（`store.py:289-290`），第一次 replace 时新包一定已经写好。
- `MoveStageToTarget` 要求 `target = None`：`store.py:168-170` 先
  `replace(target, backup)` 再 `replace(stage, target)`，第二次 replace 时 target 已经让位。
- `DropBackup` 要求 `target # None`：备份是旧包的唯一副本，`depth/` 不在时删它就是丢数据。

`NEW` 是 TLA+ 的保留字（证明语言里引入新变量），模型里写作 `NewPkg`，值仍是 `"new"`。

## 不变式与结论

| 不变式 | 含义 | cfg | TLC |
|---|---|---|---|
| `NoDataLoss` | `target ≠ None ∨ backup = OLD ∨ stage = NewPkg`：旧包或新包至少一份在盘上 | `DepthSwap.cfg` | 通过 |
| `BootedClean` | `booted ⇒ ¬pending`：开始对外服务时不能还欠着崩溃留下的账 | `DepthSwap.cfg` | 通过 |
| `CrashKeepsReadable` | `crashed ⇒ target ≠ None`：崩在换目录窗口里，读者仍要能打开一个包 | `DepthSwapRecover.cfg` | **违反** |

模型里还有一条更严的 `NeverLoseReadable`（`hadReady ⇒ target ≠ None`）：它在
`MoveTargetToBackup` 一步就违反，不需要 `Crash`，所以不进任何 cfg——它证明的是窗口存在，
不是「崩溃把窗口冻住」。`CrashKeepsReadable` 才是这次要报的那一条。

`BootedClean` 是 2026-11-13 修法的性质：`Crash` 在 `pending` 上记账，`Boot` 把备份放回去
才算清。`ServingReadable`（`booted ⇒ target ≠ None ∨ ¬hadReady`）不成立也不该成立——
保存进行中的空窗本来就在，启动自愈救不了它，写进 cfg 只会得到一个必然的反例。

`DepthSwap.cfg`：`36 states generated, 17 distinct states found`，搜索深度 8，无错误，
`max outdegree 3`（`Boot` 的分支：有备份放回、没备份、备份放回后清账）。

`DepthSwapRecover.cfg` 给出 5 个状态、4 步的反例（退出码 12）：

1. `Init`：`target = OLD`，`hadReady = FALSE`。
2. `Boot`：`booted = TRUE`，`pending = FALSE`。
3. `SaveBegins`：`stage = NewPkg`，`hadReady = TRUE`（保存前用户能读）。
4. `MoveTargetToBackup`：`target = None`，`backup = OLD`，`stage = NewPkg`：旧包离开 `depth/`。
5. `Crash`：`crashed = TRUE`，`booted = FALSE`，`pending = TRUE`，**违反**。

此时旧包还在备份里，数据没丢；丢的是可读路径：`depth_state` 只认 `depth/`，备份对它不可见。
`Boot` 会把备份放回去，但在进程重启之前，`/api/chapter/<uid>/<n>` 的 `path=none` 与
`/reader/<uid>/<n>/depth` 的 404 一直成立。

## 现场脚本

```bash
cd /home/colaqaq/Work/book-corpus
uv run --frozen python verification/depth_swap/check_swap.py
```

`--root DIR` 可指定工作根；默认 `tempfile.mkdtemp(prefix="bc-swap-")`，成功时删掉，失败时保留。
脚本用 `SimpleNamespace(work=root)` 造 layout，只调 `Store`，不建 Library、不连服务器。

它做四件事：

1. **状态对照**：盘上 `target/backup/stage` 的 8 种组合逐个造目录，断言
   `depth_state == "ready"` 当且仅当 `target` 存在，并打印 `state | target | backup | stage | depth_state | route | OK/FAIL`。
2. **崩溃注入**：干净状态（`target = OLD`、ready）上 monkeypatch
   `bookcorpus.reading.store.os.replace`，只在源路径以 `.depth-` 开头且目标是 `depth/` 时
   `raise SystemExit`；接住后断言 (a) 没有 `depth/`、(b) 备份里有旧 `chapter.html`、
   (c) `depth_state` 是 `missing`、(d) `chapter_route` 是 `('none', '尚未生成')`。
3. **下一次保存自愈**：随后正常保存一次，断言 `depth/` 存在、`depth_state` 为 `ready`、路由为 `depth`。
4. **启动自愈**：再崩一次，调 `store.heal_pending_swaps()`，断言只修这一章
   （`{"healed": ["B0001/01"], "cleaned": ["B0001/01"], "errors": []}`）、`depth/` 是旧包、
   `depth_state` 回 `ready`、路由回 `depth`、没有备份与 stage 残留，再调一次返回空。

2026-11-13 实测末行（退出码 0）：

```text
states: 8 | conformance: PASS | crash-window: CONFIRMED | heal: PASS | startup-heal: PASS
```

## TLC

用 `verification_exec` 跑，和本仓库其余形式化检查同一入口（只读目录、受限 jar、自动收 metadir）：

```text
command: tlc
spec:    verification/depth_swap/DepthSwap.tla
config:  verification/depth_swap/DepthSwap.cfg
options: ["-cleanup", "-noGenerateSpecTE", "-noGenerateSpecTEBin", "-workers", "1"]
```

再把 `config` 换成 `verification/depth_swap/DepthSwapRecover.cfg` 跑第二条。

第一条应打印 `Model checking completed. No error has been found.` 并以 0 退出；第二条故意非零
（退出码 12），打印 `Invariant CrashKeepsReadable is violated.` 与上面那条五步路径。
`-workers 1` 让反例顺序稳定。直接 `java -cp $TLA2TOOLS tlc2.TLC` 只用于本机临时调试，
报告里的读数一律以 `verification_exec` 的输出为准（它会带上 jar 的解析来源与隔离方式）。
违反组会在当前目录留下 `DepthSwap_TTrace_*.tla/.bin`，看过就删，不是交付物。

## 范围

- 单章、单进程、单次 `save_depth`。并发保存、进程在其它位置被杀、文件系统与 `os.replace`
  的原子性语义都不在模型里。
- 模型只模拟目录名与存在性，不模拟包内容；备份里的 `chapter.html` 由现场脚本 (b) 取证。
- 不覆盖 `chapter_route` 的 `legacy` 分支：本场景没有 `manifest.json`。
- 不覆盖整包 `Store.save()` 的 `.bak-` 前缀：`heal_pending_swaps` 只认 `.depth-bak-`，
  整包换目录没有对应的启动自愈（`store.py` 的另一处 `_swap_dir` 调用）。
- 模型同步对象是 `store.py` 当前行号与 `heal_pending_swaps` 的行为；产品代码基线
  `0f2c3a4` 之后的修复提交。
- `heal_pending_swaps` 在启动时全量扫一遍 `阅读/*/ch/*`。章节目录数量大时这是 O(章数)
  次 `listdir`；目前 1,954 本书的规模下可接受，模型不覆盖耗时。

## 与任务规格的差异

1. 路径：`pack_dir` 实际是 `<root>/阅读/B0001/ch/01`（`store.py:30-32, 42-44`），
   不是任务文字里的 `<root>/B0001/ch/01`。
2. `NEW` 与 TLA+ 保留字冲突，模型常量改名 `NewPkg`，值仍为 `"new"`。
3. 给 `MoveTargetToBackup` 加 `stage = NewPkg`、给 `MoveStageToTarget` 加 `target = None`、
   给 `DropBackup` 加 `target # None`。不加前两条，模型会允许「stage 没写好就换目录」与
   「target 没让位就覆盖」，`NoDataLoss` 会被代码里不存在的交错打破；不加第三条，
   备份会成为可删的唯一副本（TLC 给出过 `DropBackup` 后 `target=none, backup=none` 的反例）。
4. `NeverLoseReadable` 留在模型里但不进任何 cfg：它在 `MoveTargetToBackup` 一步就违反，
   早于 `Crash`，证明的是窗口存在而不是崩溃把它冻住。要报的是 `CrashKeepsReadable`。
5. `NextSaveHeals` 与 `afterSave` 已删除：崩溃杀掉进程，能再保存的前提就是重启过，
   `Boot` 已经跑在前面；下一次保存开头那次 `_heal_swap_crash` 由现场脚本第 3 段覆盖。
