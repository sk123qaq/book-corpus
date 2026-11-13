# depth/ 换目录的形式化验证

`Store.save_depth` 用两次 `os.replace` 换掉整包 `depth/`。第二次之前硬崩溃时，`depth/` 不在，
旧包只剩 `.depth-bak-*`；`depth_state` 报 `missing`，`chapter_route` 报 `('none', '尚未生成')`，
重启不自愈，只有下一次 `save_depth` 的 `_heal_swap_crash` 会把备份放回去。
本目录把这段窗口写成 TLA+ 模型，并给一个不碰产品代码的现场脚本。

## 代码里的事实

| 位置 | 行为 |
|---|---|
| `src/bookcorpus/reading/store.py:165-172` | `_swap_dir`：`rmtree` 旧备份，`if exists(target): os.replace(target, backup)`（169），`os.replace(stage, target)`（170），成功后再删备份（171-172） |
| `src/bookcorpus/reading/store.py:225-242` | `save_depth`：建 stage、写文件，最后调 `_swap_dir`（238）；`except Exception` 走 `_recover_dir`（239-241），`SystemExit` 是 `BaseException`，不在这条恢复路径里 |
| `src/bookcorpus/reading/store.py:216-223` | `_heal_swap_crash`：target 不在时把 `sorted(...)` 里第一个 `.depth-bak-*` 换回 target |
| `src/bookcorpus/reading/store.py:232` | `_heal_swap_crash` 的唯一调用点，在 `save_depth` 开头 |
| `src/bookcorpus/reading/store.py:259-292` | `depth_state` 只读 `depth/state.json`（`depth_json`，251-257）；`ready` 还要求 `depth/chapter.html` 在盘上（289-290） |
| `src/bookcorpus/reading/store.py:307-326` | `chapter_route`：`ready → ("depth", None)`；没有 manifest 且状态不是 ready/partial → `("none", "尚未生成")` |

`pack_dir` 是 `<layout.work>/阅读/<uid>/ch/<NN>`（`store.py:30-32, 42-44`），不是 `<layout.work>/<uid>/...`。

## 模型

`DepthSwap.tla` 的变量：

| 变量 | 取值 | 含义 |
|---|---|---|
| `target` | `None`, `OLD`, `NewPkg` | `depth/`，读者真正打开的包 |
| `backup` | `None`, `OLD` | `.depth-bak-<8hex>/`，只有 `_heal_swap_crash` 会读 |
| `stage` | `None`, `NewPkg` | `.depth-<rand>/`，正在写的新包 |
| `crashed` | `BOOLEAN` | 两次 replace 之间断电 |
| `hadReady` | `BOOLEAN` | 保存开始前有可服务的包 |
| `afterSave` | `BOOLEAN` | 自愈已经跑过一次 |

动作：`SaveBegins`、`MoveTargetToBackup`、`MoveStageToTarget`、`DropBackup`、`Crash`、
`NextSaveHeals`。`Crash` 之后只有 `NextSaveHeals` 能走，其余动作都 stutter；
`NextSaveHeals` 对应 `save_depth` 开头的 `_heal_swap_crash`，之后备份清空。

`_swap_dir` 的先后次序写进了前置条件，否则模型会允许代码里不存在的交错：

- `MoveTargetToBackup` 要求 `stage = NewPkg`：`_swap_dir` 只在 `_write_depth_files`
  成功后才被调用（`store.py:237-238`），第一次 replace 时新包一定已经写好。
- `MoveStageToTarget` 要求 `target = None`：`store.py:168-170` 先
  `replace(target, backup)` 再 `replace(stage, target)`，第二次 replace 时 target 已经让位。

`NEW` 是 TLA+ 的保留字（证明语言里引入新变量），模型里写作 `NewPkg`，值仍是 `"new"`。

## 不变式与结论

| 不变式 | 含义 | cfg | TLC |
|---|---|---|---|
| `NoDataLoss` | `target ≠ None ∨ backup = OLD ∨ stage = NewPkg`：旧包或新包至少一份在盘上 | `DepthSwap.cfg` | 通过 |
| `RecoverAfterSave` | `afterSave ⇒ target ≠ None`：下一次保存自愈后 `depth/` 一定在 | `DepthSwap.cfg` | 通过 |
| `CrashKeepsReadable` | `crashed ⇒ target ≠ None`：崩在换目录窗口里，读者仍要能打开一个包 | `DepthSwapRecover.cfg` | **违反** |

模型里还有一条更严的 `NeverLoseReadable`（`hadReady ⇒ target ≠ None`）：它在
`MoveTargetToBackup` 一步就违反，不需要 `Crash`，所以不进 `DepthSwapRecover.cfg`——
它证明的是窗口存在，不是「崩溃把窗口冻住」。`CrashKeepsReadable` 才是这次要报的那一条。

`DepthSwap.cfg`：`56 states generated, 22 distinct states found`，搜索深度 7，无错误。

`DepthSwapRecover.cfg` 给出 4 个状态、3 步的反例（退出码 12）：

1. `Init`：`target = OLD`，`hadReady = FALSE`。
2. `SaveBegins`：`stage = NewPkg`，`hadReady = TRUE`（保存前用户能读）。
3. `MoveTargetToBackup`：`target = None`，`backup = OLD`，`stage = NewPkg`：旧包离开 `depth/`。
4. `Crash`：`crashed = TRUE`，其它变量不变，**违反**。

此时旧包还在备份里，数据没丢；丢的是可读路径：`depth_state` 只认 `depth/`，备份对它不可见。
`Crash` 让这个状态停住，直到下一次 `save_depth` 才由 `_heal_swap_crash` 解开。在服务层，
这个状态对应 `/api/chapter/<uid>/<n>` 的 `path=none` 与 `/reader/<uid>/<n>/depth` 的 404；
本目录只对 Store 层做可重复断言，不启 HTTP 服务。

## 现场脚本

```bash
cd /home/colaqaq/Work/book-corpus
uv run --frozen python verification/depth_swap/check_swap.py
```

`--root DIR` 可指定工作根；默认 `tempfile.mkdtemp(prefix="bc-swap-")`，成功时删掉，失败时保留。
脚本用 `SimpleNamespace(work=root)` 造 layout，只调 `Store`，不建 Library、不连服务器。

它做三件事：

1. **状态对照**：盘上 `target/backup/stage` 的 8 种组合逐个造目录，断言
   `depth_state == "ready"` 当且仅当 `target` 存在，并打印 `state | target | backup | stage | depth_state | route | OK/FAIL`。
2. **崩溃注入**：干净状态（`target = OLD`、ready）上 monkeypatch
   `bookcorpus.reading.store.os.replace`，只在源路径以 `.depth-` 开头且目标是 `depth/` 时
   `raise SystemExit`；接住后断言 (a) 没有 `depth/`、(b) 备份里有旧 `chapter.html`、
   (c) `depth_state` 是 `missing`、(d) `chapter_route` 是 `('none', '尚未生成')`。
3. **自愈**：随后正常保存一次，断言 `depth/` 存在、`depth_state` 为 `ready`、路由为 `depth`。

2026-11-13 实测末行（退出码 0）：

```text
states: 8 | conformance: PASS | crash-window: CONFIRMED | heal: PASS
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
（退出码 12），打印 `Invariant CrashKeepsReadable is violated.` 与上面那条四步路径。
`-workers 1` 让反例顺序稳定。直接 `java -cp $TLA2TOOLS tlc2.TLC` 只用于本机临时调试，
报告里的读数一律以 `verification_exec` 的输出为准（它会带上 jar 的解析来源与隔离方式）。
违反组会在当前目录留下 `DepthSwap_TTrace_*.tla/.bin`，看过就删，不是交付物。

## 范围

- 单章、单进程、单次 `save_depth`。并发保存、进程在其它位置被杀、文件系统与 `os.replace`
  的原子性语义都不在模型里。
- 模型只模拟目录名与存在性，不模拟包内容；备份里的 `chapter.html` 由现场脚本 (b) 取证。
- 不覆盖 `chapter_route` 的 `legacy` 分支：本场景没有 `manifest.json`。
- 不改产品代码。`check_swap.py` 只在进程内 monkeypatch `os.replace`，退出即恢复；
  模型同步对象是 `store.py` 当前行号，产品代码 HEAD `3413dc7`。
- 模型按任务规格把 `crashed` 与 `afterSave` 做成一旦置真就不复位的闩：`NextSaveHeals`
  之后其余动作都因 `¬crashed` 不可用。所以 `RecoverAfterSave` 证明的是「崩溃后的下一次
  保存把 target 放回」，不是「之后任意多次保存都不再开窗」。要证后者，得在 `SaveBegins`
  复位 `afterSave`、在自愈完成时复位 `crashed`，那会改变任务给定的迁移关系，本目录没有擅自改。

## 与任务规格的差异

1. 路径：`pack_dir` 实际是 `<root>/阅读/B0001/ch/01`（`store.py:30-32, 42-44`），
   不是任务文字里的 `<root>/B0001/ch/01`。
2. `NEW` 与 TLA+ 保留字冲突，模型常量改名 `NewPkg`，值仍为 `"new"`。
3. 给 `MoveTargetToBackup` 加 `stage = NewPkg`、给 `MoveStageToTarget` 加 `target = None`。
   不加这两条，模型会允许「stage 没写好就换目录」和「target 没让位就覆盖」，
   `NoDataLoss` 会被这类代码里不存在的交错打破；加上后与 `store.py:168-170, 237-238` 一致。
4. `NeverLoseReadable`（`hadReady ⇒ target ≠ None`）留在模型里但**不进任何 cfg**：它在
   `MoveTargetToBackup` 一步就违反，早于 `Crash`，证明的是窗口存在而不是崩溃把它冻住。
   要报的那条是 `CrashKeepsReadable`（`crashed ⇒ target ≠ None`），所以 `DepthSwapRecover.cfg`
   换成它。两条都不弱化，只是分工不同。
5. 文件数是 5 个：任务第 2 项包含两个 `.cfg`。
