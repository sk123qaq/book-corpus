"""PDF 队列的 partial / content_complete 只认 manifest 的 skipped_unreadable_pages。

不读 blank_pages / short_pages；缺 manifest、缺字段、字段不是列表都算未知，
不能落到任何一边。
"""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def ops_server():
    path = Path(__file__).resolve().parents[1] / "apps" / "ops" / "server.py"
    spec = importlib.util.spec_from_file_location("ops_server_manifest_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _manifest(root, uid, skipped=None, *, field=True):
    md = Path(root) / "books" / uid / "md"
    md.mkdir(parents=True, exist_ok=True)
    row = {"pages": 10, "accepted": "人工核对"}
    if field:
        row["skipped_unreadable_pages"] = skipped
    (md / ".manifest.json").write_text(json.dumps(row), encoding="utf-8")


def test_pdf_manifest_counts_only_green_books(tmp_path, ops_server):
    _manifest(tmp_path, "B0001", [2])
    _manifest(tmp_path, "B0002", [])
    _manifest(tmp_path, "B0003", None)
    _manifest(tmp_path, "B0004", [3])
    _manifest(tmp_path, "B0005", "1")
    _manifest(tmp_path, "B0006", field=False)
    man = Path(tmp_path) / "books" / "B0007" / "md"
    man.mkdir(parents=True)
    (man / ".manifest.json").write_text(
        json.dumps({"method": "paddle-ocr", "accepted": "人工核对", "skipped_unreadable_pages": [4]}),
        encoding="utf-8",
    )
    man = Path(tmp_path) / "books" / "B0008" / "md"
    man.mkdir(parents=True)
    (man / ".manifest.json").write_text(
        json.dumps({"method": "paddle-ocr", "accepted": "", "skipped_unreadable_pages": [5]}),
        encoding="utf-8",
    )
    man = Path(tmp_path) / "books" / "B0009" / "md"
    man.mkdir(parents=True)
    (man / ".manifest.json").write_text(
        json.dumps(
            {
                "method": "paddle-ocr",
                "accepted": "MinerU 也读不出，按跳过受理：第 5 页",
                "skipped_unreadable_pages": [5],
            }
        ),
        encoding="utf-8",
    )

    counts = ops_server.pdf_manifest_counts(
        str(tmp_path),
        [
            ("B0001", "accepted"),
            ("B0002", "replaced"),
            ("B0003", "accepted"),
            ("B0004", "staged-review"),
            ("B0005", "landed"),
            ("B0006", "landed"),
            ("B0007", "skipped-has-md"),
            ("B0008", "skipped-has-md"),
            ("B0009", "accepted"),
        ],
    )
    assert counts == {"partial": 1, "content_complete": 3}
    assert ops_server.pdf_manifest_state(str(tmp_path), "B0003") is None
    assert ops_server.pdf_manifest_state(str(tmp_path), "B0006") is None
