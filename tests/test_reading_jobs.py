"""任务队列：并行上限、排队、取消即时反馈。"""

import threading
import time

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
