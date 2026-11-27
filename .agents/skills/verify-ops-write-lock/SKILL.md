---
name: verify-ops-write-lock
description: 用 TLA+ 模型检查操作台写任务调度器（apps/ops/server.py 的 Runner）的写者互斥承诺：写任务的孩子在跑时，工作区/运维/.writer.lock 必须在它手里；同一时刻最多一个写任务的孩子；孤儿标记不能落在还活着的孩子上。改到 Runner、adopt、_spawn、_run、_finalize、ledger_lock、.writer.lock 或任务取消逻辑时先跑一遍。
---

# 操作台写者互斥：形式化检查

## 被检查的承诺

**写任务在跑的时候，`工作区/运维/.writer.lock` 在同一时刻只被一个写者持有；别的写入口拿不到它。**

破了会怎样：两个进程同时拆章、改人账、重建索引。症状是某个 `books/<uid>/md/` 写了一半、`tables/ledger.db` 丢一次更新、或者 `build/` 里出现互相覆盖的产物；不报错，只是数据悄悄不对。

承诺写在 `apps/ops/README.md`（「任务、锁与取消 → 写互斥」）和 `AGENTS.md`（「一个账本一个写者」）。实现是 `apps/ops/server.py` 的 `Runner`：写任务拿到 `.writer.lock` 的 `LOCK_EX` 才起子进程，HTTP 账表写走 `ledger_lock()` 拿同一把锁。

## 当前读数（`ChildHoldsLock=TRUE`，当前代码）

| 规则 | `TRUE`（当前代码） | `FALSE`（历史基线对照） | 一句话 |
|---|---|---|---|
| `WriterHoldsLock` | **通过**（736 状态） | 破（6 步） | 写子进程通过 `pass_fds` 继承已 `flock` 的 fd，服务崩了锁也在孩子手里 |
| `NoLiveOrphan` | **通过**（736 状态） | 破（7 步） | `adopt()` 回退核对 `jobs/<id>/pid`、`/proc` 任务环境与 `.writer.lock` 占用，不误标 `orphaned` |
| `NoTwoWriteChildren` | **通过**（736 状态） | 破（12 步） | 接管后 `active_write` 占位且 `.writer.lock` 仍被持有，不会再派第二个写者 |
| `NoHttpWithChild` | **通过**（736 状态） | 破（8 步） | 接管期间 `writer_busy()` 挡住 HTTP 写，`.writer.lock` 也挡住外部进程 |
| `CancelledNeverRuns` | **通过**（736 状态） | 通过（664 状态） | 取消过的任务不会再起进程，且有活锁/启动中窗口时不在退完前误写 `cancelled` |

历史问题说明（`ChildHoldsLock=FALSE` 曾检出两条真 bug，现已修复；完整反例路径仍留档于 `spec/report.md` 与 `spec/chart.mmd`）：

- **F1 接管来的写任务不持锁（已修）。** 过去 `Popen` 不传 `lock_fd` 且 `release()` 调 `LOCK_UN`，服务崩溃时内核立刻释放锁，整个接管期间 `.writer.lock` 都是空的。现在 `_spawn()` 通过 `pass_fds=(lock_fd,)` 把已 `flock` 的锁 fd 传给子进程，且 `release()` / `resume()` 只 `os.close(lock_fd)` 不调 `LOCK_UN`——子进程退出前锁一直都在。
- **F2 pid 没写进库的窗口（已修）。** 过去 `_spawn()` 先 `Popen` 再 `store.update(pid=...)`，且 `adopt()` 只认 `row["pid"]`。现在 `_spawn()` 在 `child_env` 注入 `BOOK_CORPUS_OPS_JOB_ID` 并先原子写 `jobs/<id>/pid` 再落库；`adopt()` 依次核对 `ops.db` `pid`、`jobs/<id>/pid`、`/proc/<pid>/environ`，若写锁 `.writer.lock` 仍被占用则进入 `_watch_adopted_lock` 等锁释放，不再误标 `orphaned`。

## 怎么跑

```sh
.agents/skills/verify-ops-write-lock/scripts/check.sh
```

一条命令，5 条规则各跑一遍 TLC，约 5 秒，末行给通过/失败计数。默认 `CHILD_HOLDS_LOCK=TRUE`（当前代码）。要回看历史基线的 4 条反例，可传：

```sh
CHILD_HOLDS_LOCK=FALSE .agents/skills/verify-ops-write-lock/scripts/check.sh /tmp/ops-baseline
```

依赖：`java`（11+）与 `tla2tools.jar`。java 从 `PATH` 取（`command -v java`），不写死 JDK 路径；
`tla2tools.jar` 不在 `PATH` 里，脚本按 `~/.local/share/tla2tools/`、`~/.local/share/tlaplus/`、
`/usr/share/java/`、`/usr/local/share/tla2tools/`、`/opt/tla2tools/` 依次找。两处都能用 `JAVA=` /
`TLA2TOOLS=` 覆盖；找不到时报错会给出下载地址。日志落在技能目录的 `out/`（已 ignore）或你给的第一参数目录。

读结果用 pstack 的 `/statechart`：它把每条违规画成状态图，并按模型的动作名给最短路径；模型在
`spec/OpsRunner.tla`，时序公式在 `spec/OpsRunnerSpec.tla`（`□[Next]_vars` 要变量元组，单独放一个模块），
读法视图在 `spec/view/view.ts`。两版留档可直接对照：

- `spec/chart-current.mmd` / `spec/report-current.md`：当前代码（`ChildHoldsLock=TRUE`）的单任务图，9 状态 / 27 箭头，五条规则全成立。
- `spec/chart.mmd` / `spec/report.md`：`ChildHoldsLock=FALSE` 的历史反例，8 状态 / 9 箭头，四条规则破。

全量两任务（`Jobs={J1,J2}`）是 736 状态，超过 statechart 的 500 状态预算，只出 report 不出图；
当前代码图因此用 `spec/chart-current.cfg` 的单任务配置（53 状态 / 121 步）。复现（`<statechart>` 换成技能目录，
`--out` 必须是新目录）：

```sh
TLA2TOOLS=~/.local/share/tla2tools/tla2tools.jar \
  <statechart>/scripts/statechart spec/OpsRunnerSpec.tla \
  --config spec/chart-current.cfg --view spec/view/view.ts --out /tmp/chart-current
```

本机 Node 26 直接跑 `node_modules` 里的脚本会报 `ERR_UNSUPPORTED_NODE_MODULES_TYPE_STRIPPING`；
把 statechart 脚本复制到 `node_modules` 外再跑。

## 真 bug 还是建模错误

- 「最短路径里出现了现实中不可能的动作组合」→ 建模错误。先看 `spec/OpsRunner.tla` 里该动作的守卫是否漏了代码里的条件（例如 `MarkRunning` 必须要求 `flock = j`，即这个任务自己拿着锁，而不只是「锁被某人拿着」）。
- 「路径每一步都能在代码里找到对应行」→ 是真 bug。把路径逐动作对回代码；`spec/OpsRunner.tla` 每个动作上方都标了 `server.py` 的行号区间。
- 反例里出现 `Jobs = {J1, J2}` 之外的东西，或是 `ChildHoldsLock=TRUE` 才有的形状 → 不是今天代码的 bug。

## 现场证据（2026-11-23，一次性工作区，未碰 `~/书库`）

1. 起服务，提交一个长跑的写任务（用 `tests/acceptance/ops/check_ops.py` 的一次性根目录搭台，脚本换成 flag 触发的 sleeper），任务 `running`：
   `flock -n 工作区/运维/.writer.lock -c true` 返回 1（锁在服务手里）。
2. `SIGKILL` 服务进程（孩子是 `start_new_session`，跟着活）：同一命令返回 0（锁释放了）。
3. 重启服务：任务 `running`、摘要「接管：进程仍在跑」、孩子还活着，而 `flock -n` 立刻成功——**这就是 F1**。
4. 手工把 `ops.db` 里该行 pid 置空后重启：任务 `orphaned`，孩子仍在跑——**这就是 F2**。
   这一步要看到「两个孩子同时跑」，排队的那个任务得带上真实 `commands.json`；只用空命令的排队行会在起进程前失败。

`tests/acceptance/ops/check_ops.py` 的 `adoption_checks` / `adoption_resume_check` 覆盖了接管后的收尾与取消，但都不检查接管期间的锁，也没有 pid 为空那一格。

2026-11-24 追加（`5f03ac9`）：`tests/test_writer_lock.py` 覆盖统一后的写锁——外部进程持 `.writer.lock` 时 CLI 的 `category_put` / `categories_delete` / `ingest apply` 都会等到锁释放才落盘；`ledger_lock` 与包路径导入的 `lib_common` 同线程重入不自锁；Runner 持锁拉起 `ingest apply` 子进程能正常跑完（`BOOK_CORPUS_WRITER_LOCK_PATH` 生效）。

## 覆盖范围：改到这些就必须重跑

| 代码 | 模型里的动作 | 说明 |
|---|---|---|
| `apps/ops/server.py` `Runner.submit` / `App` 的 `WRITE_ROUTES` 409 守卫（`writer_busy`） | `Submit` | 提交入口的互斥只在本进程内 |
| `Runner._loop` | `Dispatch` | 占 `active_write` 槽位 |
| `Runner._run`（`fcntl.flock`、拿锁后回读状态、`release` 只 close 不 LOCK_UN） | `Acquire` `MarkRunning` `AbortDispatched` `Release` | 锁的获取与释放顺序 |
| `Runner._spawn`（`Popen(pass_fds=(lock_fd,))` + `BOOK_CORPUS_OPS_JOB_ID` → `jobs/<id>/pid` → `store.update(pid)`） | `Spawn` `RecordPid` | 子进程出生即持锁并带任务标记 |
| `Env.child_env`（写孩子带 `BOOK_CORPUS_WRITER_LOCK_PATH`，只读孩子剔除） | `Spawn` | 继承声明只防子进程重复拿同一把锁，不改变锁的归属；真正生效见下一行的 `writer_lock()` |
| `Runner.cancel` / `_terminate` | `CancelQueued` `CancelRunning` | 只建模「取消后不再起进程」这一格 |
| `Runner._finalize`（先放锁再写终态；`build_views` 子进程也带继承声明） | `Release` `Finish` | |
| `Runner.adopt` / `_watch_adopted` / `_watch_adopted_lock` / `resume` | `Start` `AdoptFinish` | 按 DB/pid 文件/`/proc`/写锁四级认领并占位 |
| `Runner.snapshot` / `writer_busy` / `ledger_lock` → `src/bookcorpus/scripts/lib_common.py` 的 `writer_lock_path` / `writer_lock`；`category_put` / `category_put_many` / `categories_delete`；`ingest.py` 的 `apply` 与 `rebuild` | `HttpStart` `HttpEnd` | HTTP 与 CLI 的人账写共用同一把 `.writer.lock`；同线程重入按计数放行，`BOOK_CORPUS_WRITER_LOCK_PATH` 命中时视为已继承 |
| `src/bookcorpus/scripts/normalize_corpus_batch.py` `run_batch`（`with L.writer_lock`，1172 行）/ `_run_sync_pipeline`（961 行给同步子进程带继承声明） | —（CLI 直接取锁，不经 Runner） | 批次是直接 `writer_lock` 客户端：与 Runner 写任务抢同一把锁，抢不到就阻塞；它只给自己的同步子进程（`build_library` / `build_para_index` / `embed_corpus`）带 env 声明，没有 `pass_fds`。模型不展开这个 CLI 写者；孙进程缺口见「不管什么」 |
| 任何改变「谁持有 `.writer.lock`」或「pid 何时落库」的改动 | `Crash` `Start` | |

**同步基线：2026-11-27，`f55d81a`（PR #62 头，基于 main `fd86d23`）。** 已按这个提交重核：`Runner`（`server.py:1013-1880`）与 `lib_common.writer_lock` 无改动；`App` 自基线新增 `md_canonical` 空状态块（#59，16 行）与 PR #62 的 `filtered_chapters` / `empty_books` 两键，HTTP/`ledger_lock` 行号随之 +18 并已更新；同区间新增的 `normalize_corpus_batch.py` 写入口已记入上表。复跑五条规则全过（各 736 个不同状态）。覆盖的代码在那之后变了，就把上面的行号与读数重核一遍，再更新这里；没核对过不要往前推。PR 合并（本仓 squash）后把基线换成合并提交。

## 代码变了怎么更新

1. 先跑 `scripts/check.sh`，看哪几条翻红/翻绿。
2. 改的是守卫或顺序：在 `spec/OpsRunner.tla` 找到对应动作，按代码改守卫，并在注释里更新 `server.py` 的行号。
3. 改的是「谁持有锁」：`ChildHoldsLock` 这个常量就是为这种改动留的。`TRUE` 是今天的代码：写子进程通过 `pass_fds` 继承已 flock 的 fd，退出才放；`FALSE` 是历史基线：锁只在服务进程手里，进程死即释放。两种都跑一遍，读数写进上面的表。
4. 想确认规则不是形同虚设：临时把一条守卫删掉（例如让 `Spawn` 不再要求 `flock = j`，相当于代码把 `flock` 挪到 `Popen` 之后），规则必须翻红，然后改回来。
   2026-11-24（`5f03ac9`）复做：在副本里删掉 `Spawn` 的 `flock = j`，`WriterHoldsLock` 给出
   `Submit → Dispatch → Acquire → MarkRunning → Spawn → ExitChild → Spawn` 的 7 步反例（848 个状态），
   其余四条仍绿；还原后五条全绿（各 736 个状态）。擦伤试验改过 `spec/OpsRunner.tla` 后一定要用
   `git diff` 确认已还原，再继续。
5. 规则本身不能为了通过而放松。放松了就不是「一个账本一个写者」了。

## 已落地修复要点

- `Runner._spawn()` 通过 `pass_fds=(lock_fd,)` 把已 `flock` 的锁 fd 传给子进程，且 `_run()` / `resume()` 的释放只调用 `os.close(lock_fd)` 不调用 `LOCK_UN`，确保服务崩溃后写子进程继续持有 `.writer.lock`，直到子进程退出（修掉 F1 与 F2 的互斥面）。
- `Runner._spawn()` 在 `child_env` 注入 `BOOK_CORPUS_OPS_JOB_ID` 并先原子写 `jobs/<id>/pid` 再更新 `ops.db`；`Runner.adopt()` 按 `ops.db` `pid` → `jobs/<id>/pid` → `/proc/<pid>/environ` → `.writer.lock` 占用顺序认领在跑任务，避免崩溃窗口内把活孩子误标成 `orphaned`（修掉 F2）。
- 2026-11-24（#44）：CLI 人账写入、`ingest apply` 与操作台 HTTP 账表写统一走 `lib_common.writer_lock(root)`，锁文件固定为 `工作区/运维/.writer.lock`；ops 写孩子通过 `BOOK_CORPUS_WRITER_LOCK_PATH` 声明已继承父进程的 flock，避免重复拿同一把锁自锁（`Runner._spawn` 与 `ingest.rebuild` 两处注入）。

## 不管什么

- 只读任务与 `.reader.lock`（`READ_SLOTS = 2` 的并发上限、读写不互斥）不在模型里。
- 取消的时序（`SIGTERM` 到进程真正退出之间的窗口）不建模；`CancelledNeverRuns` 只管「取消过的任务不会再起新进程」。
- 一个进程内的 `self.cv` 竞争、`store` 的 SQLite 事务、`_finalize` 里各 kind 的门槛判定，都不建模。
- 单机、单根目录、`Runner` 这一个实现。多机或多根不覆盖。
- `BOOK_CORPUS_WRITER_LOCK_PATH` 的继承声明：模型只建「孩子活着 ⇒ 锁在任务手里」，不建子进程内部对 `writer_lock()` 的重复调用，也不建孙进程。写脚本不要留下比任务活得久、又没继承锁 fd 的后台孙进程——它拿到了 env 声明却不持锁。
- `normalize_corpus_batch._run_sync_pipeline` 的同步子进程（`build_library` / `build_para_index` / `embed_corpus`）不建模：它们只拿 env 声明，`Popen` 没有 `pass_fds`。正常路径由批次进程持锁直到 `subprocess.run` 返回；只有批次进程被单独 SIGKILL/OOM 杀掉、子进程仍在跑时，锁会先释放而子进程按声明不拿锁。这一段不在 `WriterHoldsLock` 的证明范围内。
- `book-corpus normalize`（含 PR #62 的 `--reclassify-roles`）直接改 `books/<uid>/md/` 的 front matter，不调用 `writer_lock`；锁只约束 Runner 写任务与显式取锁的 CLI（`ingest`、`normalize-batch`）。直接 CLI 与 ops 写任务并发时的 md 竞争不在模型里。
- 同线程重入与 `ledger_lock` 嵌套（`category_put` 在 `with ledger_lock` 里再拿一次）不建模：重入按线程计数，第二次不再 flock，等同于同一次写。
- 模型只展开两个写任务（`Jobs = {J1, J2}`）。要更多，改 `OpsRunner.cfg` 的常量；三个任务的状态数会大一个量级，通常没必要——两个就够出「两个孩子」的反例。当前代码图用 `spec/chart-current.cfg` 收窄到 `Jobs={J1}` 才落进 500 状态预算；单任务图里 `NoTwoWriteChildren` 平凡成立，互斥仍由 `WriterHoldsLock` 守着。

## 相关

- 代码：`apps/ops/server.py`（`Runner` 从 `class Runner` 起）、`apps/ops/README.md`
- 验收：`tests/acceptance/ops/check_ops.py`
- 约定：`AGENTS.md`「一个账本一个写者」、`docs/design/入库实现流程图-2026-11-14.html`（两把锁不是同一把）
- 维护：代码改了没重跑这里，用 pstack 的 `/maintain-formal-verification-skill` 把模型与读数拉回一致。
