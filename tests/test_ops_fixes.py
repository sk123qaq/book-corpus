"""ops 服务端与 ops_status 修复项的回归单测。"""

import csv
import importlib.util
import json
import os
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
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
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
        assert len(cold["items"]) == 1
        assert cold["items"][0]["uid"] == "B9001"
        assert [d["name"] for d in cold["extra_dirs"]] == ["历史冷宫书"]

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
