# books/_冷宫

移出处理流程的书放这里。只留原书文件，不参与任何脚本。

## 规则

- 没有 `book.json` 或任何 json。元数据写在 `工作区/批次/移出-<日期>/retired.tsv` 与本文的账表。
- 不在人账 `tables/ledger.db` 里，所以不建 `library.db` 行，不进 `build/书目总表.csv`。
- 没有 `md/` 与 `html/`，所以 `book-corpus index`、`book-corpus run build_expand`、`book-corpus stats` 都发现不了它。
- 要复用：按 `retired.tsv` 重建 `book.json`，把书移回 `books/<uid>/`，人账 `tables/ledger.db` 补一行，再跑 `book-corpus library` 与 `book-corpus index --rebuild`。

`_` 开头是为了和书的 uid 目录区分。`books/_收件箱` 是新书入口，这里是出口。

## 账

| 移入日期 | uid | 书名 | 原路径 | 源文件 | 原 md | 理由 |
|---|---|---|---|---|---|---|
