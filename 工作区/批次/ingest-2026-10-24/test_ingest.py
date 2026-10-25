#!/usr/bin/env python3
"""ingest.py 的临时根目录验收：新增、替换、重复跳过、幂等、缺 subjects 拒绝。"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = "/home/shike/文档/books"
PY = os.path.join(ROOT, "工作区", ".venv-pdf", "bin", "python")
INGEST = os.path.join(ROOT, "工作区", "脚本", "ingest.py")


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(data)


def run(args, expect=0):
    r = subprocess.run([PY, INGEST] + args, capture_output=True, text=True)
    assert r.returncode == expect, (r.returncode, r.stdout, r.stderr)
    return r.stdout


def main():
    tmp = tempfile.mkdtemp(prefix="ingest-")
    lib, cat, inbox = (os.path.join(tmp, "库"), os.path.join(tmp, "目录"),
                       os.path.join(tmp, "库", "_收件箱"))
    pdf = os.path.join(lib, "文学", "严肃文学小说", "旧书", "source", "旧书.pdf")
    write(pdf, b"old pdf bytes")
    doc = {"uid": "B0001", "title": "旧书", "title_from_file": "", "author": "",
           "subjects": ["文学", "严肃文学小说"], "path": "文学/严肃文学小说/旧书",
           "sources": [{"file": "source/旧书.pdf", "format": "pdf", "bytes": os.path.getsize(pdf),
                        "sha256": sha(pdf), "primary_src": 1}],
           "awards": [], "derived": [], "notes": ""}
    write(os.path.join(lib, "文学", "严肃文学小说", "旧书", "book.json"),
          json.dumps(doc, ensure_ascii=False).encode())
    os.makedirs(cat, exist_ok=True)
    open(os.path.join(cat, "分类表.csv"), "w", encoding="utf-8").write(
        "uid,一级,二级,作者层,保留,正式书名,备注\n"
        "B0001,文学,严肃文学小说,0,keep,旧书,\n")

    write(os.path.join(inbox, "旧书 (新版).epub"), b"new epub bytes")
    write(os.path.join(inbox, "旧书副本.epub"), b"new epub bytes")
    write(os.path.join(inbox, "新书 (张三).epub"), b"brand new book")

    out = run(["scan", "--root", tmp, "--json", "--no-classify"])
    items = json.loads(out)
    acts = {i["file"]: i["action"] for i in items}
    assert acts["旧书 (新版).epub"] == "replace", acts
    assert acts["旧书副本.epub"] == "skip", acts
    assert acts["新书 (张三).epub"] == "add", acts
    print("scan ok", acts)

    plan = os.path.join(tmp, "plan.json")
    run(["scan", "--root", tmp, "--out", plan, "--no-classify"])
    items = json.load(open(plan, encoding="utf-8"))
    r = subprocess.run([PY, INGEST, "apply", plan, "--root", tmp], capture_output=True, text=True)
    assert r.returncode == 2, r.returncode
    print("refused without subjects ok")

    for i in items:
        if i["action"] == "add":
            i["subjects"] = ["文学", "严肃文学小说"]
            i["title"] = "新书"
    json.dump(items, open(plan, "w", encoding="utf-8"), ensure_ascii=False)
    run(["apply", plan, "--root", tmp, "--apply", "--no-rebuild"])

    rows = open(os.path.join(cat, "分类表.csv"), encoding="utf-8").read().strip().splitlines()
    assert len(rows) == 3, rows
    assert rows[-1].split(",")[0] == "B0002", rows[-1]
    newdir = os.path.join(lib, "文学", "严肃文学小说", "新书")
    assert os.path.isfile(os.path.join(newdir, "book.json")), newdir
    assert os.path.isfile(os.path.join(newdir, "source", "新书 (张三).epub"))
    print("add ok")

    d = json.load(open(os.path.join(lib, "文学", "严肃文学小说", "旧书", "book.json"),
                       encoding="utf-8"))
    assert d["sources"][0]["format"] == "epub", d["sources"]
    assert d["sources"][1]["format"] == "pdf", d["sources"]
    assert d["sources"][0]["primary_src"] == 1 and d["sources"][1]["primary_src"] == 0
    print("replace ok")

    assert os.path.isfile(os.path.join(cat, ".ingest.lock")) is False
    assert os.path.isfile(os.path.join(tmp, "工作区", "批次", "ingest", "journal.tsv"))
    print("journal ok")

    shutil.copy(os.path.join(newdir, "source", "新书 (张三).epub"),
                os.path.join(inbox, "新书副本.epub"))
    out = run(["scan", "--root", tmp, "--json", "--no-classify"])
    acts = {i["file"]: i["action"] for i in json.loads(out)}
    assert acts["新书副本.epub"] == "skip", acts
    print("library dedup ok")

    run(["apply", plan, "--root", tmp, "--apply", "--no-rebuild"])
    rows = open(os.path.join(cat, "分类表.csv"), encoding="utf-8").read().strip().splitlines()
    assert len(rows) == 3, "second apply duplicated rows: %s" % rows
    print("idempotent apply ok")

    sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
    import ingest as I
    items = [{"sha256": "aaa", "action": "add", "title": "性学观止", "author": "",
              "subjects": [], "note": ""}]
    I._call_classifier = lambda t, a: [{"level1": "生活与实用", "conf1": 0.9,
                                        "level2": "性学与性教育", "conf2": 0.8}]
    cache = os.path.join(tmp, "cls.json")
    I.classify_adds(items, cache, enabled=True)
    assert items[0]["subjects"] == ["生活与实用", "性学与性教育"], items[0]
    assert items[0]["classify"]["needs_review"] is False, items[0]
    low = [{"sha256": "bbb", "action": "add", "title": "杂书", "author": "",
            "subjects": [], "note": ""}]
    I._call_classifier = lambda t, a: [{"level1": "拿不准", "conf1": 0.2,
                                        "level2": "", "conf2": 0.0}]
    I.classify_adds(low, cache, enabled=True)
    assert low[0]["subjects"] == [] and "低置信" in low[0]["note"], low[0]
    assert low[0]["classify"]["needs_review"] is True, low[0]
    print("classify confidence ok")

    import classify_books as C
    rows = [{"uid": "B9001", "正式书名": "新书", "一级": "文学", "二级": "",
             "一级TypeSafe": "文学", "二级TypeSafe": "类型小说"}]
    old = [{"uid": "B0001", "正式书名": "旧书", "一级": "文学", "二级": "严肃文学小说",
            "作者层": "0", "保留": "keep", "备注": ""}]
    led, ign, added = C.merge_ledger(rows, old, add_new=True)
    assert added == ["B9001"] and led[-1]["二级"] == "类型小说", (added, led)
    assert led[-1]["备注"].startswith("auto:classify"), led[-1]
    led2, ign2, added2 = C.merge_ledger(rows, old, add_new=False)
    assert added2 == [] and ign2 == ["B9001"], (added2, ign2)
    print("classify add-new ok")

    shutil.rmtree(tmp)
    print("ALL PASS")


if __name__ == "__main__":
    main()
