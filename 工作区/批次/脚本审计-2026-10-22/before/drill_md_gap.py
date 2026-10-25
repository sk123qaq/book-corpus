#!/usr/bin/env python3
"""把「md 与源书的文本量差」落到具体页上（只读）。

对每个 spine 文档取 N 个等距窗口（默认 10 个，每个 40 字，去空白归一化），数有多少
窗口能在给定 md 目录里找到，得到该页的覆盖率：
  1.0       整页都能在 md 里找到
  0 < x < 1 部分找到（旧转换器会在正文里插脚注标记，个别窗口会因此对不上）
  0         一个窗口都对不上，页的文字不在 md 里
多窗口是为了抗插标记造成的单点失配：单窗口会把好页误报成丢失。

md 侧反向抽样：md 的片段有多少能在源书里找到，判「md 文本是否根本不来自这个源」。

用法: python3 drill_md_gap.py --src <epub> --dirs <md 目录>[,<md 目录>...] [--json out]
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.expanduser("~/.agents/skills/ebook-to-md/scripts"))
from audit_volume import Epub, raw_doc_counts, folder_chars  # noqa: E402

SHINGLE = 40
WINDOWS = 10


def norm(text):
    return re.sub(r"\s", "", text)


def body_of(path):
    t = open(path, encoding="utf-8", errors="ignore").read()
    if t.startswith("---"):
        e = t.find("\n---", 3)
        if e != -1:
            t = t[t.find("\n", e + 1) + 1:]
    return t


def windows(text, n=WINDOWS):
    """归一化文本上 n 个等距窗口；文本太短就只给一个窗口。"""
    if len(text) < 20:
        return []
    if len(text) <= SHINGLE:
        return [text]
    out = []
    for i in range(n):
        pos = i / (n - 1)
        start = max(0, min(int(len(text) * pos), len(text) - SHINGLE))
        out.append(text[start:start + SHINGLE])
    return out


def coverage(text, haystack):
    ws = windows(text)
    if not ws:
        return None
    return sum(1 for w in ws if w in haystack) / len(ws)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", required=True)
    ap.add_argument("--dirs", required=True)
    ap.add_argument("--json")
    ap.add_argument("--by-md", action="store_true",
                    help="逐 md 文件算覆盖率（哪些 md 的文字不来自这个源）")
    ap.add_argument("--show-missing", type=int, default=0, metavar="N",
                    help="每页最多打印 N 个对不上的窗口文字")
    a = ap.parse_args()

    dirs = [d for d in a.dirs.split(",") if d]
    md_text, md_files = "", 0
    for d in dirs:
        for name in sorted(os.listdir(d)):
            if name.endswith(".md") and name != "README.md":
                md_text += norm(body_of(os.path.join(d, name)))
                md_files += 1
    epub = Epub(os.path.abspath(a.src))
    doc_counts = raw_doc_counts(epub)
    total = sum(doc_counts.values())
    epub_norm, epub_all = {}, ""
    for doc in epub.content_docs():
        soup = epub.soup(doc)
        body = soup.find("body") if soup else None
        epub_norm[doc] = norm(body.get_text(" ", strip=True)) if body else ""
        epub_all += epub_norm[doc]

    print("源 %s" % os.path.basename(a.src))
    print("md %d 个文件，%d 字（去空白）；源 %d 页，%d 字\n"
          % (md_files, len(md_text), len(doc_counts), total))

    rows = []
    for doc in epub.content_docs():
        text = epub_norm[doc]
        cov = coverage(text, md_text)
        rows.append((doc, doc_counts.get(doc, 0), cov, text))
    missing = [r for r in rows if r[2] == 0]
    partial = [r for r in rows if r[2] is not None and 0 < r[2] < 1]
    full = [r for r in rows if r[2] == 1]
    print("源侧：%d 页全覆盖，%d 页部分覆盖，%d 页一个窗口都对不上"
          % (len(full), len(partial), len(missing)))
    for label, group in (("对不上", missing), ("部分覆盖", partial)):
        if not group:
            continue
        print("\n%s（按字数降序）：" % label)
        for doc, n, cov, text in sorted(group, key=lambda x: -x[1]):
            print("  %-4s %-40s %8d 字  %s" % (
                "-" if cov is None else "%.0f%%" % (cov * 100), doc[-40:], n,
                (text[:20] if text else "(空)")))
            if a.show_missing:
                shown = 0
                for w in windows(text):
                    if w not in md_text:
                        print("        缺：%s" % w[:38])
                        shown += 1
                        if shown >= a.show_missing:
                            break
    lost = sum(n for _d, n, c, _t in missing)
    part = sum(n for _d, n, c, _t in partial)
    print("\n对不上的页合计 %d 字（占源 %.1f%%）；部分覆盖 %d 字（%.1f%%）"
          % (lost, 100.0 * lost / max(1, total), part, 100.0 * part / max(1, total)))

    if a.by_md:
        print("\n逐 md 文件覆盖率（针对源书全文，非单页）：")
        for d in dirs:
            for name in sorted(os.listdir(d)):
                if not name.endswith(".md") or name == "README.md":
                    continue
                t = norm(body_of(os.path.join(d, name)))
                cov = coverage(t, epub_all)
                print("  %-4s %8d 字  %s" % ("-" if cov is None else "%.0f%%" % (cov * 100),
                                              len(t), os.path.join(os.path.basename(d), name)[-58:]))

    probes = []
    for d in dirs:
        for name in sorted(os.listdir(d)):
            if not name.endswith(".md") or name == "README.md":
                continue
            t = norm(body_of(os.path.join(d, name)))
            for pos, w in enumerate(windows(t)):
                probes.append((name, pos, w))
    hits = sum(1 for _n, _p, w in probes if w in epub_all)
    print("md 侧：%d 个抽样窗口，%d 个能在源书里找到（%.1f%%）"
          % (len(probes), hits, 100.0 * hits / max(1, len(probes))))

    if a.json:
        payload = {"src": a.src, "dirs": dirs, "md_files": md_files, "md_chars": len(md_text),
                   "epub_chars": total,
                   "missing": [{"doc": d, "chars": n} for d, n, _c, _t in
                               sorted(missing, key=lambda x: -x[1])],
                   "partial": [{"doc": d, "chars": n, "coverage": c} for d, n, c, _t in
                               sorted(partial, key=lambda x: -x[1])],
                   "missing_chars": lost, "partial_chars": part,
                   "md_probes": len(probes), "md_probe_hits": hits}
        with open(a.json + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(a.json + ".tmp", a.json)
        print("JSON 落 %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
