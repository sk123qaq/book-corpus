"""按需拉起四个本地页面服务。

首页只负责判断端口、起进程、等就绪、交回跳转地址。进程用新会话启动，
不随首页退出；端口已经有人监听时不重复起。日志落在用户缓存目录。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent


@dataclass(frozen=True)
class Service:
    key: str
    name: str
    port: int
    script: str


SERVICES = {
    "search": Service("search", "找一段", 8765, "apps/demo/server.py"),
    "reading": Service("reading", "翻开书", 8771, "apps/reading/server.py"),
    "daily": Service("daily", "往期日课", 8772, "apps/daily/server.py"),
    "ops": Service("ops", "管理", 8770, "apps/ops/server.py"),
}
DEFAULT_TARGETS = {key: f"http://127.0.0.1:{service.port}" for key, service in SERVICES.items()}
START_TIMEOUT = 12.0
_LOCKS = {key: threading.Lock() for key in SERVICES}


def log_path(key: str) -> Path:
    """启动日志放在缓存里，不往书库或工具仓写运行态文件。"""
    base = os.environ.get("BOOK_CORPUS_HOME_LOG_DIR")
    if base:
        path = Path(base)
    else:
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        path = cache / "book-corpus" / "home"
    return path / f"{key}.log"


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    """只探 TCP，不要求对方已经能返回页面。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def wait_for_port(port: int, timeout: float, probe=port_open, proc=None) -> bool:
    """等端口可连；进程先退出就不空等到超时。"""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if proc is not None and proc.poll() is not None:
            return False
        if probe(port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.12)


def command_for(key: str, root) -> tuple[list[str], dict[str, str]]:
    """返回启动命令与需要补的环境变量。只接受已知 key。"""
    service = SERVICES[key]
    command = [
        sys.executable,
        str(REPO / service.script),
        "--port",
        str(service.port),
    ]
    extra_env: dict[str, str] = {"PYTHONUNBUFFERED": "1"}
    if key == "search":
        # 演示页不接 --root，只认 BOOKS_ROOT。
        extra_env["BOOKS_ROOT"] = str(root)
    else:
        command.extend(["--root", str(root)])
    return command, extra_env


def is_local_target(key: str, target: str) -> bool:
    """自定义到别的地址时只跳转，不代起本地服务。"""
    service = SERVICES.get(key)
    if service is None:
        return False
    try:
        parts = urlsplit(str(target))
        if parts.scheme not in ("http", ""):
            return False
        if (parts.hostname or "").lower() not in ("127.0.0.1", "localhost"):
            return False
        port = parts.port if parts.port is not None else 80
    except ValueError:
        return False
    return port == service.port


def same_target(left: str, right: str) -> bool:
    return str(left).rstrip("/") == str(right).rstrip("/")


def ensure(
    key: str,
    root,
    *,
    timeout: float = START_TIMEOUT,
    allow_start: bool = True,
    probe=port_open,
) -> dict:
    """确保服务在跑；返回 already / started / disabled / failed。

    probe 可注入，测试不必真的监听 8765。返回值里的 command 是列表，
    页面显示前再转义。
    """
    service = SERVICES.get(key)
    if service is None:
        raise KeyError(key)

    if probe(service.port):
        return {"state": "already", "service": service, "log": log_path(key)}
    if not allow_start:
        return {"state": "disabled", "service": service, "log": log_path(key)}

    with _LOCKS[key]:
        # 等锁期间别的请求可能已经拉起来了。
        if probe(service.port):
            return {"state": "already", "service": service, "log": log_path(key)}

        path = log_path(key)
        command, extra_env = command_for(key, root)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as handle:
                # 命令来自固定白名单，不接受页面参数。
                proc = subprocess.Popen(
                    command,
                    cwd=str(REPO),
                    stdin=subprocess.DEVNULL,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env={**os.environ, **extra_env},
                )
        except OSError as exc:
            return {
                "state": "failed",
                "service": service,
                "error": str(exc),
                "command": command,
                "log": path,
            }

        if wait_for_port(service.port, timeout, probe=probe, proc=proc):
            return {
                "state": "started",
                "service": service,
                "pid": proc.pid,
                "command": command,
                "log": path,
            }

        return {
            "state": "failed",
            "service": service,
            "pid": proc.pid,
            "returncode": proc.poll(),
            "error": f"服务在 {timeout:.1f} 秒内没有就绪。",
            "command": command,
            "log": path,
        }
