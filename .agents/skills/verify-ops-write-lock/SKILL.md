---
name: verify-ops-write-lock
description: 用 TLA+ 模型检查操作台写任务调度器（apps/ops/server.py 的 Runner）的写者互斥承诺：写任务的孩子在跑时，工作区/运维/.writer.lock 必须在它手里；同一时刻最多一个写任务的孩子；孤儿标记不能落在还活着的孩子上。改到 Runner、adopt、_spawn、_run、_finalize、ledger_lock、.writer.lock 或任务取消逻辑时先跑一遍。
---

# 操作台写者互斥：形式化检查

## 被检查的承诺

**写任务在跑的时候，`工作区/运维/.writer.lock` 在同一时刻只被一个写者持有；别的写入口拿不到它。**

破了会怎样：两个进程同时拆章、改人账、重建索引。症状是某个 `books/<uid>/md/` 写了一半、`tables/ledger.db` 丢一次更新、或者 `build/` 里出现互相覆盖的产物；不报错，只是数据悄悄不对。

承诺写在 `apps/ops/README.md`（「任务、锁与取消 → 写互斥」）和 `AGENTS.md`（「一个账本一个写者」）。实现是 `apps/ops/server.py` 的 `Runner`：写任务拿到 `.writer.lock` 的 `LOCK_EX` 才起子进程，HTTP 账表写走 `ledger_lock()` 拿同一把锁。

## 当前读数（`ChildHoldsLock=FALSE`，即今天的代码）

| 规则 | 读数 | 一句话 |
|---|---|---|
| `WriterHoldsLock` | **破** | 服务进程一死，锁就没了，写子进程还在跑（6 步反例，最后一步是服务崩） |
| `NoLiveOrphan` | **破** | 同一个坑的下一页：新进程把还活着的孩子标成 `orphaned`（7 步） |
| `NoTwoWriteChildren` | **破** | 孤儿之后还能再派一个写任务，两个孩子一起写（12 步） |
| `NoHttpWithChild` | **破** | 孤儿孩子在写时，HTTP 账表读改写照样拿到锁（8 步） |
| `CancelledNeverRuns` | 通过 | 取消过的任务不会再起进程 |

最短反例（`spec/report.md` 由模型检查器生成，`/statechart` 画成状态图）：

```
空闲 → 提交写任务 → 调度器起任务 → 拿到写锁 → 任务开始跑 → 拉起写进程（写任务在跑，持锁）
     → 服务进程死掉（锁随进程消失，孩子在跑）
     → 新服务启动，接管残留：孩子还活着，ops.db 里没有它的 pid → 任务标 orphaned
```

两条结论，都可以在真实服务上复现（复现方法见「现场证据」）：

- **F1 接管来的写任务不持锁。** `adopt()` 认下任务后，`_watch_adopted()` 只是等孩子退出，锁在服务崩溃时就随进程释放了，整个剩余执行期间都没有锁。同进程的 HTTP 写被 `writer_busy()` 挡着（返 409），但另一个拿同一根目录的服务进程、或任何按文档用 `.writer.lock` 做互斥的写者，都不会被挡。
- **F2 pid 没写进库的窗口。** `_spawn()` 先 `Popen` 再 `store.update(pid=...)`；服务在这中间死掉，就留下「活着的孩子 + 库里 pid 为空」。重启后 `adopt()` 只看 `row["pid"]`，于是标 `orphaned`，调度器随即可以再起一个写者——两个写者同时写。

## 怎么跑

```sh
.agents/skills/verify-ops-write-lock/scripts/check.sh
```

一条命令，5 条规则各跑一遍 TLC，约 5 秒，末行给通过/失败计数。默认 `CHILD_HOLDS_LOCK=FALSE`（今天的代码）。要检查「写子进程自己拿锁」的改法：

```sh
CHILD_HOLDS_LOCK=TRUE .agents/skills/verify-ops-write-lock/scripts/check.sh /tmp/ops-childlock
```

依赖：`java`（11+）与 `tla2tools.jar`。本机默认取
`/usr/lib/jvm/java-25-openjdk/bin/java` 与 `~/.local/share/tlaplus/tla2tools.jar`（TLC 2026.11.06），
可用 `JAVA=` / `TLA2TOOLS=` 覆盖。日志落在技能目录的 `out/`（已 ignore）或你给的第一参数目录。

读结果用 pstack 的 `/statechart`：它把每条违规画成状态图，并按模型的动作名给最短路径；模型在
`spec/OpsRunner.tla`，时序公式在 `spec/OpsRunnerSpec.tla`（`□[Next]_vars` 要变量元组，单独放一个模块），
读法视图在 `spec/view/view.ts`。先例：`spec/chart.mmd` 与 `spec/report.md` 是
`ChildHoldsLock=FALSE` 那一版的图与报告，可直接对照。

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

## 覆盖范围：改到这些就必须重跑

| 代码 | 模型里的动作 | 说明 |
|---|---|---|
| `apps/ops/server.py` `Runner.submit` / `App` 的 `WRITE_ROUTES` 409 守卫（`writer_busy`） | `Submit` | 提交入口的互斥只在本进程内 |
| `Runner._loop` | `Dispatch` | 占 `active_write` 槽位 |
| `Runner._run`（`fcntl.flock`、拿锁后回读状态、`release`） | `Acquire` `MarkRunning` `AbortDispatched` `Release` | 锁的获取与释放顺序 |
| `Runner._spawn`（`Popen` → `store.update(pid)` → 写 `jobs/<id>/pid`） | `Spawn` `RecordPid` | `Popen` 与记录 pid 之间是 F2 的窗口 |
| `Runner.cancel` / `_terminate` | `CancelQueued` `CancelRunning` | 只建模「取消后不再起进程」这一格 |
| `Runner._finalize`（先放锁再写终态） | `Release` `Finish` | |
| `Runner.adopt` / `_watch_adopted` / `resume` | `Start` `AdoptFinish` | 只看 `ops.db` 的 pid；接管期间不拿锁 |
| `Runner.snapshot` / `writer_busy` / `ledger_lock` | `HttpStart` `HttpEnd` | HTTP 写与写任务不重叠 |
| 任何改变「谁持有 `.writer.lock`」或「pid 何时落库」的改动 | `Crash` `Start` | |

**同步提交：`e48718d`**（2026-11-23 核对，`apps/ops/server.py` 的 `Runner` 与上述注释行号一致）。覆盖的代码在那之后变了，就把上面的行号与读数重核一遍，再把这行改成新提交；没核对过不要往前推。

## 代码变了怎么更新

1. 先跑 `scripts/check.sh`，看哪几条翻红/翻绿。
2. 改的是守卫或顺序：在 `spec/OpsRunner.tla` 找到对应动作，按代码改守卫，并在注释里更新 `server.py` 的行号。
3. 改的是「谁持有锁」：`ChildHoldsLock` 这个常量就是为这种改动留的。它取 `FALSE` 时表示锁由服务进程持有、进程死即释放（今天的代码）；取 `TRUE` 时表示写子进程自己打开 `.writer.lock` 并 flock、退出才放。两种都跑一遍，读数写进上面的表。
4. 想确认规则不是形同虚设：临时把一条守卫删掉（例如让 `Spawn` 不再要求 `flock = j`，相当于代码把 `flock` 挪到 `Popen` 之后），规则必须翻红，然后改回来。
   做过一次这个擦伤试验：`ChildHoldsLock=TRUE` 下删掉 `Spawn` 的 `flock = j`，`WriterHoldsLock` 立刻给出
   `… Spawn → ExitChild → Spawn` 的 3 步反例，四条规则齐红（1341 个状态）；还原后回到 4 绿 1 红（930 个状态）。
   擦伤试验改过 `spec/OpsRunner.tla` 后一定要用 `git diff` 确认已还原，再继续。
5. 规则本身不能为了通过而放松。放松了就不是「一个账本一个写者」了。

## 建议的改法（未实施，只验证过模型）

`ChildHoldsLock=TRUE` 的读数是 **4 绿 1 红**（`NoLiveOrphan` 仍红，因为 `Popen` 到记录 pid 之间没有任何模型外的机制能识别这个孩子）。也就是说：

- 让写子进程自己持有 `.writer.lock`（`Popen` 时把已 `flock` 的 fd 传下去，或子进程启动时自己开一次并 flock），能同时修掉 F1 与 F2 的写者互斥面；
- 剩下的「orphaned 标记落在活着的孩子上」要靠 `adopt()` 在 `ops.db` 没有 pid 时回退读 `jobs/<id>/pid`，并且这个文件也写不成时不要断言孩子已死——把状态留在 `running`/`awaiting_review`，别标 `orphaned`。

要不要改产品代码由你定；这个技能只负责把「改了会怎样」变成可跑的读数。

## 不管什么

- 只读任务与 `.reader.lock`（`READ_SLOTS = 2` 的并发上限、读写不互斥）不在模型里。
- 取消的时序（`SIGTERM` 到进程真正退出之间的窗口）不建模；`CancelledNeverRuns` 只管「取消过的任务不会再起新进程」。
- 一个进程内的 `self.cv` 竞争、`store` 的 SQLite 事务、`_finalize` 里各 kind 的门槛判定，都不建模。
- 单机、单根目录、`Runner` 这一个实现。多机或多根不覆盖。
- 模型只展开两个写任务（`Jobs = {J1, J2}`）。要更多，改 `OpsRunner.cfg` 的常量；三个任务的状态数会大一个量级，通常没必要——两个就够出「两个孩子」的反例。

## 相关

- 代码：`apps/ops/server.py`（`Runner` 从 `class Runner` 起）、`apps/ops/README.md`
- 验收：`tests/acceptance/ops/check_ops.py`
- 约定：`AGENTS.md`「一个账本一个写者」、`docs/design/入库实现流程图-2026-11-14.html`（两把锁不是同一把）
- 维护：代码改了没重跑这里，用 pstack 的 `/maintain-formal-verification-skill` 把模型与读数拉回一致。
