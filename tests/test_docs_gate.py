"""docs.run 的门槛（gate_docs）与 import.apply 成功后的自动接转。

只看 report.json：缺产物/有失败就 failed，全 done 才 succeeded；接转幂等靠
docs-seed.json。
"""

import importlib.util
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parents[1]
SERVER = HERE / "apps" / "ops" / "server.py"


def _server():
    if SERVER not in sys.modules:
        spec = importlib.util.spec_from_file_location("ops_server_for_docs_test", SERVER)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[SERVER] = mod
        spec.loader.exec_module(mod)
    return sys.modules[SERVER]


def _env(tmp_path):
    env = _server().Env(str(tmp_path / "ws"))
    env.prepare()
    return env


def _ctx(env, job_id, rcs=None, report=None):
    server = _server()
    d = pathlib.Path(env.job_dir(job_id))
    d.mkdir(parents=True, exist_ok=True)
    if report is not None:
        with (d / "report.json").open("w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False)
    job = {"id": job_id, "kind": "docs.run"}
    return server.Ctx(env, job, [], list(rcs or []))


def _report(total, done, failed, skipped=0, failed_uids=()):
    items = [{"uid": "B0001", "status": "done"} for _ in range(done)]
    items += [{"uid": uid, "status": "failed"} for uid in failed_uids]
    items += [{"uid": "B0009", "status": "skipped"} for _ in range(skipped)]
    return {
        "version": 1,
        "summary": {"total": total, "done": done, "skipped": skipped, "failed": failed},
        "items": items,
    }


def test_kinds_registers_docs_run():
    server = _server()
    spec = server.KINDS["docs.run"]
    assert spec["readonly"] is False
    assert spec["gate"] is server.gate_docs
    assert "文档" in spec["label"]


def test_missing_report_fails(tmp_path):
    server = _server()
    state, detail = server.gate_docs(_ctx(_env(tmp_path), "job-noreport"))
    assert state == "failed"
    assert "report.json" in detail


def test_failed_items_fail_the_gate(tmp_path):
    server = _server()
    ctx = _ctx(_env(tmp_path), "job-bad", report=_report(8, 6, 1, skipped=1, failed_uids=["B0007"]))
    state, detail = server.gate_docs(ctx)
    assert state == "failed"
    assert "完成 6/8，失败 1" in detail
    assert "B0007" in detail


def test_all_done_succeeds(tmp_path):
    server = _server()
    ctx = _ctx(_env(tmp_path), "job-ok", report=_report(3, 3, 0))
    state, detail = server.gate_docs(ctx)
    assert state == "succeeded"
    assert "完成 3/3，失败 0" in detail


def test_skipped_counted_in_summary_line(tmp_path):
    server = _server()
    ctx = _ctx(_env(tmp_path), "job-skip", report=_report(5, 3, 0, skipped=2))
    state, detail = server.gate_docs(ctx)
    assert state == "succeeded"
    assert "完成 3/5，失败 0" in detail
    assert "跳过 2" in detail


def test_failed_step_wins_over_report(tmp_path):
    server = _server()
    ctx = _ctx(_env(tmp_path), "job-rc", rcs=[("docs convert", 2)], report=_report(1, 1, 0))
    state, detail = server.gate_docs(ctx)
    assert state == "failed"
    assert "docs convert" in detail


def test_bad_summary_shape_fails(tmp_path):
    server = _server()
    ctx = _ctx(_env(tmp_path), "job-shape", report={"version": 1, "summary": {"total": "x", "done": 1}})
    state, _detail = server.gate_docs(ctx)
    assert state == "failed"


def _chain(env, applied_rows=None, seed=None):
    server = _server()
    base = pathlib.Path(env.job_dir("apply-job"))
    base.mkdir(parents=True, exist_ok=True)
    if applied_rows is not None:
        with (base / "applied.json").open("w", encoding="utf-8") as fh:
            json.dump({"version": 1, "conflicts": 0, "applied": applied_rows, "failed": []}, fh, ensure_ascii=False)
    if seed is not None:
        with (base / "docs-seed.json").open("w", encoding="utf-8") as fh:
            json.dump(seed, fh)

    class FakeRunner:
        def __init__(self):
            self.env = env
            self.submitted = []
            self.logs = []

        def _log(self, job_id, text):
            self.logs.append(text)

        def submit(self, kind, title, commands):
            self.submitted.append((kind, title, commands))
            return "docs-job-1"

    runner = FakeRunner()
    server.Runner._chain_docs(runner, "apply-job")
    return runner, base


def test_chain_submits_docs_run_for_doc_formats(tmp_path):
    env = _env(tmp_path)
    rows = [
        {"uid": "B0001", "file": "甲.docx", "format": "docx", "action": "add"},
        {"uid": "B0002", "file": "乙.epub", "format": "epub", "action": "add"},
        {"uid": "B0003", "file": "丙.xlsx", "format": "XLSX", "action": "replace"},
    ]
    runner, base = _chain(env, rows)
    assert len(runner.submitted) == 1
    kind, title, commands = runner.submitted[0]
    assert kind == "docs.run"
    assert "2 本" in title

    cmds = commands("docs-job-1")
    assert [c["name"] for c in cmds] == ["docs plan", "docs convert", "build_library", "gen_book_catalog"]
    plan = cmds[0]["argv"]
    assert plan[1].endswith("docs_to_md.py")
    assert plan[2] == "plan"
    assert "--applied" in plan
    assert plan[plan.index("--applied") + 1] == str(base / "applied.json")
    assert cmds[1]["argv"][2] == "convert"

    seed = json.loads(pathlib.Path(base, "docs-seed.json").read_text(encoding="utf-8"))
    assert seed == {"docs_job": "docs-job-1"}


def test_chain_is_idempotent_with_seed(tmp_path):
    env = _env(tmp_path)
    runner, _base = _chain(env, [{"uid": "B0001", "format": "docx", "action": "add"}], seed={"docs_job": "old"})
    assert runner.submitted == []
    assert any("不重复提交" in x for x in runner.logs)


def test_chain_skips_when_no_doc_formats(tmp_path):
    env = _env(tmp_path)
    runner, base = _chain(env, [{"uid": "B0002", "format": "epub", "action": "add"}])
    assert runner.submitted == []
    assert not (base / "docs-seed.json").exists()


def test_chain_ignores_delete_rows(tmp_path):
    """重复上传的 docx 会被 delete，书本身没变，不能因此触发补转。"""
    env = _env(tmp_path)
    runner, base = _chain(env, [{"uid": "B0001", "format": "docx", "action": "delete"}])
    assert runner.submitted == []
    assert not (base / "docs-seed.json").exists()


def test_chain_skips_without_applied_json(tmp_path):
    env = _env(tmp_path)
    runner, _base = _chain(env, None)
    assert runner.submitted == []
