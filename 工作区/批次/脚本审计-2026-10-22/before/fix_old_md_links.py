#!/usr/bin/env python3
"""修旧 md 目录（不是本批次产物）里两类链接缺陷，逐文件 tmp + os.replace。

1) 自链：章节文件里指向自己的链接，`[label](本文件名.md)` 只留 label。
   当前转换器就是这么处理自链的（resolve_links 里 own_file 那一路）。
2) README 目录项：目标含空格或括号时，裸写的 markdown 目标会被解析器当普通文字。
   改成 `(<目标>)`，与转换器里的 link_dest 同一规则。

只动这两类，其它字符原样保留。幂等：修完再跑一遍，计数为 0，文件 mtime 不变。
这些旧文件与 ../books.snapshot-2026-10-18 共享 inode，所以原地写会连快照一起改，
这里一律 tmp + rename，快照因此天然保留改前副本。

用法:
  python3 fix_old_md_links.py --dry-run --dirs-from /tmp/old-repair-dirs.txt
  python3 fix_old_md_links.py --apply  --dirs-from /tmp/old-repair-dirs.txt --json /tmp/old-repair.json
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L  # noqa: E402

LINK = re.compile(r"(?<!!)\[([^\]]*)\]\((<[^>]*>|[^)]*)\)")
# label 里可能有方括号（`- [Chapter […]]` 一类），目标里可能有括号，所以两头都取最靠外的
# 那一对：label 是第一个 `[` 到最后一个 `](`，目标是其后到行尾最后一个 `)`。
TOC = re.compile(r"^(\s*- )\[(.*)\]\((.*)\)\s*$")
# 目录里印出来的行，含链接项与部题项（`- **第一部**`）
TOC_ANY = re.compile(r"^(\s*)(- (?:\[.*?\]\(.*\)|\*\*.*\*\*))\s*$")


def wrap_target(target):
    """README 目录项的目标：含空格或括号就加尖括号，其余原样。"""
    if target.startswith("<") or not any(c in target for c in "() "):
        return target
    return "<%s>" % target


def fix_chapter(text, own):
    """指向自己的链接只留 label。返回 (新文本, 改了条数)。"""
    n = 0

    def repl(m):
        nonlocal n
        label, dest = m.group(1), m.group(2)
        tgt = dest[1:-1] if dest.startswith("<") else dest
        if tgt.split("#")[0] != own:
            return m.group(0)
        n += 1
        return label

    return LINK.sub(repl, text), n


def dedent_toc(text):
    """目录首行缩进 >=4 时整段下移，让首行落到 0 缩进（与转换器的 pad 同一规则）。

    CommonMark 把 4 格缩进、前面又没有列表父项的连续行当缩进代码块，那样整段目录
    一条都不成链接。返回 (新文本, 下移的行数)。
    """
    lines = text.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "## 目录")
    except StopIteration:
        return text, 0
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    printed = [l for l in lines[start + 1:end] if TOC_ANY.match(l)]
    if not printed:
        return text, 0
    shift = len(TOC_ANY.match(printed[0]).group(1))
    if shift < 4:
        return text, 0
    n = 0
    for i in range(start + 1, end):
        m = TOC_ANY.match(lines[i])
        if not m:
            continue
        cur, rest = len(m.group(1)), m.group(2)
        lines[i] = " " * max(0, cur - shift) + rest
        n += 1
    return "\n".join(lines), n


def fix_readme(text):
    """目录项里需要包裹的裸目标改成 <...>；首行缩进 >=4 的整段下移。"""
    out = []
    n = 0
    for line in text.split("\n"):
        m = TOC.match(line)
        if m:
            head, label, dest = m.group(1), m.group(2), m.group(3)
            wrapped = wrap_target(dest)
            if wrapped != dest:
                n += 1
                line = "%s[%s](%s)" % (head, label, wrapped)
        out.append(line)
    text, shifted = dedent_toc("\n".join(out))
    return text, n + shifted


def write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def repair_dir(path, apply):
    res = {"dir": path, "self_links": 0, "readme_wraps": 0, "files_touched": 0}
    for name in sorted(os.listdir(path)):
        if not name.endswith(".md"):
            continue
        p = os.path.join(path, name)
        if not os.path.isfile(p):
            continue
        text = open(p, encoding="utf-8", errors="ignore").read()
        new, n = (fix_readme(text) if name == "README.md" else fix_chapter(text, name))
        if n:
            res["self_links" if name != "README.md" else "readme_wraps"] += n
            res["files_touched"] += 1
            if apply:
                write(p, new)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dirs-from", required=True, help="一行一个 md 目录的清单文件")
    ap.add_argument("--apply", action="store_true", help="真的写；不给就只数")
    ap.add_argument("--dry-run", action="store_true", help="只数不写（默认行为）")
    ap.add_argument("--json", help="把逐目录结果写成 JSON")
    a = ap.parse_args()

    dirs = [ln.strip() for ln in open(a.dirs_from, encoding="utf-8") if ln.strip()]
    missing = [d for d in dirs if not os.path.isdir(d)]
    if missing:
        sys.exit("目录不存在: %s" % missing[:3])
    results = [repair_dir(d, a.apply) for d in dirs]
    self_links = sum(r["self_links"] for r in results)
    readme_wraps = sum(r["readme_wraps"] for r in results)
    touched = sum(r["files_touched"] for r in results)
    print("%s：%d 个目录，自链 %d 条、README 目录项 %d 条，涉及 %d 个文件"
          % ("已写入" if a.apply else "只数不写", len(results), self_links, readme_wraps, touched))
    for r in results:
        if r["files_touched"]:
            print("   %-58.58s 自链 %4d  README %3d" %
                  (os.path.relpath(r["dir"], L.ROOT), r["self_links"], r["readme_wraps"]))
    if a.json:
        payload = {"apply": a.apply, "dirs": len(results), "self_links": self_links,
                   "readme_wraps": readme_wraps, "files_touched": touched, "results": results}
        with open(a.json + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(a.json + ".tmp", a.json)
        print("JSON: %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
