import json
import os
import subprocess
import sys

import pytest

# 这些脚本由 test_acceptance.py 以子进程入口跑，不让 pytest 二次收集。
collect_ignore_glob = [
    "acceptance/ops/test_*.py",
    "acceptance/demo/test_*.py",
]

BOOKS = [
    (
        "B0001",
        "文学",
        "甲书",
        [
            "官场的铁笼把人变成机器上的零件，科层制把每个具体的人压成一个可以互换的编号。",
            "第二段讲自由与独立判断，一个人若不肯放弃自己的判断，就只能在铁笼里保持清醒。",
        ],
    ),
    (
        "B0002",
        "人文社科",
        "乙书",
        [
            "礼治秩序与法治不同，礼治靠的是传统与教化，法治靠的是国家权力与成文规则。",
            "差序格局是乡土中国的结构，人与人的关系像石子投水，一圈一圈推出去，越远越薄。",
        ],
    ),
]


def run_cli(*args, cwd=None, env=None):
    full_env = dict(os.environ)
    full_env.pop("BOOKS_ROOT", None)
    # 测试里不建 工作区/.venv-pdf：要 20 秒、要联网。单独测这条路的用例自己开。
    full_env.setdefault("BOOKS_NO_PDF_VENV", "1")
    full_env.setdefault("BOOKS_NO_DOCS_VENV", "1")
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "bookcorpus", *map(str, args)], capture_output=True, text=True, cwd=cwd, env=full_env
    )


def _source_entry(uid, title, fmt, sha, collection, collection_file):
    entry = {
        "format": fmt,
        "bytes": 100,
        "sha256": sha or ("%064x" % int(uid[1:])),
        "primary_src": 1,
    }
    if collection:
        entry["collection"] = collection
        entry["file"] = collection_file
    else:
        entry["file"] = "source/%s.%s" % (title, fmt)
    return entry


def write_collection(root, cuid, name):
    target = os.path.join(root, "collections", cuid, "source")
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, name), "wb") as f:
        f.write(b"c" * 100)
    return cuid


def write_book(root, uid, subject, title, paragraphs=None, fmt="epub", sha=None, collection=None, collection_file=None):
    rel = uid
    bdir = os.path.join(root, "books", uid)
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
        "series": None,
        "volume": None,
        "container": None,
        "retention": "keep",
        "sources": [_source_entry(uid, title, fmt, sha, collection, collection_file)],
        "awards": [],
        "notes": "",
    }
    with open(os.path.join(bdir, "book.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return rel


def write_ledger(root, rows):
    """写人账走 ledger.db（init 起就是库，CSV 只是种子）。"""
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from bookcorpus.scripts import lib_common as L

    for uid, subject, title, note in rows:
        L.category_put(
            {"uid": uid, "一级": subject, "二级": "", "作者层": "0", "保留": "keep", "正式书名": title, "备注": note},
            "test",
            root=str(root),
        )


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


@pytest.fixture
def reading_root(tmp_path, monkeypatch):
    """8771 四读测试实例：两本可读、一本只有书目、叠加层留空。"""
    root = tmp_path / "reading-instance"
    root.mkdir()
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")
    write_book(
        root,
        "B0001",
        "文学",
        "甲书",
        [
            "第一段写一个人走进屋子，看见桌上有一盏灯，灯下压着一封没有署名的信。",
            "第二段写他坐下，听见窗外的雨声，忽然想起许多年前的一个下午。",
        ],
    )
    write_book(
        root,
        "B0002",
        "人文社科",
        "乙书",
        ["礼治秩序与法治不同，礼治靠传统与教化，法治靠国家权力与成文规则。"],
    )
    write_book(root, "B0003", "写作与编剧", "丙书", None, fmt="txt")
    payload_path = root / "books" / "B0001" / "book.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["author"] = "z-library.sk, 1lib.sk, 鲁迅"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    monkeypatch.setenv("READING_OVERLAY", str(tmp_path / "no-overlay"))
    monkeypatch.setenv("READING_ANNOTATE", "0")
    return root
