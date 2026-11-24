# OpsRunnerSpec

TLC reached 664 states, more than the chart budget of 500, so the chart draws only the bug paths: 8 states and 9 arrows.

## WriterHoldsLock breaks
The shortest path has 6 steps:
0. 空闲
1. 操作人再提交一个写任务: 写任务排队
2. 调度器起写任务: 写任务排队
3. 拿到工作区写锁: 写任务排队
4. 任务开始跑: 任务已置运行 还没起进程
5. 拉起写进程: 写任务在跑 持锁
6. 服务进程死掉: 服务中断 孩子在跑

## NoLiveOrphan breaks
The shortest path has 7 steps:
0. 空闲
1. 操作人再提交一个写任务: 写任务排队
2. 调度器起写任务: 写任务排队
3. 拿到工作区写锁: 写任务排队
4. 任务开始跑: 任务已置运行 还没起进程
5. 拉起写进程: 写任务在跑 持锁
6. 服务进程死掉: 服务中断 孩子在跑
7. 新服务启动 接管残留: 孤儿任务 孩子还在写 锁不在它手里

## NoHttpWithChild breaks
The shortest path has 8 steps:
0. 空闲
1. 操作人再提交一个写任务: 写任务排队
2. 调度器起写任务: 写任务排队
3. 拿到工作区写锁: 写任务排队
4. 任务开始跑: 任务已置运行 还没起进程
5. 拉起写进程: 写任务在跑 持锁
6. 服务进程死掉: 服务中断 孩子在跑
7. 新服务启动 接管残留: 孤儿任务 孩子还在写 锁不在它手里
8. 操作台写账表: 账表读改写中 孩子在写

## NoTwoWriteChildren breaks
The shortest path has 12 steps:
0. 空闲
1. 操作人再提交一个写任务: 写任务排队
2. 调度器起写任务: 写任务排队
3. 拿到工作区写锁: 写任务排队
4. 任务开始跑: 任务已置运行 还没起进程
5. 拉起写进程: 写任务在跑 持锁
6. 服务进程死掉: 服务中断 孩子在跑
7. 新服务启动 接管残留: 孤儿任务 孩子还在写 锁不在它手里
8. 操作人再提交一个写任务: 孤儿任务 孩子还在写 锁不在它手里
9. 调度器起写任务: 孤儿任务 孩子还在写 锁不在它手里
10. 拿到工作区写锁: 孤儿任务 孩子还在写 锁不在它手里
11. 任务开始跑: 孤儿任务 孩子还在写 锁不在它手里
12. 拉起写进程: 两个写者同时在写

## Rules that hold
CancelledNeverRuns
