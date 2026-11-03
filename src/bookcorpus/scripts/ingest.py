#!/usr/bin/env python3
"""收件箱入库：把 库/_收件箱 的文件变成人账一行、book.json 与 source/ 下的主源。

  ingest.py scan [--json] [--out plan.json] [--cache PATH]  # 只读，出计划
  ingest.py add FILE --subjects 文学/严肃文学小说 --title 书名 [--author 作者] [--apply]
  ingest.py replace FILE --uid B0929 [--apply]  # 新副本排到 sources 首位，旧源保留
  ingest.py apply plan.json [--apply] [--no-rebuild]

默认只打印，--apply 才写盘。写盘顺序：加锁、移文件、写 book.json、追人账、记
journal、重建 DB。重复执行按 sha256 幂等，已在库的文件一律跳过。

add 行的学科预填走 classify_model.classify，一次调用回一级与二级，模型默认
opencode-go/deepseek-v4.1-flash；缓存默认 工作区/批次/ingest/classify.json，可用
--cache 指到别处，按 sha256 存，旧条目没有 model 与 prompt_version 也能读。
"""
import argparse
import errno
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
    return os.path.join(root, "tables", ".ingest.lock")
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
    """库里每本书的 uid/书名/作者/分类/主源，外加 sha→uid 与全部 sha。

    分类以账表为准：账表有 1954 行且一级从不为空，book.json 是旧副本，
    可能停在分类功能上线之前。账表读不到时才退回 book.json。
    """
    lib = os.path.join(root, "books")
    ledger = {r["uid"]: [str(x).strip() for x in (r.get("一级"), r.get("二级"))
                          if str(x or "").strip()]
              for r in L.categories(root=root)}
    books, shas, sha_uid = [], set(), {}
    for dirpath, dirnames, filenames in os.walk(lib):
        dirnames[:] = [d for d in dirnames if not d.startswith("_")]
        if "book.json" not in filenames:
            continue
        doc = json.load(open(os.path.join(dirpath, "book.json"), encoding="utf-8"))
        uid = doc.get("uid", "")
        subjects = ledger.get(uid) or [str(x).strip() for x in (doc.get("subjects") or [])]
        srcs = [x for x in (doc.get("sources") or []) if x.get("file")]
        primary = next((x for x in srcs if x.get("primary_src")), srcs[0] if srcs else {})
        books.append({"uid": uid, "title": doc.get("title", ""),
                      "author": doc.get("author", ""),
                      "subjects": [s for s in subjects if s],
                      "path": os.path.relpath(dirpath, lib),
                      "format": (primary.get("format") or "").lower(),
                      "primary": primary, "sources": srcs})
        for x in srcs:
            sha = x.get("sha256")
            if sha:
                shas.add(sha)
                sha_uid.setdefault(sha, doc.get("uid", ""))
    return books, shas, sha_uid


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


def source_rank(fmt):
    return FORMATS.get((fmt or "").lower(), 0)


def norm_subjects(value):
    """分类列表归一：空值落「其他」，一级在前，去重。"""
    parts = [value] if isinstance(value, str) else list(value or [])
    out = []
    for part in parts:
        for s in re.split(r"[/,，]", str(part)):
            s = s.strip()
            if s and s not in out:
                out.append(s)
    return out or ["其他"]


def _shared_subjects(bs):
    vals = {tuple(norm_subjects(b.get("subjects"))) for b in bs}
    return list(next(iter(vals))) if len(vals) == 1 else []


def _target(item, b):
    item.update(uid=b["uid"], target=b["uid"], target_title=b["title"],
                target_author=b.get("author", ""),
                target_subjects=norm_subjects(b.get("subjects")))


def plan_for(path, books, sha_uid, seen, ret, batch_titles):
    """收件箱一个文件一行。动作只出 add/replace/delete/review 四种。

    review 是阻塞态，不是跳过：扫描拿不准时不给默认动作，必须人改成
    新增、换源或删除，整张清单才能入库。
    """
    base = os.path.basename(path)
    sha = sha256_file(path)
    title, author = parse_name(base)
    fmt = os.path.splitext(base)[1].lstrip(".").lower()
    item = {"plan_version": 2, "file": base, "path": path, "sha256": sha,
            "bytes": os.path.getsize(path), "format": fmt,
            "title": title, "author": author, "uid": "", "target": "",
            "target_title": "", "target_author": "", "target_subjects": [],
            "subjects": [], "retention": "keep", "action": "",
            "status": "", "note": ""}
    old_uid = sha_uid.get(sha)
    if old_uid:
        b = next((x for x in books if x["uid"] == old_uid), None)
        if b:
            _target(item, b)
        item.update(action="delete", status="in_library", uid=old_uid, target=old_uid,
                    target_subjects=item["target_subjects"] or ["其他"],
                    note="库里已有同一文件：%s" % old_uid)
        return item
    if sha in seen:
        item.update(action="delete", status="batch_dup", target_subjects=["其他"],
                    note="本批重复：%s" % seen[sha])
        return item
    hits = match(title, books)
    exact = [b for b in hits if norm(b["title"]) == norm(title)]
    if len(exact) == 1:
        b = exact[0]
        _target(item, b)
        item["retention"] = ret.get(b["uid"], "keep")
        old = b.get("primary") or {}
        old_sha = (old.get("sha256") or "").strip()
        old_fmt = (old.get("format") or "").lower()
        new_rank, old_rank = source_rank(fmt), source_rank(old_fmt)
        known_old = old_fmt in FORMATS
        if old_sha == sha:
            item.update(action="delete", status="in_library",
                        note="库里已有同一文件：%s" % b["uid"])
        elif known_old and new_rank > old_rank:
            item.update(action="replace", status="replace",
                        note="换源：%s → %s；旧源保留，正文需重拆"
                             % (old.get("file") or old_fmt, base))
        elif known_old:
            item.update(action="review",
                        status="same_format" if new_rank == old_rank else "weaker",
                        note="库里已有 %s，现有源 %s；确认换源还是删除"
                             % (b["uid"], old.get("file") or old_fmt))
        else:
            item.update(action="review", status="weaker",
                        note="库里已有 %s，没有可比的主源；确认换源还是删除" % b["uid"])
        return item
    if len(exact) > 1:
        item.update(action="review", status="candidates",
                    target=",".join(b["uid"] for b in exact),
                    target_subjects=_shared_subjects(exact) or ["其他"],
                    note="同名候选：%s；选一本换源或删除"
                         % "、".join("%s %s" % (b["uid"], b["title"]) for b in exact[:4]))
        return item
    if hits:
        item.update(action="review", status="fuzzy",
                    target=",".join(x["uid"] for x in hits),
                    target_subjects=_shared_subjects(hits) or ["其他"],
                    note="疑似同名《%s》；确认是同一本就换源，不是就新增"
                         % "、".join(x["title"] for x in hits[:3]))
        if len(hits) == 1:
            _target(item, hits[0])
            item["retention"] = ret.get(hits[0]["uid"], "keep")
        return item
    key = norm(title)
    if key and key in batch_titles:
        item.update(action="review", status="fuzzy", target_subjects=["其他"],
                    note="本批已有同名：%s；确认是否同一本" % batch_titles[key])
        return item
    item.update(action="add", status="new", subjects=["其他"],
                note="新书，分类未定，暂放其他")
    return item


def scan(root):
    lib = os.path.join(root, "books")
    inbox = os.path.join(lib, "_收件箱")
    books, _shas, sha_uid = library_index(root)
    rows = L.categories(root)
    ret = {r["uid"]: (r.get("保留") or "keep") for r in rows}
    base = int(next_uid(rows)[1:])
    out, seen, batch_titles = [], {}, {}
    n = 0
    for name in sorted(os.listdir(inbox)):
        p = os.path.join(inbox, name)
        if not os.path.isfile(p) or name.startswith("."):
            continue
        if name.lower() in ("readme.md", "readme.txt"):
            continue
        item = plan_for(p, books, sha_uid, seen, ret, batch_titles)
        seen.setdefault(item["sha256"], name)
        key = norm(item["title"])
        if key:
            batch_titles.setdefault(key, name)
        if item["action"] == "add":
            item["uid"] = "B%04d" % (base + n)
            n += 1
        out.append(item)
    return out


def next_uid_cmd(root):
    print(next_uid(L.categories(root)))


CLS_MIN = 0.45
CLASSIFY_MODEL = os.environ.get("INGEST_CLASSIFY_MODEL") or CM.configured()[0]
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
        item["subjects"] = ["其他"]
        item["note"] = "低置信 %.2f（%s），暂放其他，可手改" % (c1, l1 or "无")
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
            parent = os.path.dirname(cache_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            L.write_json_atomic(cache_path, cache)
        except (SystemExit, Exception) as exc:
            msg = (str(exc) or type(exc).__name__)[:90]
            print("分类失败：%s" % msg, file=sys.stderr)
            for it in todo:
                it["subjects"] = ["其他"]
                it["note"] = "分类失败：%s；暂放其他" % msg
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
            vals = (time.strftime("%Y-%m-%dT%H:%M:%S%z"), r["action"], r.get("uid", ""),
                    r.get("file", ""), r.get("sha256", ""), r.get("bytes", ""),
                    r.get("note", ""))
            fh.write("\t".join(str(x).replace("\t", " ").replace("\n", " ")
                              for x in vals) + "\n")


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
    lib = os.path.join(root, "books")
    for dirpath, dirnames, filenames in os.walk(lib):
        dirnames[:] = [d for d in dirnames if not d.startswith("_")]
        if "book.json" in filenames:
            doc = json.load(open(os.path.join(dirpath, "book.json"), encoding="utf-8"))
            if doc.get("uid") == uid:
                return os.path.relpath(dirpath, lib), doc
    return None, None


def book_json(uid, title, author, subjects, sources, notes, retention="keep"):
    return L.book_doc(uid, title, "", author, subjects, None, None, None,
                      retention, notes, sources, [])


def trash_dir(root):
    return os.path.join(root, "工作区", "批次",
                        "收件箱删除-" + time.strftime("%Y-%m-%d"))


def free_path(path):
    """目标已存在时加 -2/-3 后缀，绝不覆盖。"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists("%s-%d%s" % (base, n, ext)):
        n += 1
    return "%s-%d%s" % (base, n, ext)


def _manifest(root, row):
    path = os.path.join(trash_dir(root), "deleted.tsv")
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as fh:
        if new:
            fh.write("ts\tfile\tsha256\tbytes\treason\tuid\tdest\n")
        fh.write("\t".join(str(x) for x in row) + "\n")


def _trash_readme(root):
    path = os.path.join(trash_dir(root), "README.md")
    if os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# 收件箱删除\n\n"
                 "这里是从 `books/_收件箱/` 删除的文件，书库里的书没有动。\n\n"
                 "要找回：把文件移回 `books/_收件箱/`，再到「入库」页重新扫描；\n"
                 "确认不要：整个日期目录删掉即可。\n")


def _delete(root, it, src):
    sha = sha256_file(src)
    if it.get("sha256") and sha != it["sha256"]:
        raise SystemExit("文件已变，重新扫描: " + it["file"])
    dest = free_path(os.path.join(trash_dir(root), it["file"]))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        os.replace(src, dest)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            raise SystemExit("收件箱与回收站不在同一磁盘，先手动移: " + src)
        raise
    _trash_readme(root)
    reason = str(it.get("note") or it.get("status") or "删除")
    reason = reason.replace("\t", " ").replace("\n", " ")
    _manifest(root, (time.strftime("%Y-%m-%dT%H:%M:%S%z"), it["file"], sha,
                     os.path.getsize(dest), reason, it.get("uid", ""),
                     os.path.relpath(dest, root)))
    return dict(it, action="delete", sha256=sha,
                note="已移入回收站 %s" % os.path.relpath(dest, root))


def _add(root, it, src):
    lib = os.path.join(root, "books")
    subj = norm_subjects(it.get("subjects"))
    title = str(it.get("title") or "").strip()
    if not title:
        raise SystemExit("add 缺书名: %s" % it["file"])
    ret = it.get("retention") or "keep"
    if ret not in ("keep", "review", "drop"):
        raise SystemExit("保留值不认识: %s" % ret)
    uid = str(it.get("uid") or "").strip()
    if uid and not re.match(r"^B\d{4}$", uid):
        raise SystemExit("uid 形如 B1234: %s" % uid)
    rows = L.categories(root=root)
    used = {r["uid"] for r in rows}
    if not uid:
        uid = next_uid(rows)
    while uid in used or os.path.exists(os.path.join(lib, uid)):
        uid = "B%04d" % (int(uid[1:]) + 1)
    dest = os.path.join(lib, uid)
    os.makedirs(os.path.join(dest, "source"), exist_ok=True)
    target = free_path(os.path.join(dest, "source", it["file"]))
    shutil.move(src, target)
    name = os.path.basename(target)
    sources = [{"file": "source/" + name, "format": it["format"],
                "bytes": os.path.getsize(target), "sha256": it["sha256"],
                "primary_src": 1}]
    day = time.strftime("%Y-%m-%d")
    L.write_json_atomic(os.path.join(dest, "book.json"),
                        book_json(uid, title, it.get("author", ""), subj, sources,
                                  "ingest:%s" % day, ret))
    L.category_put({"uid": uid, "一级": subj[0],
                    "二级": subj[1] if len(subj) > 1 else "",
                    "作者层": "0", "保留": ret, "正式书名": title,
                    "备注": "ingest:%s；待复核" % day}, "ingest", root=root)
    return dict(it, action="add", uid=uid,
                note="已入库 %s（保留 %s）" % (uid, ret))


def _replace(root, it, src):
    lib = os.path.join(root, "books")
    uid = str(it.get("uid") or "").strip()
    if not re.match(r"^B\d{4}$", uid):
        raise SystemExit("replace 缺 uid: %s" % it["file"])
    rel, doc = find_by_uid(root, uid)
    if not rel:
        raise SystemExit("库里没有 " + uid)
    rows = [r for r in L.categories(root=root) if r["uid"] == uid]
    cur = rows[0] if rows else {}
    if cur and str(cur.get("一级") or "").strip():
        subj = norm_subjects([cur.get("一级"), cur.get("二级")])
    else:
        subj = norm_subjects(doc.get("subjects"))
    want = it.get("retention") or cur.get("保留") or doc.get("retention") or "keep"
    if want not in ("keep", "review", "drop"):
        raise SystemExit("保留值不认识: %s" % want)
    dest = os.path.join(lib, rel)
    os.makedirs(os.path.join(dest, "source"), exist_ok=True)
    target = free_path(os.path.join(dest, "source", it["file"]))
    shutil.move(src, target)
    name = os.path.basename(target)
    old, sources = None, []
    for s in doc.get("sources") or []:
        if old is None and s.get("primary_src"):
            old = dict(s)
        sources.append(dict(s, primary_src=0))
    if old is None and sources:
        old = dict(sources[0])
    sources.insert(0, {"file": "source/" + name, "format": it["format"],
                       "bytes": os.path.getsize(target), "sha256": it["sha256"],
                       "primary_src": 1})
    day = time.strftime("%Y-%m-%d")
    doc["sources"] = sources
    doc["retention"] = want
    doc["subjects"] = subj
    doc["notes"] = (doc.get("notes") or "") + "；替换源:%s" % day
    L.write_json_atomic(os.path.join(dest, "book.json"), doc)
    if cur:
        row = dict(cur)
        row["保留"] = want
        row["一级"] = subj[0]
        row["二级"] = subj[1] if len(subj) > 1 else ""
        L.category_put(row, "ingest", root=root)
    else:
        L.category_put({"uid": uid, "一级": subj[0],
                        "二级": subj[1] if len(subj) > 1 else "",
                        "作者层": "0", "保留": want,
                        "正式书名": doc.get("title", ""),
                        "备注": "ingest:%s；替换源" % day}, "ingest", root=root)
    md_dir = os.path.join(root, L.md_rel(uid))
    man = L.read_md_manifest(md_dir) if os.path.isdir(md_dir) else {}
    marked = False
    if man and not man.get("source_sha256") and old and old.get("sha256"):
        man["source_sha256"] = old["sha256"]
        man.setdefault("source", old.get("file", ""))
        man.setdefault("uid", uid)
        L.write_json_atomic(os.path.join(md_dir, ".manifest.json"), man)
        marked = True
    note = "新源置顶，旧源保留；md 仍是旧源产物，需重拆 %s" % uid
    if marked:
        note += "，旧 md 已标记待重拆"
    return dict(it, action="replace", uid=uid, note=note)


def apply_one(root, it, shas):
    lib = os.path.join(root, "books")
    action = it.get("action")
    if action not in ("add", "replace", "delete"):
        raise SystemExit("plan 里有未处理的动作: %s %s" % (action, it.get("file")))
    name = str(it.get("file") or "")
    if not name or name != os.path.basename(name) or name in (".", "..") or name.startswith("."):
        raise SystemExit("文件名不合法: %r" % name)
    inbox = os.path.realpath(os.path.join(lib, "_收件箱"))
    src = os.path.realpath(it.get("path") or os.path.join(inbox, name))
    if os.path.dirname(src) != inbox:
        src = os.path.realpath(os.path.join(inbox, name))
    if os.path.dirname(src) != inbox:
        raise SystemExit("源文件不在收件箱: %s" % it.get("path"))
    if action in ("add", "replace") and it.get("sha256") in shas:
        return dict(it, action="skip", note="同 sha256 已在库（幂等跳过）")
    if not os.path.isfile(src):
        raise SystemExit("收件箱里没有这个文件: " + src)
    sha = sha256_file(src)
    if it.get("sha256") and sha != it["sha256"]:
        raise SystemExit("文件已变，重新扫描: " + name)
    if action == "delete":
        return _delete(root, it, src)
    if action == "add":
        return _add(root, it, src)
    return _replace(root, it, src)


def journal_keys(root):
    path = journal_path(root)
    keys = set()
    if not os.path.exists(path):
        return keys
    with open(path, encoding="utf-8") as fh:
        next(fh, None)
        for line in fh:
            cols = line.rstrip("\n").split("\t")
            if len(cols) >= 4 and cols[1] in ("add", "replace", "delete"):
                keys.add((cols[1], cols[3]))
    return keys


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
    ap.add_argument("--cache",
                    help="分类缓存路径，默认 <root>/工作区/批次/ingest/classify.json")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-rebuild", action="store_true")
    ap.add_argument("--no-classify", action="store_true",
                    help="跳过 deepseek 分类预填")
    ap.add_argument("--retention", choices=("keep", "review", "drop"),
                    help="新增/换源后的书库去留；不填则沿用账表现值")
    a = ap.parse_args()

    if a.cmd == "uid":
        return next_uid_cmd(a.root)

    if a.cmd == "scan":
        items = scan(a.root)
        cache = a.cache or os.path.join(a.root, "工作区", "批次", "ingest", "classify.json")
        classify_adds(items, cache, enabled=not a.no_classify)
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
        if not isinstance(items, list):
            print("计划不是数组：" + a.arg, file=sys.stderr)
            return 2
        if any(not isinstance(i, dict) or i.get("plan_version") != 2 for i in items):
            print("旧清单，先重新生成入库清单", file=sys.stderr)
            return 2
    else:
        base = os.path.basename(a.arg)
        it = {"plan_version": 2, "file": base, "path": a.arg,
              "sha256": sha256_file(a.arg), "bytes": os.path.getsize(a.arg),
              "format": os.path.splitext(base)[1].lstrip(".").lower(),
              "title": a.title or parse_name(base)[0], "author": a.author,
              "uid": a.uid or "", "target": "",
              "target_title": "", "target_author": "", "target_subjects": [],
              "subjects": (a.subjects or "").split("/") if a.subjects else [],
              "retention": a.retention or "", "action": a.cmd,
              "status": "new" if a.cmd == "add" else "replace", "note": ""}
        items = [it]

    review = [i for i in items if i.get("action") == "review"]
    if review:
        print("还有 %d 行待核，先改成新增/换源/删除再入库：" % len(review), file=sys.stderr)
        for i in review:
            print("  %-44s %s" % (i.get("file", "")[:44], i.get("note", "")), file=sys.stderr)
        return 2
    unknown = [i for i in items if i.get("action") not in ("add", "replace", "delete")]
    if unknown:
        print("有认不出的动作：%s" % unknown[0].get("action"), file=sys.stderr)
        return 2
    # 分类为空不再拦：add 一律落「其他」，宁可粗分也不留空。
    for i in items:
        if i["action"] == "add":
            i["subjects"] = norm_subjects(i.get("subjects"))
    no_title = [i for i in items if not str(i.get("title") or "").strip()]
    if no_title:
        print("这些行没有书名：", file=sys.stderr)
        for i in no_title:
            print("  " + str(i.get("file") or ""), file=sys.stderr)
        return 2
    bad_ret = [i for i in items if (i.get("retention") or "keep") not in ("keep", "review", "drop")]
    if bad_ret:
        print("保留值不认识：%s" % bad_ret[0].get("retention"), file=sys.stderr)
        return 2

    _books, shas, _sha_uid = library_index(a.root)

    # add 没写 uid 时按人账往下排：scan 已经排好，但手写 plan 与单本 add 容易漏，
    # 漏了会写出一行 uid 为空的 book.json 与分类表。
    if any(i["action"] == "add" and not (i.get("uid") or "").strip() for i in items):
        rows = L.categories(a.root)
        base, n = int(next_uid(rows)[1:]), 0
        for i in items:
            if i["action"] == "add" and not (i.get("uid") or "").strip():
                i["uid"] = "B%04d" % (base + n)
                n += 1
    todo = [i for i in items if i["action"] in ("add", "replace", "delete")]
    if not a.apply:
        print_plan(items)
        print("\n未写盘。确认后加 --apply。")
        return 0

    take_lock(a.root)
    done, failed = [], []
    done_keys = journal_keys(a.root)
    try:
        for it in todo:
            src = os.path.join(a.root, "books", "_收件箱", it["file"])
            if (it["action"], it["file"]) in done_keys and not os.path.isfile(src):
                print("已处理，跳过：%s" % it["file"])
                continue
            try:
                res = apply_one(a.root, it, shas)
            except (SystemExit, OSError) as exc:
                msg = str(exc) or type(exc).__name__
                failed.append((it["file"], msg))
                journal(a.root, [dict(it, action="failed", note=msg[:200])])
                print("失败：%s：%s" % (it["file"], msg), file=sys.stderr)
                continue
            done.append(res)
            journal(a.root, [res])
            if res["action"] in ("add", "replace"):
                shas.add(it["sha256"])
    finally:
        drop_lock(a.root)
    print_plan(done)
    if failed:
        print("\n%d 行失败，修好后可重跑（已处理的行会跳过）。" % len(failed), file=sys.stderr)
    if done and any(r["action"] in ("add", "replace") for r in done) and not a.no_rebuild:
        print(rebuild())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
