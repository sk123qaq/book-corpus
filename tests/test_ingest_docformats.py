"""多格式来源：来源表（FORMATS）、白名单（ACCEPT_EXTS/REJECT_EXTS）、
scan 的拒绝计数、docx 与 epub 的强弱比较、apply --result 的机器可读形状。
"""

import json
import sys
from pathlib import Path

from conftest import run_cli, write_ledger

HERE = Path(__file__).resolve().parents[1]
SCRIPTS = HERE / "src" / "bookcorpus" / "scripts"
SRC = HERE / "src"

EXPECTED_FORMATS = {
    "epub": 3,
    "mobi": 2,
    "azw3": 2,
    "azw": 2,
    "pdf": 1,
    "docx": 1,
    "xlsx": 1,
    "xls": 1,
    "pptx": 1,
    "md": 1,
    "txt": 0,
}


def _modules():
    for p in (str(SCRIPTS), str(SRC)):
        if p not in sys.path:
            sys.path.insert(0, p)
    from bookcorpus.scripts import ingest, lib_common

    return ingest, lib_common


def _inbox(root, name, data):
    d = root / "books" / "_收件箱"
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_bytes(data)
    return path


def _scan(root):
    r = run_cli("--root", root, "run", "ingest", "scan", "--json", "--no-classify")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout), r


def _apply(root, items, result=None):
    job = root / "工作区" / "运维" / "jobs" / "docformats"
    job.mkdir(parents=True, exist_ok=True)
    plan = job / "plan.json"
    plan.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    args = ["--root", root, "run", "ingest", "apply", str(plan), "--apply", "--no-rebuild"]
    if result is not None:
        args += ["--result", str(result)]
    return run_cli(*args)


def test_formats_table_matches_design():
    ingest = _modules()[0]
    assert ingest.FORMATS == EXPECTED_FORMATS
    assert len(ingest.FORMATS) == 11
    assert ingest.source_rank("AZW") == 2
    assert ingest.source_rank("docx") == 1
    assert ingest.source_rank("xlsx") == 1
    assert ingest.source_rank("md") == 1
    assert ingest.source_rank("txt") == 0
    assert ingest.source_rank("djvu") == 0


def test_lib_common_is_the_single_source_of_extensions():
    ingest, lc = _modules()
    assert (
        frozenset({".epub", ".pdf", ".mobi", ".azw3", ".azw", ".txt", ".docx", ".xlsx", ".xls", ".pptx", ".md"})
        == lc.ACCEPT_EXTS
    )
    assert frozenset({".doc", ".ppt", ".csv", ".djvu"}) == lc.REJECT_EXTS
    assert frozenset({"txt", "md", "docx", "xlsx", "xls", "pptx"}) == lc.DOC_FORMATS
    assert lc.ACCEPT_EXTS.isdisjoint(lc.REJECT_EXTS)
    # 白名单与来源表必须一一对应，不然 scan 收得进来、plan_for 又不认识
    assert {ext.lstrip(".") for ext in lc.ACCEPT_EXTS} == set(ingest.FORMATS)
    assert set(lc.DOC_FORMATS) <= set(ingest.FORMATS)


def test_scan_rejects_doc_ppt_csv_djvu_and_counts(workspace):
    _inbox(workspace, "甲书.doc", b"doc" * 20)
    _inbox(workspace, "乙书.ppt", b"ppt" * 20)
    _inbox(workspace, "丙书.csv", b"a,b\n1,2\n")
    _inbox(workspace, "丁书.djvu", b"djvu" * 20)
    _inbox(workspace, "戊书.docx", b"docx" * 20)
    _inbox(workspace, "README.TXT", b"readme\n")

    items, r = _scan(workspace)
    files = [i["file"] for i in items]
    assert files == ["戊书.docx"], files
    assert items[0]["format"] == "docx"
    assert items[0]["action"] == "add"

    assert "跳过不支持的格式 4 个" in r.stderr
    for ext in (".doc", ".ppt", ".csv", ".djvu"):
        assert ext in r.stderr
    # init 往收件箱放了 README.md，加上这里写的 README.TXT，一共 2 个说明文件
    assert "跳过说明文件 2 个" in r.stderr


def test_docx_new_book_is_add(workspace):
    _inbox(workspace, "庚书.docx", b"geng" * 30)
    items, _r = _scan(workspace)
    row = next(i for i in items if i["file"] == "庚书.docx")
    assert row["action"] == "add"
    assert row["format"] == "docx"
    assert row["subjects"] == ["其他"]


def test_docx_against_epub_is_review_weaker(workspace):
    """epub(3) 在库，收件箱来 docx(1)：不能悄悄换源，落 review。"""
    _inbox(workspace, "甲书.docx", b"docx-version" * 10)
    items, _r = _scan(workspace)
    row = next(i for i in items if i["file"] == "甲书.docx")
    assert row["action"] == "review"
    assert row["status"] == "weaker"
    assert row["target"] == "B0001"
    assert "确认换源还是删除" in row["note"]


def test_epub_against_docx_is_replace(workspace):
    from conftest import write_book

    write_book(workspace, "B0009", "文学", "辛书", None, fmt="docx")
    write_ledger(workspace, [("B0009", "文学", "辛书", "")])
    _inbox(workspace, "辛书.epub", b"epub-version" * 10)
    items, _r = _scan(workspace)
    row = next(i for i in items if i["file"] == "辛书.epub")
    assert row["action"] == "replace"
    assert row["uid"] == "B0009"
    assert "换源" in row["note"]


def test_unknown_format_lands_review_not_replace(tmp_path):
    """白名单之外的文件若绕过 scan 直接进 plan_for，也得落 review。"""
    ingest = _modules()[0]
    path = tmp_path / "壬书.doc"
    path.write_bytes(b"old-word" * 10)
    item = ingest.plan_for(str(path), [], {}, {}, {}, {})
    assert item["action"] == "review"
    assert item["status"] == "unknown-format"
    assert "格式未纳入来源表" in item["note"]


def test_apply_result_reports_applied_rows(workspace):
    _inbox(workspace, "癸书.docx", b"gui" * 40)
    items, _r = _scan(workspace)
    row = next(i for i in items if i["file"] == "癸书.docx")
    row["subjects"] = ["文学"]
    result = workspace / "工作区" / "运维" / "jobs" / "apply-docs" / "applied.json"
    result.parent.mkdir(parents=True, exist_ok=True)

    r = _apply(workspace, items, result)
    assert r.returncode == 0, r.stdout + r.stderr

    doc = json.loads(result.read_text(encoding="utf-8"))
    assert doc["version"] == 1
    assert doc["conflicts"] == 0
    assert doc["failed"] == []
    assert doc["applied"] == [
        {"uid": row["uid"], "file": "癸书.docx", "format": "docx", "action": "add"},
    ]
    assert (workspace / "books" / row["uid"] / "book.json").is_file()
