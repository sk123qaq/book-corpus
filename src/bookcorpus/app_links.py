"""跨应用服务注册、端口探测、按需拉起与安全深链跳板。

将原先分散在 apps/home/launcher.py 与 apps/ops/server.py 的服务启停逻辑收口为一处，
并用跨进程文件锁避免多服务并发冷启动时重复拉起同一进程。
"""

from __future__ import annotations

import fcntl
import html
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

REPO = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Service:
    """本地页面服务定义。"""

    key: str
    name: str
    port: int
    script: str
    env_var: str


SERVICES: dict[str, Service] = {
    "search": Service("search", "找一段", 8765, "apps/demo/server.py", "BOOK_CORPUS_SEARCH_URL"),
    "reading": Service("reading", "翻开书", 8771, "apps/reading/server.py", "BOOK_CORPUS_READING_URL"),
    "daily": Service("daily", "往期日课", 8772, "apps/daily/server.py", "BOOK_CORPUS_DAILY_URL"),
    "ops": Service("ops", "管理", 8770, "apps/ops/server.py", "BOOK_CORPUS_OPS_URL"),
    "home": Service("home", "首页", 8773, "apps/home/server.py", "BOOK_CORPUS_HOME_URL"),
}

DEFAULT_TARGETS: dict[str, str] = {key: f"http://127.0.0.1:{svc.port}" for key, svc in SERVICES.items()}
START_TIMEOUT = 12.0
_LOCKS: dict[str, threading.Lock] = {key: threading.Lock() for key in SERVICES}


def log_path(key: str) -> Path:
    """启动日志放在用户缓存目录，不污染书库或代码仓。"""
    base = os.environ.get("BOOK_CORPUS_HOME_LOG_DIR")
    if base:
        path = Path(base)
    else:
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        path = cache / "book-corpus" / "home"
    return path / f"{key}.log"


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    """只探 TCP 监听状态，不要求页面内容返回。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def wait_for_port(
    port: int,
    timeout: float,
    probe: Callable[[int], bool] = port_open,
    proc: subprocess.Popen[bytes] | subprocess.Popen[str] | None = None,
) -> bool:
    """等端口可连；若子进程已提前退出则立即失败，不空等到超时。"""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if proc is not None and proc.poll() is not None:
            return False
        if probe(port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.12)


def command_for(key: str, root: str | Path) -> tuple[list[str], dict[str, str]]:
    """返回子进程启动命令与附加环境变量。只接受已知 key。"""
    service = SERVICES[key]
    root_str = str(root)
    command = [
        sys.executable,
        str(REPO / service.script),
        "--port",
        str(service.port),
        "--root",
        root_str,
    ]
    extra_env: dict[str, str] = {"PYTHONUNBUFFERED": "1"}
    if key == "search":
        extra_env["BOOKS_ROOT"] = root_str
    return command, extra_env


def is_local_target(key: str, target: str) -> bool:
    """判断 target 是否指向本机默认端口；自定义地址时只做跳转不代起本地服务。"""
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
    """比较两个基础 URL（忽略末尾斜杠）。"""
    return str(left).rstrip("/") == str(right).rstrip("/")


def safe_rel_path(raw: str | None) -> str:
    """仅允许站内相对路径（以单个 `/` 开头），防止开放重定向。"""
    text = (raw or "").strip()
    if not text:
        return "/"
    if not text.startswith("/") or text.startswith("//") or "\\" in text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme or parsed.netloc:
        return ""
    return text


def join_target(base_url: str, rel_path: str) -> str:
    """将安全校验后的站内路径拼到目标服务根地址后，并确保 Location 仅含 ASCII 安全字符。"""
    clean_base = str(base_url).rstrip("/")
    clean_rel = safe_rel_path(rel_path)
    if not clean_rel or clean_rel == "/":
        return clean_base + "/"
    parts = urlsplit(clean_rel)
    path_enc = quote(parts.path or "/", safe="/:@-._~!$&'()*+,;=%")
    query_enc = quote(parts.query, safe="=&/:@-._~!*()+,;%")
    frag_enc = quote(parts.fragment, safe="-._~!*()+,;:@/?=%")
    out = clean_base + path_enc
    if parts.query:
        out += "?" + query_enc
    if parts.fragment:
        out += "#" + frag_enc
    return out


def resolve_targets(overrides: Mapping[str, str | None] | None = None) -> dict[str, str]:
    """按 显式覆盖 > 环境变量 > 默认本地端口 的优先级生成各服务根地址。"""
    resolved: dict[str, str] = {}
    for key, svc in SERVICES.items():
        explicit = overrides.get(key) if overrides else None
        raw = explicit or os.environ.get(svc.env_var) or DEFAULT_TARGETS[key]
        resolved[key] = str(raw).rstrip("/")
    return resolved


def ensure(
    key: str,
    root: str | Path,
    *,
    timeout: float = START_TIMEOUT,
    allow_start: bool = True,
    probe: Callable[[int], bool] = port_open,
    spawn: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> dict[str, object]:
    """确保目标本地服务已在监听；如未运行则在锁保护下按需拉起。"""
    if key not in SERVICES:
        raise KeyError(key)
    service = SERVICES[key]
    if probe(service.port):
        return {"ok": True, "state": "already", "service": service, "log": log_path(key)}
    if not allow_start:
        return {
            "ok": False,
            "state": "disabled",
            "service": service,
            "log": log_path(key),
            "error": f"「{service.name}」({service.port}) 未运行，且已开启 --no-start。",
        }

    with _LOCKS[key]:
        if probe(service.port):
            return {"ok": True, "state": "already", "service": service, "log": log_path(key)}

        log = log_path(key)
        log.parent.mkdir(parents=True, exist_ok=True)
        lock_file = log.with_suffix(".lock")
        with lock_file.open("a+", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                if probe(service.port):
                    return {"ok": True, "state": "already", "service": service, "log": log}
                command, extra_env = command_for(key, root)
                env = dict(os.environ)
                env.update(extra_env)
                try:
                    with log.open("a", encoding="utf-8") as fh:
                        fh.write(f"\n=== start {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(command)} ===\n")
                        proc = spawn(
                            command,
                            cwd=str(REPO),
                            env=env,
                            stdin=subprocess.DEVNULL,
                            stdout=fh,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                            close_fds=True,
                        )
                except OSError as exc:
                    return {
                        "ok": False,
                        "state": "failed",
                        "service": service,
                        "command": command,
                        "log": log,
                        "error": str(exc),
                    }
                if wait_for_port(service.port, timeout, probe=probe, proc=proc):
                    return {
                        "ok": True,
                        "state": "started",
                        "service": service,
                        "pid": proc.pid,
                        "command": command,
                        "log": log,
                    }
                code = proc.poll()
                reason = f"进程已退出（code={code}）" if code is not None else f"超过 {timeout:g} 秒仍未监听端口"
                return {
                    "ok": False,
                    "state": "failed",
                    "service": service,
                    "pid": proc.pid,
                    "returncode": code,
                    "command": command,
                    "log": log,
                    "error": f"「{service.name}」({service.port}) 没起来：{reason}。日志：{log}",
                }
            finally:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def resolve_go_redirect(
    key: str,
    rel_to: str,
    *,
    root: str | Path,
    targets: Mapping[str, str],
    allow_start: bool = True,
    timeout: float = START_TIMEOUT,
    delegate_to_home: bool = True,
    probe: Callable[[int], bool] = port_open,
) -> tuple[int, str, str]:
    """统一处理 /go/<key>?to=... 跳转逻辑。

    返回 ``(status_code, location_or_empty, error_html_or_empty)``。
    """
    if key not in SERVICES or key not in targets:
        return 404, "", render_bridge_error("未知服务", f"未注册的跳转目标：{key}")
    clean_to = safe_rel_path(rel_to)
    if not clean_to:
        return 400, "", render_bridge_error("非法跳转路径", "仅允许以单个 `/` 开头的站内相对路径。")

    target = str(targets[key]).rstrip("/")
    if not is_local_target(key, target):
        return 302, join_target(target, clean_to), ""

    service = SERVICES[key]
    if probe(service.port):
        return 302, join_target(target, clean_to), ""

    home_target = str(targets.get("home") or DEFAULT_TARGETS["home"]).rstrip("/")
    if delegate_to_home and key != "home" and is_local_target("home", home_target) and probe(SERVICES["home"].port):
        return 302, f"{home_target}/go/{key}?to={quote(clean_to, safe='')}", ""

    result = ensure(key, root, timeout=timeout, allow_start=allow_start, probe=probe)
    if result.get("ok"):
        return 302, join_target(target, clean_to), ""

    err_text = str(result.get("error") or f"服务「{service.name}」未就绪")
    hint_cmd = f"uv run --frozen python {service.script} --root {root} --port {service.port}"
    return 503, "", render_bridge_error(f"无法打开「{service.name}」", f"{err_text}\n可手动运行：{hint_cmd}")


def render_bridge_error(title: str, detail: str) -> str:
    """跨应用跳转失败时的统一友好 HTML 提示页。"""
    esc_title = html.escape(title)
    esc_detail = html.escape(detail).replace("\n", "<br>")
    return (
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
        f"<title>{esc_title}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:600px;margin:10vh auto;padding:0 24px;"
        "line-height:1.7;color:#1f2328;background:#faf8f3}h1{font-size:20px;margin:0 0 12px}"
        "p{font-size:14px;color:#57606a}a{color:#0969da}</style>"
        f"<h1>{esc_title}</h1><p>{esc_detail}</p>"
        "<p><a href='javascript:history.back()'>← 返回上一页</a></p></html>"
    )
