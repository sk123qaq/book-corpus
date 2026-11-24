"""写锁统一（.writer.lock）：CLI 人账写入、ingest apply 与 ops 写任务共用同一把排他锁。"""

import fcntl
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from bookcorpus.scripts import lib_common as LC_pkg
from conftest import run_cli

REPO = Path(__file__).resolve().parents[1]


def _load_ops_server():
    path = REPO / "apps" / "ops" / "server.py"
    spec = importlib.util.spec_from_file_location("ops_server_writer_lock_test", str(path))
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ops_server_writer_lock_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_category_put_and_delete_block_while_writer_lock_held(workspace):
    ops_dir = workspace / "工作区" / "运维"
    ops_dir.mkdir(parents=True, exist_ok=True)
    lock_file = ops_dir / ".writer.lock"

    # 用子进程持有 .writer.lock 0.35s，再验证子进程调用 category_put 被阻塞到持锁结束后才落地
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import lib_common as L; "
        "L.category_put({'uid': 'B0001', '一级': '历史', '正式书名': '甲书'}, 'cli', root=sys.argv[2]); "
        "L.categories_delete(['B0002'], root=sys.argv[2])"
    )
    scripts_dir = str(REPO / "src" / "bookcorpus" / "scripts")
    fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        proc = subprocess.Popen(
            [sys.executable, "-c", code, scripts_dir, str(workspace)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.25)
        assert proc.poll() is None, "持有 .writer.lock 时，CLI 写人账不该提前穿过锁"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    out, err = proc.communicate(timeout=10)
    assert proc.returncode == 0, out + err
    rows = {r["uid"]: r for r in LC_pkg.categories(root=str(workspace), create=False)}
    assert rows["B0001"]["一级"] == "历史"
    assert "B0002" not in rows


def test_ingest_apply_blocks_on_writer_lock(workspace):
    inbox = workspace / "books" / "_收件箱"
    inbox.mkdir(parents=True, exist_ok=True)
    epub = inbox / "新书锁测试.epub"
    epub.write_bytes(b"writer-lock-epub" * 10)

    scan = run_cli("--root", workspace, "run", "ingest", "scan", "--json", "--no-classify")
    assert scan.returncode == 0, scan.stdout + scan.stderr
    plan_file = workspace / "plan.json"
    plan_file.write_text(scan.stdout, encoding="utf-8")

    ops_dir = workspace / "工作区" / "运维"
    ops_dir.mkdir(parents=True, exist_ok=True)
    lock_file = ops_dir / ".writer.lock"
    fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "bookcorpus",
                "--root",
                str(workspace),
                "run",
                "ingest",
                "apply",
                str(plan_file),
                "--apply",
                "--no-rebuild",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.25)
        assert proc.poll() is None, "持有 .writer.lock 时，ingest apply 不该提前写盘"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    out, err = proc.communicate(timeout=10)
    assert proc.returncode == 0, out + err
    assert not epub.exists()


def test_writer_lock_reentrant_across_module_aliases_and_ops_child(workspace):
    ops_mod = _load_ops_server()
    env = ops_mod.Env(str(workspace))
    env.prepare()

    # 1. ops ledger_lock 与通过包路径导入的 lib_common 在同线程重入不自锁
    with ops_mod.ledger_lock(env):
        LC_pkg.category_put(
            {"uid": "B0001", "一级": "哲学", "正式书名": "甲书"},
            updated_by="ops",
            root=str(workspace),
        )
    rows = {r["uid"]: r for r in LC_pkg.categories(root=str(workspace), create=False)}
    assert rows["B0001"]["一级"] == "哲学"

    # 2. Runner 持 .writer.lock 拉起 ingest apply 子进程不死锁，只读任务不传继承标记
    assert "BOOK_CORPUS_WRITER_LOCK_PATH" in env.child_env(writer_lock=True)
    assert "BOOK_CORPUS_WRITER_LOCK_PATH" not in env.child_env(writer_lock=False)

    inbox = workspace / "books" / "_收件箱"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "父子不死锁.epub").write_bytes(b"child-env-epub" * 10)
    scan = run_cli("--root", workspace, "run", "ingest", "scan", "--json", "--no-classify")
    assert scan.returncode == 0, scan.stdout + scan.stderr
    plan_file = workspace / "plan-child.json"
    plan_file.write_text(scan.stdout, encoding="utf-8")

    store = ops_mod.Store(env.ops_db)
    runner = ops_mod.Runner(env, store)
    runner.start()
    ingest_script = str(REPO / "src" / "bookcorpus" / "scripts" / "ingest.py")
    try:
        job_id = runner.submit(
            "pdf.confirm",
            "锁继承入库测试",
            [
                {
                    "name": "apply",
                    "argv": [
                        sys.executable,
                        ingest_script,
                        "--root",
                        str(workspace),
                        "apply",
                        str(plan_file),
                        "--apply",
                        "--no-rebuild",
                    ],
                }
            ],
        )
        deadline = time.monotonic() + 10.0
        job = None
        while time.monotonic() < deadline:
            job = store.get(job_id)
            if job and job["state"] in ops_mod.DONE_STATES:
                break
            time.sleep(0.05)
        assert job is not None
        assert job["state"] == "succeeded", json.dumps(job, ensure_ascii=False)
    finally:
        runner.stopping = True
        with runner.cv:
            runner.cv.notify_all()
        store.conn.close()
