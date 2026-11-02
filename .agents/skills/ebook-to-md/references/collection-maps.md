# Split maps: building one for a new collection

A collection EPUB (several books in one file) is split with
`scripts/split_collection.py` + a JSON map. This is how the maps for the 12
collections of the 45-EPUB batch were built, condensed into a repeatable
procedure. Always iterate the map with `scripts/verify_map.py` (dry run,
writes nothing) **before** running the split.

## 1. Dump the TOC

```bash
python3 - <<'EOF'
import sys; sys.path.insert(0, "<SKILL_DIR>/scripts")
from ebook_to_md import Epub, build_units, merge_numeric_sections
epub = Epub("合集.epub")
for i, u in enumerate(merge_numeric_sections(build_units(epub, True))):
    print(f"{i:4d}  L{u.toc_level}  {u.title[:60]!r:<62s} {u.doc}")
EOF
```

Read it like a bookshelf: books usually appear as the shallowest level (L1),
each followed by its own chapters one level deeper.

## 2. Find the book boundaries

- **Label = the TOC label text as printed** (not the folder name). `labels[0]` is
  the primary match; extra entries in `labels` are accepted aliases.
- Match is **exact first, then prefix, at the shallowest TOC level** — needed
  when a book's title also appears as a section inside another book (余华's
  《战栗》 is both a book in the collection and a section of 在细雨中呼喊).
- Some collections nest books one level down: 盗墓笔记's volumes are L2
  (`第二卷邛笼石书`) under L1 `第一季`/`第二季`; using the L2 volume labels is
  what produces one folder per 卷.
- A book owns every unit from its label up to the next label. **List books in
  reading order**; a mis-ordered map shows up in verify_map as a book owning
  0–1 pages and a neighbour owning far too many.

## 3. Collection-level pages → `skip`

Covers, 版权, 制作说明, the collection TOC itself, and anything before the
first book belong to no book: put them in `skip` with a reason — they are
listed in the collection README's 合集卷首/卷尾页 section.

```json
"skip": [{"label": "版权信息", "reason": "合集卷首页"}, "制作说明"]
```

**Duplicates**: a collection sometimes carries the same book twice (东野圭吾's
《嫌疑人X的献身》 under both the standalone list and the 伽利略系列 list, identical
text). Keep one, skip the other with `reason: "重复收录，与前书正文一致"`.

**Standalone openers**: a piece that belongs to no book but should become its
own folder (三体's 刘慈欣2018克拉克奖获奖感言) is an extra `books` entry with
`"keep_marker": true` — otherwise a book's own title page never becomes a file.

## 4. Folder names

`folder` = `<系列名>-<书名>` (e.g. `东野圭吾-白夜行`, `鬼吹灯-精绝古城`) or the bare
title for single books. The label often carries junk the folder should not:
year suffixes `（2017）`, `盗墓笔记之` prefixes, publisher tags. Clean with a
short regex pass when generating the map; keep volume suffixes distinct
(`…-大结局（上）` / `…（下）`). Folder names must be unique and contain no `/`.

## 5. Validate, split, audit

```bash
python3 "<SKILL_DIR>/scripts/verify_map.py" "合集.epub" --map split.json   # dry run
python3 "<SKILL_DIR>/scripts/split_collection.py" "合集.epub" -o "书md/" --map split.json
python3 "<SKILL_DIR>/scripts/audit_volume.py" "合集.epub" -o "书md/" --map split.json
```

`verify_map.py` checks labels resolve, positions ascend, no two books claim one
entry, folders are unique, and prints the pages each book owns. `audit_volume.py`
then compares each book's converted text against its raw share of the EPUB —
a body-only run is ~94–97%, so a LOW book means text was dropped (or a large
front/back matter share; read its README 未收录 list). When every document's
prefix is the missing part (calibre split docs misaligned with the NCX anchors),
rerun the split with `--carry-doc-prefixes` (`渡边淳一精华集` 60% → 98%); it is
the collection form of `scripts/carry_doc_prefixes.py`.

## Worked examples (from the 45-EPUB batch)

| collection | shape | notes |
| --- | --- | --- |
| 三体全集 | 4 L1 entries | 获奖感言 first with `keep_marker`; books I/II/III. II and III are coarse: their whole 上/中/下部 sit in one document each, so a book is a handful of very large files — the source TOC is that coarse. |
| 盗墓笔记 1-8 全集 | L2 volume labels | 卷 under 第一季/第二季; 上中下卷 kept as separate folders; the pure-image 绘本 volume produces a README-only folder (its 71 images live in excluded pages). |
| 东野圭吾作品集 | 69 L1 entries | 《嫌疑人X的献身》 duplicated → skipped one; two TOC shapes (direct chapters; chapter → bare 01/02 sub-numbers merged back into the chapter). |
| 理想国译丛 | 32 L1 entries | every book has 导读/序言/引用文献 front+back matter (24k chars on 创造日本) — normal body-only losses. |
| 毛选 / 王小波 / 鬼吹灯 | volumes as L1 | 毛选 `第N卷`; 王小波 10 单册; 鬼吹灯 `鬼吹灯I精绝古城`-style labels. |

Batch result: 45 epubs → 285 book folders, ~7000 chapters, every book within
~2–10% of its EPUB's raw text (the rest front/back matter, all listed).
