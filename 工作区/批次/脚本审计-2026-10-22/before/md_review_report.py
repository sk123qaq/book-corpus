#!/usr/bin/env python3
"""待查 md 的证据汇总：把 verify/audit 的报警落到可判断的事实上。

对每本书给出：
  md 侧字符构成（正文 / 图片路径）、跨文件重复的 120 字片段数、自链数
  spine 文档里 md 完全没收录的页，以及它们各占多少字
  未收录页按原因计数
只读，不改任何文件。

用法: python3 md_review_report.py <批次目录> [uid ...]
"""
import collections
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L  # noqa: E402

sys.path.insert(0, os.path.expanduser("~/.agents/skills/ebook-to-md/scripts"))
from ebook_to_md import Epub  # noqa: E402

IMG_MD = re.compile(r"!\[[^\]]*\]\(([^)]*)\)")
LINK_MD = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]*)\)")
SHINGLE = 120
STATUSES = ("staged-review", "error", "convert-failed", "split-failed", "staged")


def body_of(path):
    t = open(path, encoding="utf-8", errors="ignore").read()
    if t.startswith("---"):
        e = t.find("\n---", 3)
        if e != -1:
            t = t[t.find("\n", e + 1) + 1:]
    return t


def plain(text):
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#*`>]", "", text)
    return re.sub(r"\s", "", text)


def shingles(text, n=5):
    if len(text) < SHINGLE:
        return [text] if len(text) >= 20 else []
    step = max(1, (len(text) - SHINGLE) // n)
    return [text[i * step:i * step + SHINGLE] for i in range(n)]


def sources():
    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(L.CAT, "library.db"), uri=True)
    return {r[0]: r[1] for r in con.execute(
        "SELECT uid, file FROM source WHERE primary_src=1")}


def source_paths(uid, fmt, batch):
    src = sources().get(uid)
    if not src:
        return None, None
    if fmt not in ("mobi", "azw3", "azw"):
        return os.path.join(L.ROOT, src), os.path.join(L.ROOT, src)
    tmp = os.path.join(batch, "tmp", uid, "book.epub")
    return os.path.join(L.ROOT, src), (tmp if os.path.exists(tmp) else None)


def coverage(epub, probes):
    """spine 文档里 md 一个字都没收录的页，(字数, 文档) 降序。"""
    joined = "\n".join(probes)
    missing = []
    for d in epub.content_docs():
        soup = epub.soup(d)
        body = soup.find("body") if soup else None
        text = re.sub(r"\s", "", body.get_text(" ", strip=True)) if body else ""
        if len(text) < 60:
            continue
        if not any(sh in joined for sh in (shingles(text, 3) or [text])):
            missing.append((len(text), d))
    return sorted(missing, reverse=True)


def main():
    batch = os.path.join(L.WORK, "批次", sys.argv[1])
    only = set(sys.argv[2:]) or None
    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(L.CAT, "library.db"), uri=True)
    con.row_factory = sqlite3.Row
    out = []
    for name in sorted(os.listdir(os.path.join(batch, "logs"))):
        if not name.endswith(".json"):
            continue
        r = json.load(open(os.path.join(batch, "logs", name), encoding="utf-8"))
        if r["status"] not in STATUSES or (only and r["uid"] not in only):
            continue
        uid = r["uid"]
        folder = os.path.join(batch, "out", uid)
        info = {"uid": uid, "title": r["title"][:34], "status": r["status"],
                "format": r["format"], "chapters": r.get("chapters"),
                "ratio": r.get("audit_ratio")}
        probes = []
        if os.path.isdir(folder):
            files = [f for f in sorted(os.listdir(folder))
                     if f.endswith(".md") and f != "README.md"]
            seen, dup, img_chars, text_chars, self_links = {}, 0, 0, 0, 0
            for f in files:
                b = body_of(os.path.join(folder, f))
                img_chars += sum(len(i) for i in IMG_MD.findall(b))
                body = plain(b)
                text_chars += len(body)
                self_links += sum(1 for t in LINK_MD.findall(b) if t == f)
                for sh in shingles(body):
                    if sh in seen and seen[sh] != f:
                        dup += 1
                    seen.setdefault(sh, f)
                probes.append(body)
            info.update(files=len(files), text_chars=text_chars,
                        image_path_chars=img_chars, dup_shingles=dup,
                        self_links=self_links)
            rp = os.path.join(folder, "README.md")
            if os.path.isfile(rp):
                t = open(rp, encoding="utf-8", errors="ignore").read()
                sec = t.split("## 未收录")[-1] if "## 未收录" in t else ""
                info["excluded"] = dict(collections.Counter(
                    re.sub(r"^.*?— ", "", ln.strip()).strip()
                    for ln in sec.splitlines() if ln.strip().startswith("- ")))
        log = os.path.join(batch, "logs", uid + ".log")
        if os.path.isfile(log) and os.path.getsize(log):
            t = open(log, encoding="utf-8", errors="ignore").read()
            pats = {"identical": r"identical content[^\n]*",
                    "broken link": r"broken link ->[^\n]*",
                    "missing image": r"missing image[^\n]*",
                    "orphan image": r"orphan image on disk[^\n]*",
                    "no H1": r"no H1 chapter title[^\n]*"}
            v = {}
            for k, p in pats.items():
                hits = re.findall(p, t)
                if hits:
                    v[k] = hits[:2] if k != "identical" else hits[:3]
            if v:
                info["verify"] = v
        src, epub_path = source_paths(uid, r["format"], batch)
        if epub_path and probes:
            try:
                info["uncovered"] = coverage(Epub(epub_path), probes)
            except Exception as exc:
                info["uncovered"] = "EPUB 读不了: %s" % exc
        out.append(info)

    for i in out:
        ex = i.get("excluded") or {}
        ratio = "?" if i["ratio"] is None else "%.1f%%" % (i["ratio"] * 100)
        print(f"\n{i['uid']} {i['title']}  [{i['status']}/{i['format']}]  "
              f"ch={i['chapters']} ratio={ratio}")
        print(f"   正文 {i.get('text_chars', 0):,} | 图片路径 {i.get('image_path_chars', 0):,} | "
              f"跨文件重复片段 {i.get('dup_shingles', 0)} | 自链 {i.get('self_links', 0)}")
        un = i.get("uncovered")
        if isinstance(un, list):
            if un:
                total = sum(n for n, _ in un)
                print(f"   md 未收录的 spine 页 {len(un)} 个，合计 {total:,} 字；"
                      f"前几个：{[n for n, _ in un[:5]]}")
            else:
                print("   md 未收录的 spine 页 0 个（原文正文全覆盖）")
        elif un:
            print("  ", un)
        if i.get("verify"):
            for k, v in i["verify"].items():
                print(f"   verify {k}: {len(v)}")
                for s in v:
                    print(f"      {s.strip()[:130]}")
        if ex:
            print(f"   未收录: {ex}")
    json.dump(out, open("/tmp/md_review.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
