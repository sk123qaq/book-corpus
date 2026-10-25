#!/usr/bin/env python3
"""从 gold_spec.tsv 生成 gold.tsv：定位 anchor 所在章与行，截出 8-20 字逐字证据。

gold_spec.tsv 的 book 列是书目录（不含 库/ 前缀），md 列锁定章文件，anchor 必须在该章内只出现一次。
"""
import os, sqlite3, sys

ROOT = os.path.expanduser("~/文档/books")
HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "gold_spec.tsv")
OUT = os.path.join(HERE, "gold.tsv")
DB = os.path.join(ROOT, "目录", "library.db")


def locate(book, md, anchor):
    path = os.path.join(ROOT, "库", book, "md", md)
    if not os.path.isfile(path):
        sys.exit(f"没有章文件: {path}")
    hits = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for no, line in enumerate(f, 1):
            if anchor in line:
                hits.append((path, no, line.rstrip("\n")))
    if not hits:
        return None
    if len(hits) > 1:
        sys.exit("anchor 不唯一: %s @ %s\n%s" % (anchor, path,
                 "\n".join(f"  {no}" for _, no, _ in hits)))
    return hits[0]


def evidence(line, anchor):
    i = line.find(anchor)
    if len(anchor) >= 8:
        start = i
        span = min(len(anchor), 20)
    else:
        pad = (10 - len(anchor)) // 2
        start = max(0, i - pad)
        span = 16
    ev = line[start:start + span].strip()
    if not 8 <= len(ev) <= 20:
        sys.exit(f"证据长度不合规 {len(ev)}: {ev!r} in {line!r}")
    return ev


def main():
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    rows, bad = [], 0
    with open(SPEC, encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        for raw in f:
            if not raw.strip():
                continue
            rec = dict(zip(header, raw.rstrip("\n").split("\t")))
            hit = locate(rec["book"], rec["md"], rec["anchor"])
            if hit is None:
                print("未命中 anchor", rec["anchor"], rec["book"])
                bad += 1
                continue
            path, no, line = hit
            rel = os.path.relpath(path, ROOT)
            uid = con.execute("SELECT uid FROM book WHERE path=?", (rec["book"],)).fetchone()
            ev = evidence(line, rec["anchor"])
            rows.append([rec["category"], rec["question"], rec["concept"],
                         uid[0] if uid else "?", rel, str(no), ev, rec["terms"]])
    if bad:
        sys.exit(f"{bad} 条 anchor 未命中，未写 gold.tsv")
    cats = [r[0] for r in rows]
    assert len(rows) == 60, len(rows)
    for c in ("原词", "近义表达", "无原词"):
        assert cats.count(c) == 20, (c, cats.count(c))
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("qid\tcategory\tquestion\tconcept\tuid\tpath\tline\tevidence\tterms\n")
        for i, r in enumerate(rows, 1):
            f.write("q%03d\t" % i + "\t".join(r) + "\n")
    os.replace(tmp, OUT)
    print("gold.tsv", len(rows), "条", {c: cats.count(c) for c in set(cats)})


if __name__ == "__main__":
    main()
