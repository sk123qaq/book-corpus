"""结构化事件账：一次任务的每次模型尝试写一行 JSONL。

账在 ``工作区/阅读/logs/<job_id>.jsonl``，只记传输事实——端点、对端地址、各段耗时、
字节数、状态码、错误分类。不记密钥，不记提示词与正文。

记账失败只吞掉，不许把作业拖垮；读服务的 job 线程通过 ``job_scope`` 绑定，
``ModelClient`` 从上下文取当前账，因此调用方不必层层传参。
"""

import contextvars
import json
import threading
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

_CURRENT = contextvars.ContextVar("bookcorpus_reading_events", default=None)
_LOCK = threading.Lock()
# 栈写进账本是为了定位到行，不是为了存档；尾部保留最内层调用，超出部分丢掉。
STACK_LIMIT = 8192
STACK_FRAMES = 10


def _now_iso():
    return datetime.now(UTC).astimezone().isoformat(timespec="milliseconds")


def _safe_name(text):
    # 只留字母数字、下划线与短横：任务号不该带 ``.``、``/`` 或 ``..``。
    keep = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(text))
    return keep or "job"


class EventLog:
    """追加写一份 JSONL。同一进程内多线程共用一个文件，写时加锁。"""

    def __init__(self, directory, job_id="manual"):
        self.directory = Path(directory)
        self.job_id = str(job_id)
        self.path = self.directory / (_safe_name(job_id) + ".jsonl")
        self._closed = False

    def emit(self, kind, **fields):
        if self._closed:
            return
        record = {"ts": _now_iso(), "job": self.job_id, "kind": kind}
        for key, value in fields.items():
            if value is None:
                continue
            record[key] = value
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
            with _LOCK:
                self.directory.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except (OSError, TypeError, ValueError):
            # 记账是旁证，不是作业的一部分。
            return

    def close(self):
        self._closed = True


def path_for(directory, job_id):
    """账文件位置；读接口与写接口共用同一套文件名规则。"""
    return Path(directory) / (_safe_name(job_id) + ".jsonl")


def read(directory, job_id, limit=2000):
    """读回账里最后 ``limit`` 行；坏行跳过，没账返回空列表。"""
    path = path_for(directory, job_id)
    try:
        with path.open(encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


class NullLog:
    """没有绑定账时用。保持同一接口，省掉调用方的分支。"""

    job_id = "-"
    path = None

    def emit(self, kind, **fields):
        return

    def close(self):
        return


_NULL = NullLog()


def _stack_text(exc, frames=STACK_FRAMES):
    """异常的可定位形态：类型、消息、最内层若干帧、因果链。"""
    parts = traceback.format_exception(type(exc), exc, exc.__traceback__)
    text = "".join(parts)
    if len(text) <= STACK_LIMIT:
        return text
    # 超长时留尾部：栈底是出事的那一行，栈顶多是框架。
    return "…(前略)\n" + text[-STACK_LIMIT:]


def _cause_chain(exc):
    chain, seen = [], set()
    cur = exc
    while cur is not None and id(cur) not in seen and len(chain) < 5:
        seen.add(id(cur))
        chain.append(f"{type(cur).__name__}: {(str(cur) or '')[:200]}")
        cur = cur.__cause__ or cur.__context__
    return chain


def record_exception(exc, *, layer=None, extra=None):
    """把一次失败写成可定位的一条账：类型、分类、消息、栈、因果链。

    只在这里写 ``job_failed``，调用方不要再拼 ``str(exc)`` 当账——
    一句英文消息定不了位，栈才能定到行。
    """
    current().emit(
        "job_failed",
        error_class=type(exc).__name__,
        # 模型错误自带 kind；其余崩溃统一记 unexpected，账里不留空字段。
        error_kind=getattr(exc, "kind", None) or "unexpected",
        detail=(str(exc) or type(exc).__name__)[:500],
        layer=layer,
        cause_chain=_cause_chain(exc),
        traceback=_stack_text(exc),
        extra=extra,
    )


def current():
    """当前上下文的任务账；未绑定时返回空账。"""
    found = _CURRENT.get()
    return found if found is not None else _NULL


def bind(log):
    return _CURRENT.set(log)


def unbind(token):
    try:
        _CURRENT.reset(token)
    except (ValueError, RuntimeError):
        return


@contextmanager
def job_scope(directory, job_id):
    """把一份账绑定到当前上下文的生命周期。"""
    log = EventLog(directory, job_id) if directory else NullLog()
    token = bind(log)
    try:
        yield log
    finally:
        unbind(token)
        log.close()
