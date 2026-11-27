---------------------------- MODULE OpsRunner ----------------------------
\* 操作台写任务调度器（apps/ops/server.py 的 Runner）的写者互斥模型。
\*
\* 目标：检查两条承诺——
\*   1. 同一时刻最多一个写任务的孩子进程在写人账/书目；
\*   2. 写任务在跑时，它持有 工作区/运维/.writer.lock（跨进程互斥的根据）。
\* 只建模“写”任务；只读任务与 .reader.lock 不在范围内（见 SKILL.md 的 Scope）。
\*
\* 每个动作的注释指向 apps/ops/server.py 里对应的代码行。
\*
\* 动作名与代码的对应：
\*   Submit        HTTP 写入口的 writer_busy 检查 + Runner.submit
\*   Dispatch      Runner._loop 选队列、占 active_write 槽位
\*   Acquire       Runner._run 拿 .writer.lock（阻塞）
\*   MarkRunning   _run 拿锁后回读状态、置 running
\*   AbortDispatched  cancel 与启动撞车：拿锁回读见 cancelled，解锁退出
\*   Spawn         _spawn 的 Popen（先有进程，后写 pid）
\*   RecordPid     _spawn 的 store.update(pid=...) / jobs/<id>/pid
\*   ExitChild     子进程退出
\*   CancelQueued  Runner.cancel：没起进程的排队任务直接置 cancelled
\*   CancelRunning Runner.cancel / _terminate：在跑的任务只加内存标记
\*   Release       _run 的 release()：解锁 + 归还 active 槽位
\*   Finish        _finalize 写终态（取消优先）
\*   AdoptFinish   _watch_adopted → resume → _finalize（被接管的任务）
\*   HttpStart     /api/ledger/row 等写入口：writer_busy 检查 + ledger_lock 拿锁
\*   HttpEnd       ledger_lock 释放
\*   Crash         服务进程死掉：flock 随进程释放，内存态全丢
\*   Start         App.__init__ 里的 Runner.adopt()：只按 ops.db 的 pid 认人
\*-----------------------------------------------------------------------------

EXTENDS Naturals

CONSTANTS
    Jobs,
    \* FALSE = 历史基线：锁由服务进程持有，进程一死锁就没了，且只看 ops.db 的 pid。
    \* TRUE  = 当前实现：写子进程通过 pass_fds 继承已 flock 的 fd，退出才放；
    \*         写孩子的 env 里另带 BOOK_CORPUS_WRITER_LOCK_PATH，让 lib_common.writer_lock()
    \*         不再重复拿同一把锁（只防自锁，不改变锁的归属，模型不建这一层）；
    \*         adopt() 回退查 jobs/<id>/pid、/proc 任务环境与写锁占用。
    ChildHoldsLock

ASSUME Jobs # {}

TERMINAL == {"succeeded", "awaiting_review", "orphaned", "cancelled"}

VARIABLES
    db,          \* [Jobs -> {"new","queued","running"} ∪ TERMINAL]，ops.db 的 job.state
    pid,         \* [Jobs -> {"none","pid"}]，ops.db 里记的 pid；"none" 含 NULL
    child,       \* [Jobs -> BOOLEAN]，这个任务的孩子进程是否还活着
    adopted,     \* [Jobs -> BOOLEAN]，本次服务进程是否接管了它（内存态）
    released,    \* [Jobs -> BOOLEAN]，本次服务进程已放锁、还没写终态（内存态）
    cancels,     \* 本次服务进程的取消标记集合（内存态，重启即丢）
    serverUp,    \* 服务进程是否活着
    activeW,     \* 本次服务进程认下的写任务集合（active_write）
    flock,       \* .writer.lock 的持有者："free" / 任务名（服务进程或它的孩子）/ "http"
    httpOn       \* HTTP 账表读改写是否正在锁内

Unchanged ==
    ∧ UNCHANGED db
    ∧ UNCHANGED pid
    ∧ UNCHANGED child
    ∧ UNCHANGED adopted
    ∧ UNCHANGED released
    ∧ UNCHANGED cancels
    ∧ UNCHANGED serverUp
    ∧ UNCHANGED activeW
    ∧ UNCHANGED flock
    ∧ UNCHANGED httpOn


Children == {j ∈ Jobs : child[j]}
WriterBusy == activeW # {} ∨ (∃ k ∈ Jobs : db[k] = "queued")
IsAdoptable(j) == db[j] = "running" ∧ child[j] ∧ (pid[j] = "pid" ∨ ChildHoldsLock)

Init ==
    ∧ db       = [j ∈ Jobs |-> "new"]
    ∧ pid      = [j ∈ Jobs |-> "none"]
    ∧ child    = [j ∈ Jobs |-> FALSE]
    ∧ adopted  = [j ∈ Jobs |-> FALSE]
    ∧ released = [j ∈ Jobs |-> FALSE]
    ∧ cancels  = {}
    ∧ serverUp = TRUE
    ∧ activeW  = {}
    ∧ flock    = "free"
    ∧ httpOn   = FALSE

\* 操作人在页面上提交一个写任务。HTTP 层先查 writer_busy（server.py:3983-3993, 4010-4015；
\* Runner.submit 在 1044-1068）。
Submit(j) ==
    ∧ serverUp
    ∧ db[j] = "new"
    ∧ ¬WriterBusy
    ∧ db' = [db EXCEPT ![j] = "queued"]
    ∧ UNCHANGED pid ∧ UNCHANGED child ∧ UNCHANGED adopted ∧ UNCHANGED released ∧ UNCHANGED cancels ∧ UNCHANGED serverUp ∧ UNCHANGED activeW ∧ UNCHANGED flock ∧ UNCHANGED httpOn

\* 调度器挑一个 queued 的写任务，先占住 active_write 槽位再起线程（server.py:1251-1286）。
Dispatch(j) ==
    ∧ serverUp
    ∧ db[j] = "queued"
    ∧ activeW = {}
    ∧ activeW' = {j}
    ∧ UNCHANGED db ∧ UNCHANGED pid ∧ UNCHANGED child ∧ UNCHANGED adopted ∧ UNCHANGED released ∧ UNCHANGED cancels ∧ UNCHANGED serverUp ∧ UNCHANGED flock ∧ UNCHANGED httpOn

\* 写任务拿 .writer.lock 的 LOCK_EX（server.py:1299-1322）。锁被拿着就阻塞，
\* 这里表现为动作不可用。
Acquire(j) ==
    ∧ serverUp
    ∧ j ∈ activeW
    ∧ (db[j] = "queued" ∨ (db[j] = "running" ∧ ¬child[j] ∧ ¬released[j]))
    ∧ flock = "free"
    ∧ flock' = j
    ∧ UNCHANGED db ∧ UNCHANGED pid ∧ UNCHANGED child ∧ UNCHANGED adopted ∧ UNCHANGED released ∧ UNCHANGED cancels ∧ UNCHANGED serverUp ∧ UNCHANGED activeW ∧ UNCHANGED httpOn

\* 拿锁后回读 ops.db：还是 queued 才置 running（server.py:1324-1330）。
MarkRunning(j) ==
    ∧ serverUp
    ∧ j ∈ activeW
    ∧ db[j] = "queued"
    ∧ flock = j
    ∧ db' = [db EXCEPT ![j] = "running"]
    ∧ UNCHANGED pid ∧ UNCHANGED child ∧ UNCHANGED adopted ∧ UNCHANGED released ∧ UNCHANGED cancels ∧ UNCHANGED serverUp ∧ UNCHANGED activeW ∧ UNCHANGED flock ∧ UNCHANGED httpOn

\* 取消与启动撞车：cancel() 把 DB 置 cancelled，_run 拿锁回读后解锁退出（server.py:1322-1329）。
AbortDispatched(j) ==
    ∧ serverUp
    ∧ j ∈ activeW
    ∧ db[j] = "cancelled"
    ∧ flock = j
    ∧ flock' = "free"
    ∧ activeW' = activeW \ {j}
    ∧ UNCHANGED db ∧ UNCHANGED pid ∧ UNCHANGED child ∧ UNCHANGED adopted ∧ UNCHANGED released ∧ UNCHANGED cancels ∧ UNCHANGED serverUp ∧ UNCHANGED httpOn

\* _spawn：Popen（pass_fds 继承锁 fd、env 记 job_id 与写锁继承声明）先返回，
\* pid 文件与 ops.db 后写（server.py:1387-1421）。
Spawn(j) ==
    ∧ serverUp
    ∧ db[j] = "running"
    ∧ ¬child[j]
    ∧ flock = j
    ∧ ¬released[j]
    ∧ child' = [child EXCEPT ![j] = TRUE]
    ∧ UNCHANGED db ∧ UNCHANGED pid ∧ UNCHANGED adopted ∧ UNCHANGED released ∧ UNCHANGED cancels ∧ UNCHANGED serverUp ∧ UNCHANGED activeW ∧ UNCHANGED flock ∧ UNCHANGED httpOn

RecordPid(j) ==
    ∧ serverUp
    ∧ db[j] = "running"
    ∧ child[j]
    ∧ pid[j] = "none"
    ∧ pid' = [pid EXCEPT ![j] = "pid"]
    ∧ UNCHANGED db ∧ UNCHANGED child ∧ UNCHANGED adopted ∧ UNCHANGED released ∧ UNCHANGED cancels ∧ UNCHANGED serverUp ∧ UNCHANGED activeW ∧ UNCHANGED flock ∧ UNCHANGED httpOn

\* 进程退出可以在任何时刻发生，包括服务已经死了之后。
ExitChild(j) ==
    ∧ child[j]
    ∧ child' = [child EXCEPT ![j] = FALSE]
    ∧ (ChildHoldsLock ∧ flock = j ⇒ flock' = "free")
    ∧ (¬(ChildHoldsLock ∧ flock = j) ⇒ UNCHANGED flock)
    ∧ UNCHANGED db ∧ UNCHANGED pid ∧ UNCHANGED adopted ∧ UNCHANGED released ∧ UNCHANGED cancels ∧ UNCHANGED serverUp ∧ UNCHANGED activeW ∧ UNCHANGED httpOn

CancelQueued(j) ==
    ∧ serverUp
    ∧ db[j] = "queued"
    ∧ pid[j] = "none"
    ∧ db' = [db EXCEPT ![j] = "cancelled"]
    ∧ UNCHANGED pid ∧ UNCHANGED child ∧ UNCHANGED adopted ∧ UNCHANGED released ∧ UNCHANGED cancels ∧ UNCHANGED serverUp ∧ UNCHANGED activeW ∧ UNCHANGED flock ∧ UNCHANGED httpOn

\* 取消在跑的任务：只加内存标记并 SIGTERM，DB 里的 running 要到 _finalize 才动
\* （server.py:1074-1098）。
CancelRunning(j) ==
    ∧ serverUp
    ∧ db[j] = "running"
    ∧ child[j]
    ∧ cancels' = cancels ∪ {j}
    ∧ UNCHANGED db ∧ UNCHANGED pid ∧ UNCHANGED child ∧ UNCHANGED adopted ∧ UNCHANGED released ∧ UNCHANGED cancels ∧ UNCHANGED serverUp ∧ UNCHANGED activeW ∧ UNCHANGED flock ∧ UNCHANGED httpOn

\* release()：先关锁 fd、归还 active 槽位，终态后写（server.py:1288-1297, 1304-1316；
\* _finalize 在 1494-1500 先 release 再写 state）。
Release(j) ==
    ∧ serverUp
    ∧ j ∈ activeW
    ∧ db[j] = "running"
    ∧ ¬child[j]
    ∧ ¬adopted[j]
    ∧ flock = j
    ∧ released' = [released EXCEPT ![j] = TRUE]
    ∧ activeW' = activeW \ {j}
    ∧ flock' = "free"
    ∧ UNCHANGED db ∧ UNCHANGED pid ∧ UNCHANGED child ∧ UNCHANGED adopted ∧ UNCHANGED cancels ∧ UNCHANGED serverUp ∧ UNCHANGED httpOn

\* _finalize：取消赢过门槛（server.py:1437-1502）。终态写进 DB。
Finish(j) ==
    ∧ serverUp
    ∧ released[j]
    ∧ db[j] = "running"
    ∧ ¬adopted[j]
    ∧ ¬child[j]
    ∧ db' = [db EXCEPT ![j] = IF j ∈ cancels THEN "cancelled" ELSE "succeeded"]
    ∧ cancels' = cancels \ {j}
    ∧ released' = [released EXCEPT ![j] = FALSE]
    ∧ UNCHANGED pid ∧ UNCHANGED child ∧ UNCHANGED adopted ∧ UNCHANGED serverUp ∧ UNCHANGED activeW ∧ UNCHANGED flock ∧ UNCHANGED httpOn

\* 接管来的任务：等在飞的孩子退出或写锁释放后才 resume，resume 先拿锁再补跑剩下的步骤；
\* 单步任务直接 _finalize，拿不到退出码就 awaiting_review（server.py:1742-1770, 1805-1835）。
AdoptFinish(j) ==
    ∧ serverUp
    ∧ adopted[j]
    ∧ db[j] = "running"
    ∧ ¬child[j]
    ∧ (flock = "free" ∨ flock = j)
    ∧ db' = [db EXCEPT ![j] = IF j ∈ cancels THEN "cancelled" ELSE "awaiting_review"]
    ∧ cancels' = cancels \ {j}
    ∧ adopted' = [adopted EXCEPT ![j] = FALSE]
    ∧ activeW' = activeW \ {j}
    ∧ flock' = "free"
    ∧ UNCHANGED pid ∧ UNCHANGED child ∧ UNCHANGED released ∧ UNCHANGED serverUp ∧ UNCHANGED httpOn

\* HTTP 账表读改写：先查 writer_busy，再阻塞拿 .writer.lock（server.py:3983-3993,
\* 4010-4015；ledger_lock 5204-5208 只是包一层 lib_common.writer_lock，
\* 实现在 src/bookcorpus/scripts/lib_common.py:696-732）。
HttpStart ==
    ∧ serverUp
    ∧ ¬httpOn
    ∧ activeW = {}
    ∧ (∀ k ∈ Jobs : db[k] # "queued")
    ∧ flock = "free"
    ∧ flock' = "http"
    ∧ httpOn' = TRUE
    ∧ UNCHANGED db ∧ UNCHANGED pid ∧ UNCHANGED child ∧ UNCHANGED adopted ∧ UNCHANGED released ∧ UNCHANGED cancels ∧ UNCHANGED serverUp ∧ UNCHANGED activeW

HttpEnd ==
    ∧ httpOn
    ∧ flock' = "free"
    ∧ httpOn' = FALSE
    ∧ UNCHANGED db ∧ UNCHANGED pid ∧ UNCHANGED child ∧ UNCHANGED adopted ∧ UNCHANGED released ∧ UNCHANGED cancels ∧ UNCHANGED serverUp ∧ UNCHANGED activeW

\* 服务进程死掉：ChildHoldsLock=FALSE 时 flock 随进程释放；TRUE 时在跑的孩子继续持有锁。
Crash ==
    ∧ serverUp
    ∧ serverUp' = FALSE
    ∧ (ChildHoldsLock ∧ Children # {} ⇒ UNCHANGED flock)
    ∧ (¬(ChildHoldsLock ∧ Children # {}) ⇒ flock' = "free")
    ∧ activeW' = {}
    ∧ adopted' = [j ∈ Jobs |-> FALSE]
    ∧ released' = [j ∈ Jobs |-> FALSE]
    ∧ cancels' = {}
    ∧ httpOn' = FALSE
    ∧ UNCHANGED db ∧ UNCHANGED pid ∧ UNCHANGED child

\* 新进程启动：adopt() 依次核对 ops.db pid、jobs/<id>/pid、/proc 任务环境与 .writer.lock 占用
\* （server.py:1101-1123, 1201-1215）。认得上 → 接管并占位 active_write；否则标 orphaned，且不杀进程。
Start ==
    LET taken == {j ∈ Jobs : IsAdoptable(j)}
    IN
    ∧ ¬serverUp
    ∧ serverUp' = TRUE
    ∧ adopted' = [j ∈ Jobs |-> j ∈ taken]
    ∧ db' = [j ∈ Jobs |->
              IF db[j] = "running" ∧ j ∉ taken THEN "orphaned" ELSE db[j]]
    ∧ activeW' = taken
    ∧ (ChildHoldsLock ∧ Children # {} ⇒ UNCHANGED flock)
    ∧ (¬(ChildHoldsLock ∧ Children # {}) ⇒ flock' = "free")
    ∧ released' = [j ∈ Jobs |-> FALSE]
    ∧ cancels' = {}
    ∧ httpOn' = FALSE
    ∧ UNCHANGED pid ∧ UNCHANGED child

\* 收尾动作：所有任务都到终态且没有活孩子时，允许一个不改变状态的步，避免
\* TLC 把正常的结束态报成死锁（/statechart 的 NoDeadlock 约定）。
AllDone ==
    ∧ serverUp
    ∧ (∀ j ∈ Jobs : db[j] ∈ TERMINAL)
    ∧ Children = {}
    ∧ activeW = {}
    ∧ ¬httpOn

Done ==
    ∧ AllDone
    ∧ Unchanged

Next ==
    ∨ (∃ j ∈ Jobs : Submit(j))
    ∨ (∃ j ∈ Jobs : Dispatch(j))
    ∨ (∃ j ∈ Jobs : Acquire(j))
    ∨ (∃ j ∈ Jobs : MarkRunning(j))
    ∨ (∃ j ∈ Jobs : AbortDispatched(j))
    ∨ (∃ j ∈ Jobs : Spawn(j))
    ∨ (∃ j ∈ Jobs : RecordPid(j))
    ∨ (∃ j ∈ Jobs : ExitChild(j))
    ∨ (∃ j ∈ Jobs : CancelQueued(j))
    ∨ (∃ j ∈ Jobs : CancelRunning(j))
    ∨ (∃ j ∈ Jobs : Release(j))
    ∨ (∃ j ∈ Jobs : Finish(j))
    ∨ (∃ j ∈ Jobs : AdoptFinish(j))
    ∨ HttpStart
    ∨ HttpEnd
    ∨ Crash
    ∨ Start
    ∨ Done


\* ---------------- 规则 ----------------
\* 规则 1：写任务的孩子在跑，锁就必须在写任务手里（跨进程互斥的根据）。
WriterHoldsLock ==
    ∀ j ∈ Children : flock = j

\* 规则 2：同一时刻最多一个写任务的孩子进程。
NoTwoWriteChildren ==
    ∀ a, b ∈ Children : a = b

\* 规则 3：标成 orphaned 的任务，它的孩子进程必须已经不在。
NoLiveOrphan ==
    ∀ j ∈ Jobs : db[j] = "orphaned" ⇒ ¬child[j]

\* 规则 4：HTTP 账表读改写与写任务的孩子不重叠。
NoHttpWithChild ==
    ¬httpOn ∨ Children = {}

\* 规则 5：取消后的任务不再起进程（先给不变量做演示，当前代码应当通过）。
CancelledNeverRuns ==
    ∀ j ∈ Jobs : db[j] = "cancelled" ⇒ ¬child[j]

=============================================================================
