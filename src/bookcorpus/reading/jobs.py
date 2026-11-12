"""内存队列：最多 ``max_workers`` 个任务并行；jobs.json 整表原子持久化。"""

import json
import os
import threading
import time
import uuid

from . import events
from .store import DEPTH_LAYERS

STEPS = ("切块", "标注", "校验", "组档", "渲染")
DEPTH_STEPS = ("结构", "面板", "速览", "标准", "深读", "渲染")
STEPS_BY_KIND = {
    "reading": STEPS,
    "edition": ("解析", "编辑", "校验", "渲染", "入库"),
    "depth": DEPTH_STEPS,
}
# 账本要能说出「第 3/6 层 · 速览」，所以层名和步骤名得能互相翻译。
STEP_LAYERS = {"model": "结构", "panels": "面板", "skim": "速览", "standard": "标准", "deep": "深读"}
RENDER_STEP = "渲染"
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
        self.log_dir = os.path.join(work, "阅读", "logs")
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
                    job["error_kind"] = "restart"
                    job["failed_layer"] = job.get("layer")
                    job["updated_at"] = _now()
                    job["steps"] = self._steps_for(
                        "failed",
                        job.get("kind"),
                        layer=job.get("layer"),
                        layers_ready=job.get("layers_ready") or (),
                        failed_layer=job.get("failed_layer"),
                    )
                    job["progress"] = self._progress(
                        "failed", job.get("kind"), job.get("layers_ready") or (), job.get("phase")
                    )
                    changed = True
                elif job.get("state") == "done" and job.get("error"):
                    # 旧账：模型失败但退化为 draft 的工单曾被记成 done。
                    job["state"] = "partial"
                    job["stage"] = "partial"
                    job["status"] = "partial"
                    job["updated_at"] = _now()
                    changed = True
        if changed:
            # 落一次盘：否则进程再崩，磁盘上还是那份「运行中」的旧账。
            self._save()

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = [self._jobs[job_id] for job_id in self._order if job_id in self._jobs]
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # ---- 状态 ---------------------------------------------------------
    @staticmethod
    def _steps_for(state, kind="reading", *, layer=None, layers_ready=(), failed_layer=None, phase=None):
        labels = STEPS_BY_KIND.get(kind, STEPS)
        if kind != "depth":
            # 失败/部分失败不能把五步全标成完成：那正是「100% + 失败」的来源。
            index, _ = STAGE_TO_STEP.get(phase or state, (0, 0))
            steps = []
            for i, label in enumerate(labels):
                if state == "done":
                    status = "done"
                elif state == "cancelled":
                    status = "done" if i < index else "pending"
                elif state == "failed":
                    status = "failed" if i == index else ("done" if i < index else "pending")
                elif state == "partial":
                    status = "done" if i <= index else "pending"
                elif i < index:
                    status = "done"
                elif i == index:
                    status = "run"
                else:
                    status = "pending"
                steps.append({"name": label, "status": status})
            return steps
        # 深度任务的步骤只能按「哪几层真的在盘上」来标，不能按阶段猜。
        ready = {STEP_LAYERS[key] for key in layers_ready or () if key in STEP_LAYERS}
        current = STEP_LAYERS.get(layer)
        broken = STEP_LAYERS.get(failed_layer) if failed_layer else current
        if failed_layer in ("render", RENDER_STEP):
            broken = RENDER_STEP
        steps = []
        for label in labels:
            if label == RENDER_STEP:
                if broken == RENDER_STEP:
                    status = "failed"
                elif state == "done":
                    status = "done"
                elif state == "rendering":
                    status = "run"
                else:
                    status = "pending"
            elif label in ready:
                status = "done"
            elif state == "failed" and label == broken:
                status = "failed"
            elif not ready and state == "done":
                # 旧账没有 layers_ready，不能把历史上成功的任务全标成未做。
                status = "done"
            elif label == current and state in ACTIVE_STATES:
                status = "run"
            else:
                status = "pending"
            steps.append({"name": label, "status": status})
        if state == "failed" and not any(step["status"] == "failed" for step in steps):
            # 没有层内失败（例如空选层、章节缺失、重启时 layer 为空）时也要指出断点，
            # 否则页面写着失败、六行却全是未跑，读的人不知道停在哪。
            for step in steps:
                if step["status"] != "done":
                    step["status"] = "failed"
                    break
        return steps

    @staticmethod
    def _progress(state, kind="reading", layers_ready=(), phase=None):
        if kind != "depth":
            if state == "done":
                return 100
            # 表里的 failed/partial 都是 100，未完成的任务不许显示满格；
            # 完成度按最后跑到的阶段算（phase 在任务结束时停在最后阶段）。
            value = STAGE_TO_STEP.get(phase or state, (0, 0))[1]
            return min(value, 99) if state in ("partial", "failed") else value
        if state == "done":
            return 100
        done = len(set(layers_ready or ()) & set(DEPTH_LAYERS))
        pct = round(done / len(DEPTH_STEPS) * 100)
        # 未完成的任务不许显示 100%，否则记录页的「100% + 部分通过」自相矛盾。
        return min(pct, 99) if state in ("partial", "failed") else pct

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
            job["steps"] = self._steps_for(
                job["state"],
                job.get("kind"),
                layer=job.get("layer"),
                layers_ready=job.get("layers_ready") or (),
                failed_layer=job.get("failed_layer"),
                phase=job.get("phase"),
            )
            job["stage"] = job["state"]
            if job["state"] in ACTIVE_STATES:
                # 失败后 state/stage 都变成 failed，phase 留着最后跑到哪一阶段。
                job["phase"] = job["state"]
            if "progress" not in changes:
                job["progress"] = self._progress(
                    job["state"], job.get("kind"), job.get("layers_ready") or (), job.get("phase")
                )
            self._save()
            return dict(job)

    def note(self, job_id, **fields):
        """给运行中的任务补记事实（如标注是复用还是重做），不改阶段。"""
        return self._update(job_id, **fields)

    def set_layer(self, job_id, layer, layers_ready=None):
        """记下正在跑哪一层、哪几层已经落盘，步骤表和进度都从这两个字段推。"""
        changes = {"layer": layer}
        if layers_ready is not None:
            changes["layers_ready"] = sorted(set(layers_ready) & set(DEPTH_LAYERS))
        return self._update(job_id, **changes)

    def events_path(self, job_id):
        """原始账的磁盘位置；返回值要进 JSON，所以是 str 不是 Path。"""
        return str(events.path_for(self.log_dir, job_id))

    def events(self, job_id, limit=2000):
        """读回原始账；没有账就是空列表，不报错。"""
        return events.read(self.log_dir, job_id, limit=limit)

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
            # 深度层按显式 layers 生产，不复用三档的“空即 standard”约定。
            tiers = []
            job_layers = list(layers or [])
        else:
            tiers = [tier for tier in ("skim", "standard", "deep") if tier in set(tiers or ["standard"])]
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
            "phase": "queued",
            "progress": 0,
            "steps": self._steps_for("queued", kind),
            "error": None,
            "error_kind": None,
            "failed_layer": None,
            "attempts": None,
            "elapsed_ms": None,
            "layers_ready": [],
            "skipped": [],
            "created_at": _now(),
            "updated_at": _now(),
            "pack_id": None,
        }
        if kind == "depth":
            job["layers"] = job_layers
            job["layer"] = None
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
        started = time.monotonic()
        with self._lock:
            if self._is_cancelled_locked(job_id):
                return
            # 已取消但后台线程还没返回的任务仍可能写同一个包目录，
            # 必须继续占住章节，直到这里真正结束（见 conflicts）。
            self._running.add(job_id)
        try:
            try:
                with events.job_scope(self.log_dir, job_id):
                    result = self.runner(job_id, job)
            except Exception as exc:  # 生成失败必须留可读账，不炸整个服务
                with self._lock:
                    if self._is_cancelled_locked(job_id):
                        return
                    current = self._jobs.get(job_id) or {}
                    self._update(
                        job_id,
                        state="failed",
                        error=str(exc) or exc.__class__.__name__,
                        error_kind=getattr(exc, "kind", None),
                        attempts=getattr(exc, "attempt", None),
                        failed_layer=current.get("layer"),
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                    )
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
                    "error": error,
                    "skipped": (result or {}).get("skipped") or [],
                    "layers_ready": (result or {}).get("layers_ready") or [],
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
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
        job = self.get(job_id) or {}
        progress = self._progress(
            state,
            job.get("kind"),
            result.get("layers_ready") or job.get("layers_ready") or (),
            job.get("phase"),
        )
        return self._update(
            job_id,
            force=True,
            state=state,
            status=status,
            pack_id=result.get("pack_id"),
            layers_ready=result.get("layers_ready") or job.get("layers_ready"),
            progress=progress,
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
            # 取消不是失败：已落盘的层要留着，失败层与错误类别要清掉，
            # 否则步骤条会指着一层说它失败了，而它只是没跑完。
            keep = list(job.get("layers_ready") or [])
            return self._update(
                job_id,
                state="cancelled",
                progress=job.get("progress", 0),
                error="已取消",
                error_kind="cancelled",
                failed_layer=None,
                layers_ready=keep,
                steps=self._steps_for(
                    "cancelled",
                    job.get("kind") or "reading",
                    layers_ready=keep,
                    phase=job.get("phase"),
                ),
            )

    def cancelled(self, job_id):
        with self._lock:
            return self._is_cancelled_locked(job_id)

    def shutdown(self):
        with self._cond:
            self._shutdown = True
            self._cond.notify_all()
