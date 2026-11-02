import json
import os
import subprocess
import sys

import pytest

BOOKS = [
    ("B0001", "文学", "甲书", ["官场的铁笼把人变成机器上的零件，科层制把每个具体的人压成一个可以互换的编号。",
     "第二段讲自由与独立判断，一个人若不肯放弃自己的判断，就只能在铁笼里保持清醒。"]),
    ("B0002", "人文社科", "乙书", ["礼治秩序与法治不同，礼治靠的是传统与教化，法治靠的是国家权力与成文规则。",
     "差序格局是乡土中国的结构，人与人的关系像石子投水，一圈一圈推出去，越远越薄。"]),
]


def run_cli(*args, cwd=None, env=None):
    full_env = dict(os.environ)
    full_env.pop("BOOKS_ROOT", None)
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, "-m", "bookcorpus", *map(str, args)],
                          capture_output=True, text=True, cwd=cwd, env=full_env)


def write_book(root, uid, subject, title, paragraphs=None, fmt="epub", sha=None):
    rel = os.path.join(subject, title)
    bdir = os.path.join(root, "库", rel)
    os.makedirs(os.path.join(bdir, "source"), exist_ok=True)
    if paragraphs:
        md = os.path.join(bdir, "md")
        os.makedirs(md, exist_ok=True)
        for i, text in enumerate(paragraphs, 1):
            with open(os.path.join(md, "%03d_第%d节.md" % (i, i)), "w", encoding="utf-8") as f:
                f.write("# 第%d节\n\n%s\n" % (i, text))
    doc = {
        "uid": uid,
        "title": title,
        "title_from_file": "",
        "author": "某人",
        "subjects": [subject],
        "path": rel,
        "series": None,
        "volume": None,
        "container": None,
        "retention": "keep",
        "sources": [{
            "file": "source/%s.%s" % (title, fmt),
            "format": fmt,
            "bytes": 100,
            "sha256": sha or ("%064x" % int(uid[1:])),
            "primary_src": 1,
        }],
        "awards": [],
        "notes": "",
    }
    with open(os.path.join(bdir, "book.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return rel


def write_ledger(root, rows):
    path = os.path.join(root, "目录", "分类表.csv")
    with open(path, "a", encoding="utf-8") as f:
        for uid, subject, title, note in rows:
            f.write("%s,%s,,0,keep,%s,%s\n" % (uid, subject, title, note))


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    r = run_cli("init", root)
    assert r.returncode == 0, r.stdout + r.stderr
    rows = []
    for uid, subject, title, paragraphs in BOOKS:
        write_book(root, uid, subject, title, paragraphs)
        rows.append((uid, subject, title, ""))
    write_book(root, "B0003", "写作与编剧", "丙书", None, fmt="txt")
    rows.append(("B0003", "写作与编剧", "丙书", "无正文"))
    write_ledger(root, rows)
    return root
