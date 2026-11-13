---------------------------- MODULE DepthSwap ----------------------------
\* Store.save_depth 在两次 os.replace 之间换掉整包 depth/（src/bookcorpus/reading/store.py:225-242）：
\*
\*   store.py:169  os.replace(target, backup)   depth/ -> .depth-bak-<8hex>/
\*   store.py:170  os.replace(stage, target)    .depth-<rand>/ -> depth/
\*
\* 两次之间的硬崩溃让盘上停在 target = None、backup = OLD、stage = NewPkg。
\* depth_state 只读 depth/state.json（store.py:259-292），于是报 "missing"；
\* chapter_route 落到 ("none", "尚未生成")（store.py:307-326）。
\*
\* 进程死掉以后还能救回来，靠的是启动时扫一遍章节目录：备份还在、depth/
\* 不在，就把备份放回去，顺手清掉没写完的 stage。模型里这一步是 Boot
\* （store.py 的 heal_pending_swaps，由 ReadingService.__init__ 调用）。Boot
\* 只认 .depth-bak-* 前缀，因为那是换目录留下的名字；.depth-<rand> 是半成品，
\* 直接删。下一次保存开头的那次 heal（store.py:232）不单独建模：崩溃把进程
\* 杀掉之后，能再保存的前提就是重启过，Boot 已经跑在前面。
\*
\* target  depth/               读者真正打开的包
\* backup  .depth-bak-<8hex>/   旧包，只有 Boot 会读
\* stage   .depth-<rand>/       正在写入的新包
\*
\* hadReady 记住保存开始前有可服务的包，是模型里的「用户本来能读」。
\* booted 表示进程过完启动自愈、开始对外服务；崩溃把它打回 FALSE。
\* pending 记「崩溃那一刻该有包却没有」，Boot 放回备份或一次保存换完才清掉。
\* -----------------------------------------------------------------------------
EXTENDS Naturals

None == "none"
OLD == "old"
NewPkg == "new"  \* TLA+ 保留字 NEW 不能当标识符，这里用 NewPkg

VARIABLES target, backup, stage, crashed, hadReady, booted, pending

vars == <<target, backup, stage, crashed, hadReady, booted, pending>>

Init ==
    /\ target = OLD
    /\ backup = None
    /\ stage = None
    /\ crashed = FALSE
    /\ hadReady = FALSE
    /\ booted = FALSE
    /\ pending = FALSE

\* store.py 的 heal_pending_swaps：备份在而 depth/ 不在就放回去，然后清掉遗留的
\* stage。正常启动也走这一步，只是没有备份可放、没有 stage 可清。
\* 放不回去（备份也没了）就把 pending 留着，BootedClean 会报出来。
Boot ==
    /\ ~booted
    /\ target' = IF target = None /\ backup = OLD THEN OLD ELSE target
    /\ backup' = IF target = None /\ backup = OLD THEN None ELSE backup
    /\ stage' = None
    /\ crashed' = FALSE
    /\ booted' = TRUE
    /\ pending' = (target' = None)
    /\ UNCHANGED <<hadReady>>

\* store.py:233-237：新 stage 从旧 depth/ 的 cache 起步，再写新文件。
\* 保存开始前 target = OLD 表示用户此刻能读。写盘只发生在活着的进程里，
\* 所以要求 booted。
SaveBegins ==
    /\ booted
    /\ ~crashed
    /\ stage' = NewPkg
    /\ hadReady' = (hadReady \/ (target = OLD))
    /\ UNCHANGED <<target, backup, crashed, booted, pending>>

\* store.py:169：旧包离开 depth/，改名成备份。stage 必须已经写好：
\* _swap_dir 只在 _write_depth_files 成功之后才被调用（store.py:237-238）。
MoveTargetToBackup ==
    /\ booted
    /\ ~crashed
    /\ target = OLD
    /\ stage = NewPkg
    /\ backup' = OLD
    /\ target' = None
    /\ UNCHANGED <<stage, crashed, hadReady, booted, pending>>

\* store.py:170：新包补进 depth/。target 必须已经让位，对应 store.py:168-170
\* 先 replace(target, backup) 再 replace(stage, target) 的次序。
MoveStageToTarget ==
    /\ booted
    /\ ~crashed
    /\ stage = NewPkg
    /\ target = None
    /\ target' = NewPkg
    /\ stage' = None
    /\ pending' = FALSE
    /\ UNCHANGED <<backup, crashed, hadReady, booted>>

\* store.py:171-172：换成功之后删备份。删的时候 depth/ 必须已经在，否则删掉的
\* 就是唯一一份包（代码里这两步在同一个 _swap_dir 里，中间不会留出这个状态）。
DropBackup ==
    /\ booted
    /\ ~crashed
    /\ target # None
    /\ backup' = None
    /\ UNCHANGED <<target, stage, crashed, hadReady, booted, pending>>

\* 两次 replace 之间的硬崩溃。进程死了就不再对外服务，booted 回到 FALSE；
\* 重启要走 Boot 才能再服务，这正是启动自愈的入口。
Crash ==
    /\ ~crashed
    /\ crashed' = TRUE
    /\ booted' = FALSE
    /\ pending' = (target = None)
    /\ UNCHANGED <<target, backup, stage, hadReady>>

Next ==
    \/ Boot
    \/ SaveBegins
    \/ MoveTargetToBackup
    \/ MoveStageToTarget
    \/ DropBackup
    \/ Crash

Spec == Init /\ [][Next]_vars

\* NoDataLoss：旧包或新包至少一份在盘上，换目录不丢数据。
NoDataLoss ==
    target # None \/ backup = OLD \/ stage = NewPkg

\* CrashKeepsReadable：崩在换目录窗口里时，读者仍要能打开一个包。
\* 这是崩溃当下的那一条，启动自愈救不了它：Crash 之后、Boot 之前，
\* target 停在 None。留着它是为了锁住窗口本身，不要把它当成可修的 bug。
CrashKeepsReadable ==
    crashed => target # None

\* NeverLoseReadable：一旦有可服务的包，depth/ 就该一直在。
\* 读者只看 target，backup 与 stage 对读者不可见。这条比崩溃更严：
\* MoveTargetToBackup 一步就违反，因为窗口在第一次 replace 时已经打开。
NeverLoseReadable ==
    hadReady => target # None

\* BootedClean：过完启动自愈、开始对外服务时，不能还欠着崩溃留下的账。
\* pending 在 Crash 那一刻记下「target 是空的」，Boot 把备份放回去才算清；
\* 保存进行中的空窗不记这笔账，所以正常换目录不会误报。
BootedClean ==
    booted => ~pending

=============================================================================
