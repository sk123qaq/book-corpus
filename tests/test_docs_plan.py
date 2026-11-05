"""docs_to_md.py plan：v_pending_md 选书、--applied 标 new、--only 覆盖、排序去重、
同 sha 且有 md 的标 skipped。只读 build/library.db，不碰 venv。
"""

import json

from conftest import run_cli, write_book, write_ledger

DOCS_JOB = ("工作区", "运维", "jobs", "docs-plan")


def _build(root):
    r = run_cli("--root", root, "library")
    assert r.returncode == 0, r.stdout + r.stderr


def _plan(root, job, *extra):
    job.mkdir(parents=True, exist_ok=True)
    r = run_cli("--root", root, "run", "docs_to_md", "plan", "--job-dir", str(job), *extra)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads((job / "worklist.json").read_text(encoding="utf-8")), r


def _sha(root, uid):
    doc = json.loads((root / "books" / uid / "book.json").read_text(encoding="utf-8"))
    return doc["sources"][0]["sha256"]


def _md(root, uid, sha):
    md = root / "books" / uid / "md"
    md.mkdir(parents=True, exist_ok=True)
    (md / "001_第1节.md").write_text("# 第1节\n\n正文\n", encoding="utf-8")
    (md / ".manifest.json").write_text(
        json.dumps({"uid": uid, "source_sha256": sha, "chapters": 1}, ensure_ascii=False), encoding="utf-8"
    )


def _set_applied(job, rows):
    path = job / "applied.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"version": 1, "conflicts": 0, "applied": rows, "failed": []},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _fixture(workspace):
    """workspace 自带 B0001/B0002（epub 有 md）、B0003（txt 无 md）。"""
    write_book(workspace, "B0004", "文学", "丁书", None, fmt="docx")
    write_book(workspace, "B0005", "文学", "戊书", None, fmt="epub")
    write_book(workspace, "B0006", "文学", "己书", None, fmt="docx")
    write_ledger(
        workspace,
        [
            ("B0004", "文学", "丁书", ""),
            ("B0005", "文学", "戊书", ""),
            ("B0006", "文学", "己书", ""),
        ],
    )
    _build(workspace)


def test_plan_picks_pending_doc_formats_only(workspace):
    _fixture(workspace)
    job = workspace.joinpath(*DOCS_JOB)
    items, _r = _plan(workspace, job)

    uids = [i["uid"] for i in items]
    assert uids == ["B0003", "B0004", "B0006"]  # 排序；epub 的 B0005 与已有 md 的 B0001/B0002 不在内
    assert {i["reason"] for i in items} == {"backfill"}
    row = next(i for i in items if i["uid"] == "B0004")
    assert row["format"] == "docx"
    assert row["path"] == "books/B0004/source/丁书.docx"
    assert row["sha256"] == _sha(workspace, "B0004")
    assert "skipped" not in row

    progress = json.loads((job / "progress.json").read_text(encoding="utf-8"))
    assert progress["phase"] == "planned"
    assert (progress["done"], progress["total"]) == (0, 3)


def test_applied_add_marks_new_and_dedupes(workspace):
    _fixture(workspace)
    job = workspace.joinpath(*DOCS_JOB)
    applied = _set_applied(
        job,
        [
            {"uid": "B0006", "file": "己书.docx", "format": "docx", "action": "add"},
            {"uid": "B0006", "file": "己书.docx", "format": "docx", "action": "add"},
            {"uid": "B0005", "file": "戊书.epub", "format": "epub", "action": "add"},
            {"uid": "B0001", "file": "旧.epub", "format": "epub", "action": "delete"},
        ],
    )
    items, _r = _plan(workspace, job, "--applied", str(applied))

    uids = [i["uid"] for i in items]
    assert uids == ["B0003", "B0004", "B0006"]
    row = next(i for i in items if i["uid"] == "B0006")
    assert row["reason"] == "new"
    assert row["path"] == "books/B0006/source/己书.docx"  # 路径从库里补
    assert row["sha256"] == _sha(workspace, "B0006")
    # 非 doc 格式的 applied 行不进 worklist
    assert "B0005" not in uids


def test_only_overrides_selection(workspace):
    _fixture(workspace)
    job = workspace.joinpath(*DOCS_JOB)
    items, _r = _plan(workspace, job, "--only", "B0004, B0005, B0099")
    assert [i["uid"] for i in items] == ["B0004", "B0099"]
    assert next(i for i in items if i["uid"] == "B0004")["reason"] == "backfill"
    ghost = next(i for i in items if i["uid"] == "B0099")
    assert ghost["path"] == ""
    assert ghost["reason"] == "new"


def test_same_sha_with_md_is_skipped(workspace):
    _fixture(workspace)
    _md(workspace, "B0004", _sha(workspace, "B0004"))
    _md(workspace, "B0006", "deadbeef")  # manifest 的 sha 对不上 → 还要转
    job = workspace.joinpath(*DOCS_JOB)

    items, r = _plan(workspace, job)
    row = next(i for i in items if i["uid"] == "B0004")
    assert row.get("skipped") is True
    assert "skipped" not in next(i for i in items if i["uid"] == "B0006")
    assert "跳过 1" in r.stdout


def test_md_without_manifest_is_not_skipped(workspace):
    _fixture(workspace)
    md = workspace / "books" / "B0004" / "md"
    md.mkdir(parents=True, exist_ok=True)
    (md / "001_第1节.md").write_text("# 第1节\n\n正文\n", encoding="utf-8")
    job = workspace.joinpath(*DOCS_JOB)

    items, _r = _plan(workspace, job)
    row = next(i for i in items if i["uid"] == "B0004")
    assert "skipped" not in row


def test_plan_refuses_bad_only_uid(workspace):
    _fixture(workspace)
    job = workspace.joinpath(*DOCS_JOB)
    job.mkdir(parents=True, exist_ok=True)
    r = run_cli("--root", workspace, "run", "docs_to_md", "plan", "--job-dir", str(job), "--only", "B1")
    assert r.returncode != 0
    assert "uid 不像 B0001" in r.stderr + r.stdout
