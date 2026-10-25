#!/usr/bin/env python3
"""核对 ebook-to-md 的 README 目录链接修复（目标含空格/括号时加尖括号）。

只读脚本：除 --json 指定的文件外，不写任何东西。

  readme_link_fix_check.py --affected        # 还没重跑的书，README 里裸目标含空格或括号的行数
  readme_link_fix_check.py --verify          # 已落库的 md 与同书重跑前备份逐文件对比
  readme_link_fix_check.py --verify --json X # 把同样的数字存成 JSON
  readme_link_fix_check.py --json X          # 默认按 --verify 跑

裸目标指 `- [label](target)` 里 target 不以 `<` 开头；target 里出现空格或括号时
CommonMark 就不再把它当链接目标。渲染成字面的行数用 commonmark 解析器实测，
解析器取自隔离安装 /tmp/pycommonmark（先试直接 import）。
"""
import argparse
import datetime
import glob
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BATCH = os.path.join(ROOT, "工作区/批次/md拆分-2026-10-18")
LOGS = os.path.join(BATCH, "logs")
OUT = os.path.join(BATCH, "out")
ISOLATED_COMMONMARK = "/tmp/pycommonmark"

TOC_RE = re.compile(r"^\s*-\s+\[(?P<label>.*)\]\((?P<target>.*)\)\s*$")
HREF_RE = re.compile(r'<a href="([^"]*)"')
WRAP_CHARS = "() "


def render_html(text):
    try:
        import commonmark
    except ImportError:
        sys.path.insert(0, ISOLATED_COMMONMARK)
        import commonmark
    return commonmark.commonmark(text)


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def batch_logs():
    logs = {}
    for path in glob.glob(os.path.join(LOGS, "*.json")):
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
        logs[rec["uid"]] = rec
    return logs


def uid_key(uid):
    m = re.match(r"^[A-Za-z]*(\d+)$", uid)
    return (int(m.group(1)) if m else 0, uid)


def backups(uid):
    found = []
    for path in glob.glob(os.path.join(OUT, uid + ".prev-*")):
        if os.path.isdir(path):
            found.append(path)
    return sorted(found)


def readme_stats(path):
    """README 的目录行统计。before/after 用同一个函数量，口径一致。"""
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    rows = [m for m in (TOC_RE.match(line) for line in raw.splitlines()) if m]
    bare = 0
    for m in rows:
        target = m.group("target")
        if not target.startswith("<") and any(c in target for c in WRAP_CHARS):
            bare += 1
    anchors = HREF_RE.findall(render_html(raw))
    hrefs = {urllib.parse.unquote(html.unescape(h)) for h in anchors}
    literal = 0
    for m in rows:
        target = m.group("target")
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1]
        if target not in hrefs:
            literal += 1
    return {
        "toc_lines": len(rows),
        "bare": bare,
        "literal": literal,
        "links": len(rows) - literal,
        "html_links": len(anchors),
    }


def tree_diff(live, backup):
    """两边相对路径的并集，逐个比 sha256。返回 [(relpath, kind)]。"""
    trees = {}
    for tag, base in (("live", live), ("backup", backup)):
        names = set()
        for root, _dirs, files in os.walk(base):
            for name in files:
                names.add(os.path.relpath(os.path.join(root, name), base))
        trees[tag] = names
    out = []
    for rel in sorted(trees["live"] | trees["backup"]):
        a = os.path.join(live, rel)
        b = os.path.join(backup, rel)
        if not os.path.isfile(a) or not os.path.isfile(b):
            out.append((rel, "missing"))
        elif digest(a) != digest(b):
            out.append((rel, "content"))
    return out


def md_dir(rec):
    return os.path.join(ROOT, rec["dest"])


def mode_affected(logs):
    rows = []
    for uid in sorted(logs, key=uid_key):
        rec = logs[uid]
        readme = os.path.join(md_dir(rec), "README.md")
        if not os.path.isfile(readme):
            continue
        stats = readme_stats(readme)
        if stats["bare"]:
            rows.append((uid, stats["bare"]))
    total = sum(n for _uid, n in rows)
    for uid, n in rows:
        print("%s\t%d" % (uid, n))
    print("TOTAL %d books, %d entries" % (len(rows), total))
    return {"books": len(rows), "entries": total,
            "per_book": {uid: n for uid, n in rows}}


def mode_verify(logs):
    rows = []
    no_backup = []
    other_diffs = []
    anomalies = []
    for uid in sorted(logs, key=uid_key):
        rec = logs[uid]
        live = md_dir(rec)
        prev = backups(uid)
        entry = {"uid": uid, "title": rec.get("title"), "status": rec.get("status"),
                 "dest": rec.get("dest"), "backup": prev[-1] if prev else None}
        if os.path.isfile(os.path.join(live, "README.md")):
            entry["after"] = readme_stats(os.path.join(live, "README.md"))
        if not prev:
            no_backup.append(uid)
            entry["before"] = None
            entry["diffs"] = None
            rows.append(entry)
            continue
        backup = prev[-1]
        diffs = tree_diff(live, backup)
        readme_diff = [r for r, k in diffs if r == "README.md"]
        manifest_diff = [r for r, k in diffs if r == ".manifest.json"]
        content_diff = [(r, k) for r, k in diffs if r not in ("README.md", ".manifest.json")]
        entry["diffs"] = {"readme": readme_diff, "manifest": manifest_diff,
                          "content": content_diff}
        readme = os.path.join(backup, "README.md")
        entry["before"] = readme_stats(readme) if os.path.isfile(readme) else None
        if content_diff:
            other_diffs.append((uid, content_diff))
        rows.append(entry)

    header = ("uid", "status", "nonREADME", "bare before>after", "literal before>after",
              "toc", "html_links")
    print("%-7s %-9s %-10s %-17s %-19s %-6s %-6s" % header)
    for e in rows:
        before = e.get("before") or {}
        after = e.get("after") or {}
        diffs = e.get("diffs") or {}
        content = diffs.get("content")
        nondiff = "-" if content is None else ("%d" % len(content) + ("" if not content else "!"))
        print("%-7s %-9s %-10s %-17s %-19s %-6s %-6s" % (
            e["uid"], e.get("status"), nondiff,
            "%s>%s" % (before.get("bare", "-"), after.get("bare", "-")),
            "%s>%s" % (before.get("literal", "-"), after.get("literal", "-")),
            after.get("toc_lines", "-"), after.get("html_links", "-")))
        for name, kind in (content or []):
            print("    content diff: %s (%s)" % (name, kind))
        for label in ("before", "after"):
            stats = e.get(label) or {}
            if stats and stats["html_links"] != stats["links"]:
                anomalies.append((e["uid"], label, stats["links"], stats["html_links"]))

    with_backup = [e for e in rows if e.get("before")]
    tot = {
        "books": len(rows),
        "books_with_backup": len(with_backup),
        "books_without_backup": len(no_backup),
        "bare_before": sum(e["before"]["bare"] for e in with_backup),
        "bare_after": sum((e.get("after") or {}).get("bare", 0) for e in with_backup),
        "literal_before": sum(e["before"]["literal"] for e in with_backup),
        "literal_after": sum((e.get("after") or {}).get("literal", 0) for e in with_backup),
        "bare_after_all_books": sum((e.get("after") or {}).get("bare", 0) for e in rows),
        "literal_after_all_books": sum((e.get("after") or {}).get("literal", 0) for e in rows),
        "books_with_content_diff": len(other_diffs),
        "books_with_manifest_diff": sum(1 for e in rows if (e.get("diffs") or {}).get("manifest")),
        "books_with_readme_diff": sum(1 for e in rows if (e.get("diffs") or {}).get("readme")),
    }
    print("\n合计（%d 本有备份，%d 本无备份，共 %d 本）" %
          (tot["books_with_backup"], tot["books_without_backup"], tot["books"]))
    print("  裸目标待加尖括号  before %d  after %d" % (tot["bare_before"], tot["bare_after"]))
    print("  渲染成字面的目录行  before %d  after %d" % (tot["literal_before"], tot["literal_after"]))
    print("  README 之外的内容差异书数 %d（.manifest.json 差异书数 %d）" %
          (tot["books_with_content_diff"], tot["books_with_manifest_diff"]))
    print("  无备份的书 %d 本：%s" % (len(no_backup), " ".join(no_backup[:12]) +
                                   (" …" if len(no_backup) > 12 else "")))
    if other_diffs:
        print("\nREADME 与 .manifest.json 之外有差异的书（应为 0）")
        for uid, items in other_diffs[:40]:
            print("  %s %s" % (uid, items))
    if anomalies:
        print("\n解析出的链接数与会话数不符（应为 0）: %s" % anomalies[:10])
    return {"totals": tot, "per_book": rows, "no_backup": no_backup,
            "anomalies": anomalies, "content_diff_books": [u for u, _ in other_diffs]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--affected", action="store_true",
                    help="列出 README 里裸目标含空格或括号的 md 目录")
    ap.add_argument("--verify", action="store_true", help="逐文件对比落库产物与重跑前备份")
    ap.add_argument("--json", help="把同样的数字写成 JSON")
    a = ap.parse_args()
    if not a.affected and not a.verify:
        a.verify = True
    if a.verify and not os.path.isdir(OUT):
        raise SystemExit("缺重跑前备份目录 %s（批次收尾时已删），--verify 无从比对" % OUT)

    logs = batch_logs()
    payload = {"batch": os.path.basename(BATCH), "at": datetime.datetime.now().astimezone()
               .replace(microsecond=0).isoformat(), "batch_books": len(logs)}
    if a.affected:
        print("== affected: 目录里裸目标含空格或括号的 md 目录 ==")
        payload["affected"] = mode_affected(logs)
    if a.verify:
        print("== verify: 落库 md 与重跑前备份的逐文件对比 ==")
        payload["verify"] = mode_verify(logs)
    if a.json:
        with open(a.json + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(a.json + ".tmp", a.json)
        print("\nJSON 落 %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
