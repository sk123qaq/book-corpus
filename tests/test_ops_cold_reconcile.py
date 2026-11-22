"""冷宫页核对与校正：GET /api/cold 三态核验、POST /api/cold/reconcile 回写台账。"""

import csv
import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from test_ops_fixes import _http_server, _make_app, server


def _tsv_lines(header, rows):
    out = ["\t".join(header)]
    for row in rows:
        out.append("\t".join(str(row.get(col, "")) for col in header))
    return "\n".join(out) + "\n"


def _write_tsv(root: Path, batch: str, header, rows) -> Path:
    folder = root / "工作区" / "批次" / batch
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "retired.tsv"
    path.write_text(_tsv_lines(header, rows), encoding="utf-8")
    return path


def _row(uid, title, cold_path, note="", **extra):
    row = {
        "retired_at": "2026-11-22T17:13:35",
        "uid": uid,
        "title": title,
        "old_path": f"books/{uid}",
        "cold_path": cold_path,
        "sources": "1",
        "source_sha256": "abc",
        "format": "pdf",
        "bytes": "1024",
        "md_chapters": "0",
        "md_bytes": "0",
        "reason": "移出流程",
        "shared_with": "0",
        "series_docs": "",
        "note": note,
    }
    row.update(extra)
    return row


def _read_rows(path: Path):
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        return reader.fieldnames, [{k: (v or "") for k, v in row.items() if k is not None} for row in reader]


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(base, path, payload=None, headers=None):
    data = json.dumps(payload or {}).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode("utf-8"))


@pytest.fixture
def coldapp(workspace):
    app = _make_app(workspace)
    with _http_server(app) as base:
        yield workspace, base


def test_view_three_states(coldapp):
    root, base = coldapp
    (root / "books" / "_冷宫" / "在库样本" / "source").mkdir(parents=True)
    (root / "books" / "_冷宫" / "没人登记书").mkdir(parents=True)
    _write_tsv(
        root,
        "移出-2026-11-22",
        server.RETIRED_COLUMNS,
        [
            _row("B9001", "在库样本", "books/_冷宫/在库样本/source"),
            _row("B9002", "缺失样本", "books/_冷宫/缺失样本/source"),
        ],
    )
    cold = _get(base, "/api/cold")
    assert cold["ok"] is True
    assert cold["cold_dir"] == "books/_冷宫"
    assert cold["cold_dir_exists"] is True
    states = {item["title"]: item["state"] for item in cold["items"]}
    assert states == {"在库样本": "in_cold", "缺失样本": "missing", "没人登记书": "unregistered"}
    assert cold["counts"] == {"in_cold": 1, "missing": 1, "unregistered": 1, "backfilled": 0, "total": 3}
    assert all(item["checked_at"] for item in cold["items"])


def test_old_header_without_state_columns(coldapp):
    root, base = coldapp
    (root / "books" / "_冷宫" / "在库样本").mkdir(parents=True)
    _write_tsv(
        root,
        "移出-2026-11-20",
        server.RETIRED_COLUMNS,
        [_row("B9001", "在库样本", "books/_冷宫/在库样本")],
    )
    cold = _get(base, "/api/cold")
    assert cold["counts"]["in_cold"] == 1
    assert cold["counts"]["missing"] == 0
    assert cold["items"][0]["last_checked"] == ""


def test_reconcile_writes_states_backfills_and_is_idempotent(coldapp):
    root, base = coldapp
    (root / "books" / "_冷宫" / "在库样本" / "source").mkdir(parents=True)
    (root / "books" / "_冷宫" / "B9123 冷宫旧书").mkdir(parents=True)
    tsv = _write_tsv(
        root,
        "移出-2026-11-22",
        server.RETIRED_COLUMNS,
        [
            _row("B9001", "在库样本", "books/_冷宫/在库样本/source"),
            _row("B9002", "缺失样本", "books/_冷宫/缺失样本/source"),
        ],
    )

    code, body = _post(base, "/api/cold/reconcile")
    assert code == 200, body
    assert body["ok"] is True
    assert body["changed"] > 0
    assert body["reconcile"]["appended"] == 1
    assert body["reconcile"]["marked_missing"] == 1
    assert body["counts"] == {"in_cold": 2, "missing": 1, "unregistered": 0, "backfilled": 1, "total": 3}
    assert len(body["backups"]) == 1
    assert body["backups"][0].startswith("retired.tsv.bak-")

    header, rows = _read_rows(tsv)
    assert header[: len(server.RETIRED_COLUMNS)] == list(server.RETIRED_COLUMNS)
    assert header[len(server.RETIRED_COLUMNS) :] == ["state", "last_checked"]
    by_uid = {row["uid"]: row for row in rows if row["uid"]}
    assert by_uid["B9001"]["state"] == "in_cold"
    assert by_uid["B9001"]["last_checked"] == ""
    assert by_uid["B9002"]["state"] == "missing"
    assert by_uid["B9002"]["last_checked"]
    # 缺失行保留、不删（审计要留住）
    assert "B9002" in by_uid
    backfill = [row for row in rows if row["reason"] == server.COLD_BACKFILL_REASON]
    assert len(backfill) == 1
    assert backfill[0]["uid"] == "B9123"
    assert backfill[0]["title"] == "冷宫旧书"
    assert backfill[0]["note"] == server.COLD_BACKFILL_NOTE
    assert backfill[0]["state"] == "in_cold"
    assert backfill[0]["cold_path"] == "books/_冷宫/B9123 冷宫旧书"
    assert (tsv.parent / body["backups"][0]).is_file()

    before = tsv.read_text(encoding="utf-8")
    code2, body2 = _post(base, "/api/cold/reconcile")
    assert code2 == 200
    assert body2["changed"] == 0
    assert body2["backups"] == []
    assert tsv.read_text(encoding="utf-8") == before
    assert body2["reconcile"] == {"checked": 3, "appended": 0, "marked_missing": 0}


def test_backfill_without_uid_in_name(coldapp):
    root, base = coldapp
    (root / "books" / "_冷宫" / "无编号旧书").mkdir(parents=True)
    _write_tsv(root, "移出-2026-11-22", server.RETIRED_COLUMNS, [])
    code, body = _post(base, "/api/cold/reconcile")
    assert code == 200
    assert body["reconcile"]["appended"] == 1
    uid = [item["uid"] for item in body["items"] if item["state"] == "in_cold"]
    assert uid == [""]
    assert body["counts"]["backfilled"] == 1


def test_backfill_creates_batch_dir_when_missing(coldapp):
    root, base = coldapp
    (root / "books" / "_冷宫" / "B9500 补登书").mkdir(parents=True)
    assert not any((root / "工作区" / "批次").glob("移出-*"))
    code, body = _post(base, "/api/cold/reconcile")
    assert code == 200, body
    assert body["cold_dir_exists"] is True
    assert body["batches_exists"] is True
    batches = sorted((root / "工作区" / "批次").iterdir())
    assert len(batches) == 1
    assert batches[0].name.startswith("移出-")
    header, rows = _read_rows(batches[0] / "retired.tsv")
    assert header == [*server.RETIRED_COLUMNS, "state", "last_checked"]
    assert len(rows) == 1
    assert rows[0]["uid"] == "B9500"
    assert rows[0]["state"] == "in_cold"


def test_failed_rows_are_skipped_and_untouched(coldapp):
    root, base = coldapp
    _write_tsv(
        root,
        "移出-2026-11-22",
        server.RETIRED_COLUMNS,
        [_row("B9003", "失败样本", "books/_冷宫/失败样本", note="failed at 2026-11-22T17:00:00")],
    )
    cold = _get(base, "/api/cold")
    assert cold["items"] == []
    code, body = _post(base, "/api/cold/reconcile")
    assert code == 200
    assert body["changed"] == 0
    _header, rows = _read_rows(root / "工作区" / "批次" / "移出-2026-11-22" / "retired.tsv")
    assert rows[0]["note"].startswith("failed at")
    assert rows[0]["state"] == ""


def test_missing_cold_dir_marks_everything_missing(coldapp):
    root, base = coldapp
    shutil.rmtree(root / "books" / "_冷宫")
    _write_tsv(
        root,
        "移出-2026-11-22",
        server.RETIRED_COLUMNS,
        [_row("B9001", "样本", "books/_冷宫/样本")],
    )
    cold = _get(base, "/api/cold")
    assert cold["cold_dir_exists"] is False
    assert cold["counts"]["missing"] == 1
    assert cold["counts"]["unregistered"] == 0


def test_reconcile_keeps_only_three_backups(coldapp):
    root, base = coldapp
    tsv = _write_tsv(
        root,
        "移出-2026-11-22",
        server.RETIRED_COLUMNS,
        [_row("B9001", "样本", "books/_冷宫/样本")],
    )
    for stamp in ("20200101-000000", "20200102-000000", "20200103-000000", "20200104-000000"):
        (tsv.parent / ("retired.tsv.bak-" + stamp)).write_text("old\n", encoding="utf-8")
    code, body = _post(base, "/api/cold/reconcile")
    assert code == 200
    assert body["changed"] == 1
    backups = sorted(p.name for p in tsv.parent.iterdir() if ".bak-" in p.name)
    assert len(backups) == 3
    assert "retired.tsv.bak-20200101-000000" not in backups
    assert body["backups"][0] in backups


def test_reconcile_rejects_foreign_origin(coldapp):
    root, base = coldapp
    _write_tsv(root, "移出-2026-11-22", server.RETIRED_COLUMNS, [])
    code, body = _post(base, "/api/cold/reconcile", headers={"Origin": "http://evil.example"})
    assert code == 403
    assert body["ok"] is False
