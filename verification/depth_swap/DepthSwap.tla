---------------------------- MODULE DepthSwap ----------------------------
\* Store.save_depth 在两次 os.replace 之间换掉整包 depth/（src/bookcorpus/reading/store.py:225-242）：
\*
\*   store.py:169  os.replace(target, backup)   depth/ -> .depth-bak-<8hex>/
\*   store.py:170  os.replace(stage, target)    .depth-<rand>/ -> depth/
\*
\* 两次之间的硬崩溃让盘上停在 target = None、backup = OLD、stage = NewPkg。
\* depth_state 只读 depth/state.json（store.py:259-292），于是报 "missing"；
\* chapter_route 落到 ("none", "尚未生成")（store.py:307-326）。重启不自愈：
\* _heal_swap_crash（store.py:216-223）只在 save_depth（store.py:232）里调用，
\* 要等下一次保存才把备份放回去。
\*
\* target  depth/               读者真正打开的包
\* backup  .depth-bak-<8hex>/   旧包，只有 _heal_swap_crash 会读
\* stage   .depth-<rand>/       正在写入的新包
\*
\* hadReady 记住保存开始前有可服务的包，是模型里的「用户本来能读」；
\* afterSave 记住自愈已经跑过一次。
\* -----------------------------------------------------------------------------
EXTENDS Naturals

None == "none"
OLD == "old"
NewPkg == "new"  \* TLA+ 保留字 NEW 不能当标识符，这里用 NewPkg

VARIABLES target, backup, stage, crashed, hadReady, afterSave

vars == <<target, backup, stage, crashed, hadReady, afterSave>>

Init ==
    /\ target = OLD
    /\ backup = None
    /\ stage = None
    /\ crashed = FALSE
    /\ hadReady = FALSE
    /\ afterSave = FALSE

\* store.py:233-237：新 stage 从旧 depth/ 的 cache 起步，再写新文件。
\* 保存开始前 target = OLD 表示用户此刻能读。
SaveBegins ==
    /\ ~crashed
    /\ stage' = NewPkg
    /\ hadReady' = (hadReady \/ (target = OLD))
    /\ UNCHANGED <<target, backup, crashed, afterSave>>

\* store.py:169：旧包离开 depth/，改名成备份。stage 必须已经写好：
\* _swap_dir 只在 _write_depth_files 成功之后才被调用（store.py:237-238）。
MoveTargetToBackup ==
    /\ ~crashed
    /\ target = OLD
    /\ stage = NewPkg
    /\ backup' = OLD
    /\ target' = None
    /\ UNCHANGED <<stage, crashed, hadReady, afterSave>>

\* store.py:170：新包补进 depth/。target 必须已经让位，对应 store.py:168-170
\* 先 replace(target, backup) 再 replace(stage, target) 的次序。
MoveStageToTarget ==
    /\ ~crashed
    /\ stage = NewPkg
    /\ target = None
    /\ target' = NewPkg
    /\ stage' = None
    /\ UNCHANGED <<backup, crashed, hadReady, afterSave>>

\* store.py:171-172：换成功之后删备份。
DropBackup ==
    /\ ~crashed
    /\ backup' = None
    /\ UNCHANGED <<target, stage, crashed, hadReady, afterSave>>

\* 两次 replace 之间的硬崩溃。崩溃后没有定时器、没有 watcher、没有重启修复。
Crash ==
    /\ ~crashed
    /\ crashed' = TRUE
    /\ UNCHANGED <<target, backup, stage, hadReady, afterSave>>

\* store.py:216-223，只从 store.py:232 调用。下一次保存把备份放回去（target = OLD），
\* 或者用新包完成（target = NewPkg）；两条路都清掉备份。
NextSaveHeals ==
    /\ crashed
    /\ target' \in {OLD, NewPkg}
    /\ afterSave' = TRUE
    /\ backup' = None
    /\ UNCHANGED <<stage, crashed, hadReady>>

Next ==
    \/ SaveBegins
    \/ MoveTargetToBackup
    \/ MoveStageToTarget
    \/ DropBackup
    \/ Crash
    \/ NextSaveHeals

Spec == Init /\ [][Next]_vars

\* NoDataLoss：旧包或新包至少一份在盘上，换目录不丢数据。
NoDataLoss ==
    target # None \/ backup = OLD \/ stage = NewPkg

\* NeverLoseReadable：一旦有可服务的包，depth/ 就该一直在。
\* 读者只看 target，backup 与 stage 对读者不可见。这条比崩溃更严：
\* MoveTargetToBackup 一步就违反，因为窗口在第一次 replace 时已经打开。
NeverLoseReadable ==
    hadReady => target # None

\* CrashKeepsReadable：崩在换目录窗口里时，读者仍要能打开一个包。
\* 这是用户看得见的那一条，也是本次 finding 的本体：
\* MoveTargetToBackup 之后 Crash，target 停在 None，备份要等下一次保存才回去。
CrashKeepsReadable ==
    crashed => target # None

\* RecoverAfterSave：下一次保存自愈之后，depth/ 一定在。
RecoverAfterSave ==
    afterSave => target # None

=============================================================================
