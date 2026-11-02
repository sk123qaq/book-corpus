#!/usr/bin/env python3
"""合集重拆后的覆盖率验收：源书每页的 10 个 20 字 CJK 锚点在给定 md 目录里命中多少。

与 redo_compare.py 同一口径，用于合集（一个源文件拆多本、旧版本散在 .prev 目录）。
两边的目录都显式给出，不猜：--old 与 --new 各收一个或多个 md 目录（走遍其下所有 .md）。

用法:
  python3 collection_coverage.py --src <合集.epub> \
      --old <旧md目录1> <旧md目录2> ... --new <新md目录1> <新md目录2> ...
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L  # noqa: E402
sys.path.insert(0, os.path.join(L.skill_dir("ebook-to-md"), "scripts"))
from audit_volume import Epub  # noqa: E402
from audit_old_md import page_anchors  # noqa: E402

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def md_cjk(dirs):
    parts = []
    for d in dirs:
        for root, _sub, files in os.walk(d):
            for name in sorted(files):
                if not name.endswith(".md") or name == "README.md":
                    continue
                text = open(os.path.join(root, name), encoding="utf-8").read()
                if text.startswith("---"):
                    end = text.find("\n---", 3)
                    if end != -1:
                        nl = text.find("\n", end + 1)
                        text = text[nl + 1:] if nl != -1 else ""
                parts.append("".join(CJK_RE.findall(text)))
    return "".join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", required=True)
    ap.add_argument("--old", nargs="+", required=True)
    ap.add_argument("--new", nargs="+", required=True)
    ap.add_argument("--details", action="store_true", help="打印每页命中数")
    ap.add_argument("--out", help="把覆盖率写成 JSON")
    a = ap.parse_args()

    old = md_cjk(a.old)
    new = md_cjk(a.new)
    ep = Epub(os.path.abspath(a.src))
    total = old_hits = new_hits = 0
    pages = []
    for doc in ep.content_docs():
        soup = ep.soup(doc)
        body = soup.find("body") if soup else None
        text = "".join(CJK_RE.findall(body.get_text(" ", strip=True) if body else ""))
        anchors = page_anchors(text)
        if not anchors:
            continue
        ho = sum(1 for x in anchors if x in old)
        hn = sum(1 for x in anchors if x in new)
        total += len(anchors)
        old_hits += ho
        new_hits += hn
        pages.append({"doc": doc, "anchors": len(anchors), "cjk": len(text),
                      "old_hits": ho, "new_hits": hn})
        if a.details:
            print("  %-28s cjk=%6d old %2d/%d new %2d/%d"
                  % (doc[-28:], len(text), ho, len(anchors), hn, len(anchors)))
    if not total:
        print("源书锚点为空，无法算覆盖率")
        return 1
    print("old %d/%d = %.1f%%   new %d/%d = %.1f%%"
          % (old_hits, total, 100 * old_hits / total, new_hits, total,
             100 * new_hits / total))
    print("old CJK %d  new CJK %d" % (len(old), len(new)))
    if a.out:
        tmp = a.out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"src": os.path.abspath(a.src), "total": total,
                       "old_hits": old_hits, "new_hits": new_hits,
                       "old_ratio": round(old_hits / total, 4),
                       "new_ratio": round(new_hits / total, 4),
                       "old_cjk": len(old), "new_cjk": len(new), "pages": pages},
                      fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(tmp, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
