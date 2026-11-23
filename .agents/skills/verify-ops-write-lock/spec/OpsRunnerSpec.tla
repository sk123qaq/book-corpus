---- MODULE OpsRunnerSpec ----
\* 只放时序公式。TLA+ 的 □[A]_v 需要一个变量元组，而 /statechart 的读取器
\* 目前遇到元组会崩，所以把 Spec 留在扩展模块里，模型与规则仍只在 OpsRunner.tla。
EXTENDS OpsRunner

vars == <<db, pid, child, adopted, released, cancels, serverUp, activeW, flock, httpOn>>

Spec == Init ∧ □[Next]_vars

====
