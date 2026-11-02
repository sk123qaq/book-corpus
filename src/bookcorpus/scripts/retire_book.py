#!/usr/bin/env python3
"""移出一本书：原书进 库/_冷宫/<书名>/source/，md、html、json 删除，账本同步。

  python3 工作区/脚本/retire_book.py --uid B1234 --reason "..." --dry-run --json
  python3 工作区/脚本/retire_book.py --uid B1234 --reason "..." --plan-hash <sha> --apply

--dry-run 只读，算一份预览与 plan_hash。--apply 重算预览，哈希不一致、快照没就绪、
预览有挡住项都拒绝，避免预览后库变了。冷宫只留 source/，元数据进
工作区/批次/移出-<日期>/retired.tsv 与 库/_冷宫/README.md。
"""
import argparse
import collections
import csv
import datetime
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L  # noqa: E402

DEFAULT_sys.path.insert(0, HERE)
from lib_common import ROOT  # noqa: E402

RETIRED_HEADER = ["retired_at", "uid", "title", "old_path", "cold_path", "sources",
                  "source_sha256", "format", "bytes", "md_chapters", "md_bytes", "reason",
                  "snapshot", "shared_with", "series_docs", "note"]

README_COLS = 7


class Ctx:
    """一个根目录下的全部路径。所有写入都从这里取，不碰 lib_common.ROOT。"""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.lib = os.path.join(self.root, "库")
        self.cat = os.path.join(self.root, "目录")
        self.work = os.path.join(self.root, "工作区")
        self.views = os.path.join(self.root, "视图")
        self.cold = os.path.join(self.lib, "_冷宫")
        self.cold_readme = os.path.join(self.cold, "README.md")
        self.ledger = os.path.join(self.cat, "分类表.csv")
        self.db = os.path.join(self.cat, "library.db")
        self.index = os.path.join(self.cat, "检索", "index.db")
        self.expand_dir = os.path.join(self.cat, "检索", "expand")
        self.series_dir = os.path.join(self.cat, "系列")
        self.snapshot_name = "books.snapshot-" + datetime.date.today().isoformat()
        self.snapshot = os.path.join(os.path.dirname(self.root), self.snapshot_name)
        self._source_index = None

    def rel(self, path):
        return os.path.relpath(path, self.root).replace(os.sep, "/")

    def source_index(self):
        if self._source_index is None:
            self._source_index = scan_source_index(self)
        return self._source_index


def read_ledger(path):
    """(表头, 行列表)。表头原样保留，写回时不动别的列。"""
    if not os.path.isfile(path):
        return list(L.LEDGER_HEADER), []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return list(L.LEDGER_HEADER), []
    header, body = rows[0], rows[1:]
    return header, [dict(zip(header, r)) for r in body if r]


def iter_book_docs(ctx):
    """库/**/book.json -> (书目录, doc)。_ 开头的顶层目录不算书。"""
    if not os.path.isdir(ctx.lib):
        return
    for dirpath, dirnames, filenames in os.walk(ctx.lib):
        rel = os.path.relpath(dirpath, ctx.lib)
        if rel != "." and rel.split(os.sep)[0].startswith("_"):
            dirnames[:] = []
            continue
        if "book.json" not in filenames:
            continue
        try:
            with open(os.path.join(dirpath, "book.json"), encoding="utf-8") as fh:
                yield dirpath, json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            raise SystemExit("book.json 读不了: %s (%s)" % (dirpath, e))


def find_book(ctx, uid):
    for book_dir, doc in iter_book_docs(ctx):
        if doc.get("uid") == uid:
            return book_dir, doc
    return None, None


def resolve_source(book_dir, entry):
    return os.path.normpath(os.path.join(book_dir, entry.get("file") or ""))


def scan_source_index(ctx):
    """三条共享线索：sha256、realpath、inode。键 -> 引用它的 uid 集合。"""
    by_sha = collections.defaultdict(set)
    by_real = collections.defaultdict(set)
    by_ino = collections.defaultdict(set)
    for book_dir, doc in iter_book_docs(ctx):
        uid = doc.get("uid") or ""
        for s in doc.get("sources") or []:
            if s.get("sha256"):
                by_sha[s["sha256"]].add(uid)
            path = resolve_source(book_dir, s)
            by_real[os.path.realpath(path)].add(uid)
            try:
                st = os.stat(path)
            except OSError:
                continue
            by_ino[(st.st_dev, st.st_ino)].add(uid)
    return {"sha256": by_sha, "real": by_real, "ino": by_ino}


def same_filesystem(src, dst_dir):
    probe = os.path.abspath(dst_dir)
    while not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return True
        probe = parent
    try:
        return os.stat(src).st_dev == os.stat(probe).st_dev
    except OSError:
        return True


def like_prefix(prefix):
    """把前缀变成 LIKE 模式，转义 _ % \\ 三个元字符。"""
    out = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return out + "/md/%"


def index_counts(ctx, rel_dir):
    """(expand 行数, para 行数)。只读打开；index.db 不在或读不了就是 0。"""
    if not os.path.isfile(ctx.index):
        return 0, 0
    book_key = rel_dir.split("/", 1)[1] if "/" in rel_dir else rel_dir
    con = sqlite3.connect("file:%s?mode=ro" % ctx.index, uri=True)
    try:
        expand = con.execute(
            "SELECT count(*) FROM expand WHERE path LIKE ? ESCAPE '\\'",
            (like_prefix(rel_dir),)).fetchone()[0]
        paras = con.execute("SELECT count(*) FROM para WHERE book=?", (book_key,)).fetchone()[0]
    except sqlite3.Error:
        return 0, 0
    finally:
        con.close()
    return expand, paras


def db_facts(ctx, uid):
    """(库里有这本的 DB 行, 奖项名列表)。"""
    if not os.path.isfile(ctx.db):
        return False, []
    con = sqlite3.connect("file:%s?mode=ro" % ctx.db, uri=True)
    try:
        row = con.execute("SELECT title FROM book WHERE uid=?", (uid,)).fetchone()
        awards = [r[0] for r in con.execute(
            "SELECT award FROM book_award WHERE uid=? ORDER BY award", (uid,))]
    except sqlite3.Error:
        return False, []
    finally:
        con.close()
    return row is not None, awards


def find_view_links(ctx, book_dir):
    """视图/ 下指向这本书的软链，(奖项视图, 主题视图)，root 相对路径。"""
    target = os.path.realpath(book_dir)
    found = {"奖项": [], "主题": []}
    for top in ("奖项", "主题"):
        base = os.path.join(ctx.views, top)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            for name in list(dirnames) + list(filenames):
                link = os.path.join(dirpath, name)
                if os.path.islink(link) and os.path.realpath(link) == target:
                    found[top].append(ctx.rel(link))
    for key in found:
        found[key].sort()
    return found["奖项"], found["主题"]


def find_series_docs(ctx, doc):
    """提到了这本或它所属系列的 目录/系列/*.md，只标出，不自动改。"""
    if not os.path.isdir(ctx.series_dir):
        return []
    needles = [x for x in {(doc.get("title") or "").strip(),
                           (doc.get("series") or "").strip()} if len(x) >= 2]
    if not needles:
        return []
    hits = []
    for name in sorted(os.listdir(ctx.series_dir)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(ctx.series_dir, name)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        if any(n in text for n in needles):
            hits.append(ctx.rel(path))
    return hits


def cold_layout(ctx, doc):
    """(冷宫书目录, 冷宫 source 目录)，root 相对。同名目录已存在就带 uid。"""
    title = L.path_safe(doc.get("title") or "") or (doc.get("uid") or "未命名")
    rel_dir = "库/_冷宫/" + title
    if os.path.exists(os.path.join(ctx.root, rel_dir)):
        rel_dir = "库/_冷宫/%s（%s）" % (title, doc.get("uid") or "")
    return rel_dir, rel_dir + "/source"


def build_sources(ctx, book_dir, doc, cold_source_rel):
    """每个源的 sha256、字节、共享本数、动作、冷宫目标。"""
    index = ctx.source_index()
    uid = doc.get("uid") or ""
    used = set()
    out = []
    for s in doc.get("sources") or []:
        path = resolve_source(book_dir, s)
        sha = s.get("sha256") or ""
        size = s.get("bytes") or 0
        exists = os.path.isfile(path)
        if exists:
            real_size = os.path.getsize(path)
            if real_size != size:
                size = real_size
                sha = L.sha256_file(path)
        others = set()
        if sha:
            others |= index["sha256"].get(sha, set())
        others |= index["real"].get(os.path.realpath(path), set())
        if exists:
            st = os.stat(path)
            others |= index["ino"].get((st.st_dev, st.st_ino), set())
        others.discard(uid)
        base = os.path.basename(path) or uid
        name, n = base, 1
        while name in used:
            n += 1
            stem, dot, ext = base.rpartition(".")
            name = "%s-%d.%s" % (stem, n, ext) if dot else "%s-%d" % (base, n)
        used.add(name)
        action = "move" if not others else "hardlink"
        fallback = ""
        if action == "hardlink" and not same_filesystem(
                path, os.path.join(ctx.root, cold_source_rel)):
            fallback = "copy"
        out.append({
            "path": ctx.rel(path),
            "sha256": sha,
            "bytes": size,
            "shared_with": len(others),
            "shared_uids": sorted(others)[:10],
            "action": action,
            "fallback": fallback,
            "primary_src": bool(s.get("primary_src")),
            "exists": exists,
            "in_book_dir": os.path.realpath(path).startswith(
                os.path.realpath(book_dir) + os.sep),
            "cold": cold_source_rel + "/" + name,
        })
    return out


def delete_list(book_dir):
    """书目录里要删的东西，书目录相对名。source/ 由源文件那步单独处理。"""
    out = []
    for name in ("md", "html"):
        if os.path.isdir(os.path.join(book_dir, name)):
            out.append(name + "/")
    if os.path.isfile(os.path.join(book_dir, "book.json")):
        out.append("book.json")
    for name in sorted(os.listdir(book_dir)):
        path = os.path.join(book_dir, name)
        if name in ("md", "html", "book.json", "source"):
            continue
        if os.path.isfile(path) and name.endswith(".json"):
            out.append(name)
    return out


def leftover_files(book_dir, delete, source_entries):
    """书目录里既不删、也不进冷宫的东西。正常只有空的 source/，这里是兜底。"""
    skip_top = {x.rstrip("/") for x in delete}
    referenced = {os.path.normpath(os.path.join(book_dir, s.get("file") or ""))
                  for s in source_entries if s.get("file")}
    out = []
    for dirpath, _dirnames, filenames in os.walk(book_dir):
        for name in filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, book_dir)
            if rel.split(os.sep)[0] in skip_top:
                continue
            if os.path.normpath(path) in referenced:
                continue
            out.append(rel.replace(os.sep, "/"))
    return sorted(out)


def plan_hash(preview):
    """预览里决定破坏范围的部分。派生状态（索引行数、时间）不进哈希。"""
    keep = ["uid", "title", "book_dir", "sources", "delete", "leftovers", "ledger", "db",
            "awards", "views", "series_docs", "md_chapters", "md_bytes", "expand_md",
            "cold_path"]
    body = {k: preview.get(k) for k in keep}
    body["sources"] = [{k: v for k, v in s.items() if k != "shared_uids"}
                       for s in preview.get("sources") or []]
    blob = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def readme_state(ctx):
    """冷宫 README 的表头是不是 7 列。写行前先看，写不了就别动书。"""
    if not os.path.isfile(ctx.cold_readme):
        return True, ""
    header = []
    with open(ctx.cold_readme, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("|"):
                header = [x.strip() for x in line.strip().strip("|").split("|")]
                break
    if not header:
        return True, ""
    if len(header) != README_COLS:
        return False, "库/_冷宫/README.md 表头是 %d 列，不是 %d 列" % (len(header), README_COLS)
    return True, ""


def build_preview(ctx, uid):
    book_dir, doc = find_book(ctx, uid)
    if doc is None:
        raise LookupError("uid 不在 库/ 里: %s" % uid)
    rel_dir = ctx.rel(book_dir)
    _header, ledger_rows = read_ledger(ctx.ledger)
    led_row = next((r for r in ledger_rows if (r.get("uid") or "").strip() == uid), None)
    cold_rel, cold_source_rel = cold_layout(ctx, doc)
    sources = build_sources(ctx, book_dir, doc, cold_source_rel)
    delete = delete_list(book_dir)
    leftovers = leftover_files(book_dir, delete, doc.get("sources") or [])
    has_db_row, db_awards = db_facts(ctx, uid)
    award_views, topic_views = find_view_links(ctx, book_dir)
    series_docs = find_series_docs(ctx, doc)
    md_dir = os.path.join(book_dir, "md")
    chapters, md_bytes = L.count_md_dir(md_dir) if os.path.isdir(md_dir) else (0, 0)
    expand_rows, index_rows = index_counts(ctx, rel_dir)
    expand_md = os.path.join(ctx.expand_dir, uid + ".md")
    awards = [a for a in (doc.get("awards") or []) if isinstance(a, str)] or db_awards

    blockers = []
    warnings = []
    if rel_dir.split("/")[0] != "库" or rel_dir.startswith("库/_"):
        blockers.append("书目录不在正常书区: " + rel_dir)
    if any(not s["exists"] for s in sources):
        warnings.append("有源文件不在磁盘上: " + ", ".join(
            s["path"] for s in sources if not s["exists"]))
    if led_row is None:
        warnings.append("分类表.csv 里没有这行，删行会跳过")
    readme_ok, readme_note = readme_state(ctx)
    if not readme_ok:
        blockers.append(readme_note)

    preview = {
        "uid": uid,
        "title": doc.get("title") or "",
        "author": doc.get("author") or "",
        "series": doc.get("series"),
        "path": rel_dir,
        "book_dir": rel_dir,
        "sources": sources,
        "delete": delete,
        "leftovers": leftovers,
        "ledger": led_row is not None,
        "ledger_row": led_row or {},
        "db": has_db_row,
        "awards": sorted(awards),
        "views": award_views + topic_views,
        "view_awards": award_views,
        "view_topics": topic_views,
        "series_docs": series_docs,
        "md_chapters": chapters,
        "md_bytes": md_bytes,
        "expand_rows": expand_rows,
        "expand_md": ctx.rel(expand_md) if os.path.isfile(expand_md) else "",
        "index_rows": index_rows,
        "cold_path": cold_source_rel,
        "cold_dir": cold_rel,
        "snapshot": ctx.rel(ctx.snapshot),
        "snapshot_ok": os.path.isdir(ctx.snapshot),
        "blockers": blockers,
        "warnings": warnings,
    }
    preview["plan_hash"] = plan_hash(preview)
    return preview


def format_preview(preview):
    lines = ["%s  %s" % (preview["uid"], preview["title"]),
             "  书目录  %s" % preview["book_dir"],
             "  删除    %s" % (", ".join(preview["delete"]) or "（无）"),
             "  附带    %s" % (", ".join(preview["leftovers"]) or "（无）"),
             "  md      %d 章 / %d 字节" % (preview["md_chapters"], preview["md_bytes"])]
    for s in preview["sources"]:
        lines.append("  源      %s  %d 字节  sha256 %s  共享 %d 本  %s%s" % (
            s["path"], s["bytes"], s["sha256"][:12], s["shared_with"], s["action"],
            "（跨文件系统改复制）" if s["fallback"] == "copy" else ""))
    lines.append("  账      人账行 %s  DB 行 %s  奖项 %d  视图 %d  系列文档 %d" % (
        "有" if preview["ledger"] else "无", "有" if preview["db"] else "无",
        len(preview["awards"]), len(preview["views"]), len(preview["series_docs"])))
    lines.append("  检索    expand %d 行  expand.md %s  para %d 行" % (
        preview["expand_rows"], preview["expand_md"] or "无", preview["index_rows"]))
    lines.append("  冷宫    %s" % preview["cold_path"])
    lines.append("  快照    %s  %s" % (
        preview["snapshot"], "就绪" if preview["snapshot_ok"] else "缺失"))
    lines.append("  计划    %s" % preview["plan_hash"])
    for b in preview["blockers"]:
        lines.append("  挡住    " + b)
    for w in preview["warnings"]:
        lines.append("  注意    " + w)
    return "\n".join(lines)


def _prune(path, protected):
    for name in sorted(os.listdir(path)):
        child = os.path.join(path, name)
        if os.path.realpath(child) in protected:
            continue
        if os.path.isdir(child) and not os.path.islink(child):
            _prune(child, protected)
            if not os.listdir(child):
                os.rmdir(child)
        else:
            os.remove(child)


def _move_or_link(src, dst, action, fallback):
    if action == "move":
        shutil.move(src, dst)
        return "move"
    if action == "hardlink" and not fallback:
        os.link(src, dst)
        a, b = os.stat(src), os.stat(dst)
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino) or a.st_size != b.st_size:
            raise RuntimeError("硬链接后 inode 或字节对不上: %s" % dst)
        return "hardlink"
    shutil.copy2(src, dst)
    if os.path.getsize(dst) != os.path.getsize(src):
        raise RuntimeError("复制后字节对不上: %s" % dst)
    return "copy"


def retired_tsv_path(ctx):
    day = datetime.date.today().isoformat()
    return os.path.join(ctx.work, "批次", "移出-" + day, "retired.tsv")


def _cell(value):
    return str(value).replace("\t", " ").replace("\n", " ")


def append_retired_tsv(ctx, row):
    """追加一行。先读旧文再 tmp + replace，不原地改。"""
    path = retired_tsv_path(ctx)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    old = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8", newline="") as fh:
            old = fh.read()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        if old:
            fh.write(old if old.endswith("\n") else old + "\n")
        else:
            fh.write("\t".join(RETIRED_HEADER) + "\n")
        fh.write("\t".join(_cell(row.get(k, "")) for k in RETIRED_HEADER) + "\n")
    os.replace(tmp, path)
    return ctx.rel(path)


def append_cold_readme(ctx, preview, reason):
    """账表加一行，7 列。表头列数不对就报错，不改文件。"""
    ok, note = readme_state(ctx)
    if not ok:
        raise RuntimeError(note)
    os.makedirs(ctx.cold, exist_ok=True)
    path = ctx.cold_readme
    old = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
    if ("| %s |" % preview["uid"]) in old:
        return ctx.rel(path), False
    today = datetime.date.today().isoformat()
    src_text = "；".join(
        "`%s` %s 字节，sha256 `%s…`（%s，与 %d 本共用%s）" % (
            os.path.basename(s["path"]), format(s["bytes"], ","), s["sha256"][:8],
            "移动" if s["action"] == "move" else "硬链接", s["shared_with"],
            "，跨文件系统复制" if s["fallback"] == "copy" else "")
        for s in preview["sources"]) or "无源文件"
    md_text = ("%d 章 / %s 字节；%s 删除" % (
        preview["md_chapters"], format(preview["md_bytes"], ","), today)
        if preview["md_chapters"] else "无 md")
    cells = [today, preview["uid"], preview["title"], preview["book_dir"], src_text,
             md_text, reason]
    row = "| " + " | ".join(_cell(c).replace("|", "／") for c in cells) + " |"
    lines = old.splitlines() if old else [
        "# 库/_冷宫", "",
        "| 移入日期 | uid | 书名 | 原路径 | 源文件 | 原 md | 理由 |",
        "|---|---|---|---|---|---|---|"]
    last_table = max((i for i, line in enumerate(lines) if line.startswith("|")), default=-1)
    if last_table >= 0:
        lines.insert(last_table + 1, row)
    else:
        lines += ["", "| 移入日期 | uid | 书名 | 原路径 | 源文件 | 原 md | 理由 |",
                  "|---|---|---|---|---|---|---|", row]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return ctx.rel(path), True


def retired_row(preview, reason, snapshot, note=""):
    sources = preview["sources"]
    return {
        "retired_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "uid": preview["uid"],
        "title": preview["title"],
        "old_path": preview["book_dir"],
        "cold_path": preview["cold_dir"],
        "sources": len(sources),
        "source_sha256": ";".join(s["sha256"] for s in sources),
        "format": ";".join(os.path.splitext(s["path"])[1].lstrip(".") for s in sources),
        "bytes": sum(s["bytes"] for s in sources),
        "md_chapters": preview["md_chapters"],
        "md_bytes": preview["md_bytes"],
        "reason": reason,
        "snapshot": snapshot,
        "shared_with": ";".join(str(s["shared_with"]) for s in sources),
        "series_docs": ";".join(preview["series_docs"]),
        "note": note,
    }


def rebuild_derived(ctx, preview, run):
    """DB、书目表、expand 行、软链视图。只在真库上跑。"""
    done = []
    if os.path.isfile(ctx.index):
        con = sqlite3.connect(ctx.index)
        try:
            con.execute("DELETE FROM expand WHERE path LIKE ? ESCAPE '\\'",
                        (like_prefix(preview["book_dir"]),))
            con.execute("INSERT INTO expand_fts(expand_fts) VALUES('rebuild')")
            con.commit()
        finally:
            con.close()
        done.append("expand rows")
    if preview["expand_md"]:
        path = os.path.join(ctx.root, preview["expand_md"])
        if os.path.isfile(path):
            os.remove(path)
            done.append(preview["expand_md"])
    cmds = [
        [sys.executable, os.path.join(HERE, "build_library.py"), "--emit-json"],
        [sys.executable, os.path.join(HERE, "gen_book_catalog.py")],
        [sys.executable, os.path.join(HERE, "migrate_layout.py"), "--views"],
        [sys.executable, os.path.join(HERE, "build_topic_views.py")],
    ]
    if "html/" in preview["delete"]:
        cmds.append([sys.executable, os.path.join(HERE, "build_index.py")])
    for cmd in cmds:
        name = os.path.basename(cmd[1])
        rc = run(cmd)
        done.append("%s rc=%d" % (name, rc))
        if rc != 0:
            raise RuntimeError("%s 退出码 %d" % (name, rc))
    return done


def apply(ctx, preview, reason, snapshot_gate, rebuild, run):
    uid = preview["uid"]
    book_dir = os.path.join(ctx.root, preview["book_dir"])
    cold_source = os.path.join(ctx.root, preview["cold_path"])
    protected = {os.path.realpath(os.path.join(ctx.root, s["path"]))
                 for s in preview["sources"]
                 if s["shared_with"] > 0 and s["in_book_dir"]}
    steps = []
    current = "start"

    def note(name, detail=""):
        steps.append({"step": name, "detail": detail})

    try:
        current = "snapshot"
        if snapshot_gate and not preview["snapshot_ok"]:
            raise RuntimeError("当天快照不在: %s" % preview["snapshot"])
        note("snapshot", preview["snapshot"] if snapshot_gate else "跳过（--no-snapshot）")

        current = "cold-source"
        os.makedirs(cold_source, exist_ok=True)
        for s in preview["sources"]:
            src = os.path.join(ctx.root, s["path"])
            dst = os.path.join(ctx.root, s["cold"])
            if not os.path.isfile(src):
                note("cold-source", "源不在，跳过: " + s["path"])
                continue
            if os.path.exists(dst):
                raise RuntimeError("冷宫目标已存在: " + s["cold"])
            how = _move_or_link(src, dst, s["action"], s["fallback"])
            note("cold-source", "%s -> %s (%s)" % (s["path"], s["cold"], how))

        current = "book-files"
        for name in preview["delete"]:
            path = os.path.join(book_dir, name.rstrip("/"))
            if name.endswith("/") and os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.isfile(path):
                os.remove(path)
        note("book-files", ", ".join(preview["delete"]) or "无")

        current = "book-dir"
        if os.path.isdir(book_dir):
            _prune(book_dir, protected)
            if os.listdir(book_dir):
                note("book-dir", "留下被别的书引用的源: " + ", ".join(sorted(os.listdir(book_dir))))
            else:
                os.rmdir(book_dir)
                note("book-dir", "删空 " + preview["book_dir"])
        else:
            note("book-dir", "书目录已经不在")

        current = "ledger"
        header, rows = read_ledger(ctx.ledger)
        kept = [r for r in rows if (r.get("uid") or "").strip() != uid]
        if len(kept) == len(rows):
            note("ledger", "人账本来就没有这行")
        else:
            L.write_csv(ctx.ledger, kept, header)
            note("ledger", "删 %d 行，剩 %d 行" % (len(rows) - len(kept), len(kept)))

        current = "records"
        tsv = append_retired_tsv(ctx, retired_row(preview, reason, preview["snapshot"]))
        readme, added = append_cold_readme(ctx, preview, reason)
        note("records", "%s；%s%s" % (tsv, readme, "" if added else "（已有该行）"))

        current = "rebuild"
        if rebuild:
            note("rebuild", "; ".join(rebuild_derived(ctx, preview, run)))
        else:
            note("rebuild", "跳过（--no-rebuild），索引与验收留给收尾")
    except Exception as e:  # noqa: BLE001
        try:
            tsv = append_retired_tsv(ctx, retired_row(
                preview, reason, preview["snapshot"],
                note="failed at %s: %s" % (current, e)))
        except Exception:  # noqa: BLE001
            tsv = ""
        return {"ok": False, "uid": uid, "failed_step": current, "error": str(e),
                "steps": steps, "retired_tsv": tsv, "cold_path": preview["cold_path"]}
    return {"ok": True, "uid": uid, "title": preview["title"],
            "cold_path": preview["cold_path"], "plan_hash": preview["plan_hash"],
            "steps": steps}


def _run(cmd):
    return subprocess.run(cmd, cwd=DEFAULT_ROOT, shell=False).returncode


def retired_record(ctx, uid):
    """工作区/批次/移出-*/retired.tsv 里这个 uid 的冷宫路径，没有就是空。"""
    for path in sorted(glob.glob(os.path.join(ctx.work, "批次", "移出-*", "retired.tsv")),
                       reverse=True):
        with open(path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                if (row.get("uid") or "").strip() == uid and not row.get("note"):
                    return row.get("cold_path") or ""
    return ""


def main(argv=None):
    ap = argparse.ArgumentParser(description="移出一本书：原书进冷宫，md、html、json 删除。")
    ap.add_argument("--uid", required=True)
    ap.add_argument("--reason", required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="只算预览，不动文件")
    mode.add_argument("--apply", action="store_true", help="按 plan_hash 落地")
    ap.add_argument("--json", action="store_true", help="stdout 只打 JSON")
    ap.add_argument("--plan-hash", default="")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--no-snapshot", action="store_true", help="跳过快照门槛，只给测试用")
    ap.add_argument("--no-rebuild", action="store_true",
                    help="不重建 DB、书目表、视图与 expand，留给收尾")
    args = ap.parse_args(argv)
    ctx = Ctx(args.root)
    uid = args.uid.strip()

    try:
        preview = build_preview(ctx, uid)
    except LookupError as e:
        cold = retired_record(ctx, uid) if args.apply else ""
        if cold:
            out = {"ok": True, "uid": uid, "already_retired": True, "cold_path": cold}
            print(json.dumps(out, ensure_ascii=False) if args.json
                  else "%s 已经移出，冷宫在 %s" % (uid, cold))
            return 0
        payload = {"ok": False, "error": str(e)}
        print(json.dumps(payload, ensure_ascii=False) if args.json else str(e),
              file=sys.stdout if args.json else sys.stderr)
        return 2

    if args.dry_run:
        if args.json:
            print(json.dumps(preview, ensure_ascii=False, indent=1))
        else:
            print(format_preview(preview))
        return 0 if not preview["blockers"] else 2

    refuse = None
    if not args.plan_hash:
        refuse = "--apply 要给 --plan-hash，先跑 --dry-run 拿哈希"
    elif preview["blockers"]:
        refuse = "预览有挡住项: " + "；".join(preview["blockers"])
    elif args.plan_hash != preview["plan_hash"]:
        refuse = ("plan_hash 不一致，库在预览后变了，拒绝落地\n  给的 %s\n  现在 %s"
                  % (args.plan_hash, preview["plan_hash"]))
    elif not args.no_snapshot and not preview["snapshot_ok"]:
        refuse = "当天快照不在: %s（只给测试用 --no-snapshot）" % preview["snapshot"]
    elif not args.no_rebuild and ctx.root != DEFAULT_ROOT:
        refuse = "--root 不是真库却要重建派生数据，测试请加 --no-rebuild"
    if refuse:
        print(json.dumps({"ok": False, "uid": uid, "refused": refuse,
                          "plan_hash": preview["plan_hash"]}, ensure_ascii=False)
              if args.json else refuse, file=sys.stdout if args.json else sys.stderr)
        return 2

    report = apply(ctx, preview, args.reason, not args.no_snapshot, not args.no_rebuild, _run)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        if report["ok"]:
            print("已移出 %s %s -> %s" % (report["uid"], preview["title"], report["cold_path"]))
        else:
            print("移到 %s 步失败: %s" % (report["failed_step"], report["error"]))
        for s in report["steps"]:
            print("  %-12s %s" % (s["step"], s["detail"]))
    return 0 if report["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
