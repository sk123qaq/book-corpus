#!/usr/bin/env python3
"""旧 md 的文本量审计（批次 md拆分-2026-10-18 之前就有的那 494 本）。

那些目录是旧版转换器写的，没有 `.manifest.json`，从来没过过 audit_volume.py。
本脚本补这一遍，口径跟批次用的 audit_volume.py 逐字同一份代码（直接 import 它）。

口径：与批次同一份 audit_volume.py 代码（直接 import）。除了「md 字数 / 源字数」，
还量一项「源正文覆盖率」：源书的 CJK 字窗口有多少能在 md 里找到。
后者是关键——旧版转换器会为「篇」同时写篇文件和它的子章节文件，篇文件里是子章节
全文，字数因此虚高一倍（B0191 实测），只看比值会把这类书误判；覆盖率不受影响。
pdf 源（MinerU 那条线）不在同一口径，只登记不审。
只读：不改 库/ 里任何文件；mobi/azw3 的中间 EPUB 落 <out>/tmp/。

用法: python3 audit_old_md.py [--out 工作区/批次/旧md文本量审计-2026-10-18] [--limit N]
"""
import argparse
import collections
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L  # noqa: E402

SKILL = os.path.join(L.skill_dir("ebook-to-md"), "scripts")
sys.path.insert(0, SKILL)
from audit_volume import Epub, COUNT_RE  # noqa: E402

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
ANCHOR = 20      # 每页抽 10 个 20 字锚点，只数「有/没有」，比 40 字窗口耐换行与标点差异
PER_PAGE = 10
POLICY = "anchor%dx%d" % (ANCHOR, PER_PAGE)
TOC_HEAD = "## 未收录"
# 旧书 README 的未收录清单列得不全，页首出现这些字样的算卷首卷尾（不算丢正文）
FRONTBACK = ("导读", "序言", "前言", "后记", "致谢", "目录", "版权", "书名", "扉页", "封面",
             "献词", "编者", "出版", "参考书目", "参考文献", "注释", "脚注", "索引", "附录",
             "延伸阅读", "译后记", "代序", "推荐序", "总目录", "简介", "凡例", "CIP", "插图",
             "地图", "正文前", "插页", "题记", "引言", "结语", "尾声", "译序", "公众号",
             "人物介绍", "人名", "译名", "词表", "年表", "前言", "出版说明", "内容提要")
BODY_MIN = 1000   # 缺的正文加起来不到这么多字就当噪声


def md_text_of(folder):
    """按 folder_chars 的口径读一个 md 目录（跳过 README、剥 YAML 头），
    另返回 CJK-only 串供覆盖率用。"""
    if not os.path.isdir(folder):
        return 0, "", 0
    total, files, cjk = 0, 0, []
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".md") or name == "README.md":
            continue
        files += 1
        text = open(os.path.join(folder, name), encoding="utf-8").read()
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                text = text[text.find("\n", end + 1) + 1:]
        total += len(COUNT_RE.findall(text))
        cjk.append("".join(CJK_RE.findall(text)))
    return total, "".join(cjk), files


def source_texts(epub):
    """每个 spine 页的正文：COUNT_RE 计数、CJK-only 串、以及 (页名, CJK 串) 表。"""
    counts, cjk, pages = {}, [], []
    for doc in epub.content_docs():
        soup = epub.soup(doc)
        body = soup.find("body") if soup else None
        text = body.get_text(" ", strip=True) if body else ""
        counts[doc] = len(COUNT_RE.findall(text))
        page_cjk = "".join(CJK_RE.findall(text))
        cjk.append(page_cjk)
        pages.append((doc, page_cjk))
    return counts, "".join(cjk), pages


def page_anchors(text, k=PER_PAGE, length=ANCHOR):
    """归一到 CJK 后的文本上取 k 个等距锚点（每个 length 字），文本太短就少取几个。"""
    if len(text) < length:
        return []
    out = []
    for i in range(k):
        pos = i / (k - 1) if k > 1 else 0.5
        start = max(0, min(int(len(text) * pos), len(text) - length))
        out.append(text[start:start + length])
    return out


def declared_exclusions(dirs):
    """md 目录 README 的 `## 未收录` 里声明过的页名。"""
    labels = []
    for d in dirs:
        p = os.path.join(d, "README.md")
        if not os.path.isfile(p):
            continue
        text = open(p, encoding="utf-8", errors="ignore").read()
        if TOC_HEAD not in text:
            continue
        body = text.split(TOC_HEAD, 1)[1]
        for line in body.splitlines():
            if line.startswith("## "):
                break
            line = line.strip()
            if line.startswith("- "):
                label = line[2:].split(" — ")[0].strip()
                if label:
                    labels.append(label)
    return labels


def page_audit(pages, md_cjk, all_source_cjk, exclusions):
    """逐页：10 个锚点里有几个能在 md 里找到。
    0 个命中、且该页锚点在源内别处也只出现一次的，算「缺页」，再按 README 的未收录声明分类。"""
    hits_total = anchors_total = 0
    absent, partial = [], []
    for doc, text in pages:
        a = page_anchors(text)
        if not a:
            continue
        hits = sum(1 for x in a if x in md_cjk)
        hits_total += hits
        anchors_total += len(a)
        if hits == len(a):
            continue
        own_only = sum(1 for x in a if all_source_cjk.count(x) == 1)
        entry = {"doc": doc, "cjk": len(text), "hits": hits, "anchors": len(a),
                 "own_only": own_only, "head": text[:24]}
        if hits == 0 and own_only:
            absent.append(entry)
        else:
            partial.append(entry)
    for e in absent:
        e["declared"] = next((lab for lab in exclusions if lab in e["head"][:12]), "")
    return hits_total, anchors_total, absent, partial


def classify_pages(absent, partial):
    """每个缺页/部分缺页算出「缺多少字」并分类：
    declared（README 未收录里声明过）/ frontback（页首是卷首卷尾字样）/ body（其余，真缺正文）。
    缺字数是按锚点比例折算的：该页 anchors 个锚点里缺 miss 个，其中只在本页出现的占 uniq。"""
    pages = []
    for e in absent + partial:
        miss = e["anchors"] - e["hits"]
        uniq = e["own_only"] / e["anchors"]
        miss_chars = int(round(e["cjk"] * miss * uniq / e["anchors"]))
        head = e["head"]
        if e.get("declared"):
            kind = "declared"
        elif any(k in head[:10] for k in FRONTBACK):
            kind = "frontback"
        else:
            kind = "body"
        pages.append({"doc": e["doc"], "cjk": e["cjk"], "hits": e["hits"],
                      "anchors": e["anchors"], "miss_chars": miss_chars, "kind": kind,
                      "declared": e.get("declared", ""), "head": head})
    return pages

BATCH_LOGS = os.path.join(L.WORK, "批次", "md拆分-2026-10-18", "logs")
CALIBRE = "ebook-convert"


def md_dirs():
    out = []
    for root, subdirs, _files in os.walk(os.path.join(L.ROOT, "库")):
        if "md" in subdirs:
            out.append(os.path.join(root, "md"))
    return sorted(out)


def batch_md_dirs():
    dirs = set()
    for name in os.listdir(BATCH_LOGS):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(BATCH_LOGS, name), encoding="utf-8") as fh:
            rec = json.load(fh)
        dirs.add(os.path.join(L.ROOT, rec["dest"]))
    return dirs


def scope():
    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(L.CAT, "library.db"), uri=True)
    con.row_factory = sqlite3.Row
    batch = batch_md_dirs()
    old = [d for d in md_dirs() if d not in batch]
    books, unresolved = [], []
    for d in old:
        rel = os.path.relpath(d, os.path.join(L.ROOT, "库"))[: -len("/md")]
        r = con.execute(
            """SELECT b.uid AS uid, b.path AS path, b.title AS title, b.author AS author,
                      s.format AS format, s.file AS src
               FROM book b JOIN source s ON s.uid=b.uid AND s.primary_src=1
               WHERE b.path=?""", (rel,)).fetchone()
        if r:
            books.append(dict(r, dir=d))
        else:
            unresolved.append(rel)
    return books, unresolved


def group(books):
    bysrc = collections.defaultdict(list)
    for b in books:
        bysrc[b["src"]].append(b)
    items = []
    for src, members in bysrc.items():
        items.append({"src": src, "format": members[0]["format"], "members": members,
                      "n_books": len(members),
                      "mode": "direct" if len(members) == 1 else "collection"})
    items.sort(key=lambda i: (i["mode"], i["src"]))
    return items


def source_epub(item, tmpdir):
    if item["format"] == "epub":
        return os.path.join(L.ROOT, item["src"])
    stem = os.path.basename(item["src"]).rsplit(".", 1)[0][:60] + ".epub"
    dest = os.path.join(tmpdir, stem)
    if not os.path.isfile(dest):
        p = subprocess.run([CALIBRE, os.path.join(L.ROOT, item["src"]), dest],
                           capture_output=True, text=True, timeout=3600)
        if p.returncode != 0 or not os.path.isfile(dest):
            return None
    return dest


def audit(item, tmpdir):
    row = {"mode": item["mode"], "format": item["format"], "source": item["src"],
           "books": [m["uid"] for m in item["members"]],
           "n_books": len(item["members"])}
    if item["format"] == "pdf":
        row.update(status="skipped-pdf", note="pdf 走 MinerU，不在 audit_volume 口径")
        return row
    epub_path = source_epub(item, tmpdir)
    if epub_path is None:
        row.update(status="convert-failed")
        return row
    doc_counts, source_cjk, source_pages = source_texts(Epub(os.path.abspath(epub_path)))
    raw = sum(doc_counts.values())
    got, md_cjk, files = 0, "", 0
    for m in item["members"]:
        c, j, n = md_text_of(m["dir"])
        got += c
        md_cjk += j
        files += n
    ratio = (got / raw) if raw else 1.0
    cover, absent, partial = 1.0, [], []
    if raw >= 100:
        exclusions = declared_exclusions([m["dir"] for m in item["members"]])
        hits, anchors, absent, partial = page_audit(source_pages, md_cjk, source_cjk, exclusions)
        cover = (hits / anchors) if anchors else 1.0
    pages = classify_pages(absent, partial)
    body_pages = [p for p in pages if p["kind"] == "body"]
    body_lost_chars = sum(p["miss_chars"] for p in body_pages)
    body_lost = [p for p in body_pages if p["miss_chars"] >= 100]
    lost_enough = body_lost_chars >= BODY_MIN
    if raw < 100:
        status = "ok (no-text source)"
    elif got == 0:
        status = "NOT CONVERTED"
    elif lost_enough:
        status = "LOST"
    elif absent:
        status = "excluded"
    elif cover < 0.90:
        status = "short"
    elif ratio > 1.15:
        status = "HIGH"
    else:
        status = "ok"
    row.update(raw=raw, got=got, ratio=ratio, md_files=files, cover=cover,
               absent=absent, partial=partial, pages=pages,
               lost_pages=len(body_lost), lost_chars=body_lost_chars,
               frontback_pages=sum(1 for p in pages if p["kind"] == "frontback"),
               declared_pages=sum(1 for p in pages if p["kind"] == "declared"),
               policy=POLICY, status=status)
    return row

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(
        L.WORK, "批次", "旧md文本量审计-2026-10-18"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--summarize", action="store_true",
                    help="只读 results.jsonl 重出报告与 summary.json，不重新测量")
    a = ap.parse_args()

    batch = os.path.abspath(a.out)
    tmpdir = os.path.join(batch, "tmp")
    os.makedirs(tmpdir, exist_ok=True)

    if a.summarize:
        path = os.path.join(batch, "results.jsonl")
        rows = [json.loads(line) for line in open(path, encoding="utf-8")]
        books, unresolved = [], []
        for r in rows:
            r["status"] = rerender_status(r)
        counts = collections.Counter(r["status"] for r in rows)
        report_rows(rows, counts, unresolved)
        write_summary(batch, rows, counts, len(rows), unresolved)
        return 0

    books, unresolved = scope()
    items = group(books)
    if a.limit:
        items = items[: a.limit]
    print("旧 md 目录 %d 个 → 单 owner %d 本、合集 %d 个源（%d 本）"
          % (len(books), sum(1 for i in items if i["mode"] == "direct"),
             sum(1 for i in items if i["mode"] == "collection"),
             sum(i["n_books"] for i in items if i["mode"] == "collection")))
    print("格式：%s" % dict(collections.Counter(i["format"] for i in items)))
    if unresolved:
        print("解析不到书：%s" % unresolved)

    scope = os.path.join(batch, "scope.tsv")
    if os.path.isfile(scope):
        os.replace(scope, scope + ".prev-" + time.strftime("%Y%m%dT%H%M%S"))
    with open(scope, "w", encoding="utf-8") as fh:
        fh.write("mode\tformat\tuid\ttitle\tdir\tsrc\n")
        for i in items:
            for m in i["members"]:
                fh.write("\t".join([i["mode"], i["format"], m["uid"], m["title"],
                                    os.path.relpath(m["dir"], L.ROOT),
                                    i["src"]]) + "\n")

    rows = []
    jsonl = os.path.join(batch, "results.jsonl")
    seen = {}
    if os.path.isfile(jsonl):
        for line in open(jsonl, encoding="utf-8"):
            r = json.loads(line)
            if r.get("policy") == POLICY:
                seen[r["source"]] = r
    if seen:
        print("续跑：results.jsonl 里已有 %d 项是同一口径（%s），跳过" % (len(seen), POLICY))
    mode = "a" if os.path.isfile(jsonl) else "w"
    with open(jsonl, mode, encoding="utf-8") as fh:
        for n, i in enumerate(items, 1):
            if i["src"] in seen:
                rows.append(seen[i["src"]])
                continue
            try:
                r = audit(i, tmpdir)
            except Exception as exc:  # 源书自身坏了（如 OPF 未声明前缀）不该拖停整遍审计
                r = {"mode": i["mode"], "format": i["format"], "source": i["src"],
                     "books": [m["uid"] for m in i["members"]], "n_books": i["n_books"],
                     "status": "error", "note": "%s: %s" % (type(exc).__name__, exc)}
            rows.append(r)
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            label = i["members"][0]["title"] if i["mode"] == "direct" \
                else "%s(+%d)" % (i["members"][0]["title"], i["n_books"] - 1)
            print("[%d/%d] %-9s %-38.38s 比 %6s 覆盖 %6s%s" % (
                n, len(items), r["status"], label,
                "%.1f%%" % (r["ratio"] * 100) if "ratio" in r else "-",
                "%.1f%%" % (r["cover"] * 100) if "cover" in r else "-",
                ("  %d md" % r["md_files"] if r.get("md_files") else "")
                + ("  缺正文 %d 页/%d 字" % (r.get("lost_pages", 0), r["lost_chars"])
                   if r.get("lost_chars") else "")),
                flush=True)

    counts = collections.Counter(r["status"] for r in rows)
    report_rows(rows, counts, unresolved)
    write_summary(batch, rows, counts, len(books), unresolved)
    print("\n结果落 %s" % os.path.relpath(jsonl, L.ROOT))
    return 1 if counts.get("error") or counts.get("convert-failed") else 0


def rerender_status(r):
    """按存下来的缺页表重算结论（供 --summarize 用，口径与测量时同一套）。"""
    if r["status"] in ("error", "convert-failed", "skipped-pdf", "NOT CONVERTED",
                       "ok (no-text source)"):
        return r["status"]
    pages = r.get("pages") or []
    body = [p for p in pages if p["kind"] == "body"]
    r["lost_pages"] = len(body)
    r["lost_chars"] = sum(p["miss_chars"] for p in body)
    if r["lost_chars"] >= BODY_MIN:
        return "LOST"
    if any(p.get("hits") == 0 for p in pages):
        return "excluded"
    if r.get("raw", 0) < 100:
        return "NOT CONVERTED"
    if (r.get("cover") or 1.0) < 0.90:
        return "short"
    if (r.get("ratio") or 1.0) > 1.15:
        return "HIGH"
    return "ok"


def report_rows(rows, counts, unresolved):
    lost = [r for r in rows if r["status"] == "LOST"]
    low = [r for r in rows if r["status"] in ("LOST", "excluded", "short", "NOT CONVERTED",
                                              "convert-failed", "error")]
    print("\n状态统计")
    for k, v in sorted(counts.items()):
        print("  %-20s %d" % (k, v))
    if lost:
        print("\n丢正文的 %d 项（缺页未在 README 的未收录里声明）" % len(lost))
        for r in sorted(lost, key=lambda x: -x["lost_chars"]):
            print("  %-40.40s 缺 %6d 字，共 %d 页  源 %s" % (
                r["books"][0], r["lost_chars"], r.get("lost_pages", 0),
                os.path.basename(r["source"])[:28]))
            for e in sorted((p for p in r["pages"] if p["kind"] == "body"),
                            key=lambda x: -x["miss_chars"])[:6]:
                print("        缺 %6d 字 / 页共 %6d 字  %-24s %s"
                      % (e["miss_chars"], e["cjk"], e["doc"][-24:], e["head"][:28]))
    if low:
        print("\n其余要看的 %d 项" % (len(low) - len(lost)))
        for r in low:
            if r["status"] == "LOST":
                continue
            print("  %-9s %-40.40s 比 %6s 覆盖 %6s 缺页 %d %s" % (
                r["status"], os.path.basename(r["source"]),
                "%.1f%%" % (r["ratio"] * 100) if "ratio" in r else "-",
                "%.1f%%" % (r["cover"] * 100) if "cover" in r else "-",
                len(r.get("absent") or []), r.get("note", "")[:30]))


def write_summary(batch, rows, counts, nbooks, unresolved):
    lost = [r for r in rows if r["status"] == "LOST"]
    low = [r for r in rows if r["status"] in ("LOST", "excluded", "short", "NOT CONVERTED",
                                              "convert-failed", "error")]
    summary = {"items": len(rows), "books": nbooks, "unresolved": unresolved,
               "counts": dict(counts),
               "lost": [{"source": r["source"], "books": r["books"],
                         "lost_chars": r["lost_chars"], "lost_pages": r.get("lost_pages"),
                         "cover": r.get("cover"), "ratio": r.get("ratio"),
                         "pages": [e for e in r.get("pages", []) if e["kind"] == "body"]}
                        for r in lost],
               "low": [{"source": r["source"], "status": r["status"],
                        "ratio": r.get("ratio"), "cover": r.get("cover"),
                        "books": r["books"],
                        "note": r.get("note")} for r in low]}
    with open(os.path.join(batch, "summary.json") + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(os.path.join(batch, "summary.json") + ".tmp",
               os.path.join(batch, "summary.json"))


if __name__ == "__main__":
    sys.exit(main())
