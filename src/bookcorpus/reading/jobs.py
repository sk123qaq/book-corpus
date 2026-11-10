"""内存队列：最多 ``max_workers`` 个任务并行；jobs.json 整表原子持久化。"""

import json
import os
import threading
import time
import uuid

STEPS = ("切块", "标注", "校验", "组档", "渲染")
DEPTH_STEPS = ("结构", "速览", "标准", "深读", "研究", "引用", "渲染")
STEPS_BY_KIND = {
    "reading": STEPS,
    "edition": ("解析", "编辑", "校验", "渲染", "入库"),
    "depth": DEPTH_STEPS,
}
ACTIVE_STATES = ("queued", "chunking", "annotating", "validating", "projecting", "rendering")
STAGE_TO_STEP = {
    "queued": (0, 0),
    "chunking": (0, 15),
    "annotating": (1, 40),
    "validating": (2, 60),
    "projecting": (3, 75),
    "editorial": (4, 85),
    "rendering": (4, 90),
    "done": (5, 100),
    "partial": (5, 100),
    "failed": (5, 100),
    "cancelled": (0, 0),
}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


class JobQueue:
    """``runner(job_id, job)`` 返回 service 生成结果；异常统一落 failed。

    ``work`` 是 ``工作区``；账写在 ``工作区/阅读/jobs.json``。
    固定大小的 daemon worker 池让最多 4 个任务并行，其余任务保持 queued。
    """

    def __init__(self, work, runner, max_workers=4):
        self.work = work
        self.runner = runner
        self.path = os.path.join(work, "阅读", "jobs.json")
        self.max_workers = max(1, int(max_workers or 1))
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._jobs = {}
        self._order = []
        self._queue = []
        self._running = set()
        self._workers = []
        self._shutdown = False
        self._load()

    # ---- 持久化 -------------------------------------------------------
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, list):
            for job in data:
                if isinstance(job, dict) and job.get("id"):
                    self._jobs[job["id"]] = job
                    self._order.append(job["id"])
            # 上次进程留下的运行态统一标失败，避免永远转圈。
            for job in self._jobs.values():
                if job.get("state") in ACTIVE_STATES:
                    job["state"] = "failed"
                    job["stage"] = "failed"
                    job["error"] = "服务重启，任务中断"
                    job["updated_at"] = _now()
                elif job.get("state") == "done" and job.get("error"):
                    # 旧账：模型失败但退化为 draft 的工单曾被记成 done。
                    job["state"] = "partial"
                    job["stage"] = "partial"
                    job["status"] = "partial"
                    job["updated_at"] = _now()

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = [self._jobs[job_id] for job_id in self._order if job_id in self._jobs]
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # ---- 状态 ---------------------------------------------------------
    @staticmethod
    def _steps_for(state, kind="reading"):
        index, _ = STAGE_TO_STEP.get(state, (0, 0))
        labels = STEPS_BY_KIND.get(kind, STEPS)
        steps = []
        for i, label in enumerate(labels):
            if state == "cancelled":
                status = "pending"
            elif state in ("done", "partial", "failed") or i < index:
                status = "done"
            elif i == index:
                status = "run" if state not in ("failed",) else "failed"
            else:
                status = "pending"
            steps.append({"name": label, "status": status})
        return steps

    def _update(self, job_id, force=False, **changes):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            # 取消是终态：后台线程晚到的 stage/result 不能把取消覆盖回运行态。
            # 只有 ``mark_saved`` 在产物已经落盘时用 force 校正状态。
            if not force and job.get("state") == "cancelled" and changes.get("state") != "cancelled":
                return dict(job)
            job.update(changes)
            job["updated_at"] = _now()
            job["steps"] = self._steps_for(job["state"], job.get("kind"))
            _index, progress = STAGE_TO_STEP.get(job["state"], (0, 0))
            job["stage"] = job["state"]
            if "progress" not in changes:
                job["progress"] = progress
            self._save()
            return dict(job)

    def note(self, job_id, **fields):
        """给运行中的任务补记事实（如标注是复用还是重做），不改阶段。"""
        return self._update(job_id, **fields)

    def set_stage(self, job_id, state):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("state") == "cancelled" or job.get("cancel_requested"):
                return dict(job) if job else None
            return self._update(job_id, state=state)

    # ---- 队列 ---------------------------------------------------------
    def submit(
        self,
        uid,
        n,
        tiers,
        model=None,
        effort=None,
        overwrite=False,
        rebuild=False,
        kind="reading",
        layers=None,
        force_annotation=False,
        allow_unverified=False,
    ):
        job_layers = None
        if kind == "depth":
            # 深度层按显式 layers 生产，不复用四档的“空即 standard”约定。
            tiers = []
            job_layers = list(layers or [])
        else:
            tiers = [tier for tier in ("skim", "standard", "deep", "research") if tier in set(tiers or ["standard"])]
            if not tiers:
                tiers = ["standard"]
        job = {
            "id": "r-" + uuid.uuid4().hex[:10],
            "kind": kind,
            "uid": uid,
            "n": "%02d" % int(n),
            "tiers": tiers,
            "model": model,
            "effort": effort,
            "overwrite": bool(overwrite),
            "rebuild": bool(rebuild),
            "force_annotation": bool(force_annotation),
            "allow_unverified": bool(allow_unverified),
            "state": "queued",
            "stage": "queued",
            "progress": 0,
            "steps": self._steps_for("queued", kind),
            "error": None,
            "created_at": _now(),
            "updated_at": _now(),
            "pack_id": None,
        }
        if kind == "depth":
            job["layers"] = job_layers
        with self._lock:
            self._jobs[job["id"]] = job
            self._order.append(job["id"])
            self._queue.append(job["id"])
            self._save()
            # 新任务入队必须唤醒等在 Condition 上的 worker；否则只有第一个
            # worker 会被首次 submit 唤醒，后续任务永远 queued。
            self._cond.notify_all()
        self._ensure_workers()
        return dict(job)

    def _ensure_workers(self):
        with self._lock:
            self._workers = [worker for worker in self._workers if worker.is_alive()]
            while len(self._workers) < self.max_workers:
                worker = threading.Thread(
                    target=self._loop, name="reading-worker-%d" % (len(self._workers) + 1), daemon=True
                )
                self._workers.append(worker)
                worker.start()

    def _loop(self):
        while True:
            with self._cond:
                while not self._queue and not self._shutdown:
                    self._cond.wait()
                if self._shutdown:
                    return
                job_id = self._queue.pop(0)
                job = self._jobs.get(job_id)
                if not job or job.get("state") == "cancelled":
                    continue
            self._run_one(job_id, job)

    def _is_cancelled_locked(self, job_id):
        job = self._jobs.get(job_id) or {}
        return bool(job.get("cancel_requested")) or job.get("state") == "cancelled"

    def _run_one(self, job_id, job):
        with self._lock:
            if self._is_cancelled_locked(job_id):
                return
            # 已取消但后台线程还没返回的任务仍可能写同一个包目录，
            # 必须继续占住章节，直到这里真正结束（见 conflicts）。
            self._running.add(job_id)
        try:
            try:
                result = self.runner(job_id, job)
            except Exception as exc:  # 生成失败必须留可读账，不炸整个服务
                with self._lock:
                    if self._is_cancelled_locked(job_id):
                        return
                self._update(job_id, state="failed", progress=100, error=str(exc) or exc.__class__.__name__)
                return
            with self._lock:
                cancelled = self._is_cancelled_locked(job_id)
                saved = bool((result or {}).get("pack_id"))
                # 取消后才落盘的结果不能丢：整包已经替换成功，任务按完成校正。
                if cancelled and not saved:
                    return
                status = (result or {}).get("status", "ok")
                state = status if status in ("done", "partial", "failed") else "done"
                error = (result or {}).get("error")
                if error and state == "done":
                    # 模型失败但已落 draft：任务完成了一部分，不是纯成功。
                    state = "partial"
                    status = "partial"
                changes = {
                    "state": state,
                    "status": status,
                    "pack_id": (result or {}).get("pack_id"),
                    "progress": 100,
                    "error": error,
                }
            if cancelled and saved:
                self.mark_saved(job_id, result)
            else:
                self._update(job_id, **changes)
        finally:
            with self._lock:
                self._running.discard(job_id)

    def mark_saved(self, job_id, result=None):
        """产物已落盘时校正任务状态，即使 cancel 先到也不能记成无包取消。"""
        result = result or {}
        status = result.get("status", "ok")
        state = status if status in ("done", "partial", "failed") else "done"
        error = result.get("error")
        if error and state == "done":
            state = "partial"
            status = "partial"
        return self._update(
            job_id,
            force=True,
            state=state,
            status=status,
            pack_id=result.get("pack_id"),
            progress=100,
            error=error,
        )

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self, limit=50):
        with self._lock:
            jobs = [dict(self._jobs[job_id]) for job_id in reversed(self._order) if job_id in self._jobs]
        return jobs[:limit]

    def _is_active_locked(self, job_id, job):
        """排队/运行中，外加已取消但后台线程还没返回的任务。"""
        return job.get("state") in ACTIVE_STATES or job_id in self._running

    def active_for(self, uid=None, n=None):
        """当前章/全站的排队或运行任务，供工单内联状态使用。"""
        normalized = None if n is None else "%02d" % int(n)
        with self._lock:
            out = []
            for job_id in self._order:
                job = self._jobs.get(job_id)
                if not job or not self._is_active_locked(job_id, job):
                    continue
                if uid is not None and job.get("uid") != uid:
                    continue
                if normalized is not None and job.get("n") != normalized:
                    continue
                out.append(dict(job))
            return out

    def conflicts(self, uid, n, tiers=None):
        """同一章只允许一个任务：不同档位也会竞争同一份 staging/manifest。

        取消是终态，但后台线程可能还在写盘；这类任务也留在冲突集里，
        直到 ``_run_one`` 真正返回才释放章节。
        """
        normalized = None if n is None else "%02d" % int(n)
        with self._lock:
            out = []
            for job_id in self._order:
                job = self._jobs.get(job_id)
                if not job or not self._is_active_locked(job_id, job):
                    continue
                if uid is not None and job.get("uid") != uid:
                    continue
                if normalized is not None and job.get("n") != normalized:
                    continue
                out.append(dict(job))
            return out

    def cancel(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job["state"] not in ACTIVE_STATES:
                return dict(job)
            job["cancel_requested"] = True
            if job["state"] == "queued" and job_id in self._queue:
                self._queue.remove(job_id)
            # 立即把状态改成 cancelled，记录页/工单页不用等后台步骤结束才反馈。
            return self._update(
                job_id,
                state="cancelled",
                progress=job.get("progress", 0),
                error="已取消",
            )

    def cancelled(self, job_id):
        with self._lock:
            return self._is_cancelled_locked(job_id)

    def shutdown(self):
        with self._cond:
            self._shutdown = True
            self._cond.notify_all()
