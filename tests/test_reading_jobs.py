"""任务队列：并行上限、排队、取消即时反馈、失败时账要说真话。"""

import json
import threading
import time

from bookcorpus.reading import events
from bookcorpus.reading.jobs import JobQueue


def _wait_until(predicate, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_at_most_four_jobs_run_and_fifth_stays_queued(tmp_path):
    release = threading.Event()
    started = []
    lock = threading.Lock()

    def runner(job_id, _job):
        with lock:
            started.append(job_id)
        release.wait(5)
        return {"status": "done"}

    queue = JobQueue(str(tmp_path), runner, max_workers=4)
    try:
        jobs = [queue.submit("B0001", n, ["standard"]) for n in range(1, 6)]
        assert _wait_until(lambda: len(started) == 4)
        time.sleep(0.1)
        assert len(started) == 4
        running_ids = set(started)
        assert sum(1 for job in jobs if job["id"] not in running_ids) == 1

        release.set()
        assert _wait_until(lambda: all(queue.get(job["id"])["state"] == "done" for job in jobs), timeout=5)
        assert len(started) == 5
    finally:
        release.set()
        queue.shutdown()


def test_cancel_running_job_is_immediate_and_late_result_is_ignored(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def runner(_job_id, _job):
        started.set()
        release.wait(5)
        return {"status": "done"}

    queue = JobQueue(str(tmp_path), runner, max_workers=1)
    try:
        job = queue.submit("B0001", 1, ["standard"])
        assert started.wait(2)
        cancelled = queue.cancel(job["id"])
        assert cancelled["state"] == "cancelled"
        assert cancelled["error"] == "已取消"
        assert queue.cancelled(job["id"])

        release.set()
        time.sleep(0.15)
        assert queue.get(job["id"])["state"] == "cancelled"
    finally:
        release.set()
        queue.shutdown()


def test_cancel_queued_job_removes_it_without_running(tmp_path):
    release = threading.Event()
    started = []

    def runner(job_id, _job):
        started.append(job_id)
        release.wait(5)
        return {"status": "done"}

    queue = JobQueue(str(tmp_path), runner, max_workers=4)
    try:
        blockers = [queue.submit("B0001", n, ["standard"]) for n in range(1, 5)]
        assert _wait_until(lambda: len(started) == 4)
        queued = queue.submit("B0001", 5, ["standard"])
        assert queued["state"] == "queued"

        cancelled = queue.cancel(queued["id"])
        assert cancelled["state"] == "cancelled"
        assert cancelled["error"] == "已取消"

        release.set()
        assert _wait_until(lambda: all(queue.get(job["id"])["state"] == "done" for job in blockers), timeout=5)
        assert queued["id"] not in started
        assert queue.get(queued["id"])["state"] == "cancelled"
    finally:
        release.set()
        queue.shutdown()


def test_cancel_after_save_reconciles_to_done_with_pack(tmp_path):
    release = threading.Event()
    started = threading.Event()

    def runner(job_id, _job):
        started.set()
        release.wait(5)
        return {"status": "done", "pack_id": "B0001/ch/01"}

    queue = JobQueue(str(tmp_path), runner, max_workers=1)
    try:
        job = queue.submit("B0001", 1, ["standard"])
        assert started.wait(2)
        cancelled = queue.cancel(job["id"])
        assert cancelled["state"] == "cancelled"
        release.set()
        assert _wait_until(
            lambda: queue.get(job["id"])["state"] == "done" and queue.get(job["id"])["pack_id"] == "B0001/ch/01",
            timeout=5,
        )
    finally:
        release.set()
        queue.shutdown()


def test_cancelled_running_job_keeps_blocking_chapter_until_runner_returns(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def runner(job_id, _job):
        started.set()
        release.wait(5)
        return {"status": "done"}

    queue = JobQueue(str(tmp_path), runner, max_workers=1)
    try:
        job = queue.submit("B0001", 1, ["standard"])
        assert started.wait(2)
        cancelled = queue.cancel(job["id"])
        assert cancelled["state"] == "cancelled"

        # 已取消但线程还在跑：仍占着这一章，不能放第二个任务进同一包目录。
        blocked = queue.conflicts("B0001", 1)
        assert [item["id"] for item in blocked] == [job["id"]]
        # 同一条线也要能派到界面：取消中的任务不能从工作区状态里消失。
        active = queue.active_for("B0001", 1)
        assert [item["id"] for item in active] == [job["id"]]
        assert [item["id"] for item in queue.active_for() if item["id"] == job["id"]] == [job["id"]]
        assert queue.active_for("B0002", 1) == []

        release.set()
        assert _wait_until(lambda: queue.conflicts("B0001", 1) == [], timeout=3)
        assert _wait_until(lambda: queue.active_for("B0001", 1) == [], timeout=3)
        assert queue.get(job["id"])["state"] == "cancelled"

        again = queue.submit("B0001", 1, ["deep"])
        assert _wait_until(lambda: queue.get(again["id"])["state"] == "done")
        assert queue.conflicts("B0001", 1) == []
    finally:
        release.set()
        queue.shutdown()


def test_depth_job_keeps_layers_and_does_not_force_a_tier(tmp_path):
    def runner(_job_id, _job):
        return {"status": "done"}

    queue = JobQueue(str(tmp_path), runner, max_workers=1)
    try:
        job = queue.submit("B0003", 6, [], kind="depth", layers=["model", "skim"])
        assert job["kind"] == "depth"
        assert job["tiers"] == []
        assert job["layers"] == ["model", "skim"]
        assert [step["name"] for step in job["steps"]] == ["结构", "面板", "速览", "标准", "深读", "渲染"]
        assert _wait_until(lambda: queue.get(job["id"])["state"] == "done")
        assert queue.get(job["id"])["layers"] == ["model", "skim"]
    finally:
        queue.shutdown()


class _TransportError(RuntimeError):
    kind = "transport_closed"
    attempt = 4


def test_failed_depth_job_marks_only_the_layer_that_broke(tmp_path):
    """失败的六行里只有一行是红的；进度按真正落盘的层算，不再报 100%。"""

    def runner(job_id, _job):
        queue.set_layer(job_id, "model")
        queue.note(job_id, layers_ready=["model"])
        queue.set_layer(job_id, "panels")
        queue.note(job_id, layers_ready=["model", "panels"])
        queue.set_layer(job_id, "skim")
        raise _TransportError("Remote end closed connection without response")

    queue = JobQueue(str(tmp_path), runner, max_workers=1)
    try:
        job = queue.submit("B0003", 6, [], kind="depth", layers=["model", "panels", "skim"])
        assert _wait_until(lambda: queue.get(job["id"])["state"] == "failed")
        failed = queue.get(job["id"])
        assert [step["status"] for step in failed["steps"]] == [
            "done",
            "done",
            "failed",
            "pending",
            "pending",
            "pending",
        ]
        assert failed["progress"] == 33
        assert failed["failed_layer"] == "skim"
        assert failed["error_kind"] == "transport_closed"
        assert failed["attempts"] == 4
        assert failed["elapsed_ms"] is not None
        assert failed["elapsed_ms"] >= 0
    finally:
        queue.shutdown()


def test_failed_job_keeps_a_readable_event_log(tmp_path):
    """看原始账读的是事件账，不是任务字段。"""

    def runner(_job_id, _job):
        events.current().emit("layer_commit", status="partial", layers_ready=["model"])
        raise _TransportError("Remote end closed connection without response")

    queue = JobQueue(str(tmp_path), runner, max_workers=1)
    try:
        job = queue.submit("B0003", 6, [], kind="depth", layers=["model"])
        assert _wait_until(lambda: queue.get(job["id"])["state"] == "failed")
        assert queue.events_path(job["id"]).endswith(".jsonl")
        records = queue.events(job["id"])
        assert records
        assert records[0]["kind"] == "layer_commit"
        assert records[0]["layers_ready"] == ["model"]
    finally:
        queue.shutdown()


def test_restart_marks_interrupted_job_with_restart_kind(tmp_path):
    """进程被杀留下的运行态：标失败、点明第几层、进度不虚报。"""
    path = tmp_path / "阅读" / "jobs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(
            [
                {
                    "id": "r-restart",
                    "kind": "depth",
                    "uid": "B0003",
                    "n": "06",
                    "state": "annotating",
                    "stage": "annotating",
                    "layer": "skim",
                    "layers_ready": ["model"],
                    "layers": ["model", "panels", "skim", "standard", "deep"],
                    "tiers": [],
                    "progress": 0,
                    "created_at": "2026-11-12T00:00:00",
                    "updated_at": "2026-11-12T00:00:00",
                }
            ],
            fh,
            ensure_ascii=False,
        )
    queue = JobQueue(str(tmp_path), lambda _job_id, _job: {"status": "done"}, max_workers=1)
    try:
        job = queue.get("r-restart")
        assert job["state"] == "failed"
        assert job["error_kind"] == "restart"
        assert job["failed_layer"] == "skim"
        assert job["progress"] == 17
        assert [step["status"] for step in job["steps"]][:3] == ["done", "pending", "failed"]
    finally:
        queue.shutdown()


def test_cancel_keeps_finished_layers_and_drops_the_failure(tmp_path):
    # 取消不是失败：已落盘的层要留着，失败层与错误类别要清掉，
    # 否则步骤条会指着一层说它失败了，而它只是没跑完。
    started = threading.Event()
    release = threading.Event()

    def runner(job_id, job):
        queue.set_layer(job_id, "skim")
        queue.note(job_id, layers_ready=["model", "panels"])
        queue.set_stage(job_id, "annotating")
        started.set()
        release.wait(timeout=5)

    queue = JobQueue(str(tmp_path), runner, max_workers=1)
    try:
        job = queue.submit("B0003", 6, [], model="fake-model", kind="depth", layers=["model", "panels", "skim"])
        assert started.wait(timeout=5)
        cancelled = queue.cancel(job["id"])
        assert cancelled["state"] == "cancelled"
        assert cancelled["error_kind"] == "cancelled"
        assert cancelled["failed_layer"] is None
        assert [step["status"] for step in cancelled["steps"]] == [
            "done",
            "done",
            "pending",
            "pending",
            "pending",
            "pending",
        ]
    finally:
        release.set()
        queue.shutdown()


def test_non_depth_failure_does_not_show_all_steps_done():
    # 记录页最刺眼的一条是「状态 失败 / 100%」；五步全绿是它的另一半。
    steps = JobQueue._steps_for("failed", "reading", phase="annotating")
    assert [step["status"] for step in steps] == ["done", "failed", "pending", "pending", "pending"]
    assert JobQueue._progress("failed", "reading", (), "annotating") < 100


def test_render_failure_marks_the_render_row():
    # 渲染层传下来的是层键 "render"，不是中文标签，两边一直对不上。
    steps = JobQueue._steps_for(
        "failed",
        "depth",
        layers_ready=["model", "panels", "skim", "standard", "deep"],
        failed_layer="render",
    )
    assert steps[-1] == {"name": "渲染", "status": "failed"}
    assert [step["status"] for step in steps[:-1]] == ["done"] * 5


def test_failure_without_a_layer_still_points_at_a_row():
    # 空选层、章节缺失、重启时不知道跑到哪一层：六行全未跑 + 红字失败，读的人不知道停在哪。
    steps = JobQueue._steps_for("failed", "depth", layers_ready=["model", "panels"])
    assert [step["status"] for step in steps] == [
        "done",
        "done",
        "failed",
        "pending",
        "pending",
        "pending",
    ]


def test_mark_saved_partial_job_does_not_report_full_progress(tmp_path):
    def runner(job_id, job):
        queue.set_layer(job_id, "skim")
        queue.note(job_id, layers_ready=["model", "panels"])
        return {"status": "partial", "pack_id": "B0003/ch/06", "layers_ready": ["model", "panels"]}

    queue = JobQueue(str(tmp_path), runner, max_workers=1)
    try:
        job = queue.submit("B0003", 6, [], kind="depth", layers=["model", "panels", "skim"])
        assert _wait_until(lambda: queue.get(job["id"])["state"] == "partial", timeout=5)
        saved = queue.get(job["id"])
        assert saved["state"] == "partial"
        assert saved["progress"] < 100
    finally:
        queue.shutdown()


def test_events_route_does_not_accept_path_traversal():
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "apps" / "reading" / "server.py"
    spec = importlib.util.spec_from_file_location("reading_server_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    events_route = next(pattern for pattern, name in module.ROUTES if name == "job_events")
    assert events_route.match("/api/jobs/r-68635efa23/events")
    assert not events_route.match("/api/jobs/../etc/events")
    assert not events_route.match("/api/jobs/a%2Fb/events")
    assert not events_route.match("/api/jobs/./events")
