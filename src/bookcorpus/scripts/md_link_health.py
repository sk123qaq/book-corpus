#!/usr/bin/env python3
"""全库 md 链接健康检查（只读，不改任何文件）。

两条判据：
  1. 章节文件里的自链（链接目标恰好是自己文件名）0 条。
  2. 每个 README.md 的 `## 目录` 段的目录行全部成链接，且目录首行缩进 < 4
     （>=4 会被当缩进代码块）。有 commonmark 时按渲染结果判；没有时按 CommonMark
     的链接目标规则判：`<...>` 形式恒可，裸目标不得含空白且括号要配对。

用法:
  python3 md_link_health.py                     # 摘要
  python3 md_link_health.py --verbose           # 有问题的书逐本列出
  python3 md_link_health.py --json X            # 机器可读结果落 X
"""
import argparse
import datetime
import html
import json
import os
import re
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib_common import ROOT  # noqa: E402
LIB = os.path.join(ROOT, "库")

LINK_MD = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]*)\)")
TOC_RE = re.compile(r"^(\s*)[-*]\s+\[(?P<label>.*)\]\((?P<target>.*)\)\s*$")
HREF_RE = re.compile(r'<a href="([^"]*)"')


def render_html(text):
    """有 commonmark 就按渲染结果判；没有时返回 None，改用 link_dest_ok 的规则。"""
    try:
        import commonmark
    except ImportError:
        return None
    return commonmark.commonmark(text)


def link_dest_ok(target):
    """CommonMark 的行内链接目标：`<...>` 恒可；裸目标不得含空白，括号要配对。"""
    t = target.strip()
    if t.startswith("<") and t.endswith(">"):
        return True
    depth = 0
    for ch in t:
        if ch.isspace():
            return False
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def body_of(path):
    t = open(path, encoding="utf-8", errors="ignore").read()
    if t.startswith("---"):
        e = t.find("\n---", 3)
        if e != -1:
            t = t[t.find("\n", e + 1) + 1:]
    return t


def toc_section(text):
    """`## 目录` 之后的正文，到下一个 `## ` 标题为止。没有这一节时返回 None。"""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "## 目录":
            start = i + 1
            break
    if start is None:
        return None
    out = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        out.append(line)
    return out


def readme_stats(path):
    text = open(path, encoding="utf-8", errors="ignore").read()
    lines = toc_section(text)
    if lines is None:
        return {"toc_section": False, "toc_lines": 0, "literal": 0, "links": 0,
                "first_indent": None}
    rows = []
    first_indent = None
    for line in lines:
        if first_indent is None and line.strip():
            first_indent = len(line) - len(line.lstrip(" "))
        m = TOC_RE.match(line)
        if m:
            rows.append((m.group(1), m.group("target")))
    rendered = render_html("\n".join(lines))
    hrefs = ({urllib.parse.unquote(html.unescape(h)) for h in HREF_RE.findall(rendered)}
             if rendered is not None else None)
    literal = 0
    for _indent, target in rows:
        if hrefs is None:
            if not link_dest_ok(target):
                literal += 1
            continue
        t = target
        if t.startswith("<") and t.endswith(">"):
            t = t[1:-1]
        if t not in hrefs:
            literal += 1
    return {"toc_section": True, "toc_lines": len(rows), "literal": literal,
            "links": len(rows) - literal, "first_indent": first_indent}


def scan():
    dirs = []
    for root, subdirs, _files in os.walk(LIB):
        for name in list(subdirs):
            if name == "md":
                dirs.append(os.path.join(root, name))
        subdirs[:] = [d for d in subdirs if not d.startswith("_")]
    dirs.sort()
    self_links = []
    readmes = []
    no_readme = []
    for d in dirs:
        for name in sorted(os.listdir(d)):
            if not name.endswith(".md"):
                continue
            p = os.path.join(d, name)
            if name == "README.md":
                continue
            body = body_of(p)
            n = sum(1 for t in LINK_MD.findall(body) if t.strip().strip("<>") == name)
            if n:
                self_links.append((os.path.relpath(p, ROOT), n))
        rp = os.path.join(d, "README.md")
        if not os.path.isfile(rp):
            no_readme.append(os.path.relpath(d, ROOT))
            continue
        s = readme_stats(rp)
        s["readme"] = os.path.relpath(rp, ROOT)
        readmes.append(s)
    return {"md_dirs": len(dirs), "readmes": readmes, "no_readme": no_readme,
            "self_links": self_links}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verbose", action="store_true", help="逐本列出有问题的书")
    ap.add_argument("--json", help="机器可读结果落这个路径")
    a = ap.parse_args()

    r = scan()
    bad_self = r["self_links"]
    bad_literal = [s for s in r["readmes"] if s["literal"]]
    bad_indent = [s for s in r["readmes"] if s["first_indent"] is not None
                  and s["first_indent"] >= 4]
    no_section = [s for s in r["readmes"] if not s["toc_section"]]
    total_lines = sum(s["toc_lines"] for s in r["readmes"])
    total_links = sum(s["links"] for s in r["readmes"])

    print("md 目录 %d 个，README %d 个（无 README %d 个）"
          % (r["md_dirs"], len(r["readmes"]), len(r["no_readme"])))
    print("判据 1 · 自链  %d 个文件，共 %d 条" % (len(bad_self), sum(n for _p, n in bad_self)))
    print("判据 2 · 目录行合计 %d，成链接 %d，渲染成字面 %d"
          % (total_lines, total_links, total_lines - total_links))
    print("判据 2 · 首行缩进 >=4 的 README %d 个" % len(bad_indent))
    print("判据 2 · 没有 `## 目录` 段的 README %d 个" % len(no_section))

    if a.verbose:
        for p, n in bad_self:
            print("  自链 %d 条  %s" % (n, p))
        for s in bad_literal + bad_indent + no_section:
            print("  目录问题  %s  行 %d 字面 %d 首行缩进 %s"
                  % (s["readme"], s["toc_lines"], s["literal"], s["first_indent"]))
        for p in r["no_readme"]:
            print("  无 README  %s" % p)

    ok = not (bad_self or bad_literal or bad_indent or no_section)
    payload = {
        "at": datetime.datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "md_dirs": r["md_dirs"], "readmes": len(r["readmes"]),
        "no_readme": r["no_readme"],
        "self_links": {"files": len(bad_self), "entries": sum(n for _p, n in bad_self),
                       "per_file": {p: n for p, n in bad_self}},
        "toc": {"toc_lines": total_lines, "links": total_links,
                "literal": total_lines - total_links,
                "bad_literal": {s["readme"]: s["literal"] for s in bad_literal},
                "bad_indent": {s["readme"]: s["first_indent"] for s in bad_indent},
                "no_section": [s["readme"] for s in no_section]},
        "ok": ok,
    }
    if a.json:
        with open(a.json + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(a.json + ".tmp", a.json)
        print("JSON 落 %s" % a.json)
    print("判据 1 与 2 %s" % ("全过" if ok else "未过"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
