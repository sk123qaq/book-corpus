# 检索层（可重建产物，非人账）

状态：living · 2026-11-20
失效源：src/bookcorpus/scripts/qsearch.py src/bookcorpus/scripts/book_query.py src/bookcorpus/scripts/vec_search.py src/bookcorpus/scripts/embed_corpus.py src/bookcorpus/scripts/build_para_index.py src/bookcorpus/scripts/rerank*.py

检索层是 `build/检索/` 下的可重建产物，只读 `books/<uid>/md/`，绝不写正文或人账。删掉整目录不影响任何书的可用性。

用户可见的 CLI 行为与逐条验证命令见：

- [.agents/skills/verify-book-corpus/features/search-query.md](../../.agents/skills/verify-book-corpus/features/search-query.md)（`book-corpus search` / `query`）
- [.agents/skills/verify-book-corpus/features/embed.md](../../.agents/skills/verify-book-corpus/features/embed.md)（`book-corpus embed`）
- [.agents/skills/verify-book-corpus/features/find-a-passage.md](../../.agents/skills/verify-book-corpus/features/find-a-passage.md)（8765 找一段）

## 现行产物

| 路径 | 内容 | 构建入口 |
|---|---|---|
| `build/检索/index.db` | 段落倒排（SQLite FTS5 CJK bigram + unigram）、概念剖面、`expand` 扩展文本与 `indexed_book` 增量跟踪表 | `book-corpus index --rebuild` / `--sync` |
| `build/检索/expand/<uid>.md` | 章级上下文前缀 / 伪查询的人读账（索引文本，**不是原文证据**） | `book-corpus run build_expand` |
| `build/检索/embeddings/emb.db`、`vectors.f16` | 全库 1024 维 float16 块向量，断点续跑、单写者锁（可选增强，设计见 [全库 embedding](../design/全库embedding.md)） | `book-corpus embed` |

人账不在本目录：概念表与动作词在 `tables/ledger.db`；评测题集与历史批次账在实例根的 `工作区/批次/` 下。

## 现行检索链路

1. **词法召回（必选）**：`src/bookcorpus/scripts/lib_fts_cjk.py` 做 CJK 重叠二元组 + 同位置 unigram 切分，`qsearch.py` 在 `para_fts` 做 BM25 召回与组覆盖度重排（`--pool medium=2000`）。
2. **书级规划（默认入口 `book-corpus query`）**：`book_query.py` 走 `best_support` + B2.5 最多 5 轮词法循环；触发时默认弹 5–8 张书卡（`--no-cards` 关闭）；支持内部 `scope` 书目集合过滤。`qsearch.py --book` 保留旧 `mix` 口径作对照。
3. **向量混合检索（可选）**：当 `emb.db`、`vectors.f16` 与 `$BOOKS_EMBED_URL`（默认端口 18080）同时就绪时，`vec_search.py` 在书、章、段三层做 RRF 融合（`fusion: "hybrid"`）；任一缺失时静默退回 `bm25_only`。
4. **交叉编码器精排（可选）**：有可用 GPU 且启动 `book-corpus rerank-serve`（默认 `18081`）时，用 `bge-reranker-v2-m3` 精排；服务离线时自动降级回 `BM25 + 向量` RRF。
5. **引文核对（强制）**：所有回答引文必须回 `books/<uid>/md/` 原文文件逐字核对行号，绝不把 `expand` 或摘要当原文引用。

## 日常同步与增量维护

日常通过操作台完成入库、拆章、文档转换、PDF 落地或退库时，服务端自动接力 `build_para_index.py --sync` 与异步 `embed_corpus.py --sync`（向量库未初始化时跳过）；只读差集采集（`ops_status` / `diff_index` / `diff_embed`）复用单遍 `os.scandir` 签名扫描并通过 `chunk_book` 松散索引探测失活残留块；详见 [操作台重构方案](../design/操作台重构-事件驱动全自动同步方案-2026-11-19.md)。

## 相关文档

- [指标口径](指标口径.md)：书级评测与性能读数定义。
- [踩坑记录](踩坑记录.md)：按类型记录的检索层故障与规避方式（改检索脚本前先查）。
- [演进与进度-2026-10.md](演进与进度-2026-10.md)：早期 v1/v2 方案论证、基线实测与 2026-10 进度账（历史）。
- [检索组件与外部证据-2026-11-14.md](检索组件与外部证据-2026-11-14.md)：2026-11-14 组件默认开关与外部读数快照（历史）。
