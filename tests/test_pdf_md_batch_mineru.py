"""MinerU 兜底失败不能被当成「MinerU 也读不出」而按跳过受理落地。

策略：
- mineru_rescue 投了但失败（rc!=0/无 content list/超时）→ 可疑页留在人工复核，不 accepted、不落地；
- 只有 rc=0、拿到 content list 且确实 0 字 → 才允许按跳过受理；
- 超过 MINERU_MAX_PAGES 没提交的页不能被算进 skipped，也不能借它落地；
- 失败说明（result note）要带 rc 与 stderr 尾部。
"""

import io
import json
import pathlib
import subprocess
import sys
import types

import pytest

from bookcorpus.scripts import pdf_md_batch as P


class _FakeDoc:
    def insert_pdf(self, *args, **kwargs):
        pass

    def save(self, path, **kwargs):
        pathlib.Path(path).write_bytes(b"%PDF-1.4 fake")

    def close(self):
        pass


def _fake_pymupdf(monkeypatch):
    mod = types.ModuleType("pymupdf")
    mod.open = lambda *args, **kwargs: _FakeDoc()
    monkeypatch.setitem(sys.modules, "pymupdf", mod)


def _fake_mineru(tmp_path):
    """按 FAKE_MINERU_MODE 演三种回话：rc=1、rc=0 无 content list、rc=0 且 0 字。"""
    script = tmp_path / "fake_mineru.py"
    script.write_text(
        "\n".join(
            [
                "import os, pathlib, sys",
                "mode = os.environ.get('FAKE_MINERU_MODE', 'ok')",
                "out = sys.argv[sys.argv.index('-o') + 1]",
                "if mode == 'rc1':",
                "    print('quota exhausted: qps limit', file=sys.stderr)",
                "    sys.exit(1)",
                "if mode == 'nocontent':",
                "    print('upload ok but no result', file=sys.stderr)",
                "    sys.exit(0)",
                "pathlib.Path(out, 'slice_content_list.json').write_text('[]', encoding='utf-8')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return str(script)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    root = tmp_path
    book = root / "books" / "B0001"
    (book / "source").mkdir(parents=True)
    src = book / "source" / "x.pdf"
    src.write_bytes(b"%PDF-1.4 fake source")
    for d in ("work", "out", "logs", "tmp", "review", "batch/overrides"):
        (root / d).mkdir(parents=True)

    merged = root / "work" / "B0001" / "merged"
    merged.mkdir(parents=True)
    (merged / "run.json").write_text(
        json.dumps({"per_page": [{"page": 1, "seq": 1, "chars": 0}, {"page": 2, "seq": 2, "chars": 0}]}),
        encoding="utf-8",
    )

    _fake_pymupdf(monkeypatch)
    monkeypatch.setattr(P.L, "ROOT", str(root))
    monkeypatch.setattr(P.L, "PATHS", P.L.Layout(str(root)))
    monkeypatch.setattr(P, "VENV_PY", sys.executable)
    monkeypatch.setattr(P, "MINERU_CLI", _fake_mineru(tmp_path))

    state = {"suspects": [{"page": 1, "chars": 0, "ink": 0.2}]}

    def merge(ctx, uid, row, ledger, log, result):
        return str(merged), {
            "pages": 2,
            "book_pages": 2,
            "per_page": [{"page": 1, "seq": 1, "chars": 0}, {"page": 2, "seq": 2, "chars": 0}],
            "coverage_ok": True,
            "missing_images": [],
        }

    def assemble(ctx, row, merged_dir, stage, log, result):
        pathlib.Path(stage).mkdir(parents=True, exist_ok=True)
        pathlib.Path(stage, ".manifest.json").write_text("{}", encoding="utf-8")
        return 0

    def verify(ctx, stage, log):
        return 0, {"chapters": 1, "chars": 100, "images": 0, "problems": [], "verify_rc": 0}

    def pdf_checks(ctx, row, per_page, log, result):
        result["suspect_pages"] = [s["page"] for s in state["suspects"]]
        return [], list(state["suspects"]), []

    def enrich_manifest(stage, ctx, row, v, doc, coverage_ok, blank, suspect, short, accept, rescued=(), skipped=()):
        return {"accepted": accept}

    monkeypatch.setattr(P, "merge", merge)
    monkeypatch.setattr(P, "assemble", assemble)
    monkeypatch.setattr(P, "verify", verify)
    monkeypatch.setattr(P, "pdf_checks", pdf_checks)
    monkeypatch.setattr(P, "enrich_manifest", enrich_manifest)
    monkeypatch.setattr(P, "work_chunk", lambda *a, **k: True)

    row = {
        "uid": "B0001",
        "title": "mineru-repro",
        "format": "pdf",
        "pages": "2",
        "src": "books/B0001/source/x.pdf",
        "sha256": P.L.sha256_file(src),
    }
    ctx = {
        "batch": str(root / "batch"),
        "batch_name": "test",
        "tool_version": "x",
        "out": str(root / "out"),
        "work": str(root / "work"),
        "logs": str(root / "logs"),
        "tmp": str(root / "tmp"),
        "review": str(root / "review"),
        "stamp": "t",
        "api": True,
        "route": "ocr",
        "assemble_only": False,
        "force": True,
        "replace": False,
        "land": True,
    }
    return types.SimpleNamespace(root=root, row=row, ctx=ctx, state=state)


def _assert_stayed_in_review(res, harness, expect_pages, unsubmitted=None):
    assert res["status"] == "staged-review", res
    assert res.get("accepted") == ""
    assert not (harness.root / "books" / "B0001" / "md").exists()
    assert res["suspect_pages"] == expect_pages
    ledger = harness.root / "batch" / "skipped-pages.tsv"
    text = ledger.read_text(encoding="utf-8") if ledger.is_file() else ""
    assert "mineru-failed" not in text
    assert res.get("mineru_unsubmitted_pages") == unsubmitted


def test_mineru_rc_failure_keeps_suspect_in_review(harness, monkeypatch):
    monkeypatch.setenv("FAKE_MINERU_MODE", "rc1")
    res = P.run_one(harness.row, harness.ctx)
    _assert_stayed_in_review(res, harness, [1])
    assert not res.get("skipped_unreadable_pages")
    assert res["mineru_error"]["rc"] == 1
    assert "rc=1" in res["note"]
    assert "quota exhausted: qps limit" in res["note"]


def test_human_override_records_unreadable_pages(harness, monkeypatch):
    monkeypatch.setenv("FAKE_MINERU_MODE", "rc1")
    override = harness.root / "batch" / "overrides" / "B0001.json"
    override.write_text(json.dumps({"accept": "人工核对：第 1 页为封底，非正文"}, ensure_ascii=False), encoding="utf-8")
    seen = {}

    def enrich(stage, ctx, row, v, doc, coverage_ok, blank, suspect, short, accept, rescued=(), skipped=()):
        seen["skipped"] = list(skipped)
        return {"accepted": accept}

    monkeypatch.setattr(P, "enrich_manifest", enrich)
    res = P.run_one(harness.row, harness.ctx)
    assert res["status"] == "accepted", res
    assert res["skipped_unreadable_pages"] == [1]
    assert seen["skipped"] == [1]
    assert "未读出页：1" in res["note"]
    assert (harness.root / "books" / "B0001" / "md").is_dir()


def test_mineru_missing_content_list_keeps_suspect_in_review(harness, monkeypatch):
    monkeypatch.setenv("FAKE_MINERU_MODE", "nocontent")
    res = P.run_one(harness.row, harness.ctx)
    _assert_stayed_in_review(res, harness, [1])
    assert not res.get("skipped_unreadable_pages")
    assert "无 content list" in res["note"]
    assert "upload ok but no result" in res["note"]


def test_mineru_timeout_keeps_suspect_in_review(harness, monkeypatch):
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="mineru.py", timeout=1800, stderr="tls handshake stalled")

    monkeypatch.setattr(P.subprocess, "run", boom)
    res = P.run_one(harness.row, harness.ctx)
    _assert_stayed_in_review(res, harness, [1])
    assert res["mineru_error"]["reason"] == "超时"
    assert "超时" in res["note"]
    assert "tls handshake stalled" in res["note"]


def test_mineru_zero_chars_after_success_allows_skip(harness, monkeypatch):
    monkeypatch.setenv("FAKE_MINERU_MODE", "ok")
    res = P.run_one(harness.row, harness.ctx)
    assert res["status"] == "accepted", res
    assert res["skipped_unreadable_pages"] == [1]
    assert "MinerU 也读不出" in res["accepted"]
    assert (harness.root / "books" / "B0001" / "md").is_dir()
    ledger = (harness.root / "batch" / "skipped-pages.tsv").read_text(encoding="utf-8")
    assert "\tskip\t" in ledger
    assert "\t0\t" in ledger
    attempts = (harness.root / "batch" / P.MINERU_ATTEMPTS).read_text(encoding="utf-8")
    assert "\tok\t" in attempts
    assert P.mineru_pages_today(harness.ctx) == 1


def test_mineru_failure_counts_against_daily_quota(harness, monkeypatch):
    monkeypatch.setenv("FAKE_MINERU_MODE", "rc1")
    P.run_one(harness.row, harness.ctx)
    attempts_path = harness.root / "batch" / P.MINERU_ATTEMPTS
    rows = list(P.csv.DictReader(attempts_path.open(encoding="utf-8"), delimiter="\t"))
    assert len(rows) == 1, rows
    assert rows[0]["uid"] == "B0001"
    assert rows[0]["pages"] == "1"
    assert rows[0]["outcome"] == "failed"
    assert rows[0]["rc"] == "1"
    assert P.mineru_pages_today(harness.ctx) == 1

    # 失败已经吃掉当日额度：下一本在投递前就被封顶，不再产生新 attempt。
    monkeypatch.setattr(P, "MINERU_DAY_PAGES", 1)
    merged = str(harness.root / "work" / "B0001" / "merged")
    result = {}
    buf = io.StringIO()
    out = P.mineru_rescue(
        harness.ctx,
        harness.row,
        merged,
        {"per_page": []},
        harness.state["suspects"],
        buf,
        result,
    )
    assert out is None
    assert "封顶" in result["mineru_skip_reason"], result
    assert P.mineru_pages_today(harness.ctx) == 1
    assert len(attempts_path.read_text(encoding="utf-8").splitlines()) == 2


def test_unsubmitted_suspect_pages_block_landing(harness, monkeypatch):
    monkeypatch.setenv("FAKE_MINERU_MODE", "ok")
    monkeypatch.setattr(P, "MINERU_MAX_PAGES", 1)
    harness.state["suspects"] = [
        {"page": 1, "chars": 0, "ink": 0.2},
        {"page": 2, "chars": 0, "ink": 0.2},
    ]
    res = P.run_one(harness.row, harness.ctx)
    _assert_stayed_in_review(res, harness, [1, 2], unsubmitted=[2])
    assert res["skipped_unreadable_pages"] == [1], "skipped 只含实际提交的页"
    assert res["mineru_unsubmitted_pages"] == [2]
    assert not (harness.root / "books" / "B0001" / "md").exists()
