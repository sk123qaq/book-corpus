# 全库 md embedding

状态：implemented · 2026-11-14
验收：`uv run --frozen pytest tests/test_embed_corpus.py`，`book-corpus embed --verify`

把每本书 `md/` 下的章节正文切块、用本地 llama.cpp 服务算向量，供找段与语义检索使用。
入口是 `book-corpus embed`，实现只有 `src/bookcorpus/scripts/embed_corpus.py` 一处。

## 数据形状

产物在 `build/检索/embeddings/`，两部分必须一起备份或删除：

| 文件 | 内容 |
|---|---|
| `emb.db` | `meta`（模型、维度、last_id、tokens_in、seconds）、`book`（每本 uid、sig、状态、块数）、`chunk`（id、book、path、seq、line、chars、text、active） |
| `vectors.f16` | float16 行向量，无文件头；`chunk.id` 为 N 的行在第 `(N-1) * dim * 2` 字节 |

向量维度是 1024（Qwen3-Embedding-0.6B），已 L2 归一化，内积即余弦。
一行向量对应一块，块文本存在 `chunk.text`，路径是相对书库根的 `books/<uid>/md/...`。

## 切块与续跑

- 按章切，块上限 `--chunk-chars`（默认 700 字），段落取自 `defpass.paragraphs`，
  跳过 front matter、纯图片行与分割线，`README.md` 不算正文。
- `meta.last_id` 是已提交块数；向量先落盘、再在同一个事务里写 `chunk` 并推进 `last_id`。
  崩溃后重开库，多余半截向量按 `last_id` 截掉，未提交的 `chunk` 行删除。
- 每本书记 `sig`（章文件名、大小、mtime_ns 的哈希）。`sig` 不变就跳过；`--force` 重嵌，
  旧块标 `active=0` 但向量留在文件里，`--verify` 会把差值报成垃圾块。
- 搜索时只取 `chunk.active=1` 且 `book.status='done'` 的块。

## 命令

```bash
book-corpus embed --status                 # 进度、体积、累计吞吐
book-corpus embed --dry-run                # 只切块，报块数与预估耗时
book-corpus embed                          # 全部未完成的书，断点续跑
book-corpus embed --books B0001,B0002      # 指定书
book-corpus embed --verify                 # 校验文件长度、id 连续、抽样范数
book-corpus embed --serve                  # 前台只起 llama-server，供手工调用
```

服务与模型默认放在 `工作区/中间态/embed/`：`llama-b*/llama-server` 与
`models/*-Embedding-*.gguf`。可用 `BOOKS_LLAMA_SERVER`、`BOOKS_EMBED_MODEL`、
`BOOKS_EMBED_URL` 覆盖。脚本自己起服务时，跑完会关掉；`--keep-server` 可留。

## 2026-11-14 实测

- 语料：1,823 本有正文，1,100,934 块，699,480,456 字，约 4.66 亿 token。
- 单请求基准 24,848 tok/s（RX 9070 GRE，batch 24 到 128 已到平台）。
- 全库实跑约 17k tok/s（含切块、写库与逐书空档），预计 6 到 7 小时；
  向量约 2.25 GB，`emb.db` 含块文本预计另占 1 到 1.5 GB。
- 只覆盖有 md 的书；133 本无 md 与 4 本只有目录页的书不在库内。
