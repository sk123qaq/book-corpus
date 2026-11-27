"""ops 服务端与 ops_status 修复项的回归单测。"""

import csv
import fcntl
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

from conftest import run_cli, write_book, write_ledger

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps" / "ops"))

_spec = importlib.util.spec_from_file_location("ops_server_fixes_test", REPO / "apps" / "ops" / "server.py")
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)

from bookcorpus.scripts import ops_status


def _paths(root: Path) -> ops_status.Paths:
    return ops_status.Paths(str(root))


def _make_app(root: Path) -> server.App:
    env = server.Env(
        root=str(root),
        scripts=str(REPO / "src" / "bookcorpus" / "scripts"),
    )
    app = server.App.__new__(server.App)
    app.env = env
    env.prepare()
    app.store = server.Store(env.ops_db)
    app.runner = server.Runner(env, app.store)
    app.status_cache = (0.0, None)
    app.status_lock = threading.Lock()
    app.status_gen = 0
    app.pdf_probe_down_until = 0.0
    app.pdf_page_cache = {}
    app.pdf_page_lock = threading.Lock()
    return app


@contextmanager
def _http_server(app: server.App):
    class TestHandler(server.Handler):
        def log_message(self, fmt, *args):
            return

    TestHandler.app = app
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _http_json(url: str, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode("utf-8"))


def _insert_job(store, job_id, kind, state, *, title="t", summary=None, batch_dir=None):
    store.create(
        {
            "id": job_id,
            "kind": kind,
            "title": title,
            "argv": "[]",
            "state": state,
            "created": "2026-11-19T12:00:00+08:00",
            "summary": summary,
            "batch_dir": batch_dir,
        }
    )


def test_inbox_filters_readme_and_unsupported(workspace):
    inbox = workspace / "books" / "_收件箱"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "README.md").write_text("# inbox\n", encoding="utf-8")
    (inbox / "readme.txt").write_text("note\n", encoding="utf-8")
    (inbox / "legacy.doc").write_bytes(b"doc")
    (inbox / "scan.djvu").write_bytes(b"djvu")
    (inbox / "新书A.epub").write_bytes(b"epub")
    (inbox / "新书B.pdf").write_bytes(b"%PDF")

    p = _paths(workspace)
    state = {"problems": []}
    ops_status.inbox(p, state)
    got = state["inbox"]
    assert got["files"] == 2
    assert got["ignored_count"] == 2
    assert {item["name"] for item in got["ignored"]} == {"legacy.doc", "scan.djvu"}

    app = _make_app(workspace)
    assert [it["name"] for it in app.inbox()] == ["新书A.epub", "新书B.pdf"]
    assert {it["name"] for it in app.inbox_ignored()} == {"legacy.doc", "scan.djvu"}


def test_env_child_env_isolates_books_root(tmp_path):
    env = server.Env(
        root=str(tmp_path),
        scripts=str(REPO / "src" / "bookcorpus" / "scripts"),
    )
    child = env.child_env()
    assert child["BOOKS_ROOT"] == str(tmp_path.resolve())


def test_empty_scan_succeeds_and_empty_apply_rejected(workspace):
    app = _make_app(workspace)
    job_id = "20261119-000001-aaaa"
    job_dir = Path(app.env.job_dir(job_id))
    job_dir.mkdir(parents=True, exist_ok=True)
    plan_file = job_dir / "plan.json"
    plan_file.write_text("[]", encoding="utf-8")
    _insert_job(app.store, job_id, "import.scan", "succeeded", title="扫收件箱")

    ctx = server.Ctx(app.env, {"id": job_id}, [], [("scan", 0)])
    state, summary = server.gate_import_scan(ctx)
    assert state == "succeeded"
    assert "没有可入库" in summary

    with _http_server(app) as base:
        code, latest = _http_json(base + "/api/import/latest")
        assert code == 200
        assert latest["plan"] is None
        assert latest["next_uid"] == "B0004"

        code2, body2 = _http_json(
            base + "/api/import/apply",
            {"plan_path": app.env.rel(str(plan_file))},
        )
        assert code2 == 400
        assert body2["ok"] is False


def test_awaiting_review_converges_after_downstream_finish(workspace, monkeypatch):
    write_book(workspace, "B0004", "文学", "待分诊PDF", None, fmt="pdf")
    write_ledger(workspace, [("B0004", "文学", "待分诊PDF", "")])
    r = run_cli("--root", workspace, "library")
    assert r.returncode == 0, r.stdout + r.stderr

    app = _make_app(workspace)
    monkeypatch.setattr(server.Handler, "app", app, raising=False)

    # 1) import.scan awaiting_review (whose plan file is gone from inbox) -> import.apply -> succeeded
    scan_dir = Path(app.env.job_dir("job-scan-1"))
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "plan.json").write_text(json.dumps([{"file": "已入库.epub", "action": "add"}]), encoding="utf-8")
    _insert_job(app.store, "job-scan-1", "import.scan", "awaiting_review", title="扫描收件箱", summary="待确认 1 本")
    app.runner._resolve_reviews("job-apply-1", {"kind": "import.apply"}, "succeeded")
    assert app.store.get("job-scan-1")["state"] == "succeeded"

    # 2) split.run awaiting_review in same batch_dir -> split.run succeeded in same batch_dir -> succeeded
    batch_name = "split-ops-20261119-0001"
    batch_rel = "工作区/批次/" + batch_name
    logs_dir = Path(app.env.batches) / batch_name / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "B0001.json").write_text(json.dumps({"uid": "B0001", "status": "landed"}), encoding="utf-8")
    _insert_job(
        app.store,
        "job-split-1",
        "split.run",
        "awaiting_review",
        title="电子书拆章",
        summary="staged 1",
        batch_dir=batch_rel,
    )
    app.runner._resolve_reviews("job-split-2", {"kind": "split.run", "batch_dir": batch_rel}, "succeeded")
    assert app.store.get("job-split-1")["state"] == "succeeded"

    # 3) pdf.triage awaiting_review -> settled -> pdf.confirm resolves it
    ocr_dir = Path(app.env.batches) / "pdf-ocr-ops-2026-11-19"
    ocr_dir.mkdir(parents=True, exist_ok=True)
    with (ocr_dir / "triage_final.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["uid", "route", "reason"])
        w.writerow(["B0004", "OCR", "ok"])
    triage_job_dir = Path(app.env.job_dir("job-triage-1")) / "B0004"
    triage_job_dir.mkdir(parents=True, exist_ok=True)
    (triage_job_dir / "result.json").write_text(json.dumps({"uid": "B0004"}), encoding="utf-8")
    _insert_job(app.store, "job-triage-1", "pdf.triage", "awaiting_review", title="批量分诊 1 本", summary="1 本出建议")
    app.runner._resolve_reviews("job-confirm-1", {"kind": "pdf.confirm"}, "succeeded")
    assert app.store.get("job-triage-1")["state"] == "succeeded"


def test_pdf_books_index_route_overrides_skip_list_and_sets_has_md(workspace):
    write_book(workspace, "B0004", "文学", "图册改转", None, fmt="pdf")
    write_book(workspace, "B0005", "文学", "换源后PDF", ["第一段"], fmt="pdf")
    (workspace / "books" / "B0005" / "md" / ".manifest.json").write_text(
        json.dumps({"uid": "B0005", "source_sha256": "old_sha", "chapters": 1}),
        encoding="utf-8",
    )
    write_ledger(
        workspace,
        [
            ("B0004", "文学", "图册改转", ""),
            ("B0005", "文学", "换源后PDF", ""),
        ],
    )
    r = run_cli("--root", workspace, "library")
    assert r.returncode == 0, r.stdout + r.stderr

    p = _paths(workspace)
    census_dir = Path(p.batches) / "pdf-普查-2026-10-19"
    census_dir.mkdir(parents=True, exist_ok=True)
    with (census_dir / "skip_list.tsv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["uid", "title", "verdict", "why"])
        w.writerow(["B0004", "图册改转", "不转-记图册", "旧判定"])

    ocr_dir = Path(p.batches) / "pdf-ocr-ops-2026-11-19"
    ocr_dir.mkdir(parents=True, exist_ok=True)
    with (ocr_dir / "triage_final.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["uid", "route", "reason"])
        w.writerow(["B0004", "日文暂缓", "手动改日文"])
        w.writerow(["B0005", "OCR", "已转过"])

    app = _make_app(workspace)
    idx = app.pdf_books_index()
    assert idx["B0004"]["disposition"] == "ja"
    assert idx["B0004"]["has_md"] is False
    assert idx["B0005"]["has_md"] is True


def test_file_allow_and_pdf_triage_info(workspace):
    app = _make_app(workspace)
    job_id = "20261119-000002-bbbb"
    ok_thumb = f"工作区/运维/jobs/{job_id}/B0004/thumbs/01.png"
    ok_res = f"工作区/运维/jobs/{job_id}/B0004/result.json"
    assert any(pat.match(ok_thumb) for pat in server.FILE_ALLOW)
    assert any(pat.match(ok_res) for pat in server.FILE_ALLOW)
    assert not any(pat.match(f"工作区/运维/jobs/{job_id}/../secrets.txt") for pat in server.FILE_ALLOW)

    tdir = Path(app.env.job_dir(job_id)) / "B0004"
    (tdir / "thumbs").mkdir(parents=True, exist_ok=True)
    (tdir / "thumbs" / "01.png").write_bytes(b"png")
    (tdir / "result.json").write_text(
        json.dumps(
            {
                "uid": "B0004",
                "metrics": {"pages": 120, "text_page_share": 0.1},
                "thumbs": ["thumbs/01.png"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _insert_job(app.store, job_id, "pdf.triage", "awaiting_review", title="批量分诊")

    with _http_server(app) as base:
        code, info = _http_json(base + "/api/pdf/triage-info?uid=B0004")
        assert code == 200
        assert info["job_id"] == job_id
        assert info["result"]["metrics"]["pages"] == 120
        assert info["thumbs"] == [ok_thumb]

        with urllib.request.urlopen(base + "/api/file?path=" + urllib.parse.quote(ok_thumb), timeout=5) as resp:
            assert resp.status == 200
            assert resp.read() == b"png"


def test_batch_accept_and_pdf_run_auto_replace(workspace):
    write_book(workspace, "B0004", "文学", "已有MD重转", ["段落"], fmt="pdf")
    (workspace / "books" / "B0004" / "md" / ".manifest.json").write_text(
        json.dumps({"uid": "B0004", "source_sha256": "old_sha", "chapters": 1}),
        encoding="utf-8",
    )
    write_ledger(workspace, [("B0004", "文学", "已有MD重转", "")])
    r = run_cli("--root", workspace, "library")
    assert r.returncode == 0, r.stdout + r.stderr

    batches = Path(_paths(workspace).batches)
    split_batch = "split-ops-20261119-0001"
    (batches / split_batch).mkdir(parents=True, exist_ok=True)

    today_ocr = server.TRIAGE_BATCH_PREFIX + time.strftime("%Y-%m-%d")
    ocr_dir = batches / today_ocr
    ocr_dir.mkdir(parents=True, exist_ok=True)
    with (ocr_dir / "triage_final.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["uid", "route", "reason"])
        w.writerow(["B0004", "OCR", "need"])

    app = _make_app(workspace)
    queued = []
    app.runner.submit = lambda kind, title, cmds, **kwargs: (
        queued.append(cmds("job-test-1") if callable(cmds) else cmds),
        "job-test-1",
    )[1]

    with _http_server(app) as base:
        code, res = _http_json(
            base + "/api/split/accept",
            {"uid": "B0004", "batch": split_batch, "reason": "序言无需收录"},
        )
        assert code == 200
        assert res["ok"] is True
        ov = json.loads((batches / split_batch / "overrides" / "B0004.json").read_text(encoding="utf-8"))
        assert ov == {"accept": "序言无需收录"}

        code2, res2 = _http_json(
            base + "/api/pdf/accept",
            {"uid": "B0004", "reason": "扫图版吻合"},
        )
        assert code2 == 200
        assert res2["ok"] is True
        ov_pdf = json.loads((ocr_dir / "overrides" / "B0004.json").read_text(encoding="utf-8"))
        assert ov_pdf == {"accept": "扫图版吻合"}

        # api_pdf_run auto-enables --replace when has_md=True
        code3, res3 = _http_json(base + "/api/pdf/run", {"uids": ["B0004"], "workers": 1, "limit": 1})
        assert code3 == 200
        assert res3["ok"] is True
        ocr_step = next(s for s in queued[0] if s["name"] == "pdf ocr")
        assert "--replace" in ocr_step["argv"]


def test_split_routes_reject_dot_batch_names(workspace):
    """批次名是路径：点段必须在排队或读 logs 前就被拒。"""
    write_book(workspace, "B0004", "文学", "已有MD重转", ["段落"], fmt="pdf")
    write_ledger(workspace, [("B0004", "文学", "已有MD重转", "")])
    r = run_cli("--root", workspace, "library")
    assert r.returncode == 0, r.stdout + r.stderr

    batches = Path(_paths(workspace).batches)
    valid_batch = "split-ops-20261119-0001"
    (batches / valid_batch).mkdir(parents=True, exist_ok=True)

    app = _make_app(workspace)
    queued = []
    app.runner.submit = lambda kind, title, cmds, **kwargs: (
        queued.append(cmds("job-test-1") if callable(cmds) else cmds),
        "job-test-1",
    )[1]

    with _http_server(app) as base:
        for batch in (".", ".."):
            code, res = _http_json(
                base + "/api/split/run",
                {"uids": ["B0004"], "only": "B0004", "batch": batch},
            )
            assert code == 400, (batch, code, res)
        for route in ("/api/split/recheck", "/api/split/inspect"):
            code, res = _http_json(base + route, {"uid": "B0004", "batch": ".."})
            assert code == 400, (route, code, res)
        code, res = _http_json(
            base + "/api/split/accept",
            {"uid": "B0004", "batch": "..", "reason": "x"},
        )
        assert code == 400
        assert queued == []
        work = Path(workspace) / "工作区"
        assert not (work / "logs").exists()
        assert not (work / "out").exists()
        assert not (work / "tmp").exists()
        assert not (work / "worklist.tsv").exists()

        # 正常批次照旧受理，且 argv 里原样带着批次名
        code, res = _http_json(
            base + "/api/split/run",
            {"uids": ["B0004"], "only": "B0004", "batch": valid_batch, "workers": 1},
        )
        assert code == 200, (code, res)
        assert res["batch"] == valid_batch
        assert valid_batch in queued[0][0]["argv"]


def test_ledger_stale_and_pending_groups(workspace):
    write_book(workspace, "B0004", "文学", "待转docx", None, fmt="docx")
    write_book(workspace, "B0005", "文学", "待转pdf", None, fmt="pdf")
    write_book(workspace, "B0006", "文学", "待转djvu", None, fmt="djvu")
    write_ledger(
        workspace,
        [
            ("B0004", "文学", "待转docx", ""),
            ("B0005", "文学", "待转pdf", ""),
            ("B0006", "文学", "待转djvu", ""),
        ],
    )
    r = run_cli("--root", workspace, "library")
    assert r.returncode == 0, r.stdout + r.stderr

    p = _paths(workspace)
    st = {"problems": []}
    ops_status.library(p, st)
    assert st["pending_groups"]["doc"] == 2  # B0003 (fixture txt) + B0004 (docx)
    assert st["pending_groups"]["pdf"] == 1
    assert st["pending_groups"]["other"] == 1

    # Bump ledger.db mtime beyond library.db + 2s
    now = time.time() + 10
    ledger_db = Path(workspace) / "tables" / "ledger.db"
    os.utime(ledger_db, (now, now))
    ops_status.ledger(p, st)
    assert st["ledger"]["stale"] is True


def test_stage2_ledger_auto_sync_cold_settings_and_reconcile(workspace):
    r = run_cli("--root", workspace, "library", "--emit-json")
    assert r.returncode == 0, r.stdout + r.stderr

    app = _make_app(workspace)
    # 1) 人账编辑保存后自动同步：更新 B0001 书名 -> book.json 立即同步，ledger.stale == False
    with _http_server(app) as base:
        code, res = _http_json(
            base + "/api/ledger/row",
            {"row": {"uid": "B0001", "正式书名": "甲书自动同步版"}},
        )
        assert code == 200
        assert res["ok"] is True
        assert res["synced"] is True
        b1 = json.loads((workspace / "books" / "B0001" / "book.json").read_text(encoding="utf-8"))
        assert b1["title"] == "甲书自动同步版"

        # 2) 冷宫只读接口严格隔离，不混入活库
        cold_extra = workspace / "books" / "_冷宫" / "历史冷宫书"
        cold_extra.mkdir(parents=True, exist_ok=True)
        ret_dir = workspace / "工作区" / "批次" / "移出-2026-11-19"
        ret_dir.mkdir(parents=True, exist_ok=True)
        (ret_dir / "retired.tsv").write_text(
            "retired_at\tuid\ttitle\told_path\tcold_path\tsources\tsource_sha256\tformat\tbytes\tmd_chapters\tmd_bytes\treason\tshared_with\tseries_docs\tnote\n"
            "2026-11-19T10:00:00\tB9001\t退库样本\tbooks/B9001\tbooks/_冷宫/退库样本/source\t1\tabc\tepub\t100\t1\t50\t测试\t0\t\t\n",
            encoding="utf-8",
        )
        code_c, cold = _http_json(base + "/api/cold")
        assert code_c == 200
        assert cold["ok"] is True
        assert len(cold["items"]) == 2
        assert [i["uid"] for i in cold["items"] if i["state"] == "missing"] == ["B9001"]
        assert [i["title"] for i in cold["items"] if i["state"] == "unregistered"] == ["历史冷宫书"]
        assert cold["counts"] == {"in_cold": 0, "missing": 1, "unregistered": 1, "backfilled": 0, "total": 2}

        # 3) settings auto_embed 读写
        code_s, s0 = _http_json(base + "/api/settings")
        assert code_s == 200
        assert s0["auto_embed"] is True
        code_s2, s1 = _http_json(base + "/api/settings", {"auto_embed": False})
        assert code_s2 == 200
        assert s1["auto_embed"] is False

        # 4) services 探测
        code_sv, sv = _http_json(base + "/api/services")
        assert code_sv == 200
        assert {x["key"] for x in sv["services"]} == {"home", "search", "reading", "daily"}

        # 5) reconcile / advanced / manual rescan 路由与参数校验
        # 向量库未初始化（chunks=0）时，日常 reconcile 自动跳过 embed sync，防止误触发全库建库
        queued = []
        app.runner.submit = lambda kind, title, cmds, **kw: (
            queued.append((kind, [c["name"] for c in (cmds("j1") if callable(cmds) else cmds)])),
            "job-s2",
        )[1]
        c_rec, b_rec = _http_json(base + "/api/sync/reconcile", {"steps": ["meta", "fts", "embed", "audit"]})
        assert c_rec == 200
        assert b_rec["ok"] is True
        assert queued[-1] == ("sync.reconcile", ["db", "awards", "fts sync", "audit"])

        # 显式 force_embed 或已有向量基线时才包含 embed sync
        c_rec_f, _ = _http_json(
            base + "/api/sync/reconcile", {"steps": ["meta", "fts", "embed", "audit"], "force_embed": True}
        )
        assert c_rec_f == 200
        assert queued[-1] == ("sync.reconcile", ["db", "awards", "fts sync", "embed sync", "audit"])

        c_init, b_init = _http_json(base + "/api/maintain/advanced", {"action": "embed_init"})
        assert c_init == 200
        assert b_init["action"] == "embed_init"
        assert queued[-1] == ("sync.embed", ["embed init"])

        c_adv, b_adv = _http_json(base + "/api/maintain/advanced", {"action": "embed_compact"})
        assert c_adv == 200
        assert b_adv["action"] == "embed_compact"
        assert queued[-1] == ("embed.compact", ["embed compact"])

        c_bad, _ = _http_json(base + "/api/maintain/advanced", {"action": "invalid_op"})
        assert c_bad == 400

        c_man, b_man = _http_json(base + "/api/manual/rescan", {})
        assert c_man == 200
        assert b_man["ok"] is True
        assert queued[-1][0] == "manual.rescan"


def test_stage2_chain_auto_sync_coalesces_fts_jobs(workspace):
    r = run_cli("--root", workspace, "library")
    assert r.returncode == 0, r.stdout + r.stderr

    app = _make_app(workspace)
    # 建初始 index.db 含 B0001/B0002；再写新书 B0004 md 制造 FTS 差集
    r_idx = run_cli("--root", workspace, "run", "build_para_index", "--rebuild", "--root", str(workspace))
    assert r_idx.returncode == 0, r_idx.stdout + r_idx.stderr
    write_book(workspace, "B0004", "科技", "自动同步新书", ["自动同步触发词法索引的测试段落。"])

    # 第一次落地触发 _chain_auto_sync -> 排队 1 个 sync.fts
    j1_dir = Path(app.env.job_dir("job-doc-1"))
    j1_dir.mkdir(parents=True, exist_ok=True)
    app.runner._chain_auto_sync("job-doc-1", "docs.run")
    fts_jobs_1 = [j for j in app.store.queued() if j["kind"] == "sync.fts"]
    assert len(fts_jobs_1) == 1

    # 第二次连续落地（前一个 sync.fts 还在 queued）-> 合并去重，不创建第二个 sync.fts
    j2_dir = Path(app.env.job_dir("job-doc-2"))
    j2_dir.mkdir(parents=True, exist_ok=True)
    app.runner._chain_auto_sync("job-doc-2", "split.run")
    fts_jobs_2 = [j for j in app.store.queued() if j["kind"] == "sync.fts"]
    assert len(fts_jobs_2) == 1

    # 验证 ops_status.collect 包含精确 sync_delta 且 cold_count 独立
    p = _paths(workspace)
    st = ops_status.collect(p, deep=False)
    assert st["sync_delta"]["fts"]["added"] == ["B0004"]
    assert st["sync_delta"]["has_drift"] is True
    assert st["sync_delta"]["embed"]["initialized"] is False
    assert st["sync_delta"]["cold_count"] == 0

    # 防线验证：即使假装配好了 llama-server + GGUF 模型，只要 emb.db 未首次构建（chunks=0），
    # _chain_auto_sync 绝不拉起全库 sync.embed，且 FTS 同步后 has_drift 为 False
    emb_tools = workspace / "工作区" / "中间态" / "embed"
    srv_dir = emb_tools / "llama-stub"
    mod_dir = emb_tools / "models"
    srv_dir.mkdir(parents=True, exist_ok=True)
    mod_dir.mkdir(parents=True, exist_ok=True)
    srv_bin = srv_dir / "llama-server"
    srv_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    srv_bin.chmod(0o755)
    (mod_dir / "Qwen3-Embedding-0.6B-Q8_0.gguf").write_bytes(b"gguf")

    j3_dir = Path(app.env.job_dir("job-doc-3"))
    j3_dir.mkdir(parents=True, exist_ok=True)
    app.runner._chain_auto_sync("job-doc-3", "docs.run")
    assert [j for j in app.store.queued() if j["kind"] == "sync.embed"] == []

    # 同步 FTS 后：虽然 engine_ready=True 且 emb 未建，但 has_drift 必须为 False
    r_sync = run_cli("--root", workspace, "run", "build_para_index", "--sync", "--root", str(workspace))
    assert r_sync.returncode == 0, r_sync.stdout + r_sync.stderr
    st_clean = ops_status.collect(p, deep=False)
    assert st_clean["sync_delta"]["embed"]["engine_ready"] is True
    assert st_clean["sync_delta"]["embed"]["initialized"] is False
    assert st_clean["sync_delta"]["has_drift"] is False


def test_awaiting_review_narrowed_gates_and_reconcile(workspace):
    write_book(workspace, "B0004", "文学", "待分诊PDF", None, fmt="pdf")
    write_ledger(workspace, [("B0004", "文学", "待分诊PDF", "")])
    r = run_cli("--root", workspace, "library")
    assert r.returncode == 0, r.stdout + r.stderr

    app = _make_app(workspace)

    # 1) gate_import_scan returns succeeded & api_import_latest still loads it
    inbox_file = workspace / "books" / "_收件箱" / "新书测试.epub"
    inbox_file.parent.mkdir(parents=True, exist_ok=True)
    inbox_file.write_bytes(b"epub")
    scan_dir = Path(app.env.job_dir("job-scan-succ"))
    scan_dir.mkdir(parents=True, exist_ok=True)
    plan = [{"plan_version": 2, "file": "新书测试.epub", "action": "add", "subjects": ["文学"], "title": "新书测试"}]
    (scan_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    st, msg = server.gate_import_scan(server.Ctx(app.env, {"id": "job-scan-succ"}, [], [("scan", 0)]))
    assert st == "succeeded"
    assert "add 1" in msg
    _insert_job(app.store, "job-scan-succ", "import.scan", "succeeded", title="扫收件箱", summary=msg)
    with _http_server(app) as base:
        code, latest = _http_json(base + "/api/import/latest")
        assert code == 200
        assert latest["job_id"] == "job-scan-succ"
        assert len(latest["plan"]) == 1

    # 2) gate_triage & gate_triage_batch return succeeded
    tr_dir = Path(app.env.job_dir("job-tr-single"))
    tr_dir.mkdir(parents=True, exist_ok=True)
    (tr_dir / "result.json").write_text(
        json.dumps({"uid": "B0004", "model": {"route": "OCR", "confidence": 0.9}, "thumbs": ["a.png"]}),
        encoding="utf-8",
    )
    st_t, _ = server.gate_triage(server.Ctx(app.env, {"id": "job-tr-single"}, [], [("triage", 0)]))
    assert st_t == "succeeded"

    trb_dir = Path(app.env.job_dir("job-tr-batch"))
    (trb_dir / "B0004").mkdir(parents=True, exist_ok=True)
    (trb_dir / "B0004" / "result.json").write_text(
        json.dumps({"uid": "B0004", "model": {"route": "OCR", "confidence": 0.9}, "thumbs": []}),
        encoding="utf-8",
    )
    st_tb, _ = server.gate_triage_batch(server.Ctx(app.env, {"id": "job-tr-batch"}, [], [("triage B0004", 0)]))
    assert st_tb == "succeeded"

    # 3) gate_split: hard fail -> failed; staged-review -> awaiting_review; landed -> succeeded
    sp_fail = Path(app.env.job_dir("job-sp-fail"))
    sp_fail.mkdir(parents=True, exist_ok=True)
    (sp_fail / "report.json").write_text(
        json.dumps({"batch": "b1", "status_counts": {"split-failed": 1}, "pending_review": ["B0004"]}),
        encoding="utf-8",
    )
    assert server.gate_split(server.Ctx(app.env, {"id": "job-sp-fail"}, [], [("split", 0)]))[0] == "failed"

    sp_staged = Path(app.env.job_dir("job-sp-staged"))
    sp_staged.mkdir(parents=True, exist_ok=True)
    (sp_staged / "report.json").write_text(
        json.dumps({"batch": "b2", "status_counts": {"staged-review": 1}, "pending_review": ["B0004"]}),
        encoding="utf-8",
    )
    assert server.gate_split(server.Ctx(app.env, {"id": "job-sp-staged"}, [], [("split", 0)]))[0] == "awaiting_review"

    # 4) gate_pdf: deferred / remaining without hard/staged -> succeeded; staged-review -> awaiting_review
    pdf_def = Path(app.env.job_dir("job-pdf-def"))
    pdf_def.mkdir(parents=True, exist_ok=True)
    (pdf_def / "report.json").write_text(
        json.dumps({"landed": 1, "remaining_books": 1, "remaining_pages": 50, "status_counts": {"deferred": 1}}),
        encoding="utf-8",
    )
    assert server.gate_pdf(server.Ctx(app.env, {"id": "job-pdf-def"}, [], [("pdf ocr", 0)]))[0] == "succeeded"

    pdf_stg = Path(app.env.job_dir("job-pdf-stg"))
    pdf_stg.mkdir(parents=True, exist_ok=True)
    (pdf_stg / "report.json").write_text(
        json.dumps({"landed": 0, "remaining_books": 1, "remaining_pages": 50, "status_counts": {"staged-review": 1}}),
        encoding="utf-8",
    )
    assert server.gate_pdf(server.Ctx(app.env, {"id": "job-pdf-stg"}, [], [("pdf ocr", 0)]))[0] == "awaiting_review"

    # 5) reconcile_stale_reviews cleans legacy rows while keeping real staged-review and unknown-rc jobs
    b_landed = Path(app.env.batches) / "split-ops-20261120-0001" / "logs"
    b_landed.mkdir(parents=True, exist_ok=True)
    (b_landed / "B0001.json").write_text(json.dumps({"uid": "B0001", "status": "landed"}), encoding="utf-8")
    b_staged = Path(app.env.batches) / "split-ops-20261120-0002" / "logs"
    b_staged.mkdir(parents=True, exist_ok=True)
    (b_staged / "B0004.json").write_text(json.dumps({"uid": "B0004", "status": "staged-review"}), encoding="utf-8")

    _insert_job(app.store, "old-scan", "import.scan", "awaiting_review", title="扫收件箱", summary="旧扫描")
    _insert_job(app.store, "old-triage", "pdf.triage", "awaiting_review", title="分诊 B0004", summary="旧分诊")
    _insert_job(
        app.store,
        "old-split-landed",
        "split.run",
        "awaiting_review",
        title="拆章",
        summary="旧拆章",
        batch_dir="工作区/批次/split-ops-20261120-0001",
    )
    _insert_job(
        app.store,
        "keep-split-staged",
        "split.run",
        "awaiting_review",
        title="拆章",
        summary="待复核",
        batch_dir="工作区/批次/split-ops-20261120-0002",
    )
    _insert_job(app.store, "keep-adopted", "maintain", "awaiting_review", title="维护", summary="接管：退出码拿不到")

    app.reconcile_stale_reviews()
    assert app.store.get("old-scan")["state"] == "succeeded"
    assert app.store.get("old-triage")["state"] == "succeeded"
    assert app.store.get("old-split-landed")["state"] == "succeeded"
    assert app.store.get("keep-split-staged")["state"] == "awaiting_review"
    assert app.store.get("keep-adopted")["state"] == "awaiting_review"


def test_job_resolve_endpoint(workspace):
    app = _make_app(workspace)
    _insert_job(app.store, "job-rev-1", "split.run", "awaiting_review", title="拆章", summary="待复核 1 本")
    _insert_job(app.store, "job-rev-2", "split.run", "awaiting_review", title="拆章", summary="待复核 1 本")
    _insert_job(app.store, "job-done-1", "split.run", "succeeded", title="拆章", summary="完成")

    with _http_server(app) as base:
        code, body = _http_json(base + "/api/jobs/job-rev-1/resolve", {"resolution": "succeeded", "note": "人工核对ok"})
        assert code == 200
        assert body["job"]["state"] == "succeeded"
        assert "人工核对ok" in body["job"]["summary"]

        code2, body2 = _http_json(base + "/api/jobs/job-rev-2/resolve", {"resolution": "dismissed", "note": "跳过"})
        assert code2 == 200
        assert body2["job"]["state"] == "cancelled"

        code3, _ = _http_json(base + "/api/jobs/job-done-1/resolve", {"resolution": "succeeded"})
        assert code3 == 409

        code4, _ = _http_json(base + "/api/jobs/no-such-job/resolve", {"resolution": "succeeded"})
        assert code4 == 404


def test_auto_audit_refresh_after_writes_and_swr_cache(workspace):
    (workspace / "books" / "B0001" / "source" / "甲书.epub").write_bytes(b"epub")
    (workspace / "books" / "B0002" / "source" / "乙书.epub").write_bytes(b"epub")
    (workspace / "books" / "B0003" / "source" / "丙书.txt").write_bytes(b"txt")
    r = run_cli("--root", workspace, "library", "--emit-json")
    assert r.returncode == 0, r.stdout + r.stderr
    r_idx = run_cli("--root", workspace, "run", "build_para_index", "--rebuild", "--root", str(workspace))
    assert r_idx.returncode == 0, r_idx.stdout + r_idx.stderr
    r_cat = run_cli("--root", workspace, "catalog")
    assert r_cat.returncode == 0, r_cat.stdout + r_cat.stderr
    r_views = run_cli("--root", workspace, "run", "build_views", "--root", str(workspace))
    assert r_views.returncode == 0, r_views.stdout + r_views.stderr

    app = _make_app(workspace)
    # 1) Ledger update triggers background audit refresh and clears audit.stale
    with _http_server(app) as base:
        code, res = _http_json(base + "/api/ledger/row", {"row": {"uid": "B0001", "正式书名": "甲书体检自动刷新"}})
        assert code == 200
        assert res["synced"] is True
        assert app.wait_audit_refresh(timeout=15.0)
        audit_file = workspace / "工作区" / "运维" / "last-audit.json"
        assert audit_file.is_file()
        cache = json.loads(audit_file.read_text(encoding="utf-8"))
        assert cache["deep"] is True
        assert cache["fail"] == []
        p = _paths(workspace)
        st = ops_status.collect(p, deep=False)
        assert st["audit"]["ran"] is True
        assert st["audit"]["stale"] is False

        # 2) SWR cache window returns cached value immediately while refreshing in background
        code_s, st_live = _http_json(base + "/api/status")
        assert code_s == 200
        assert st_live["ok"] is True
        with app.status_lock:
            at_old, val_old = app.status_cache
            app.status_cache = (time.time() - (server.STATUS_TTL + 5.0), val_old)
            gen_before = app.status_gen
        st_swr = app.status(refresh=False)
        assert st_swr["ok"] is True
        deadline = time.time() + 5.0
        while time.time() < deadline:
            with app.status_lock:
                if app.status_gen > gen_before and not app._swr_running and app.status_cache[0] > at_old:
                    break
            time.sleep(0.02)
        with app.status_lock:
            assert app.status_cache[0] > at_old


def test_diff_embed_loose_scan_and_pruned_catalog_audit(workspace):
    import embed_corpus as ec
    import gen_book_catalog as gbc
    import lib_common as lc

    # 1) diff_embed detects orphan active chunk even if book table marked 'removed'
    emb_dir = workspace / "build" / "检索" / "embeddings"
    store = ec.Store(emb_dir / "emb.db", emb_dir / "vectors.f16", dim=4, model="t")
    store.append_book(
        "B0001",
        "甲书",
        ec.sig_of(ec.chapter_paths(workspace, "B0001")),
        [{"path": "books/B0001/md/001.md", "seq": 0, "line": 1, "chars": 10, "text": "甲块"}],
        ec.pack_f16([[1.0, 0.0, 0.0, 0.0]], 4),
        10,
    )
    store.db.execute(
        "INSERT INTO chunk(id, book, path, seq, line, chars, text, active) "
        "VALUES(99, 'B9999', 'x', 0, 1, 5, 'orphan', 1)"
    )
    store.db.commit()
    store.db.close()

    diff = ec.diff_embed(workspace, db_path=emb_dir / "emb.db", check_network=False)
    assert "B9999" in diff["orphan"]
    assert diff["chunks"] == 2

    # 2) pruned gen_book_catalog.audit still detects orphan source file
    r = run_cli("--root", workspace, "library")
    assert r.returncode == 0, r.stdout + r.stderr
    (workspace / "books" / "B0001" / "source" / "extra_orphan.epub").write_bytes(b"x")
    saved = (lc.ROOT, lc.BOOKS, lc.TABLES)
    lc.ROOT, lc.BOOKS, lc.TABLES = str(workspace), str(workspace / "books"), str(workspace / "tables")
    import sqlite3

    con = sqlite3.connect(f"file:{workspace / 'build' / 'library.db'}?mode=ro", uri=True)
    try:
        probs, _cnts = gbc.audit(con)
    finally:
        con.close()
        lc.ROOT, lc.BOOKS, lc.TABLES = saved
    assert any("extra_orphan.epub" in p for p in probs)


def test_cancel_during_spawn_terminates_process_promptly(workspace, monkeypatch):
    """在 Popen 返回、procs 登记前收到 cancel，子进程必须被立刻终止而不是等跑完。"""
    app = _make_app(workspace)
    slow_script = workspace / "slow_sleep.py"
    slow_script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

    commands = [{"name": "slow", "script": "slow_sleep.py", "argv": [sys.executable, str(slow_script)]}]
    job_id = app.runner.submit("split.inspect", "竞态取消回归", commands)
    job = app.store.get(job_id)

    spawned_pids: list[int] = []
    real_popen = server.subprocess.Popen
    cancel_results: list[bool] = []

    def racing_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        spawned_pids.append(proc.pid)
        cancel_results.append(app.runner.cancel(job_id))
        return proc

    monkeypatch.setattr(server.subprocess, "Popen", racing_popen)
    t0 = time.monotonic()
    with app.runner.cv:
        app.runner.active[job_id] = "split.inspect"
        app.runner.active_read += 1
    app.runner._run(job)
    elapsed = time.monotonic() - t0

    assert cancel_results == [True]
    assert len(spawned_pids) == 1
    assert elapsed < 3.0, f"cancel during spawn stalled for {elapsed:.2f}s"
    done = app.store.get(job_id)
    assert done is not None
    assert done["state"] == "cancelled"
    assert not server.pid_alive(spawned_pids[0])


def test_terminal_state_published_after_writer_release(workspace, monkeypatch):
    """终态写入 DB 时写锁与活跃写槽位必须已释放，避免轮询看到 succeeded 后紧接着发写请求撞 409。"""
    app = _make_app(workspace)
    noop = workspace / "noop_write.py"
    noop.write_text("print('ok')\n", encoding="utf-8")

    commands = [{"name": "noop", "script": "noop_write.py", "argv": [sys.executable, str(noop)]}]
    job_id = app.runner.submit("pdf.confirm", "终态释放顺序回归", commands)
    job = app.store.get(job_id)

    snapshots_at_terminal: list[dict] = []
    real_update = app.store.update

    def spy_update(jid, **fields):
        if jid == job_id and fields.get("state") in server.DONE_STATES:
            snapshots_at_terminal.append(app.runner.snapshot())
        return real_update(jid, **fields)

    monkeypatch.setattr(app.store, "update", spy_update)
    with app.runner.cv:
        app.runner.active[job_id] = "pdf.confirm"
        app.runner.active_write += 1
    app.runner._run(job)

    assert len(snapshots_at_terminal) == 1
    snap = snapshots_at_terminal[0]
    assert snap["writer"] is False
    assert job_id not in snap["active"]
    done = app.store.get(job_id)
    assert done is not None
    assert done["state"] == "succeeded"


def _insert_running_write_job(app: server.App, job_id: str, argv: list[str], pid: int | None = None) -> None:
    stamp = server.now_iso()
    app.store.create(
        {
            "id": job_id,
            "kind": "split.run",
            "title": "接管测试",
            "argv": json.dumps(argv, ensure_ascii=False),
            "state": "running",
            "pid": pid,
            "created": stamp,
            "started": stamp,
            "log_path": app.env.rel(str(Path(app.env.job_dir(job_id)) / "log.txt")),
        }
    )


def _kill_proc_group(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        proc.kill()
    proc.wait(timeout=5)


def test_adopt_recovers_pid_from_file_when_db_pid_missing(workspace):
    """ops.db 里 pid 为空但 jobs/<id>/pid 存在时，adopt 必须认领而不标 orphaned。"""
    app = _make_app(workspace)
    script = workspace / "adopt_file_probe.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    argv = [sys.executable, str(script)]
    proc = subprocess.Popen(argv, cwd=workspace, start_new_session=True, stdout=subprocess.DEVNULL)
    try:
        job_id = "adopt-file-pid"
        _insert_running_write_job(app, job_id, argv, pid=None)
        server.write_text_atomic(str(Path(app.env.job_dir(job_id)) / "pid"), f"{proc.pid}\n")

        app.runner.adopt()

        row = app.store.get(job_id)
        assert row is not None
        assert row["state"] == "running"
        assert row["pid"] == proc.pid
        assert "接管" in (row["summary"] or "")
        assert app.runner.snapshot()["writer"] is True
        assert app.runner.cancel(job_id) is True
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            cur = app.store.get(job_id)
            if cur and cur["state"] in server.DONE_STATES:
                break
            time.sleep(0.05)
        assert app.store.get(job_id)["state"] == "cancelled"
    finally:
        _kill_proc_group(proc)


def test_adopt_recovers_pid_from_proc_environ_when_both_pids_missing(workspace):
    """ops.db 与 jobs/<id>/pid 都缺失时，adopt 靠 /proc 环境变量认出活孩子并写回 pid。"""
    app = _make_app(workspace)
    script = workspace / "adopt_env_probe.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    job_id = "adopt-env-pid"
    argv = [sys.executable, str(script)]
    child_env = app.env.child_env()
    child_env["BOOK_CORPUS_OPS_JOB_ID"] = job_id
    proc = subprocess.Popen(argv, cwd=workspace, env=child_env, start_new_session=True, stdout=subprocess.DEVNULL)
    try:
        time.sleep(0.1)
        _insert_running_write_job(app, job_id, argv, pid=None)

        app.runner.adopt()

        row = app.store.get(job_id)
        assert row is not None
        assert row["state"] == "running"
        assert row["pid"] == proc.pid
        assert (Path(app.env.job_dir(job_id)) / "pid").is_file()
        assert app.runner.snapshot()["writer"] is True
    finally:
        _kill_proc_group(proc)


def test_adopt_holds_writer_slot_when_lock_held_without_pid(workspace):
    """找不到 PID 但 .writer.lock 仍被占用时，adopt 保持 running 等锁释放，绝不标 orphaned。"""
    app = _make_app(workspace)
    flag = workspace / ".release-lock-child"
    holder_script = workspace / "lock_holder.py"
    holder_script.write_text(
        "import fcntl, os, sys, time\n"
        "fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o644)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX)\n"
        "open(sys.argv[2], 'w').close()\n"
        "end = time.monotonic() + 10\n"
        "while time.monotonic() < end and not os.path.exists(sys.argv[3]):\n"
        "    time.sleep(0.05)\n",
        encoding="utf-8",
    )
    ready = workspace / ".holder-ready"
    proc = subprocess.Popen(
        [sys.executable, str(holder_script), app.env.lock, str(ready), str(flag)],
        cwd=workspace,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.02)
        assert ready.exists()

        job_id = "adopt-lock-only"
        _insert_running_write_job(app, job_id, [sys.executable, str(workspace / "missing.py")], pid=None)
        server.write_json_atomic(str(Path(app.env.job_dir(job_id)) / "commands.json"), [])
        app.store.add_step(job_id, 0, "split", "running")

        app.runner.adopt()

        row = app.store.get(job_id)
        assert row is not None
        assert row["state"] == "running"
        assert app.runner.snapshot()["writer"] is True
        # 未知 PID 时调 cancel 也不能在写锁还占着的时候提前改写成 cancelled
        assert app.runner.cancel(job_id) is True
        assert app.store.get(job_id)["state"] == "running"

        flag.write_text("go", encoding="utf-8")
        proc.wait(timeout=5)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            cur = app.store.get(job_id)
            if cur and cur["state"] in ("cancelled", "awaiting_review", "failed"):
                break
            time.sleep(0.05)
        assert app.store.get(job_id)["state"] == "cancelled"
        assert app.runner.snapshot()["writer"] is False
    finally:
        _kill_proc_group(proc)


def test_spawn_inherits_writer_lock_across_parent_close(workspace):
    """_spawn 通过 pass_fds 把 .writer.lock 传给子进程，父端关闭 fd 不影响子进程继续持锁。"""
    app = _make_app(workspace)
    ready = workspace / ".spawn-lock-ready"
    release_flag = workspace / ".spawn-lock-release"
    child_script = workspace / "child_lock_probe.py"
    child_script.write_text(
        "import os, sys, time\n"
        "open(sys.argv[1], 'w').close()\n"
        "end = time.monotonic() + 10\n"
        "while time.monotonic() < end and not os.path.exists(sys.argv[2]):\n"
        "    time.sleep(0.05)\n",
        encoding="utf-8",
    )
    job_id = app.runner.submit(
        "pdf.confirm",
        "锁继承测试",
        [
            {
                "name": "probe",
                "script": "child_lock_probe.py",
                "argv": [sys.executable, str(child_script), str(ready), str(release_flag)],
            }
        ],
    )
    lock_fd = os.open(app.env.lock, os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    holder = None
    done = threading.Event()

    def run_spawn():
        try:
            app.runner._spawn(
                job_id,
                {
                    "name": "probe",
                    "script": "child_lock_probe.py",
                    "argv": [sys.executable, str(child_script), str(ready), str(release_flag)],
                },
                lock_fd=lock_fd,
            )
        finally:
            done.set()

    t = threading.Thread(target=run_spawn, daemon=True)
    t.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.02)
        assert ready.exists()
        # 模拟服务进程崩溃：只关闭父进程的 lock_fd（系统收回 fd）
        os.close(lock_fd)
        lock_fd = -1

        holder = os.open(app.env.lock, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = False
        except OSError:
            held = True
        assert held is True, "父进程关闭 fd 后，子进程仍必须持有 .writer.lock"

        release_flag.write_text("go", encoding="utf-8")
        assert done.wait(timeout=5)
        # 子进程退出后，锁必须立刻释放
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(holder, fcntl.LOCK_UN)
    finally:
        release_flag.write_text("go", encoding="utf-8")
        if lock_fd >= 0:
            os.close(lock_fd)
        if holder is not None:
            os.close(holder)
        t.join(timeout=5)


def test_store_migrates_legacy_ops_db_idempotently(tmp_path):
    """老版本 ops.db 缺 batch_dir/plan_hash 等可空列时，Store.__init__ 自动幂等迁移。"""
    import sqlite3

    db_path = tmp_path / "工作区" / "运维" / "ops.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute(
        """
        CREATE TABLE job (
          id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          title TEXT NOT NULL,
          argv TEXT NOT NULL,
          state TEXT NOT NULL,
          created TEXT NOT NULL
        )
        """
    )
    con.execute(
        "INSERT INTO job (id, kind, title, argv, state, created) VALUES (?,?,?,?,?,?)",
        ("legacy-1", "maintain", "旧任务", "[]", "succeeded", "2026-10-01T12:00:00+08:00"),
    )
    con.commit()
    con.close()

    store = server.Store(str(db_path))
    cols = {row["name"] for row in store.conn.execute("PRAGMA table_info(job)").fetchall()}
    for name, _ in server.Store._JOB_MIGRATE_COLS:
        assert name in cols

    store.create(
        {
            "id": "new-1",
            "kind": "delete.apply",
            "title": "新任务",
            "argv": "[]",
            "state": "succeeded",
            "created": "2026-11-24T12:00:00+08:00",
            "batch_dir": "工作区/批次/b1",
            "plan_hash": "abc123",
        }
    )
    assert store.get("legacy-1")["title"] == "旧任务"
    got = store.get("new-1")
    assert got["batch_dir"] == "工作区/批次/b1"
    assert got["plan_hash"] == "abc123"
    assert {r["id"] for r in store.list()} == {"legacy-1", "new-1"}

    # 重复初始化幂等且不破坏数据
    store2 = server.Store(str(db_path))
    assert store2.get("new-1")["plan_hash"] == "abc123"


def test_clean_dry_run_preview_and_path_boundary(workspace):
    """POST /api/jobs/clean 支持 dry_run=true 预览，并对 job_id 路径越界做防护。"""
    app = _make_app(workspace)
    jobs_root = Path(app.env.jobs)
    old_mtime = time.time() - 100 * 86400

    def add_job(jid, state, *, log_mtime=None, fresh_thumb=False, make_dir=True):
        _insert_job(app.store, jid, "maintain", state, title=jid)
        if not make_dir:
            return
        jdir = jobs_root / jid
        jdir.mkdir(parents=True, exist_ok=True)
        log = jdir / "log.txt"
        log.write_text("done\n", encoding="utf-8")
        if log_mtime is not None:
            os.utime(log, (log_mtime, log_mtime))
        if fresh_thumb:
            tdir = jdir / "thumbs"
            tdir.mkdir(exist_ok=True)
            (tdir / "t.png").write_bytes(b"png")

    add_job("clean-old", "succeeded", log_mtime=old_mtime)
    add_job("clean-fresh", "succeeded")
    add_job("clean-thumb", "succeeded", log_mtime=old_mtime, fresh_thumb=True)
    add_job("clean-running", "running", log_mtime=old_mtime)

    # 构造一个尝试逃逸 jobs/ 根目录的异常 job_id（没有 log.txt 时 ended=0，需靠超出 keep_jobs 或直接插最老记录触发？
    # 给它一个落到 jobs/../outside/log.txt 的旧 mtime，确保它能进候选集并验证 rmtree 不会删到 outside）
    outside_dir = Path(app.env.ops) / "outside"
    outside_dir.mkdir(parents=True, exist_ok=True)
    sentinel = outside_dir / "keep.txt"
    sentinel.write_text("must-survive", encoding="utf-8")
    outside_log = outside_dir / "log.txt"
    outside_log.write_text("old", encoding="utf-8")
    os.utime(outside_log, (old_mtime, old_mtime))
    _insert_job(app.store, "../outside", "maintain", "succeeded", title="traversal")

    with _http_server(app) as base:
        gen_before = app.status_gen

        code, bad = _http_json(f"{base}/api/jobs/clean", {"dry_run": "yes"})
        assert code == 400
        assert bad["ok"] is False

        code, preview = _http_json(f"{base}/api/jobs/clean", {"dry_run": True})
        assert code == 200
        assert preview["ok"] is True
        assert preview["dry_run"] is True
        assert preview["removed"] == 0
        assert preview["would_remove"] == 2
        assert set(preview["ids"]) == {"clean-old", "../outside"}
        assert preview["keep_days"] == 90
        assert preview["keep_jobs"] == 200
        assert preview["thumb_days"] == 30
        assert (jobs_root / "clean-old").exists()
        assert app.store.get("clean-old") is not None
        assert sentinel.exists()
        assert app.status_gen == gen_before

        code, cleaned = _http_json(f"{base}/api/jobs/clean", {})
        assert code == 200
        assert cleaned["ok"] is True
        assert cleaned["removed"] == 2
        assert not (jobs_root / "clean-old").exists()
        assert app.store.get("clean-old") is None
        assert app.store.get("../outside") is None
        assert sentinel.exists(), "越界路径绝对不能被 rmtree 删除"
        assert (jobs_root / "clean-fresh").exists()
        assert (jobs_root / "clean-thumb").exists()
        assert (jobs_root / "clean-running").exists()
        assert app.status_gen == gen_before + 1


def test_ops_status_and_audit_align_with_role_filter_and_empty_books(workspace):
    """含 front_back/notes 水章与纯图无正文书同步后，ops_status（轻/深）与 audit_state 均不误报落后。"""
    import importlib.util

    scripts_dir = REPO / "src" / "bookcorpus" / "scripts"
    bpi_spec = importlib.util.spec_from_file_location("bpi_ops_align_test", scripts_dir / "build_para_index.py")
    bpi = importlib.util.module_from_spec(bpi_spec)
    bpi_spec.loader.exec_module(bpi)

    # 给 B0001 增加 front_back 与 notes 章节；新增纯图无正文书 B0601（全 front_back）
    b1_md = workspace / "books" / "B0001" / "md"
    (b1_md / "000_封面.md").write_text(
        "---\ntitle: 封面\nchapter_role: front_back\n---\n# 封面\n\n![](images/c.png)\n", encoding="utf-8"
    )
    (b1_md / "999_注释.md").write_text(
        "---\ntitle: 注释\nchapter_role: notes\n---\n# 注释\n\n[^1]: 注释一条。\n", encoding="utf-8"
    )

    b601_dir = workspace / "books" / "B0601"
    (b601_dir / "md").mkdir(parents=True)
    (b601_dir / "source").mkdir(parents=True)
    (b601_dir / "source" / "gallery.pdf").write_bytes(b"%PDF-1.4")
    (b601_dir / "book.json").write_text(
        json.dumps({"uid": "B0601", "title": "纯图漫画", "subjects": ["艺术"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (b601_dir / "md" / "01_Page 1.md").write_text(
        "---\ntitle: Page 1\nchapter_role: front_back\n---\n# Page 1\n\n![](images/p1.png)\n",
        encoding="utf-8",
    )
    (b601_dir / "md" / "README.md").write_text("# 纯图漫画\n", encoding="utf-8")

    # 补全源文件与人账并重建 library.db + index.db
    for uid, src in (("B0001", "甲书.epub"), ("B0002", "乙书.epub"), ("B0003", "丙书.txt")):
        (workspace / "books" / uid / "source" / src).write_bytes(b"src")
    write_ledger(
        workspace,
        [
            ("B0001", "社科", "甲书", ""),
            ("B0002", "社科", "乙书", ""),
            ("B0003", "文学", "丙书", ""),
            ("B0601", "艺术", "纯图漫画", ""),
        ],
    )
    assert run_cli("--root", workspace, "library", "--emit-json").returncode == 0
    con = bpi.build(str(workspace / "build" / "检索" / "index.db"), root=str(workspace))
    con.close()

    # 1) ops_status 轻量模式与 --deep 均应报 lag_books=0, lag_chapters=0，并准确统计跳过水章与无正文书
    p = _paths(workspace)
    st_light = ops_status.collect(p, deep=False)
    assert st_light["index"]["lag_books"] == 0
    assert st_light["index"]["lag_chapters"] == 0
    assert st_light["index"]["filtered_chapters"] == 3
    assert st_light["index"]["empty_books"] == 1

    st_deep = ops_status.collect(p, deep=True)
    assert st_deep["index"]["lag_books"] == 0
    assert st_deep["index"]["lag_chapters"] == 0
    assert st_deep["index"]["filtered_chapters"] == 3
    assert st_deep["index"]["empty_books"] == 1

    # 2) audit_state --json 不报 index.db 落后 WARN，且 FAIL 为空
    proc = subprocess.run(
        [sys.executable, str(scripts_dir / "audit_state.py"), "--json", "--root", str(workspace)],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    audit_payload = json.loads(proc.stdout)
    assert audit_payload["fail"] == []
    assert not any("index.db 落后" in w for w in audit_payload["warn"]), audit_payload["warn"]

    # 3) 若磁盘真实新增一章 body（未 --sync），立即准确体现为 lag_books=1 / lag_chapters=1
    (workspace / "books" / "B0002" / "md" / "002_新正文.md").write_text(
        "# 新正文\n\n真实新增的正文段落。\n", encoding="utf-8"
    )
    st_lag = ops_status.collect(p, deep=False)
    assert st_lag["index"]["lag_chapters"] == 1
    assert st_lag["sync_delta"]["fts"]["modified"] == ["B0002"]
