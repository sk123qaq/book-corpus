# 找一段

演示页拿一句人话去书库里找段落：先拆词、换词循环、给书打分，再把候选书和命中章递给用户，
最后给出带依据的回答。页面有两个：默认打开的多轮会话页，和保留五阶段过程的 `/classic`。

## Sub-features

- `ask-open` 打开会话页，上一轮没跑完的回答标为中断。
- `ask-session` 新开会话、多轮追问、会话落盘。
- `ask-history` 历史页签：按问题筛选、多选删除。
- `ask-scope` 候选书多选：把检索限定到某几本，或全选。
- `ask-thinking` 思考等级（off / low / medium / high）**只在经典检索页 `/classic`**（控件 `think-select`）。会话页没有这个控件：它把 `THINKING_LEVEL = 'high'` 写死（`apps/demo/web/session.js:15`，提交在 `:1852`/`:1939`/`:1987`），只在结果 meta 里显示「思考 高」。想在会话页省 token 或换档，代码里没开关。
- `ask-trace` 依据与落点：回答里的书卡、章节锚点、命中段原文。
- `ask-classic` `/classic` 五阶段：理解问题 → 词法循环 → 打分与选章 → 书卡 → 结果。
- `ask-export` 导出单个会话。
- `ask-degrade` 没有可用的模型时降级：拆词阶段记「模型返回不可用（rc=N）」，经典页标「降级」并按概念表包含匹配。

## How to get to it (user POV)

- 浏览器打开 `http://127.0.0.1:18765/`（多轮会话页）。
- 在输入框敲问题，按回车或点发送。
- 想看过程：打开 `http://127.0.0.1:18765/classic`；**示例问句只在这个页面上**。
- 点结果里的书卡，或点历史页签翻旧会话。

## Driving it with curl + tests/manual/demo 的 CDP 脚本

Preconditions:

- 工作区按 [README](./README.md) 建好，`$ROOT/build/检索/index.db` 与 `$ROOT/build/library.db` 都存在。
- 演示页由本轮起：`BOOKS_ROOT=$ROOT python3 apps/demo/server.py --port 18765`，启动行是 `书库检索演示：http://127.0.0.1:18765`。
- 该页不接 `--root`，数据根按 `BOOKS_ROOT` → 向上找 `books.toml` → 进程 cwd 的顺序解析；解析不到时 `/api/health` 答 `db: missing`。
- 真实作答要模型：`ANSWER_MODEL` 环境变量 / `books.toml` 的 `[model]` / 内置默认，走 pi 的登录；单本 15-40 秒、多本 65-87 秒。
- 时间上限有两层：pi 子进程 240 秒，HTTP 等待 300 秒，超了页面/接口回 `504 回答仍在生成，请稍后刷新`。

- **服务就绪。** `curl -s http://127.0.0.1:18765/api/health` 输出 `{"ok": true, "db": "ok", "meta": {"books": 1, "paras": 123, ...}}`。
- **两个页面都在。** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18765/` 与同一命令打 `/classic`，都是 `200`。
- **示例问句只在经典页。** `curl -s http://127.0.0.1:18765/ | grep -c 'class="sample"'` 输出 `0`；`curl -s http://127.0.0.1:18765/classic | grep -c 'data-q='` 输出 `3`，三条是 `礼治社会靠什么维持秩序`、`内卷和教育之间是什么关系`、`乡土中国 差序格局`。
- **五阶段骨架。** `curl -s http://127.0.0.1:18765/classic | grep -c 'data-stage='` 输出 `5`，五段标题依次是「理解问题」「词法循环」「打分与选章」「书卡」「结果」。
- **会话列表。** `curl -s http://127.0.0.1:18765/api/sessions` 返回 `{"ok": true, "count": N, "sessions": [...]}`；每条带 `id`、`turns`、`question`、`answer_state`、`answer_error`、`books`、`last_question`、`updated_at`。**临时根上 N 不为 0**：本轮是 `7`（2026-11-14），全是操作员的会话；N 随操作员使用变化，别写死。
- **真实作答。** 输入问题并发送，等结果卡片出现。`python3 tests/manual/demo/shot.py --url 'http://127.0.0.1:18765/classic?q=…&instant=1&top=1'` 会开一次真问题，末行形如 `CONSOLE ERROR 0`。本次未跑：要一次模型调用。
- **历史 UI。** `python3 tests/manual/demo/check_session_ui.py --base http://127.0.0.1:18765` 逐项打「过/败」，末行 `ALL PASS`。它按 `/api/sessions` 里「3 轮会话」和「最后一轮带多书 sources」动态选样本；找不到时分别是 `没有找到可用的 3 轮会话；先用 check_session.py 生成一轮` 与 `没有找到带 2 本以上 sources 的多书会话…`（exit 1），这是样本缺失，不是页面坏了。
- **不留外部请求。** `check_session_ui.py` 同时断言控制台 0 错误、页面不向外域发请求。
- **回答合同。** 2026-11-12 晚起回答阶段收成单合同（`apps/demo/answer.py` 只走 `answer_question`，不再主答/补充双写），离线钉这份合同的是 `uv run --frozen pytest -q tests/test_demo_answer_contract.py`，全过；旧的 `tests/acceptance/demo/test_answer_anchored.py` 已删，别再按它打命令。
- **Host。** `curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: example.com' http://127.0.0.1:18765/` 输出 `403`（2026-11-12 晚补的 `ALLOWED_HOST`；之前这里是 200，旧文档的 200 已作废）。

## Gotchas

- 会话库在仓库内 `apps/demo/缓存/sessions/`，`BOOKS_ROOT` 只管书、不管会话：临时根的新实例照样列出操作员的会话（2026-11-14 为 7 条，随使用变化）。驱动会话会写进这份共享库，验证前先确认能接受，或先备份该目录。
- `缓存/terms`、`缓存/answers` 同样在仓库内，会跨实例命中；换问题验缓存时先看清命中的是哪一次。
- `tests/manual/demo/shot.py` 默认打 `http://127.0.0.1:8765/classic`，也就是操作员实例。必须显式 `--url` 指到本轮端口。
- `tests/manual/demo/check_session_ui.py` 的 `SESSION` / `SESSION_MULTI` 常量每次运行都会被覆盖，不是「优先用环境变量」；没有匹配会话时报的是「找不到会话」，不是页面坏了。2026-11-14 本机只有 1 轮会话，脚本止于 `没有找到可用的 3 轮会话；先用 check_session.py 生成一轮`；这是样本缺失，离线合同仍应单跑 `tests/test_demo_answer_contract.py`。
- 示例问句只在 `/classic`；在 `/` 上 `grep 'class="sample"'` 得 0，是正常的，不是模板没渲染。
- 没有模型 key 时，演示页**不会**报「配置错误」：拆词阶段记「模型返回不可用（rc=N）」，经典页标「降级」并改用概念表包含匹配。别拿「配置错误」这四个字做断言。（配置预检只在分层读与 CLI 上做，演示页没有。）
- 作答是长请求：单本 15-40 秒、多本 65-87 秒。断言要等结果卡片，不要固定 sleep；超过 300 秒会拿到 504，不是空回答。
- `/classic` 的 `?q=&instant=1&top=1` 是自动跑一条问题的入口，链接一打开就花一次模型调用。
