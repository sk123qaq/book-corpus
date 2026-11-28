"""import.apply 成功后自动接力电子书拆章（_chain_split）及与 _chain_docs / _chain_auto_sync 衔接测试。"""

import importlib.util
import json
import pathlib
import sys
import threading
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parents[1]
SERVER = HERE / "apps" / "ops" / "server.py"
APP_JS = HERE / "apps" / "ops" / "web" / "app.js"


def _server():
    key = "ops_server_for_auto_split_test"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, SERVER)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


def _env(tmp_path):
    env = _server().Env(str(tmp_path / "ws"))
    env.prepare()
    return env


def _make_fake_runner(env, *, applied_rows=None, split_seed=None, docs_seed=None, settings=None):
    base = pathlib.Path(env.job_dir("20261128-102141-f3cc"))
    base.mkdir(parents=True, exist_ok=True)
    if applied_rows is not None:
        (base / "applied.json").write_text(
            json.dumps({"version": 1, "conflicts": 0, "applied": applied_rows, "failed": []}, ensure_ascii=False),
            encoding="utf-8",
        )
    if split_seed is not None:
        (base / "split-seed.json").write_text(json.dumps(split_seed, ensure_ascii=False), encoding="utf-8")
    if docs_seed is not None:
        (base / "docs-seed.json").write_text(json.dumps(docs_seed, ensure_ascii=False), encoding="utf-8")
    if settings is not None:
        pathlib.Path(env.settings).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(env.settings).write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")

    class FakeRunner:
        def __init__(self):
            self.env = env
            self.submitted = []
            self.logs = []
            self._seq = 0

        def _log(self, job_id, text):
            self.logs.append(text)

        def submit(self, kind, title, commands, batch_dir=None, plan_hash=None):
            self._seq += 1
            jid = f"{kind.replace('.', '-')}-{self._seq}"
            self.submitted.append(
                {
                    "id": jid,
                    "kind": kind,
                    "title": title,
                    "commands": commands,
                    "batch_dir": batch_dir,
                    "plan_hash": plan_hash,
                }
            )
            return jid

    return FakeRunner(), base, "20261128-102141-f3cc"


def test_chain_split_pure_ebooks_add(tmp_path):
    server = _server()
    env = _env(tmp_path)
    rows = [
        {"uid": "B0001", "file": "甲.epub", "format": "epub", "action": "add"},
        {"uid": "B0002", "file": "乙.mobi", "format": "MOBI", "action": "add"},
        {"uid": "B0003", "file": "丙.azw3", "format": "azw3", "action": "add"},
        {"uid": "B0004", "file": "丁.azw", "format": "azw", "action": "add"},
    ]
    runner, base, jid = _make_fake_runner(env, applied_rows=rows)
    assert server.Runner._chain_split(runner, jid) is True
    assert len(runner.submitted) == 1

    item = runner.submitted[0]
    assert item["kind"] == "split.run"
    assert "拆章 4 本" in item["title"]
    assert item["batch_dir"].startswith("工作区/批次/split-ops-")
    cmds = item["commands"]
    assert [c["name"] for c in cmds] == ["split", "build_library", "gen_book_catalog"]
    argv = cmds[0]["argv"]
    assert argv[1].endswith("split_md_batch.py")
    assert argv[argv.index("--uids") + 1] == "B0001,B0002,B0003,B0004"
    assert "--report-json" in argv
    assert "--replace" not in argv

    seed = json.loads((base / "split-seed.json").read_text(encoding="utf-8"))
    assert seed["split_job"] == item["id"]
    assert seed["split_jobs"] == [item["id"]]
    assert seed["batch"] == pathlib.Path(item["batch_dir"]).name


def test_chain_mixed_doc_ebook_pdf_dispatches_independently(tmp_path):
    server = _server()
    env = _env(tmp_path)
    rows = [
        {"uid": "B0001", "file": "文档.docx", "format": "docx", "action": "add"},
        {"uid": "B0002", "file": "电子书.epub", "format": "epub", "action": "add"},
        {"uid": "B0003", "file": "扫描.pdf", "format": "pdf", "action": "add"},
        {"uid": "B0004", "file": "重复.epub", "format": "epub", "action": "delete"},
    ]
    runner, base, jid = _make_fake_runner(env, applied_rows=rows)
    assert server.Runner._chain_docs(runner, jid) is True
    assert server.Runner._chain_split(runner, jid) is True
    assert [s["kind"] for s in runner.submitted] == ["docs.run", "split.run"]

    split_item = runner.submitted[1]
    split_argv = split_item["commands"][0]["argv"]
    assert split_argv[split_argv.index("--uids") + 1] == "B0002"
    assert "--replace" not in split_argv

    assert (base / "docs-seed.json").is_file()
    assert (base / "split-seed.json").is_file()


def test_chain_split_replace_and_mixed_add_replace(tmp_path):
    server = _server()
    env = _env(tmp_path)
    rows = [
        {"uid": "B0001", "file": "新书.epub", "format": "epub", "action": "add"},
        {"uid": "B0002", "file": "换源.epub", "format": "epub", "action": "replace"},
    ]
    runner, base, jid = _make_fake_runner(env, applied_rows=rows)
    assert server.Runner._chain_split(runner, jid) is True
    assert len(runner.submitted) == 2

    add_job, rep_job = runner.submitted
    assert add_job["kind"] == "split.run"
    assert "拆章 1 本" in add_job["title"]
    assert add_job["batch_dir"].startswith("工作区/批次/split-ops-")
    assert "--replace" not in add_job["commands"][0]["argv"]
    assert add_job["commands"][0]["argv"][add_job["commands"][0]["argv"].index("--uids") + 1] == "B0001"

    assert rep_job["kind"] == "split.run"
    assert "重拆 1 本" in rep_job["title"]
    assert rep_job["batch_dir"].startswith("工作区/批次/resplit-ops-")
    assert "--replace" in rep_job["commands"][0]["argv"]
    assert rep_job["commands"][0]["argv"][rep_job["commands"][0]["argv"].index("--uids") + 1] == "B0002"

    seed = json.loads((base / "split-seed.json").read_text(encoding="utf-8"))
    assert seed["split_job"] == add_job["id"]
    assert seed["split_jobs"] == [add_job["id"], rep_job["id"]]
    assert len(seed["batches"]) == 2
    assert seed["batches"][0] != seed["batches"][1]


def test_chain_split_skips_when_no_ebooks_or_delete_only_or_missing_applied(tmp_path):
    server = _server()
    for rows in (
        [{"uid": "B0001", "file": "甲.pdf", "format": "pdf", "action": "add"}],
        [{"uid": "B0002", "file": "乙.docx", "format": "docx", "action": "add"}],
        [{"uid": "B0003", "file": "丙.epub", "format": "epub", "action": "delete"}],
        None,
    ):
        env = _env(tmp_path / f"case-{id(rows)}")
        runner, base, jid = _make_fake_runner(env, applied_rows=rows)
        assert server.Runner._chain_split(runner, jid) is False
        assert runner.submitted == []
        assert not (base / "split-seed.json").exists()


def test_chain_split_is_idempotent_and_respects_auto_split_setting(tmp_path):
    server = _server()
    env1 = _env(tmp_path / "idem")
    runner1, _base1, jid1 = _make_fake_runner(
        env1,
        applied_rows=[{"uid": "B0001", "file": "甲.epub", "format": "epub", "action": "add"}],
        split_seed={"split_job": "old-split", "batch": "split-ops-old"},
    )
    assert server.Runner._chain_split(runner1, jid1) is False
    assert runner1.submitted == []
    assert any("不重复提交" in x for x in runner1.logs)

    env2 = _env(tmp_path / "disabled")
    runner2, base2, jid2 = _make_fake_runner(
        env2,
        applied_rows=[{"uid": "B0001", "file": "甲.epub", "format": "epub", "action": "add"}],
        settings={"auto_split": False, "auto_embed": True},
    )
    assert server.Runner._chain_split(runner2, jid2) is False
    assert runner2.submitted == []
    assert not (base2 / "split-seed.json").exists()
    assert any("auto_split=false" in x for x in runner2.logs)


def test_finalize_chains_split_to_auto_sync_and_preserves_review_gate(tmp_path, monkeypatch):
    """端到端走 Runner._finalize：import.apply → split.run → gate_split / _chain_auto_sync，且 API 暴露 split_job。"""
    server = _server()
    root = tmp_path / "ws"
    root.mkdir(parents=True)
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")
    scripts = root / "工作区" / "脚本"
    scripts.mkdir(parents=True)

    # stub 脚本：只负责把 split/library/catalog/fts 跑通并产出 report.json
    (scripts / "build_library.py").write_text(
        'import json\nprint(json.dumps({"books": 1, "conflicts": 0}))\n',
        encoding="utf-8",
    )
    (scripts / "gen_book_catalog.py").write_text('print("未匹配 0 条")\n', encoding="utf-8")
    (scripts / "build_para_index.py").write_text(
        """import sys
# --sync
def diff_index(indexdb, root=None):
    return {"added": ["B0001"], "modified": [], "removed": []}
if __name__ == "__main__":
    print("sync fts ok")
""",
        encoding="utf-8",
    )
    (scripts / "split_md_batch.py").write_text(
        """import json, os, sys
args = sys.argv[1:]
batch = args[args.index("--batch") + 1]
uids = args[args.index("--uids") + 1].split(",")
mode = os.environ.get("TEST_SPLIT_MODE", "landed")
status_counts = {mode: len(uids)}
pending = uids if mode == "staged-review" else []
print(json.dumps({"batch": batch, "status_counts": status_counts, "pending_review": pending}))
""",
        encoding="utf-8",
    )

    monkeypatch.delitem(sys.modules, "build_para_index", raising=False)
    monkeypatch.syspath_prepend(str(scripts))

    env = server.Env(str(root), scripts=str(scripts))
    env.prepare()
    app = server.App.__new__(server.App)
    app.env = env
    store = server.Store(env.ops_db)
    runner = server.Runner(env, store, app=app)
    app.store = store
    app.runner = runner
    app.status_cache = (0.0, None)
    app.status_lock = threading.Lock()
    app.status_gen = 0
    app._swr_running = False
    app._cold_event = None
    app._audit_lock = threading.Lock()
    app._audit_running = False
    app._audit_pending = False
    app._audit_done = threading.Event()
    app._audit_done.set()
    app.pdf_probe_down_until = 0.0
    app.pdf_page_cache = {}
    app.pdf_page_lock = threading.Lock()

    # 1) settings 默认开启 auto_split，且可读写
    assert app.get_settings()["auto_split"] is True
    assert app.save_settings({"auto_split": False})["auto_split"] is False
    assert app.save_settings({"auto_split": True})["auto_split"] is True

    # 2) 模拟一个 import.apply 成功落盘 applied.json 后收口 -> 自动排队 split.run
    apply_id = runner.submit("import.apply", "导入测试", [])
    base = pathlib.Path(env.job_dir(apply_id))
    (base / "applied.json").write_text(
        json.dumps(
            {
                "version": 1,
                "conflicts": 0,
                "applied": [{"uid": "B0001", "file": "样书.epub", "format": "epub", "action": "add"}],
                "failed": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (base / "log.txt").write_text('"conflicts": 0\n未匹配 0 条\n', encoding="utf-8")
    apply_job = store.get(apply_id)
    runner._finalize(apply_id, apply_job, [], [("ingest apply", 0), ("build_library", 0), ("gen_book_catalog", 0)])

    assert store.get(apply_id)["state"] == "succeeded"
    seed = json.loads((base / "split-seed.json").read_text(encoding="utf-8"))
    split_id = seed["split_job"]
    split_job = store.get(split_id)
    assert split_job["kind"] == "split.run"
    assert split_job["state"] == "queued"

    # 3) 通过 HTTP GET /api/jobs/<apply_id> 核对暴露了 split_job / split_batch / split-seed.json
    class _H(server.Handler):
        pass

    _H.app = app
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/jobs/{apply_id}", timeout=5) as resp:
            detail = json.loads(resp.read().decode("utf-8"))
        assert detail["split_job"] == split_id
        assert detail["split_batch"] == seed["batch"]
        assert detail["split_jobs"] == [split_id]
        assert "split-seed.json" in detail["files"]
    finally:
        httpd.shutdown()
        httpd.server_close()

    # 4) 真正启动 Runner 跑完 split.run，核对它完成后继续自动接力 sync.fts
    runner.start()
    deadline = time.time() + 15
    while time.time() < deadline:
        sj = store.get(split_id)
        jobs = store.list(limit=20)
        fts = [j for j in jobs if j["kind"] == "sync.fts"]
        if sj["state"] == "succeeded" and fts and fts[0]["state"] == "succeeded":
            break
        time.sleep(0.1)
    assert store.get(split_id)["state"] == "succeeded"
    fts_jobs = [j for j in store.list(limit=20) if j["kind"] == "sync.fts"]
    assert len(fts_jobs) == 1
    assert fts_jobs[0]["state"] == "succeeded"

    # 5) 若 split.run 产出 staged-review，gate_split 仍必须落 awaiting_review（不绕过校验）
    review_id = runner.submit("split.run", "待复核样本", [])
    rbase = pathlib.Path(env.job_dir(review_id))
    (rbase / "log.txt").write_text(
        json.dumps({"batch": "split-ops-rev", "status_counts": {"staged-review": 1}, "pending_review": ["B0002"]})
        + "\n",
        encoding="utf-8",
    )
    runner._finalize(review_id, store.get(review_id), [], [("split", 0), ("build_library", 0), ("gen_book_catalog", 0)])
    assert store.get(review_id)["state"] == "awaiting_review"
    sys.modules.pop("build_para_index", None)


def test_app_js_import_handoff_supports_auto_split():
    src = APP_JS.read_text(encoding="utf-8")
    assert "detail.split_job" in src
    assert "detail.split_batch" in src
    assert "已自动接力电子书拆章" in src
    assert 'data-act="split-goto"' in src
    assert "async 'split-goto'()" in src
