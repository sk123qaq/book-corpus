# 书库术语表（GLOSSARY）

状态：living · 2026-11-22
失效源：.agents/skills/verify-book-corpus/features/*.md src/bookcorpus/reading/book_types.py apps/reading/server.py apps/ops/server.py apps/demo/server.py

本表定义 `book-corpus` 五个本地应用与阅读/检索/运维链路的现行术语，口径以 `.agents/skills/verify-book-corpus/features/*.md` 为准。书库目录与账本词（`uid`、人账、机账、批次、拆章、存档、冷宫）另见 `AGENTS.md`「文件地图」。

## 五个本地应用

- **书库首页（`apps/home`，127.0.0.1:8773）**：默认唯一常驻入口，服务端渲染三张入口卡（找一段 / 翻开书 / 往期日课）、右栏最新一期与页脚「管理」，通过 `/go/<key>` 按需拉起其余四个服务。
- **找一段（`apps/demo`，127.0.0.1:8765）**：书库检索与流式问答页。默认 `/` 是多轮会话页（纸昼/墨夜双主题，不设 `/classic` 入口），手动访问 `/classic` 保留无状态五阶段过程页（发 `stateless:true`，不建会话）。支持 SSE 流式输出、进程内 checkpoint 暂停/续答、会话导出（MD / DOCX）与 `mode=trace|prefill|related`（跨书按相关性降序再按阈值筛选）跨页入口。
- **伴读（`apps/reading`，127.0.0.1:8771，原称「分层读」）**：正文伴读工作台。包含书架（`/`）、书目（`/catalog`）、书页（`/book/<uid>`）、章页（`/chapter/<uid>/<n>`）与操作日志（`/records`），外加跨页定位入口 `/open`。书架与首页书目按 `READING_SHELF_TTL` 重新核对盘上（默认 2 秒），新落地的书不用重启。
- **往期日课（`apps/daily`，127.0.0.1:8772）**：每日一章的只读阅读页，读取 `<root>/日课/<YYYY-MM-DD>.json`，提供「在伴读打开这一章」跳转。
- **统一操作台（`apps/ops`，127.0.0.1:8770）**：本地运维控制台，收口为六个主模块（总览、新书入库、书目与分类、正文生产、同步与维护、任务与日志）及六个兼容子页签（`docs` / `pdf` / `pdf-list` / `manual` / `retire` / `cold`）。

## 阅读工作台（伴读）

- **正文工作台**：章页打开即 0 秒直出 100% 原文，不设阻塞阅读的工单或审批。正文经 `mdparse` 切分为带稳定编号 `B%02d`（DOM `#blk-B01`）与原文件行号（`start_line`/`end_line`）的块。
- **脚注跳转与跨文件直达**：`mdparse` 与 `src/bookcorpus/reading/footnotes.py` 解析 EPUB/PDF 转档残留的各类行内注标与注释定义，在 `/api/chapter` 返回块内 `fn_refs` / `item_fn_refs` 与章级 `footnotes`。章内注点击上标滚至对应注文（`.reader-footnote-item`），经底部浮条（`#fn-return-bar`）或注段内「↩ 返回正文」回到原注释点；正文与注释分属不同文件时弹出 `#fn-tip-popover`，可定位具体注释块时经 `?from=footnote&fn=...&src_n=...#blk-<id>` 新标签直达目标块并显示「来自正文注释」来源条。
- **阅读视角（`reading_profile`）**：由 `src/bookcorpus/reading/book_types.py` 按人账学科（`book.json` 的 `subjects`）即时推导出的 7 类阅读策略：`practical`（实用型书）、`literature`（想象文学，含 `fiction/drama/poetry` 子类）、`history`（历史书）、`science`（科学与数学）、`philosophy`（哲学书）、`social`（社会科学，兼兜底）、`visual`（图册与视觉）。读者可在章页 `#profile-select` 临时切换（存浏览器 `localStorage`，不写回 `tables/ledger.db`）。
- **结构蓝图（Blueprint）**：跨 7 类阅读视角共 18 种强类型图表骨架（DAG / 矩阵 / 曲线三家族），走五步防重叠流水线（蓝图 Schema 约束 → 模型填纯语义 JSON → `diagram_audit.py` 审计与超长分流 → `diagram_renderer.js` 离屏测量与正交避障渲染 → 实时几何碰撞质检）。所有节点带 `Bxx` 原文锚点，点击可回跳正文；支持 `#zoom-modal` 宽屏全览。全章缓存落 `工作区/阅读/<uid>/ch/<nn>/chapter_tools/`。
- **视角扩展（Extension Action）**：按阅读视角置顶的 7 个专属伴读动作（`checklist`、`imagery`、`compare_context`、`boundary_check`、`sparring_dossier`、`method_audit`、`craft_notes`），复用 `POST /api/assist`。其中 `compare_context` 与 `method_audit` 带 `cross_search:true`，在工具条提供「跨书对照 ↗」跳往 8765。
- **按需知识地图（Chapter Map）**：经 `POST /api/chapter-map/<uid>/<n>` 按需生成的章节结构树与核心术语，落 `工作区/阅读/<uid>/ch/<nn>/chapter_map.json`。源文件哈希变化时只在读取时标内存态 `stale:true`，不覆盖原文件。
- **短章门控与容量预检（Preflight）**：`/api/chapter` 返回两套独立预检。`preflight.chapter_map` 拦截过短章节（支持 `force:true` 强制生成）；`preflight.capacity` 在超长章节触发 422 `source_too_large`，只影响知识地图生成，不阻断直读正文。
- **划词伴读（Assist）**：`POST /api/assist`，支持 `selection` / `chapter` 两种作用域、5 个基础动作（`explain` / `translate` / `diagram` / `critique` / `ask`）与 7 个视角扩展动作；默认 SSE 流式返回（`delta` / `diagram` / `done` / `error`）。划词条另附「跨书找相关 ↗」直达 8765 的 `mode=related`。
- **旁批与钉住（Pins）**：经 `POST /api/assist/pin` 将伴读结果锚定到段落末尾，持久化在 `工作区/阅读/<uid>/ch/<nn>/assist_notes.json`；删除走 `DELETE /api/assist/pin/<uid>/<n>/<id>`。
- **跨页定位（`/open`）**：从 8765 找一段或 8772 往期日课携带 `uid`、`path`、`line`、`q`、`from` 跳转到对应章页，渲染顶部来源条 `#arrival-banner` 并高亮命中块（`.reader-block.hit`、`mark.hl-hit`）。
- **操作日志（Records）**：追加写入 `工作区/阅读/ops_log.jsonl`，在 `/records` 按全部、知识地图、划词伴读、旁批变更、失败五个页签检索，支持「定位 →」回跳段落。

## 检索与同步

- **书级规划（`book-corpus query`）**：`book_query.py` 的默认书级入口，采用 `best_support` 与 B2.5 最多 5 轮词法循环，触发时默认弹 5–8 张书卡（`--no-cards` 关闭）。`book-corpus search --book` 保留旧 `mix` 口径作对照。
- **混合检索（Hybrid Retrieval）**：在 FTS5 CJK bigram 词法召回之上叠加 `book-corpus embed` 产出的块向量（`emb.db` + `vectors.f16`）与本地 embedding 服务，做书/章/段三层 RRF 融合（`fusion:"hybrid"`）；索引或服务缺失时静默退回 `bm25_only`。有 GPU 且精排服务在线时可叠加 `bge-reranker-v2-m3`。
- **事件驱动增量同步（`sync_delta`）**：操作台在入库、改分类、拆章、文档转换、PDF 落地或退库完成后，自动接力元数据重建、FTS5 增量同步（`build_para_index.py --sync`）、异步合并去重的向量同步（`embed_corpus.py --sync`）与后台 `last-audit.json` 体检刷新；向量库未初始化时跳过向量步骤。仅暂存复核与重启未知退出码停在 `awaiting_review`（可经 `POST /api/jobs/<id>/resolve` 人工收口）。`/api/status` 通过 `sync_delta` 暴露各层偏差供「同步与维护」兜底。

## 已退役术语

下列概念与路由均已下线，不在现行代码与页面中存在；历史背景见对应文档，退役词表由 `docs/reference/retired.toml` 守卫：

- **四读 / 三档分层读（`skim` / `standard` / `deep`）/ `research` 档与 `citations` 层**：已退役（2026-11-11、2026-11-16），替代见 [阅读页重构方案](docs/design/阅读页重构-正文伴读与工具台-2026-11-15.md) 与 [ADR 0001](docs/adr/0001-four-reads-two-body.md)、[ADR 0002](docs/adr/0002-three-tier-reading.md)。
- **视觉读本（visual edition）与 `/reader/<uid>/<n>/edition`**：已退役（2026-11-16），历史说明见 [视觉读本](docs/design/视觉读本.md)。
- **读法（reading mode）与 `tables/ledger.db` 的 `读法` 表、面板（panels）、场景速览（scene skim）**：已退役（2026-11-16），旧阻塞式工单已移除；按书型差异化工具改由不改人账的「阅读视角」承接，见 [分层读书型工具与图表框架方案](docs/design/分层读书型工具与图表框架-方案-2026-11-19.md) 与 [ADR 0003](docs/adr/0003-reading-modes-and-panels.md)。
- **读本页、`/reader/...`、`/api/depth/...` 与阅读侧工单队列 `/api/jobs`**：已退役（2026-11-16），路由一律 404。
- **研究档外部搜索配置（`READING_SEARCH_PROVIDER` / `READING_FETCH_PROVIDER`）**：已退役（2026-11-16），原文档 [研究检索配置](docs/design/研究检索配置.md) 已标 `superseded`。
- **操作台旧七页签 / 八页签导航与手动「一键收尾」主流程**：已退役（2026-11-19），替代见 [操作台重构方案](docs/design/操作台重构-事件驱动全自动同步方案-2026-11-19.md)。
- **删书硬链接快照门槛（`books.snapshot-<日期>`、`--no-snapshot`、「回滚副本」）**：已退役（2026-11-20），退库改以冷宫 `books/_冷宫/` 与 `retired.tsv` 留档，不再依赖库外硬链接快照目录；两者是否对齐由 `GET /api/cold` 核验、`POST /api/cold/reconcile` 按实物校正（缺失行保留并记 `last_checked`）。
