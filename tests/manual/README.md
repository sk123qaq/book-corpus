# 手工验收

这些脚本要 Chrome、真实模型或真实会话，不进 `uv run --frozen pytest`。每条都在自己的目录下直接跑，退出码 0 且末行 `ALL PASS` 才算过。

实例根默认 `~/文档/book-corpus-local`，可用 `BOOKS_ROOT` 覆盖；端口冲突时先停掉占用 8765/8770 的进程。

## ops

| 脚本 | 需要 | 判据 |
|---|---|---|
| `ops/shot_ops.py` | Chrome/Playwright、运行中的操作台 | 1440x900 与 390x844 两档截图，报 `CONSOLE ERROR 0` 与 `overflow=0` |
| `ops/repro-pdf-limit.py` | 真实 PDF 批次与 `.venv-pdf` | 复现 PDF 页数上限问题，打印命中页与退出码 |
| `ops/repro-triage-progress.py` | 真实批次账 | 复现分诊进度不前进，末行给判断 |

## demo

| 脚本 | 需要 | 判据 |
|---|---|---|
| `demo/check_demo.py` | 真实模型、真实索引 | 健康检查、两个示例的 trace、三体题的窗口与回答 |
| `demo/check_session.py` | 真实模型 | 多轮会话接口全过（约 29 条） |
| `demo/check_session_live.py` | 真实模型、运行中的演示页 | 会话追问的实时链路 |
| `demo/check_session_ui.py` | Chrome、运行中的演示页 | 会话页 UI 验收（约 56 条） |
| `demo/check_result_ms.py` | 真实模型 | 各阶段耗时与页脚数字一致 |
| `demo/shot.py` | Chrome、运行中的演示页 | 截图与横向溢出/控制台报错检查 |

离线入口在 `tests/acceptance/{ops,demo}/`，由 pytest 收集，不要在这里重复跑。
