# README 目录链接修复：报告

批次 `md拆分-2026-10-18`，日期 2026-10-18，命令在 `/home/shike/文档/books` 下执行。
每个数字后面标了它的出处文件或命令。本报告按指挥方的收尾要求只收录四类内容，另附产物路径与未完成的检查。

## 1. 一行改动

文件 `/home/shike/.agents/skills/ebook-to-md/scripts/ebook_to_md.py`，第 1718 行，README 目录段。

```diff
--- /tmp/ebook_to_md.py.orig	2026-10-18 21:04:17.443332930 +0800
+++ ebook_to_md.py	2026-10-18 21:04:20.325243786 +0800
@@ -1715,7 +1715,7 @@
         for u in units:
             pad = "  " * max(0, u.toc_level - 1)
             if u.written:
-                lines.append(f"{pad}- [{u.title}]({u.filename})")
+                lines.append(f"{pad}- [{u.title}]({link_dest(u.filename)})")
             elif is_part_title(u.title):
                 lines.append(f"{pad}- **{u.title}**")
         skipped = [u for u in units if not u.written and not is_part_title(u.title)]
```

出处 `readme-link-fix.diff`，用 `diff -u /tmp/ebook_to_md.py.orig ebook_to_md.py` 生成，改动前文件另存 `/tmp/ebook_to_md.py.orig`。
`diff` 只有这一段，说明全文件只有这一行变了。改动前 md5 `27cf05d0291225acfe53ea9d20b219a4`，改动后 `dc33f90668b96138dfbf16290851e626`。
`python3 -c "import ast;ast.parse(open('ebook_to_md.py').read())"` 通过。
`link_dest` 是文件里既有的助手，第 1163 行，目标里出现 `(`、`)` 或空格时套尖括号。
改后文件的 sha256 头 12 位是 `1932df458a07`，391 本重跑产物的 `.manifest.json` 里 `tool_version` 全是 `sha256:1932df458a07`，说明重跑用的就是改后的工具。

## 2. `--affected` 修前与修后

判据：README.md 里匹配 `- [label](target)` 且 target 不以 `<` 开头、含空格或括号的目录行。

修前，`python3 工作区/脚本/readme_link_fix_check.py --affected`，输出存 `readme-link-fix-affected-before.log`，末行：

```
TOTAL 391 books, 8498 entries
```

逐行与任务给的权威名单 `readme-links-affected.tsv` 做了 `diff`，391 行完全相同，无一行出入。

修后，同一条命令，输出存 `readme-link-fix-affected-after.log`，全文：

```
== affected: 目录里裸目标含空格或括号的 md 目录 ==
TOTAL 0 books, 0 entries
```

656 本批次书的 md 目录全扫过。
修前修后的对照是 391 本 8498 条 变为 0 本 0 条。

三本括号书的抽查，目标是括号加尖括号，`logs/<uid>.json` 与 `grep -o '](<'` 实测：

| uid | 书名 | 修前裸目标 | 修后裸目标 | 修后尖括号目标 |
|---|---|---|---|---|
| B1562 | 荒原上 | 24 | 0 | 24 |
| B1362 | 回乡记 | 52 | 0 | 52 |
| B1237 | 纪德作品集（套装共10册） | 50 | 0 | 50 |

## 3. 重跑状态统计

命令：

```bash
nohup python3 工作区/脚本/split_md_batch.py --batch md拆分-2026-10-18 \
  --only "$(cut -f1 工作区/批次/md拆分-2026-10-18/readme-links-affected.tsv | tr '\n' ',' | sed 's/,$//')" \
  --force --replace --workers 8 > 工作区/批次/md拆分-2026-10-18/rerun-readme-links.log 2>&1 &
```

日志 `rerun-readme-links.log` 末段「状态统计」：

```
  accepted           32
  replaced           359
```

明细行 `[n/391] uid status title ratio` 正好 391 条，没有别的状态字，没有 `staged-review`，没有 `split-failed`、`convert-failed`、`error`。
`logs/*.json` 里 `at` 为 21:0x 的正好 391 条，集合与 affected 名单相等；其余 265 条仍是 20:32 的旧记录，未被改写。
按任务预期是 391 个 `replaced`，实际是 359 个 `replaced` 加 32 个 `accepted`。
`accepted` 是已受理的例外，原因在 `overrides/<uid>.json`，与重跑前备份里的 `.manifest.json` 的 `accepted` 字段、以及新 `.manifest.json` 的同一字段逐字相同，32 本全对上。
原因分布：源书自身重复页 21 本，正文只收正文 4 本，审计分母偏低 4 本，孤儿图片 2 本，打包者插入的公众号页 1 本。
日志末段另列 9 本「未受理的文本量出界」（审计比在 0.88 到 1.15 之外），它们在 `logs/*.json` 里的状态都是 `accepted`，不属于失败。

## 4. 缩进导致目录行仍渲染成字面文本

这批有两处不同的成因都能让目录行不成链接。含空格或括号的一处已由本次一行改动消除。另一处是缩进，没有被本次改动触及。

现象与数字，出处 `readme-link-fix.json` 的 `verify.totals` 与 `verify.per_book`，用 `commonmark.commonmark()` 实测渲染结果：

| 量 | 修前 | 修后 |
|---|---|---|
| 裸目标（含空格或括号） | 8498 | 0 |
| 其中真渲染成字面文本 | 8398 | 0 |
| 非裸目标却渲染成字面文本 | 126 | 130 |
| 字面目录行合计 | 8524 | 130 |

分解数由 `/tmp/toc_breakdown.py` 用同一口径重算，两侧都满足「字面合计等于两类之和」。
另有 100 条裸目标虽然含括号，括号配对，CommonMark 仍当链接，所以从 8498 条裸目标里只消掉了 8398 条字面行。

修后剩下的 130 条全在 B1187《孤独与沉思》一本，它是 `verify.per_book` 里唯一一项 `after.literal > 0`。
成因不是空格或括号，而是 4 空格缩进。该 README 的 `## 目录` 之后第一行就是 4 空格缩进的条目：

```
## 目录

    - [灵感](001_灵感.md)
```

CommonMark 把 4 空格缩进、前面又没有列表父项的连续行当作缩进代码块，132 条里 130 条落进 `<pre><code>`，只有末尾 2 条 2 空格缩进的条目成为链接。
`commonmark.commonmark()` 渲染实测该 README 的 HTML 以 `<pre><code>` 开头，`links` 为 2、`html_links` 为 2。
缩进来源是同一段代码的 `pad = "  " * (u.toc_level - 1)`，toc 层级到 3 时缩进 4 格。

对照 B1143《长恨歌》，它的首条目录行也是 4 格，但前一行是 0 格的部题行 `- **第一部**`，4 格条目成了它的子列表，45 条全部是链接。
全批次里首条目录行缩进 4 格的有 2 本（B1143、B1187），只有 B1187 缺了低缩进的父项，于是整段变代码块。
另有 88 本存在 4 格及更深缩进的目录行，但都在 0 格或 2 格父项之下，`after.literal` 实测全为 0。
B1187 的 4 条裸目标本次也被加上尖括号，但修前修后字面行都是 130，说明这一改动对它是必要条件，不充分。
这一条我没有动，任务限定只改一行。可行的后续是把顶层 `pad` 封顶到 2 格，或给目录段补一个 0 格首行。

## 5. 我产生的产物

| 路径 | 内容 |
|---|---|
| `工作区/脚本/readme_link_fix_check.py` | 新建的核对脚本，只读，除 `--json` 指定的文件外不写盘 |
| `工作区/批次/md拆分-2026-10-18/readme-link-fix.diff` | 一行改动的 `diff -u` |
| `工作区/批次/md拆分-2026-10-18/readme-link-fix-affected-before.log` | 修前 `--affected`，391 本 8498 条 |
| `工作区/批次/md拆分-2026-10-18/readme-link-fix-affected-after.log` | 修后 `--affected`，0 本 0 条 |
| `工作区/批次/md拆分-2026-10-18/rerun-readme-links.log` | 重跑 391 本的完整输出 |
| `工作区/批次/md拆分-2026-10-18/readme-link-fix-verify.log` | `--verify` 的逐本表与合计 |
| `工作区/批次/md拆分-2026-10-18/readme-link-fix.json` | 同样的数字，机器可读 |
| `工作区/批次/md拆分-2026-10-18/replay-8.log`、`replay-8.json` | 8 本随机非受影响书的重放对比 |
| `工作区/批次/md拆分-2026-10-18/three-books-verify.log` | 三本括号书的 verify 与 audit 原始输出 |
| `工作区/批次/md拆分-2026-10-18/tmp-replay/<uid>/` | 8 本重放树，按边界要求未清理 |
| `工作区/批次/md拆分-2026-10-18/logs/<uid>.json`、`logs/<uid>.log` | 391 本重跑改写的批次账 |
| `工作区/批次/md拆分-2026-10-18/out/<uid>.prev-2026-10-18T210620-0800/` | 391 本重跑前的 md，按边界要求未清理 |
| `工作区/批次/md拆分-2026-10-18/readme-link-fix-report.md` | 本报告 |

辅助脚本在 `/tmp`，未落进书库：`replay8.py`、`manifest_probe.py`、`probe_toc.py`、`toc_breakdown.py`、`after_literal_probe.py`、`survey_prev.py`。
改动前的工具副本在 `/tmp/ebook_to_md.py.orig`。

## 6. 未完成的检查

- 修后 `--affected` 的日志 `readme-link-fix-affected-after.log` 是在我把核对脚本的 href 比对改成先做 HTML 反转义之前生成的。那次改动只影响 `--verify` 一侧的 href 比对，裸目标判定逻辑没变，所以 0 本 0 条仍然成立，但我没有在改动后再跑一次 `--affected` 留新证据。标记为未完成。
- 缩进缺陷只定位到批次内这一本 B1187，没有跨到批次外的 md 目录去扫。任务边界禁止我碰批次外的 md。
- B1187 的缩进缺陷本身没有修，见 §4 末段。标记为未完成，等指挥方决定。

## 7. 边界

- 批次区没有删除任何东西。重跑前备份目录从 652 个变成 1043 个，正好是 652 加 391，`tmp/` 与 `tmp-replay/` 都留着。
- 只改了 391 本批次书的 `md/`。391 本的 `README.md` 修改时间落在 21:06 到 21:09，且这 391 个 md 目录里没有任何文件是 21:15 之后写的（`os.path.getmtime` 实测，0 个命中）。
- 没有跑 `build_library.py`、`gen_book_catalog.py`、`verify_phase.py`，没有改 `分类表.csv`。批次区里的 `build_library.log` 与 `gen_catalog.log` 时间戳是 20:34 与 21:21，不是我产生的。
- 批次区另有同批次的另一个作业在 21:18 到 21:20 写了 `old-md-repair-dirs.tsv`、`old-md-repair.json`、`old-md-exceptions.tsv`，并在 21:19 改写了 184 个 md 目录里的 README。那不是我的进程。判据：那 184 个目录与 656 本批次的 dest 交集为 0，我的 391 本在 21:09 前就写完了。

## 8. 复核命令

```bash
cd /home/shike/文档/books
diff -u /tmp/ebook_to_md.py.orig ~/.agents/skills/ebook-to-md/scripts/ebook_to_md.py
python3 工作区/脚本/readme_link_fix_check.py --affected
python3 工作区/脚本/readme_link_fix_check.py --verify --json 工作区/批次/md拆分-2026-10-18/readme-link-fix.json
python3 /tmp/toc_breakdown.py
```
