#!/usr/bin/env python3
"""收件箱入库：把 库/_收件箱 的文件变成人账一行、book.json 与 source/ 下的主源。

  ingest.py scan [--json] [--out plan.json]     # 只读，出计划
  ingest.py add FILE --subjects 文学/严肃文学小说 --title 书名 [--author 作者] [--apply]
  ingest.py replace FILE --uid B0929 [--apply]  # 新副本排到 sources 首位，旧源保留
  ingest.py apply plan.json [--apply] [--no-rebuild]

默认只打印，--apply 才写盘。写盘顺序：加锁、移文件、写 book.json、追人账、记
journal、重建 DB。重复执行按 sha256 幂等，已在库的文件一律跳过。

add 行的学科预填走 classify_model.classify，一次调用回一级与二级，模型默认
opencode-go/deepseek-v4.1-flash；缓存 工作区/批次/ingest/classify.json 按 sha256
存，旧条目没有 model 与 prompt_version 也能读。
"""
import argparse
import hashlib
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
import classify_model as CM  # noqa: E402

ROOT = L.ROOT
def journal_path(root):
    return os.path.join(root, "工作区", "批次", "ingest", "journal.tsv")


def lock_path(root):
    return os.path.join(root, "目录", ".ingest.lock")
FORMATS = {"epub": 3, "mobi": 2, "azw3": 2, "pdf": 1, "txt": 0, "djvu": 0}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def strip_tail_paren(stem):
    end = len(stem) - 1
    while end >= 0 and stem[end].isspace():
        end -= 1
    if end < 0 or stem[end] not in (")", "）"):
        return stem, None
    depth = 0
    for i in range(end, -1, -1):
        c = stem[i]
        if c in (")", "）"):
            depth += 1
        elif c in ("(", "（"):
            depth -= 1
            if depth == 0:
                return stem[:i].rstrip(), stem[i + 1:end].strip()
    return stem, None


def parse_name(name):
    stem = os.path.splitext(os.path.basename(name))[0]
    paren = []
    while True:
        stem, inner = strip_tail_paren(stem)
        if inner is None:
            break
        paren.insert(0, inner)
    author = ""
    for p in paren:
        if re.search(r"[著编译]|[\u4e00-\u9fff]{1,4}[·•]", p) or "." in p:
            author = p
    return stem.strip(), author


def norm(s):
    return re.sub(r"\s+", "", s or "").lower()


def library_index(root):
    lib = os.path.join(root, "库")
    books, shas = [], set()
    for dirpath, dirnames, filenames in os.walk(lib):
        dirnames[:] = [d for d in dirnames if not d.startswith("_")]
        if "book.json" not in filenames:
            continue
        doc = json.load(open(os.path.join(dirpath, "book.json"), encoding="utf-8"))
        srcs = [x for x in (doc.get("sources") or []) if x.get("file")]
        primary = next((x for x in srcs if x.get("primary_src")), srcs[0] if srcs else {})
        books.append({"uid": doc.get("uid", ""), "title": doc.get("title", ""),
                      "path": os.path.relpath(dirpath, lib),
                      "format": (primary.get("format") or "").lower()})
        shas.update(x.get("sha256") for x in srcs if x.get("sha256"))
    return books, shas


def match(title, books):
    t = norm(title)
    if not t:
        return []
    exact = [b for b in books if norm(b["title"]) == t]
    if exact:
        return exact
    return [b for b in books
            if len(norm(b["title"])) >= 4 and (norm(b["title"]) in t or t in norm(b["title"]))]


def next_uid(rows):
    nums = [int(m.group(1)) for m in (re.match(r"^B(\d+)$", r["uid"]) for r in rows) if m]
    return "B%04d" % (max(nums) + 1 if nums else 1)


def plan_for(path, books, shas):
    base = os.path.basename(path)
    sha = sha256_file(path)
    title, author = parse_name(base)
    fmt = os.path.splitext(base)[1].lstrip(".").lower()
    item = {"file": base, "path": path, "sha256": sha,
            "bytes": os.path.getsize(path), "format": fmt,
            "title": title, "author": author, "uid": "", "target": "",
            "subjects": [], "retention": "keep", "action": "", "note": ""}
    if sha in shas:
        item["action"], item["note"] = "skip", "同 sha256 已在库"
        return item
    hits = match(title, books)
    if len(hits) > 1:
        item["action"] = "conflict"
        item["note"] = "同名候选 %s" % ",".join(b["uid"] for b in hits[:4])
        return item
    if len(hits) == 1:
        b = hits[0]
        item["uid"] = b["uid"]
        new, old = FORMATS.get(fmt, 0), FORMATS.get(b["format"], 0)
        if new > old:
            item["action"] = "replace"
            item["note"] = "匹配《%s》，%s 替 %s" % (b["title"], fmt, b["format"])
        else:
            item["action"] = "conflict"
            item["note"] = "匹配《%s》，已有同格式主源 %s" % (b["title"], b["format"] or "?")
        return item
    item["action"] = "add"
    item["note"] = "新书，需填 subjects"
    return item


def scan(root):
    lib, cat = os.path.join(root, "库"), os.path.join(root, "目录")
    inbox = os.path.join(lib, "_收件箱")
    books, shas = library_index(root)
    rows = L.read_csv(os.path.join(cat, "分类表.csv")) if os.path.exists(
        os.path.join(cat, "分类表.csv")) else []
    uid = next_uid(rows)
    out = []
    n = 0
    seen = {}
    for name in sorted(os.listdir(inbox)):
        p = os.path.join(inbox, name)
        if not os.path.isfile(p) or name.startswith("."):
            continue
        if name.lower() in ("readme.md", "readme.txt"):
            continue
        item = plan_for(p, books, shas)
        if item["sha256"] in seen and item["action"] != "skip":
            item["action"] = "skip"
            item["note"] = "收件箱内重复：" + seen[item["sha256"]]
        seen.setdefault(item["sha256"], name)
        if item["action"] == "add":
            item["uid"] = "B%04d" % (int(uid[1:]) + n)
            n += 1
        out.append(item)
    return out


def next_uid_cmd(root):
    rows = L.read_csv(os.path.join(root, "目录", "分类表.csv"))
    print(next_uid(rows))


CLASSIFY_CACHE = os.path.join(ROOT, "工作区", "批次", "ingest", "classify.json")
CLS_MIN = 0.45
CLASSIFY_MODEL = os.environ.get("INGEST_CLASSIFY_MODEL", CM.DEFAULT_MODEL)
CLASSIFY_TIMEOUT = CM.TIMEOUT


def load_class_cache(path):
    if not os.path.exists(path):
        return {}
    return json.load(open(path, encoding="utf-8"))


def _call_classifier(titles, authors):
    return CM.classify(titles, authors, CLASSIFY_MODEL, CLASSIFY_TIMEOUT)


def _conf(rec, key):
    """新条目只有一个 confidence，旧条目分开存 conf1 与 conf2，两种都读。"""
    v = rec.get(key)
    if v in (None, ""):
        v = rec.get("confidence")
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def apply_class(item, rec):
    l1, l2 = rec.get("level1", ""), rec.get("level2", "")
    c1, c2 = _conf(rec, "conf1"), _conf(rec, "conf2")
    if l1 in ("", "拿不准") or c1 < CLS_MIN:
        item["note"] = "低置信 %.2f（%s），需人工确认" % (c1, l1 or "无")
        item["classify"] = dict(rec, needs_review=True)
        return
    subs = [l1]
    note = "自动分类 %s" % l1
    if l2 and l2 != "不细分" and c2 >= CLS_MIN:
        subs.append(l2)
        note += "/" + l2
    else:
        note += "（二级待定）"
    item["subjects"] = subs
    item["note"] = "%s（%.2f/%.2f）" % (note, c1, c2)
    item["classify"] = dict(rec, needs_review=False)


def classify_adds(items, cache_path, enabled=True):
    cache = load_class_cache(cache_path)
    todo = [i for i in items if i["action"] == "add" and i["sha256"] not in cache]
    if todo and enabled:
        try:
            got = _call_classifier([i["title"] for i in todo], [i["author"] for i in todo])
            for it, rec in zip(todo, got):
                cache[it["sha256"]] = dict(rec, at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                                            model=CLASSIFY_MODEL,
                                            prompt_version=CM.PROMPT_VERSION)
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            L.write_json_atomic(cache_path, cache)
        except (SystemExit, Exception) as exc:
            msg = (str(exc) or type(exc).__name__)[:90]
            print("分类失败：%s" % msg, file=sys.stderr)
            for it in todo:
                it["note"] = "分类失败：%s；需人工填 subjects" % msg
    for it in items:
        rec = cache.get(it["sha256"])
        if it["action"] == "add" and rec:
            apply_class(it, rec)


def print_plan(items):
    print("%-4s %-10s %-52s %s" % ("动作", "uid", "文件/书名", "说明"))
    for it in items:
        name = it["file"] if it["action"] != "add" else "%s → %s" % (it["file"], it["title"])
        print("%-4s %-10s %-52s %s" % (it["action"], it["uid"] or "-", name[:52], it["note"]))


def journal(root, rows):
    path = journal_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as fh:
        if new:
            fh.write("ts\taction\tuid\tfile\tsha256\tbytes\tnote\n")
        for r in rows:
            fh.write("\t".join(str(x) for x in (
                time.strftime("%Y-%m-%dT%H:%M:%S%z"), r["action"], r.get("uid", ""),
                r.get("file", ""), r.get("sha256", ""), r.get("bytes", ""),
                r.get("note", ""))) + "\n")


def take_lock(root):
    path = lock_path(root)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit("已有 ingest 在跑，锁文件 %s 存在" % path)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)


def drop_lock(root):
    path = lock_path(root)
    if os.path.exists(path):
        os.unlink(path)


def find_by_uid(root, uid):
    lib = os.path.join(root, "库")
    for dirpath, dirnames, filenames in os.walk(lib):
        dirnames[:] = [d for d in dirnames if not d.startswith("_")]
        if "book.json" in filenames:
            doc = json.load(open(os.path.join(dirpath, "book.json"), encoding="utf-8"))
            if doc.get("uid") == uid:
                return os.path.relpath(dirpath, lib), doc
    return None, None


def book_json(uid, title, author, subjects, rel, sources, notes):
    return L.book_doc(uid, title, "", author, subjects, rel, None, None, None,
                      "keep", notes, sources, [])


def apply_one(root, it, shas):
    lib, cat = os.path.join(root, "库"), os.path.join(root, "目录")
    if it["sha256"] in shas:
        return dict(it, action="skip", note="同 sha256 已在库（幂等跳过）")
    src = it.get("path") or os.path.join(lib, "_收件箱", it["file"])
    if not os.path.isfile(src):
        src = os.path.join(lib, "_收件箱", it["file"])
    if not os.path.isfile(src):
        raise SystemExit("收件箱里没有这个文件: " + src)
    if it["action"] == "add":
        subj = [s for s in (it.get("subjects") or []) if s]
        if len(subj) < 1:
            raise SystemExit("add 缺 subjects: %s（写在 plan 里或命令行给 --subjects）" % it["file"])
        rel = it.get("target") or "/".join(subj + [it["title"]])
        dest = os.path.join(lib, rel)
        if os.path.exists(dest):
            raise SystemExit("目标目录已存在: " + dest)
        os.makedirs(os.path.join(dest, "source"), exist_ok=True)
        name = it["file"]
        shutil.move(src, os.path.join(dest, "source", name))
        sources = [{"file": "source/" + name, "format": it["format"],
                    "bytes": it["bytes"], "sha256": it["sha256"], "primary_src": 1}]
        L.write_json_atomic(os.path.join(dest, "book.json"),
                            book_json(it["uid"], it["title"], it.get("author", ""), subj,
                                      rel, sources, "ingest:%s" % time.strftime("%Y-%m-%d")))
        rows = L.read_csv(os.path.join(cat, "分类表.csv"))
        rows.append({"uid": it["uid"], "一级": subj[0], "二级": subj[1] if len(subj) > 1 else "",
                     "作者层": "0", "保留": "keep", "正式书名": it["title"],
                     "备注": "ingest:%s；待复核" % time.strftime("%Y-%m-%d")})
        L.write_csv(os.path.join(cat, "分类表.csv"), rows, L.LEDGER_HEADER)
        return dict(it, action="add", note="已入库 %s" % rel)
    if it["action"] == "replace":
        uid = it["uid"]
        rows = [r for r in L.read_csv(os.path.join(cat, "分类表.csv")) if r["uid"] == uid]
        if not rows:
            raise SystemExit("人账里没有 " + uid)
        rel, doc = find_by_uid(root, uid)
        if not rel:
            raise SystemExit("库里没有 " + uid)
        dest = os.path.join(lib, rel)
        name = it["file"]
        os.makedirs(os.path.join(dest, "source"), exist_ok=True)
        shutil.move(src, os.path.join(dest, "source", name))
        sources = [{"file": "source/" + name, "format": it["format"], "bytes": it["bytes"],
                    "sha256": it["sha256"], "primary_src": 1}]
        for s in doc.get("sources") or []:
            sources.append(dict(s, primary_src=0))
        doc["sources"] = sources
        doc["notes"] = (doc.get("notes") or "") + "；替换源:%s" % time.strftime("%Y-%m-%d")
        L.write_json_atomic(os.path.join(dest, "book.json"), doc)
        return dict(it, action="replace",
                    note="新源置顶，旧源保留；md 仍是旧源产物，需重拆 %s" % uid)
    raise SystemExit("plan 里有未处理的动作: %s %s" % (it["action"], it["file"]))


def rebuild():
    cmd = [sys.executable, os.path.join(HERE, "build_library.py"), "--emit-json"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("重建失败：\n" + (r.stdout + r.stderr)[-1500:])
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("scan", "add", "replace", "apply", "uid"))
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--subjects", help="一级/二级，例如 文学/严肃文学小说")
    ap.add_argument("--title")
    ap.add_argument("--author", default="")
    ap.add_argument("--uid")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-rebuild", action="store_true")
    ap.add_argument("--no-classify", action="store_true",
                    help="跳过 deepseek 分类预填")
    a = ap.parse_args()

    if a.cmd == "uid":
        return next_uid_cmd(a.root)

    if a.cmd == "scan":
        items = scan(a.root)
        classify_adds(items, os.path.join(a.root, "工作区", "批次", "ingest", "classify.json"),
                      enabled=not a.no_classify)
        if a.json or a.out:
            blob = json.dumps(items, ensure_ascii=False, indent=1)
            if a.out:
                L.write_json_atomic(a.out, json.loads(blob))
                print("plan → " + a.out)
            else:
                print(blob)
        if not a.json:
            print_plan(items)
        return 0

    if a.cmd == "apply":
        items = json.load(open(a.arg, encoding="utf-8"))
    else:
        base = os.path.basename(a.arg)
        it = {"file": base, "path": a.arg, "sha256": sha256_file(a.arg),
              "bytes": os.path.getsize(a.arg),
              "format": os.path.splitext(base)[1].lstrip(".").lower(),
              "title": a.title or parse_name(base)[0], "author": a.author,
              "uid": a.uid or "", "target": "",
              "subjects": (a.subjects or "").split("/") if a.subjects else [],
              "retention": "keep", "action": a.cmd, "note": ""}
        items = [it]

    bad = [i for i in items if i["action"] == "add" and not (i.get("subjects") or [])]
    if bad:
        print("这些 add 还没填 subjects，先写进 plan 再 apply：", file=sys.stderr)
        for i in bad:
            print("  %-44s %s" % (i["file"][:44], i["uid"]), file=sys.stderr)
        return 2

    books, shas = library_index(a.root)

    # add 没写 uid 时按人账往下排：scan 已经排好，但手写 plan 与单本 add 容易漏，
    # 漏了会写出一行 uid 为空的 book.json 与分类表。
    if any(i["action"] == "add" and not (i.get("uid") or "").strip() for i in items):
        led = os.path.join(a.root, "目录", "分类表.csv")
        rows = L.read_csv(led) if os.path.exists(led) else []
        base, n = int(next_uid(rows)[1:]), 0
        for i in items:
            if i["action"] == "add" and not (i.get("uid") or "").strip():
                i["uid"] = "B%04d" % (base + n)
                n += 1
    todo = [i for i in items if i["action"] in ("add", "replace")]
    if not a.apply:
        print_plan(items)
        print("\n未写盘。确认后加 --apply。")
        return 0

    take_lock(a.root)
    done = []
    try:
        for it in todo:
            done.append(apply_one(a.root, it, shas))
            shas.add(it["sha256"])
        journal(a.root, done)
    finally:
        drop_lock(a.root)
    print_plan(done)
    if not a.no_rebuild:
        print(rebuild())
    return 0


if __name__ == "__main__":
    sys.exit(main())
