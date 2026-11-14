# 全库 embedding

`book-corpus embed` 把每本有 md 的书的章节切块，交给本机 llama-server 算向量，
f16 追加写到工作区的 `build/检索/embeddings/`。断点续跑，同一份向量只允许一个写者。
这是 CLI 面，没有浏览器页面，产物也还没有检索面在读。

## Sub-features

- `embed-dry-run` 只切块：报块数、字数、估算 token 与耗时，不请求服务。
- `embed-run` 逐本嵌入：进度行带块数、字数、token、速率与剩余时间；每本结束即提交。
- `embed-resume` 断点续跑：章文件名、大小、mtime 不变的书跳过；`--force` 重嵌并留下垃圾块。
- `embed-books` 选书：`--books B0001,B0002`、`--limit N` 缩小范围，默认全部有 md 的书。
- `embed-status` 看库：书状态计数、有效块、垃圾块、last_id、向量体积、累计吞吐。
- `embed-verify` 校验：向量文件长度、chunk 行数与 last_id、抽样范数、sqlite integrity。
- `embed-serve` 前台只起 llama-server，供手工请求 `/v1/embeddings`。
- `embed-lock` 单写者：第二个写者在拿锁时退出，退出码 1，不碰向量文件。

## How to get to it (user POV)

- 在终端跑 `book-corpus embed`。它是全局命令（本机是 `uv tool install --editable .` 装的），
  仓库内等价写法是 `uv run --frozen book-corpus embed`。
- `--root <工作区>` 可放在任意位置；不传时依次看 `BOOKS_ROOT`、当前目录上层的 `books.toml`。
- 第一次跑会自动起 llama-server，跑完关掉；已有健康的同 URL 服务则直接复用，且不会替你关。
- 想先看规模，跑 `book-corpus embed --dry-run`；跑完看 `--status`，再 `--verify` 收口。
- 产物在 `build/检索/embeddings/emb.db` 与 `vectors.f16`，两件必须一起备份或删除。
  目前没有检索面读它们，别把「嵌入成功」当成「找一段已经能按语义召回」。

## Driving it with the book-corpus CLI

Preconditions:

- 按 [README](./README.md) 建一次性根：`init` + `sample`，样书 `B0001`（阿Q正传，9 章）。
  本文实测口径是 34 块、21,690 字、16,602 token；样书或切块参数变了，数字跟着变，别当常量。
- 需要 llama-server 与 GGUF 模型。本机只读引用操作员的中间态，不复制、不写：
  `mkdir -p "$ROOT/工作区/中间态/embed"`，再把
  `/home/shike/书库/工作区/中间态/embed/llama-b12377` 与 `.../embed/models` 两个目录软链进
  `$ROOT/工作区/中间态/embed/`。脚本按 `llama*/llama-server` 与 `models/*.gguf` 找；
  也可用 `BOOKS_LLAMA_SERVER`、`BOOKS_EMBED_MODEL` 指定，但日志会写到 binary 的上级目录，
  容易写进操作员的 `llama-server.log`，优先软链。
- 端口用私有 `18780`。操作员的实例在 `18080`，`~/书库/build/检索/embeddings/` 有正在写的真库；
  这两处都不碰。跑前确认 `ss -ltn | grep ':18780 '` 为空。
- `--status` 与 `--verify` 不拿写锁，但打开库时会把超长 `vectors.f16` 截回 `last_id`，
  并删掉未提交的 chunk 行；只在没有写者时跑。
- 首跑含模型加载，约 5 到 20 秒；之后同一台机器几秒。`--batch 8` 足够小样书。

- **dry-run。**
  `uv run --frozen book-corpus embed --root "$ROOT" --books B0001 --dry-run`。
  stdout 首行是 `书 1 本，块 34，字 21,690，估算 token 14,467`，随后两行耗时估算，
  最后 `块数前 5：` 与 `B0001  阿Q正传  34 块 21,690 字`；退出码 0。
  副作用：建出 `emb.db`（32KB）与 0 字节的 `vectors.f16`，不是纯只读。
- **首跑。**
  `uv run --frozen book-corpus embed --root "$ROOT" --books B0001 --server http://127.0.0.1:18780 --batch 8 --timeout 120`。
  stderr 一行 `起 llama-server pid <pid>，日志 <ROOT>/工作区/中间态/embed/llama-server.log`。
  stdout 是 `[1/1] B0001 阿Q正传 | 34 块 21,690 字 | 16,602 tok | <速率>k tok/s | 剩 0m00s`
  与 `本次：新嵌 1 本，跳过 0 本，失败 0 本；库内 34 块，68.0 KB`；退出码 0。
  跑完 `ss -ltn | grep ':18780 '` 应为空，服务随进程退出。
- **断点续跑。**
  同一条首跑命令再跑一次，stdout 只有 `本次：新嵌 0 本，跳过 1 本，失败 0 本；库内 34 块，68.0 KB`；
  stderr 仍会打印一行起服务，因为服务在进书循环之前起。
- **status。**
  `uv run --frozen book-corpus embed --root "$ROOT" --status` 输出七行：
  库路径、`模型 qwen3-embed  维度 1024  dtype float16`、`书 done=1`、
  `有效块 34  垃圾块 0  last_id 34`、`正文字符 21,690  向量 68.0 KB`、
  `累计 16,602 token / 0m01s，8569 tok/s`，末行 `估算 token 14,467`；退出码 0。
  `累计` 只除嵌书耗时，不含起服务与切块，小样本的 tok/s 不能当整库吞吐。
- **verify。**
  `uv run --frozen book-corpus embed --root "$ROOT" --verify` 输出
  `全过：34 块，1 本 done，68.0 KB 向量，抽样 32 条正常`；退出码 0。
  坏库时先打 `失败：` 再逐条缩进原因，退出码 1。
- **--force。**
  首跑命令加 `--force`，stdout 仍报 `新嵌 1 本`，但 `库内 34 块，136.0 KB`。
  随后 `--status` 是 `有效块 34  垃圾块 34  last_id 68`、`向量 136.0 KB`；
  `--verify` 仍 `全过：34 块，1 本 done，136.0 KB 向量`，退出码 0。
- **单写者锁。**
  `flock -n "$ROOT/build/检索/embeddings/emb.db.lock" sleep 20 &`，等半秒，再跑首跑命令加 `--force`。
  stdout 为空，stderr 是
  `另一个 embed 进程正在写 <emb.db>（锁 <emb.db.lock>），等它跑完再试`，退出码 1；
  `18780` 未被占用，因为锁在起服务之前拿。收尾 `kill %1`。
- **--serve。**
  `uv run --frozen book-corpus embed --root "$ROOT" --serve --server http://127.0.0.1:18780 &`，
  轮询 `curl -sf http://127.0.0.1:18780/health` 直到 `{"status":"ok"}`（模型加载约 5 秒，上限 180 秒）。
  `ss -ltnp | grep ':18780 '` 能看到 `llama-server`；`kill` 该 pid 后端口释放。

## Gotchas

- 不传 `--server` 时默认 `http://127.0.0.1:18080`。操作员的服务正健康，脚本会复用它并在跑完后
  **不关**，验证看起来「通过」但用的不是本轮的库。本技能一律传 18780。
- 模型和 binary 用软链进一次性根；用环境变量指到 `~/书库` 时，`ensure_server` 把日志追加到
  binary 上级目录，会写进操作员的 `llama-server.log`。
- dry-run 会建库（`emb.db` 与空 `vectors.f16`），因为 `main` 先构造 Store 再分派。别在「干净库」
  断言里把它当没发生。
- `--force` 不删旧向量，只把旧 chunk 标 `active=0`；`--status` 的 `垃圾块` 才会涨。
  `--verify` 只查文件长度与 id 对账，**不会**把垃圾块报成问题，设计文档里那句是错的。
- 向量是否 L2 归一化取决于 llama-server 默认行为，脚本没有 `--embd-normalize`，也不自己归一化；
  `--verify` 只拒绝范数小于 `1e-6` 的向量，不能证明单位范数。
- 只验接口不等于验用户路径：这里用户路径就是命令本身，断言必须看 stdout/stderr 与退出码，
  不能只看 `emb.db` 里有行。
- 只跑跳过时也会先起一次服务。2026-11-14 首次实测留下过一个加载中的 `llama-server`，
  一分钟后仍活着；复跑一次没重现。每轮收尾都查 `ss -ltnp | grep ':18780 '` 与
  `pgrep -af "$ROOT"`，有残留就按 pid 杀，再删根。
- `--verify` 的「块数」只数全部 chunk，不区分 active；`有效块` 与 `垃圾块` 只在 `--status` 里。
- 产物目前没有消费者：仓库里除 `embed_corpus.py`、`layout.py`、测试外没有读 `emb.db` 的代码。
  验证只到「向量落盘且自洽」，语义召回要等检索层落地后另写功能文件。
