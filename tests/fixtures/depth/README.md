# 深度包 gold fixtures

这组 fixture 是四读深度包 phase 0 的验收样本。正文都从本机书库的真实章节截取，
gold 由人工按 `src/bookcorpus/reading/depth/schema.py` 的 chapter_model 形状校订，
不调用模型，也不引入原文之外的事实。

每份 gold 都带 `core_units` 与四档 `tiers`。`core_units` 是外部核心清单，给 G10
`audit_conservation(..., expected_core=...)` 用；`tiers` 是确定性计划器应产出的
逐单元处置。schema 不接受 `tiers`，测试会先把它从 payload 里拿掉再校验。

## 样本与来源

| fixture | 来源 | 截取范围 | 形状 |
| --- | --- | --- | --- |
| `b0003_ch06.md` / `b0003_ch06_gold.json` | `/home/colaqaq/书库/books/B0003/md/06_第4章前景理论和价值函数.md` | 第 18–34 行，选 4 块（H2、正文段、引文段、正文段；中间图片与图注未收） | 论证：1 个 H2，4 个 unit，1 个非语义节标题 |
| `b0001_ch04_narrative.md` / `b0001_ch04_narrative_gold.json` | `/home/colaqaq/书库/books/B0001/md/04_第四章恋爱的悲剧.md` | 第 51–67 行，9 个连续空行段（起于“女人……”，止于秀才拿竹杠） | 叙事：无 H2，3 节，6 个 unit，按场景节拍分节 |
| `b0004_ch04_table.md` / `b0004_ch04_table_gold.json` | `/home/colaqaq/书库/books/B0004/md/04_笨办法才最有效.md` | 第 1402–1424 行，选 6 块；省略 1413、1415 两段 | 表格：2 节，13 个 unit，两张表拆行 |
| `b0004_ch01_image.md` / `b0004_ch01_image_gold.json` | `/home/colaqaq/书库/books/B0004/md/01_从0到1.md` | 第 12、206、281–291 行，选 6 块；省略 285 行的过渡段；封面、段落、两组图片+图注 | 图片：3 节，3 个 unit，1 张装饰图，2 张语义图 |

说明：

- 块 id（`B01`…）是 mdparse 给截取片段重新编的局部编号，不是源文件行号，也不是
  整章里的全局块号。每份 fixture 都从 `B01` 重新开始。
- 叙事样本的起止锚点之间实际有 9 个空行段。任务书写“5–6 段”，但截到
  “阿Q对了墙壁跪着也发楞……”会丢掉“秀才拿竹杠”的后果；为保留
  setup→turn→consequence，样本收满 9 段。
- 表格样本为了控制在 6 块，省略了表 2 前的两段铺垫（1413、1415）。表 2 的表头、
  4 行数据与表后结论都完整保留。
- 图片样本的 B02 是第 206 行的短段落，不和第 281 行以后的图片相邻；它们属于同一
  节的论证与图示。第 285 行的“从宏观层次看……”过渡段为了控制样本聚焦省略；
  第 208–219 行的“从0到1/从1到n”对比表没有收进 fixture。

## 三种形状的差异

### 叙事

`b0001_ch04_narrative` 没有 heading block。三个 section 按场景节拍命名：
`舂米夜`、`跪下`、`挨打`，不是 H2 标题。U03（跪下）和 U06（秀才拿竹杠）是
`core`；U03 是转折，U06 是后果。U05/U06 共用源块 B09，因为同一段里先写阿Q
发楞，再写头上挨打。U06 的 `hard_info_note` 是“那秀才便拿了一支大竹杠站在他面前。”，
skim 档必须把它留在摘要里。

### 表格

`b0004_ch04_table` 的 B02 与 B05 各是一个 table block。表 1 的表头是空左上格 +
“结果证明你是对的/错的”两列；4 个结果格拆成 U03–U06，每格引本格文本与对应行标签。
表 2 的表头拆成 U08，4 行数据拆成 U09–U12。U02、U07、U08、U13 是 `core`；
表 1、表 2 的 8 个数据行 U03–U06、U09–U12 都是 `core`，skim 档用 `condensed`
保留，不允许变成 `indexed`；它们不标 `hard_info`，避免把整句表格文本硬塞进摘要。
9 个行组（表 1 的 4 个结果格 + 表 2 的表头与 4 行）都有独立 unit，没有整表压成
一个 unit。B04 是表格之间的小节标题，进 `non_semantic`。

### 图片

`b0004_ch01_image` 的 B01 是封面图，无图注、无相邻标题，进 `non_semantic`，
reason 写 `decorative`，不建 unit，也不进 figure。B03+B04 与 B05+B06 是两组
语义图：unit 的 `source_blocks` 同时包含图片块和图注块，`quotes` 引图注；
F01/F02 的 `origin` 是 `source`，`units` 指回对应 unit，`caption` 写图注。
U01 是唯一 `core`，它给出“从0到1/从1到n”的核心主张；U02、U03 是语义图，作 support，图注作为 hard_info。

## 已知边界

- **合并/空白单元格没有结构位置。** schema 没有 cell 坐标，表 1 左上角的空表头
  不能写进 quote；表头 unit 只引两个非空列标题。合并单元格的归属只能写进
  `summary`。`hard_info_note` 必须是某条 quote 的逐字子串，所以不能把“这个格
  属于哪一行/列”写进 note，除非原文里真有这句话。
- **schema 不检查块覆盖。** `validate_chapter_model` 只查引用、引文、section 与
  non_semantic 的一致性；每个正文块是否被 unit 或 non_semantic 覆盖，由 G10
  `audit_conservation` 查。gold 测试都显式传 `expected_core`，否则删掉的核心
  单元不可见。
- **`decorative` 是约定，不是枚举。** schema 对 `non_semantic.reason` 只要求
  非空字符串；本目录约定装饰图写 `decorative`。
- **图片 alt 的可引用性取决于 mdparse。** mdparse 把图片 alt 放进 image block
  的 text；alt 为空时 text 为空。本目录的语义图都有图注，所以 quote 用图注；
  没有图注也没有 alt 的图不能编造 quote。
- **gold 不是可直接喂给 schema 的 chapter_model。** gold 顶层多出 `core_units`
  和 `tiers`；schema 拒绝 `tiers`。测试先 pop `tiers`；CLI 在两者同时存在时会
  剥掉 `tiers` 与 `core_units` 再校验。

## 重取与校验

重取原文时按上表行号从书库复制，保留空行分段，不要改标点。重写 gold 后至少跑：

```bash
cd /home/colaqaq/Work/book-corpus-depth-2
uv run --frozen pytest tests/test_reading_depth.py -q
```

fixture 的 block id 是截取片段里的局部编号，不能直接拿真实书库的整章去校验：
CLI 会按整章的 `B01`、`B02` 去对，局部编号对不上，`source_sha256` 也不一样。
要跑 CLI，先把片段装进一个临时实例，让 `source_sha256` 与局部编号对齐：

```bash
tmp=$(mktemp -d)
touch "$tmp/books.toml"
mkdir -p "$tmp/books/B0004/md"
cp tests/fixtures/depth/b0004_ch04_table.md "$tmp/books/B0004/md/004_fixture.md"
uv run --frozen python -m bookcorpus.reading.depth.cli report \
  --root "$tmp" --uid B0004 --n 4 \
  --file tests/fixtures/depth/b0004_ch04_table_gold.json \
  --gold tests/fixtures/depth/b0004_ch04_table_gold.json
rm -rf "$tmp"
```

`report` 会跑四档处置与 G10，输出里的 `gate`、`core_coverage`、
`hard_info_coverage` 和 `core_limitation` 是验收读数。换另外三份时，按上表的
uid、n 建临时实例，md 文件名分别用 `006_fixture.md`（B0003）、
`004_fixture.md`（B0001）、`001_fixture.md`（B0004 图片样本）。
`validate` 同样用这个临时 root，只查 chapter_model 结构与 `--gold` 里的核心 id
是否存在，不跑 G10；守恒账以 `report` 为准。
