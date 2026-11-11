# 找一段

演示页拿一句人话去书库里找段落：先拆词、换词循环、给书打分，再把候选书和命中章递给用户，
最后给出带依据的回答。页面有两个：默认打开的多轮会话页，和保留五阶段过程的 `/classic`。

## Sub-features

- `ask-open` 打开会话页，上一轮没跑完的回答标为中断。
- `ask-session` 新开会话、多轮追问、会话落盘。
- `ask-history` 历史页签：按问题筛选、多选删除。
- `ask-scope` 候选书多选：把检索限定到某几本，或全选。
- `ask-thinking` 思考等级（off / low / medium / high）。
- `ask-trace` 依据与落点：回答里的书卡、章节锚点、命中段原文。
- `ask-classic` `/classic` 五阶段：理解问题 → 词法循环 → 打分与选章 → 书卡 → 结果。
- `ask-export` 导出单个会话。

## How to get to it (user POV)

- 浏览器打开 `http://127.0.0.1:18765/`。
- 在输入框敲问题，按回车或点发送。
- 点页面上的示例问句（`button.sample[data-q]`）。
- 点结果里的书卡，或点历史页签翻旧会话。
- 想看过程：打开 `http://127.0.0.1:18765/classic`。

## Driving it with tests/manual/demo 的 CDP 脚本

Preconditions:

- 工作区按 [README](./README.md) 建好，`$ROOT/build/检索/index.db` 与 `$ROOT/build/library.db` 都存在。
- 演示页由本轮起：`BOOKS_ROOT=$ROOT python3 apps/demo/server.py --port 18765`，启动行是 `书库检索演示：http://127.0.0.1:18765`。
- 该页不接 `--root`，数据根只认 `BOOKS_ROOT`；不设就落到进程 cwd，`/api/health` 会答 `db: missing`。
- 真实作答要模型（`opencode-go/deepseek-v4.1-flash`，走 pi 的登录），单本 15-40 秒、多本 65-87 秒，硬超时 240 秒。

- **服务就绪。** `curl -s http://127.0.0.1:18765/api/health` 输出 `{"ok": true, "db": "ok", ...}`，`meta.books` 为 `1`、`meta.paras` 为 `123`。
- **两个页面都在。** `curl -s -o /dev/null -w '%{http_code} %{size_download}\n' http://127.0.0.1:18765/` 与同一命令打 `/classic`，都是 `200`。
- **会话列表。** 打开历史。`curl -s http://127.0.0.1:18765/api/sessions` 返回 `{"ok": true, "count": N, "sessions": [...]}`；每条带 `id`、`turns`、`question`。
- **五阶段骨架。** 打开经典页。`curl -s http://127.0.0.1:18765/classic | grep -c 'data-stage='` 输出 `5`，五段标题依次是「理解问题」「词法循环」「打分与选章」「书卡」「结果」。
- **示例问句。** 看输入区下方。页面含 `data-q="礼治社会靠什么维持秩序"`、`data-q="内卷和教育之间是什么关系"`、`data-q="乡土中国 差序格局"`。
- **真实作答。** 输入问题并发送，等结果卡片出现。`python3 tests/manual/demo/shot.py --url 'http://127.0.0.1:18765/classic?q=%E4%BD%A0%E9%82%A3%E9%87%8C%E9%85%8D%E5%A7%93%E8%B5%B5&instant=1&top=1' --out /tmp/demo-classic` 会开一次真问题，末行形如 `CONSOLE ERROR 0`。本次未跑：要一次模型调用。
- **历史 UI。** 在既有会话上点历史、筛选、选中：「`python3 tests/manual/demo/check_session_ui.py --base http://127.0.0.1:18765`」逐项打「过/败」，末行 `ALL PASS`。它按 `/api/sessions` 里「3 轮会话」和「最后一轮带多书 sources」动态选样本，临时根上没有这种会话就会失败。
- **不留外部请求。** `check_session_ui.py` 同时断言控制台 0 错误、页面不向外域发请求。

## Gotchas

- 会话库在仓库内 `apps/demo/缓存/sessions/`，`BOOKS_ROOT` 只管书、不管会话：临时根的新实例照样列出操作员的会话。驱动会话会写进这份共享库，验证前先确认能接受，或先备份该目录。
- `缓存/terms`、`缓存/answers` 同样在仓库内，会跨实例命中；换问题验缓存时先看清命中的是哪一次。
- `tests/manual/demo/shot.py` 默认打 `http://127.0.0.1:8765/classic`，也就是操作员实例。必须显式 `--url` 指到本轮端口。
- `tests/manual/demo/check_session_ui.py` 的 `SESSION` / `SESSION_MULTI` 常量是兜底 id，没有匹配会话时报的是「找不到会话」，不是页面坏了。
- 拆词阶段本身就要模型调用，3-5 秒；没有 key 时页面报的是配置错误，不是空结果。
- 作答是长请求：单本 15-40 秒、六本 65-87 秒。断言要等结果卡片，不要固定 sleep。
- `/classic` 的 `?q=&instant=1&top=1` 是自动跑一条问题的入口，链接一打开就花一次模型调用。
