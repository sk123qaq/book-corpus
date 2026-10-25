# B2 新普通层（heldout 20 题）说明

日期 2026-10-23。文件：`book_gold-heldout.tsv`、`book_gold-heldout.freeze.txt`、`probe_b2_heldout.py`、`live/B2-heldout/`。

## 为什么加这层

旧 60 题的普通层 v2 在看过 v1 名次后重写 `terms`，还做过 `mix` 前 20 可答性筛查；v3 沿用这 20 题。`ordinary=1.000` 与第 4 条门槛因此不是独立证据。

这层是独立对照：20 道 6–499 章普通书题，选题与写题时不跑检索、不看名次；目标章必须在索引里；写完冻结 sha256，跑读数前不再改。它不参与调参，也不并进原 60 题总表。

## 构造规则

- 20 本已索引、`derived.chapters` 在 6–499 之间的书；覆盖人文社科、科技、商业、文学。
- 每题一个目标书、一个目标章；`terms` 2 到 3 个，从问句/章内容手写，不用检索结果反推。
- `target_book` 不含 `库/`，`target_chapters`/`path` 含 `库/.../md/...`；`line` 是证据行。
- 冻结文件：`book_gold-heldout.freeze.txt`，sha256 `2112495970c65cb77dae91c92f974b653fc79edf44a8f2b80dbc0c6ba53be40a`。
- 校验脚本 `build_heldout.py` 会检查章存在、章在 `index.db`、章数 6–499；`probe_b2_heldout.py` 会再校验 freeze sha 与 20 行。

## 读数（`probe_b2_heldout.py`）

| 方案 | hit@10 | hit@20 | MRR | ev10 |
|---|---|---|---|---|
| sum | 0.900 | 1.000 | 0.568 | 1.000 |
| mean | 0.700 | 0.750 | 0.316 | 1.000 |
| best | 1.000 | 1.000 | 0.860 | 1.000 |
| mix | 1.000 | 1.000 | 0.910 | 1.000 |
| best_support | 1.000 | 1.000 | 0.852 | 1.000 |
| best_top3_support | 1.000 | 1.000 | 0.885 | 1.000 |
| rrf5 | 0.250 | 0.500 | 0.108 | 1.000 |
| best_support_length | 1.000 | 1.000 | 0.854 | 1.000 |

读法：

- 这层太容易，hit@10 对五个候选方案没有区分力；它只能证明 `best_support` 没有在这 20 题上掉队。
- MRR 上 `mix` 0.910 高于 `best_support` 0.852（20 题，差约 1 题）；`best` 0.860、`best_top3_support` 0.885。样本小，不据此改默认；原 60 题上 `best_support` 与 `mix` 的 hit@10 相同，MRR 只差 0.004。
- `sum` 0.900 / `mean` 0.700 / `rrf5` 0.250 仍明显差；`rrf5` 是命中数先验，不是 RRF。

## 原 60 题的 `single_hit` 修正

`book_eval.py` 现在按「目标书 `chapters == 1`」算 `single_hit`，不再拿 short 层冒充。当前索引上复跑（`live/B2-single/summary.tsv`）：

- `best` / `mix` / `best_support` / `best_top3_support` / `best_support_length`：`single_n=4`，`single_hit10=1.000`。
- `sum` / `mean` / `rrf5`：`single_hit10=0.750`。
- `book_eval` 的当前读数与历史 B2 报告不完全可比：索引/库在 OCR 续跑中变了（当前全库基准段数 0.2619，历史 0.2797；mix hit@10 0.967 对历史 0.983）。历史 B2 报告保留原读数，新读数看 `live/B2-single/`。

## 结论

- 新层 20 题不改变默认：`best_support` 的 hit@10 与 `mix` 相同；MRR 的 5.8 点差在 n=20 上不足以推翻原 60 题。
- `single_hit` 已按单章书重算，五个候选方案 1.000，B2 门槛的 `g2` 仍过；但样本只有 4 题，报告要带 `single_n`。
- 下一轮若要更有区分力，应该增加 20–40 道更难的 6–499 章题（多章候选、开放问法），而不是继续用这 20 道。
