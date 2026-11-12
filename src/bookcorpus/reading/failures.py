"""任务失败现场：把异常携带的原始输出落到工作区，供事后复现。

失败文件不能放进 ``books/<uid>/depth/``：``save_depth`` 会整目录替换，现场会被
下一次成功生成带走。放在 ``工作区/阅读/failures`` 只受本模块的保留策略约束。

写不进去不能连累任务失败：调用方拿到的第二个返回值是失败原因，记进任务账。
"""

import contextlib
import json
import re
import threading
import time
from pathlib import Path

KEEP_FILES = 200
RAW_PAYLOAD_LIMIT = 200 * 1024
_SAFE_RE = re.compile(r"[^0-9A-Za-z_.-]+")
_LOCK = threading.Lock()


def _safe(text):
    return _SAFE_RE.sub("_", str(text or "job"))[:120] or "job"


def _prune(directory):
    try:
        paths = sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        return
    for path in paths[KEEP_FILES:]:
        with contextlib.suppress(OSError):
            path.unlink()


def _dumps(record):
    return json.dumps(record, ensure_ascii=False, indent=1, default=str)


def write_failure(work, job_id, job, exc):
    """写一份失败现场，返回（相对工作区的路径，写失败的原因）。

    成功时第二个返回值为 None；失败时第一个返回值为 None。没有 payload 也要写，
    否则操作台只能看到一个「质量失败」却没有原文可核对。
    """
    payload = getattr(exc, "payload", None)
    layer = job.get("layer") or "job"
    root = Path(work)
    directory = root / "阅读" / "failures"
    path = directory / f"{_safe(job_id)}-{_safe(layer)}.json"
    record = {
        "job_id": job_id,
        "uid": job.get("uid"),
        "n": job.get("n"),
        "layer": layer,
        "error_kind": getattr(exc, "kind", None) or "unexpected",
        "error": str(exc),
        "stats": getattr(exc, "stats", None),
        "raw_payload": payload,
        "failed_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
    }
    try:
        text = _dumps(record)
        if len(text.encode("utf-8")) > RAW_PAYLOAD_LIMIT:
            record["raw_payload"] = None
            record["payload_omitted"] = True
            text = _dumps(record)
        with _LOCK:
            directory.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
            _prune(directory)
    except Exception as exc2:  # 现场写不进去不能连累任务状态，原因交给调用方记录
        return None, f"{type(exc2).__name__}: {exc2}"
    return path.relative_to(root).as_posix(), None
