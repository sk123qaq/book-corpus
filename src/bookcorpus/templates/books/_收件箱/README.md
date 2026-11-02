# books/_收件箱

新书落到这里，再由入库命令处理。这是入口；`books/_冷宫/` 是出口。

## 用法

把书（epub / pdf / mobi / azw3 / txt）直接拷进本目录，不要建子目录。然后：

```bash
cd <工作区根>

# 只读：出计划，看 uid、学科预填、重复判定
book-corpus ingest scan --out plan.json

# 真的写盘：移文件进 books/<uid>/source/、写 book.json、追人账、重建 library.db
book-corpus ingest apply plan.json --apply
```

想单独加一本、手填学科与书名：

```bash
book-corpus run ingest add books/_收件箱/某本书.epub \
  --subjects 文学/严肃文学小说 --title 书名 --author 作者 --apply
```

## 规则

- **默认只打印，`--apply` 才写盘**。不加 `--apply` 跑多少遍都不会动文件。
- 按 sha256 判重。已在库里的文件一律跳过；库里已有同名不同 sha 的副本时用 `replace`，新源排到 `sources` 首位，旧源保留。
- uid 从 `tables/分类表.csv` 现有最大号往下排（`B0001` 起）。分类表空着就是从 `B0001` 开始，所以空工作区可以直接用。
- 学科预填调模型（`classify_model.classify`），结果缓存在 `工作区/批次/ingest/classify.json`。不想调模型加 `--no-classify`，回落到 `--subjects`。
- 入库只放源和 `book.json`，还没有 md。要能检索得先拆章，见 README「准备语料」。
- `*.pdf`、`*.epub` 等二进制在 `.gitignore` 里。收件箱内容不进 Git。
