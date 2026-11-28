"""移动网关进程托管、配置持久化（0o600）、多网卡探测与手机局域网分享拉取。"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bookcorpus.scripts.lib_common import ACCEPT_EXTS

from .qr import render_svg

DEFAULT_PORT = 8780
DEFAULT_BIND = "0.0.0.0"  # noqa: S104
DEFAULT_SEARCH_URL = "http://127.0.0.1:8765"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_COMPANION_BYTES = 32 * 1024 * 1024
MAX_LAN_BOOK_BYTES = 512 * 1024 * 1024
SHARE_PACKAGE_EXTS = frozenset({".bcnote", ".bcshare"})

_VIRTUAL_IFACE_PREFIXES = (
    "lo",
    "singbox",
    "tun",
    "tap",
    "docker",
    "br-",
    "virbr",
    "veth",
    "cni",
    "flannel",
    "kube",
    "podman",
    "wg",
    "zt",
)
_TAILSCALE_NET = ipaddress.IPv4Network("100.64.0.0/10")
_LINK_LOCAL_NET = ipaddress.IPv4Network("169.254.0.0/16")
_SAFE_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
_SAFE_FILENAME_STRIP_RE = re.compile(r"[^A-Za-z0-9._\u4e00-\u9fa5 -]+")


def _now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.is_file():
        try:
            text = stat_path.read_text(encoding="utf-8", errors="replace")
            tail = text[text.rfind(")") + 1 :].split()
            if tail and tail[0] in {"Z", "X", "x"}:
                return False
        except OSError:
            return False
    return True


def _cmdline_of(pid: int) -> list[str]:
    cmd_path = Path(f"/proc/{pid}/cmdline")
    try:
        raw = cmd_path.read_bytes()
    except OSError:
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def _is_gateway_pid(pid: int) -> bool:
    if not _pid_alive(pid):
        return False
    parts = _cmdline_of(pid)
    return any("bookcorpus.mobile_gateway" in part for part in parts)


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def is_private_or_local_host(host: str) -> bool:
    """校验目标主机是否为私网/回环/mDNS 地址，防止 SSRF 访问公网或云元数据端点。"""
    clean = host.strip().lower().strip("[]")
    if not clean:
        return False
    if clean in {"localhost", "127.0.0.1", "::1"}:
        return True
    if clean.endswith((".local", ".lan", ".home.arpa")):
        return True
    try:
        ip = ipaddress.ip_address(clean)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        if ip in _LINK_LOCAL_NET:
            return False
        if ip in _TAILSCALE_NET:
            return True
        return ip.is_private or ip.is_loopback
    return ip.is_loopback or ip.is_private


def normalize_pairing_code(raw: str) -> str:
    """归一化 6 位局域网配对码（去连字符与空格）。"""
    return re.sub(r"[\s-]+", "", str(raw or "").strip())


def sanitize_upload_filename(raw_name: str, *, fallback: str = "book.epub") -> str:
    """清洗上传或分享包里的文件名，防路径穿越与非法控制字符。"""
    decoded = urllib.parse.unquote(str(raw_name or "")).replace("\\", "/").split("/")[-1].strip()
    if not decoded or decoded in {".", ".."} or decoded.startswith(".") or ".." in decoded:
        return fallback
    if any(ch in decoded for ch in "\x00\r\n"):
        return fallback
    cleaned = _SAFE_FILENAME_STRIP_RE.sub("_", decoded).strip(" .")
    return cleaned or fallback


def build_pair_uri(base_url: str, token: str) -> str:
    """构造标准 URL 编码的 ``bc-pair://v1`` 配对 URI。"""
    encoded_base = urllib.parse.quote(base_url.rstrip("/"), safe="")
    encoded_token = urllib.parse.quote(token.strip(), safe="")
    return f"bc-pair://v1?base={encoded_base}&token={encoded_token}"


def enumerate_host_candidates() -> dict[str, Any]:
    """枚举本机候选局域网/mDNS/Tailscale/回环地址并过滤虚拟 TUN/容器接口。"""
    candidates: list[dict[str, Any]] = []
    filtered: list[dict[str, str]] = []
    seen_hosts: set[str] = set()

    def add_candidate(host: str, kind: str, iface: str, label: str, note: str) -> None:
        if not host or host in seen_hosts:
            return
        seen_hosts.add(host)
        candidates.append({"host": host, "kind": kind, "iface": iface, "label": label, "note": note})

    lan_rows: list[tuple[str, str]] = []
    tailscale_rows: list[tuple[str, str]] = []

    ip_bin = shutil.which("ip")
    if ip_bin:
        try:
            proc = subprocess.run(
                [ip_bin, "-4", "-j", "addr", "show"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if proc.returncode == 0:
                ifaces = json.loads(proc.stdout)
                if isinstance(ifaces, list):
                    for entry in ifaces:
                        if not isinstance(entry, dict):
                            continue
                        ifname = str(entry.get("ifname") or "")
                        lower_name = ifname.lower()
                        for info in entry.get("addr_info") or []:
                            if not isinstance(info, dict) or info.get("family") != "inet":
                                continue
                            addr_str = str(info.get("local") or "").strip()
                            if not addr_str:
                                continue
                            try:
                                ip_obj = ipaddress.IPv4Address(addr_str)
                            except ValueError:
                                continue
                            if ip_obj.is_loopback or ip_obj in _LINK_LOCAL_NET:
                                continue
                            if lower_name.startswith("tailscale") or ip_obj in _TAILSCALE_NET:
                                tailscale_rows.append((addr_str, ifname))
                                continue
                            if any(lower_name.startswith(pref) for pref in _VIRTUAL_IFACE_PREFIXES):
                                filtered.append({"host": addr_str, "iface": ifname})
                                continue
                            if ip_obj.is_private:
                                lan_rows.append((addr_str, ifname))
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            pass

    if not lan_rows:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("192.0.2.1", 80))
                probe_ip = str(sock.getsockname()[0])
            ip_obj = ipaddress.IPv4Address(probe_ip)
            if ip_obj.is_private and not ip_obj.is_loopback and ip_obj not in _LINK_LOCAL_NET:
                lan_rows.append((probe_ip, "default"))
        except (OSError, ValueError):
            pass

    for addr_str, ifname in lan_rows:
        add_candidate(addr_str, "lan", ifname, f"局域网 {addr_str}（{ifname}）", "同 Wi-Fi 局域网直连（推荐）")

    hostname = socket.gethostname().strip()
    if hostname and hostname.lower() not in {"localhost", "localhost.localdomain"}:
        mdns_host = hostname if hostname.endswith(".local") else f"{hostname}.local"
        add_candidate(mdns_host, "mdns", "mDNS", f"本地域名 {mdns_host}", "Android 原生白名单可用，需路由器支持 mDNS")

    for addr_str, ifname in tailscale_rows:
        add_candidate(addr_str, "tailscale", ifname, f"Tailscale {addr_str}（{ifname}）", "跨网安全组网直连")

    add_candidate("127.0.0.1", "loopback", "lo", "本机回环 127.0.0.1", "USB adb reverse tcp:<端口> tcp:<端口> 专用")
    return {"candidates": candidates, "filtered": filtered}


def _atomic_write_json(path: Path, payload: dict[str, Any], *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{uuid.uuid4().hex[:8]}")
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if mode is not None:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            os.write(fd, text.encode("utf-8"))
        finally:
            os.close(fd)
        with contextlib.suppress(OSError):
            tmp.chmod(mode)
    else:
        tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    if mode is not None:
        with contextlib.suppress(OSError):
            path.chmod(mode)


def _redact_log_text(text: str, token: str) -> str:
    out = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._-]+", r"\1[redacted]", text)
    out = re.sub(r"(?i)(token=)[^&\s]+", r"\1[redacted]", out)
    if token and len(token) >= 8:
        out = out.replace(token, "[redacted]")
    return out


def _inspect_share_zip(zip_path: Path) -> dict[str, Any]:
    """安全解析 .bcnote / .bcshare 压缩包里的 share_manifest.json 与原书条目。"""
    manifest: dict[str, Any] = {}
    book_entry_name = ""
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            name = info.filename
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"分享包内包含非法路径: {name}")
        if "share_manifest.json" in zf.namelist():
            raw = zf.read("share_manifest.json")
            if len(raw) > MAX_MANIFEST_BYTES:
                raise ValueError("share_manifest.json 超过大小上限")
            doc = json.loads(raw.decode("utf-8"))
            if isinstance(doc, dict):
                manifest = doc
        for info in zf.infolist():
            if not info.is_dir() and info.filename.startswith("book/"):
                book_entry_name = info.filename
                break
    return {"manifest": manifest, "book_entry": book_entry_name}


class MobileGatewayManager:
    """管理移动网关配置、Token 持久化/轮换、子进程生命周期与手机收件箱。"""

    def __init__(
        self,
        root: Path | str,
        *,
        search_url: str = DEFAULT_SEARCH_URL,
        port_override: int | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.search_url = search_url.rstrip("/")
        self.port_override = int(port_override) if port_override else None
        self.ops_dir = self.root / "工作区" / "运维"
        self.config_path = self.ops_dir / "mobile_gateway.json"
        self.pid_path = self.ops_dir / "mobile_gateway.pid"
        self.log_path = self.ops_dir / "mobile_gateway.log"
        self.mobile_inbox_dir = self.root / "工作区" / "阅读" / "mobile_inbox"
        self.books_inbox_dir = self.root / "books" / "_收件箱"
        self._lock = threading.RLock()

    def _default_config(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "token": secrets.token_hex(32),
            "bind": DEFAULT_BIND,
            "port": self.port_override or DEFAULT_PORT,
            "preferred_host": "",
            "custom_host": "",
            "allow_upload": True,
            "updated_at": _now_iso(),
        }

    def load_config(self) -> dict[str, Any]:
        """读取或初始化移动网关配置（`0o600` 持久化）。"""
        with self._lock:
            changed = False
            cfg = self._default_config()
            if self.config_path.is_file():
                try:
                    raw = json.loads(self.config_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    raw = None
                if isinstance(raw, dict):
                    token = str(raw.get("token") or "").strip()
                    if len(token) >= 16:
                        cfg["token"] = token
                    else:
                        changed = True
                    bind = str(raw.get("bind") or DEFAULT_BIND).strip()
                    cfg["bind"] = bind if bind in {"0.0.0.0", "127.0.0.1"} else DEFAULT_BIND  # noqa: S104
                    try:
                        port = int(raw.get("port") or DEFAULT_PORT)
                    except (TypeError, ValueError):
                        port = DEFAULT_PORT
                    cfg["port"] = port if 1024 <= port <= 65535 else DEFAULT_PORT
                    cfg["preferred_host"] = str(raw.get("preferred_host") or "").strip()
                    cfg["custom_host"] = str(raw.get("custom_host") or "").strip()
                    cfg["allow_upload"] = bool(raw.get("allow_upload", True))
                    cfg["updated_at"] = str(raw.get("updated_at") or _now_iso())
                else:
                    changed = True
            else:
                changed = True

            if self.port_override is not None and cfg["port"] != self.port_override:
                cfg["port"] = self.port_override
                changed = True

            if changed:
                _atomic_write_json(self.config_path, cfg, mode=0o600)
            return cfg

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        """更新配置并落盘；如端口/绑定/上传开关变化且网关正在运行则自动重启。"""
        with self._lock:
            cfg = self.load_config()
            old_runtime = (cfg["bind"], cfg["port"], cfg["allow_upload"])

            if "bind" in updates:
                bind = str(updates["bind"] or "").strip()
                if bind not in {"0.0.0.0", "127.0.0.1"}:  # noqa: S104
                    raise ValueError("bind 仅支持 0.0.0.0（局域网）或 127.0.0.1（仅本机）")
                cfg["bind"] = bind
            if "port" in updates and self.port_override is None:
                try:
                    port = int(updates["port"])
                except (TypeError, ValueError) as exc:
                    raise ValueError("port 必须是 1024–65535 之间的整数") from exc
                if not (1024 <= port <= 65535):
                    raise ValueError("port 必须在 1024–65535 之间")
                cfg["port"] = port
            if "preferred_host" in updates:
                cfg["preferred_host"] = str(updates["preferred_host"] or "").strip()[:128]
            if "custom_host" in updates:
                custom = str(updates["custom_host"] or "").strip()[:128]
                if custom and any(ch in custom for ch in " /\\?#@\r\n"):
                    raise ValueError("自定义主机地址不能包含空白或路径字符")
                cfg["custom_host"] = custom
            if "allow_upload" in updates:
                cfg["allow_upload"] = bool(updates["allow_upload"])

            cfg["updated_at"] = _now_iso()
            _atomic_write_json(self.config_path, cfg, mode=0o600)

            restarted = False
            new_runtime = (cfg["bind"], cfg["port"], cfg["allow_upload"])
            if new_runtime != old_runtime and self._managed_pid() is not None:
                res = self.restart()
                restarted = bool(res.get("ok"))
            return {"ok": True, "restarted": restarted, "status": self.status()}

    def _read_pid(self) -> int | None:
        if not self.pid_path.is_file():
            return None
        try:
            text = self.pid_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return int(text) if text.isdigit() and int(text) > 0 else None

    def _managed_pid(self) -> int | None:
        pid = self._read_pid()
        if pid is None:
            return None
        if _is_gateway_pid(pid):
            return pid
        with contextlib.suppress(OSError):
            self.pid_path.unlink(missing_ok=True)
        return None

    def _probe_health(self, port: int, token: str, *, timeout: float = 0.8) -> tuple[bool, int | None, str]:
        url = f"http://127.0.0.1:{port}/api/mobile/v1/health"
        req = urllib.request.Request(url, method="GET", headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                body = json.loads(resp.read().decode("utf-8"))
                return bool(resp.status == 200 and isinstance(body, dict) and body.get("ok")), int(resp.status), ""
        except urllib.error.HTTPError as exc:
            return False, int(exc.code), f"HTTP {exc.code}"
        except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            return False, None, str(exc)

    def _probe_search_health(self) -> bool:
        url = f"{self.search_url}/api/health"
        try:
            with urllib.request.urlopen(url, timeout=0.6) as resp:  # noqa: S310
                return bool(resp.status == 200)
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    def _resolve_active_host(self, cfg: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
        if cfg.get("bind") == "127.0.0.1":
            return "127.0.0.1"
        custom = str(cfg.get("custom_host") or "").strip()
        if custom:
            return custom
        preferred = str(cfg.get("preferred_host") or "").strip()
        if preferred and any(item["host"] == preferred for item in candidates):
            return preferred
        if candidates:
            return str(candidates[0]["host"])
        return "127.0.0.1"

    def log_tail(self, *, lines: int = 50, token: str = "") -> str:
        """读取脱敏后的移动网关日志尾部。"""
        if not self.log_path.is_file():
            return ""
        try:
            size = self.log_path.stat().st_size
            with self.log_path.open("rb") as fh:
                if size > 64_000:
                    fh.seek(size - 64_000)
                raw = fh.read().decode("utf-8", "replace")
        except OSError:
            return ""
        tail = "\n".join(raw.splitlines()[-lines:])
        return _redact_log_text(tail, token)

    def quick_status(self) -> dict[str, Any]:
        """轻量运行态探活（供 /api/services 状态卡使用，不生成 QR 也不调用外部 ip 命令）。"""
        with self._lock:
            cfg = self.load_config()
            port = int(cfg["port"])
            token = str(cfg["token"])
            pid = self._managed_pid()
            healthy, _code, _err = self._probe_health(port, token, timeout=0.4)
            if healthy:
                state, running, managed, external_conflict = "running", True, pid is not None, False
            elif _port_open(port, timeout=0.2):
                state, running, managed, external_conflict = "external", False, False, True
            else:
                state, running, managed, external_conflict = "stopped", False, False, False
            return {
                "state": state,
                "running": running,
                "managed": managed,
                "external_conflict": external_conflict,
                "port": port,
            }

    def status(self, *, include_qr: bool = True) -> dict[str, Any]:
        """返回移动网关完整状态：运行态、配置、候选地址、配对 URI、离线 QR SVG、8765 状态与日志尾。"""
        with self._lock:
            cfg = self.load_config()
            port = int(cfg["port"])
            token = str(cfg["token"])
            pid = self._managed_pid()
            healthy, http_code, probe_err = self._probe_health(port, token)

            if healthy:
                state = "running"
                running = True
                managed = pid is not None
                external_conflict = False
            elif _port_open(port):
                state = "external"
                running = False
                managed = False
                external_conflict = True
            else:
                state = "stopped"
                running = False
                managed = False
                external_conflict = False

            host_info = enumerate_host_candidates()
            candidates = host_info["candidates"]
            active_host = self._resolve_active_host(cfg, candidates)
            base_url = f"http://{active_host}:{port}"
            pair_uri = build_pair_uri(base_url, token)
            web_drop_uri = f"{base_url}/m#t={token}"
            adb_cmd = f"adb reverse tcp:{port} tcp:{port}"

            payload: dict[str, Any] = {
                "ok": True,
                "state": state,
                "running": running,
                "managed": managed,
                "external_conflict": external_conflict,
                "pid": pid,
                "port": port,
                "bind": cfg["bind"],
                "allow_upload": bool(cfg["allow_upload"]),
                "preferred_host": cfg["preferred_host"],
                "custom_host": cfg["custom_host"],
                "active_host": active_host,
                "base_url": base_url,
                "pair_uri": pair_uri,
                "web_drop_uri": web_drop_uri,
                "adb_reverse_cmd": adb_cmd,
                "token_masked": f"{token[:6]}…{token[-4:]}" if len(token) >= 12 else "****",
                "candidates": candidates,
                "filtered_ifaces": host_info["filtered"],
                "search_running": self._probe_search_health(),
                "probe_code": http_code,
                "probe_error": "" if healthy else probe_err,
                "log_tail": self.log_tail(lines=50, token=token),
                "updated_at": cfg["updated_at"],
            }
            if include_qr:
                payload["pair_qr_svg"] = render_svg(pair_uri, label="伴读 App 配对二维码")
                payload["web_qr_svg"] = render_svg(web_drop_uri, label="手机浏览器快传与配对二维码")
            return payload

    def start(self, *, timeout: float = 8.0) -> dict[str, Any]:
        """幂等启动受操作台托管的移动网关子进程。"""
        with self._lock:
            cfg = self.load_config()
            port = int(cfg["port"])
            token = str(cfg["token"])
            bind = str(cfg["bind"])
            allow_upload = bool(cfg["allow_upload"])

            healthy, _code, _err = self._probe_health(port, token)
            if healthy:
                return {"ok": True, "already": True, "status": self.status()}

            if _port_open(port):
                return {
                    "ok": False,
                    "error": f"端口 {port} 已被其他进程占用（非当前操作台托管或令牌不匹配），请先停止该进程或更换端口",
                    "status": self.status(),
                }

            self.ops_dir.mkdir(parents=True, exist_ok=True)
            cmd = [
                sys.executable,
                "-m",
                "bookcorpus.mobile_gateway",
                "--bind",
                bind,
                "--port",
                str(port),
                "--root",
                str(self.root),
                "--search-url",
                self.search_url,
                "--no-print-token",
                "--allow-upload" if allow_upload else "--no-allow-upload",
            ]
            child_env = dict(os.environ)
            child_env["BOOKS_ROOT"] = str(self.root)
            child_env["BOOKCORPUS_MOBILE_TOKEN"] = token
            child_env["BOOKCORPUS_MOBILE_ALLOW_UPLOAD"] = "1" if allow_upload else "0"
            child_env["PYTHONUNBUFFERED"] = "1"
            src_dir = str(Path(__file__).resolve().parents[2])
            existing_pp = child_env.get("PYTHONPATH", "")
            if src_dir not in existing_pp.split(os.pathsep):
                child_env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing_pp}" if existing_pp else src_dir

            with self.log_path.open("a", encoding="utf-8") as log_fh:
                log_fh.write(f"\n=== start {_now_iso()} bind={bind} port={port} upload={allow_upload} ===\n")
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(self.root),
                    env=child_env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )

            deadline = time.monotonic() + max(1.0, timeout)
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    return {
                        "ok": False,
                        "error": f"移动网关进程提前退出（code={proc.returncode}），请查看下方运行日志",
                        "status": self.status(),
                    }
                ok, _c, _e = self._probe_health(port, token, timeout=0.35)
                if ok:
                    tmp_pid = self.pid_path.with_name(f"{self.pid_path.name}.tmp")
                    tmp_pid.write_text(f"{proc.pid}\n", encoding="utf-8")
                    tmp_pid.replace(self.pid_path)
                    return {"ok": True, "already": False, "pid": proc.pid, "status": self.status()}
                time.sleep(0.04)

            with contextlib.suppress(OSError):
                proc.terminate()
            return {
                "ok": False,
                "error": f"移动网关在 {timeout:g} 秒内未通过健康检查，请查看运行日志",
                "status": self.status(),
            }

    def stop(self, *, timeout: float = 5.0, include_status: bool = True) -> dict[str, Any]:
        """停止由操作台托管的移动网关子进程（校验 PID 与 cmdline，不误杀外部进程）。"""
        with self._lock:
            pid = self._managed_pid()
            if pid is None:
                st = self.status(include_qr=include_status)
                if st["external_conflict"]:
                    return {
                        "ok": False,
                        "error": f"端口 {st['port']} 由外部进程占用，操作台不自动终止非托管进程",
                        "status": st,
                    }
                return {"ok": True, "was_running": False, "status": st}

            with contextlib.suppress(OSError):
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            deadline = time.monotonic() + max(0.5, timeout)
            while time.monotonic() < deadline and _pid_alive(pid):
                time.sleep(0.03)
            if _pid_alive(pid):
                with contextlib.suppress(OSError):
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                time.sleep(0.05)
            with contextlib.suppress(OSError):
                self.pid_path.unlink(missing_ok=True)
            out: dict[str, Any] = {"ok": True, "was_running": True}
            if include_status:
                out["status"] = self.status()
            return out

    def restart(self) -> dict[str, Any]:
        """重启受托管的移动网关。"""
        with self._lock:
            stop_res = self.stop(include_status=False)
            if not stop_res.get("ok"):
                return stop_res
            return self.start()

    def rotate_token(self) -> dict[str, Any]:
        """轮换 Bearer Token（`0o600` 落盘）；如网关正在运行则自动重启生效。"""
        with self._lock:
            was_managed_running = self._managed_pid() is not None
            cfg = self.load_config()
            cfg["token"] = secrets.token_hex(32)
            cfg["updated_at"] = _now_iso()
            _atomic_write_json(self.config_path, cfg, mode=0o600)
            if was_managed_running:
                res = self.restart()
                if not res.get("ok"):
                    return res
                return {"ok": True, "restarted": True, "status": res["status"]}
            return {"ok": True, "restarted": False, "status": self.status()}

    # ---- 手机接收箱（mobile_inbox）与 BanRead 9721 局域网拉取 ----------

    def _parse_lan_target(self, raw_url: str, code_override: str = "") -> tuple[str, str]:
        clean = str(raw_url or "").strip()
        parsed = urllib.parse.urlsplit(clean)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("分享链接必须以 http:// 或 https:// 开头并包含主机地址")
        if not is_private_or_local_host(parsed.hostname):
            raise ValueError(f"安全限制：仅允许拉取局域网或本机地址，已拒绝 {parsed.hostname}")
        qs = urllib.parse.parse_qs(parsed.query)
        raw_code = code_override.strip() or (qs.get("code") or [""])[0]
        code = normalize_pairing_code(raw_code)
        if not code:
            raise ValueError("缺少 6 位局域网配对码")
        port_part = f":{parsed.port}" if parsed.port else ""
        base = f"{parsed.scheme}://{parsed.hostname}{port_part}"
        return base, code

    def _http_fetch_bytes(self, url: str, code: str, *, max_bytes: int, timeout: float = 15.0) -> bytes:
        req = urllib.request.Request(url, method="GET", headers={"X-BanRead-Code": code})  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                chunks: list[bytes] = []
                got = 0
                while True:
                    block = resp.read(64 * 1024)
                    if not block:
                        break
                    got += len(block)
                    if got > max_bytes:
                        raise ValueError(f"远端响应超过体积上限 ({max_bytes} bytes)")
                    chunks.append(block)
                return b"".join(chunks)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise ValueError("6 位配对码校验失败（HTTP 401）") from exc
            if exc.code == 404:
                raise FileNotFoundError("手机端分享已过期或不包含该资源（HTTP 404）") from exc
            raise ValueError(f"手机端返回 HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ValueError(f"无法连接手机局域网分享服务：{exc}") from exc

    def _http_fetch_to_file(self, url: str, code: str, dest: Path, *, max_bytes: int, timeout: float = 60.0) -> int:
        req = urllib.request.Request(url, method="GET", headers={"X-BanRead-Code": code})  # noqa: S310
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(f"{dest.name}.{uuid.uuid4().hex[:8]}.part")
        got = 0
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp, part.open("wb") as fh:  # noqa: S310
                while True:
                    block = resp.read(64 * 1024)
                    if not block:
                        break
                    got += len(block)
                    if got > max_bytes:
                        raise ValueError(f"原书文件超过体积上限 ({max_bytes} bytes)")
                    fh.write(block)
            part.replace(dest)
            return got
        except Exception:
            part.unlink(missing_ok=True)
            raise

    def lan_pull_preview(self, url: str, code: str = "") -> dict[str, Any]:
        """预览手机 BanRead 局域网分享清单（不落盘）。"""
        base, norm_code = self._parse_lan_target(url, code)
        raw = self._http_fetch_bytes(f"{base}/share/v1/manifest", norm_code, max_bytes=MAX_MANIFEST_BYTES)
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("手机端返回的 share_manifest.json 无法解析") from exc
        if not isinstance(manifest, dict):
            raise ValueError("share_manifest.json 必须是 JSON 对象")
        return {"ok": True, "base_url": base, "manifest": manifest}

    def lan_pull_fetch(self, url: str, code: str = "", *, include_book: bool = True) -> dict[str, Any]:
        """从手机 BanRead 局域网分享拉取伴读资料与可选原书到 ``工作区/阅读/mobile_inbox/``。"""
        base, norm_code = self._parse_lan_target(url, code)
        manifest_raw = self._http_fetch_bytes(f"{base}/share/v1/manifest", norm_code, max_bytes=MAX_MANIFEST_BYTES)
        try:
            manifest = json.loads(manifest_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("share_manifest.json 无法解析") from exc
        if not isinstance(manifest, dict):
            raise ValueError("share_manifest.json 必须是 JSON 对象")

        companion_raw = self._http_fetch_bytes(f"{base}/share/v1/companion", norm_code, max_bytes=MAX_COMPANION_BYTES)
        item_id = f"lan-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        item_dir = self.mobile_inbox_dir / item_id
        item_dir.mkdir(parents=True, exist_ok=True)
        (item_dir / "share_manifest.json").write_bytes(manifest_raw)
        (item_dir / "companion.json").write_bytes(companion_raw)

        book_rel = ""
        book_bytes = 0
        if include_book and bool(manifest.get("includeBookFile")):
            raw_fname = str(manifest.get("bookFileName") or "book.epub")
            safe_fname = sanitize_upload_filename(raw_fname, fallback="book.epub")
            book_dest = item_dir / "book" / safe_fname
            book_bytes = self._http_fetch_to_file(
                f"{base}/share/v1/book",
                norm_code,
                book_dest,
                max_bytes=MAX_LAN_BOOK_BYTES,
            )
            book_rel = f"book/{safe_fname}"

        meta = {
            "item_id": item_id,
            "source": "lan_pull",
            "source_url": base,
            "received_at": _now_iso(),
            "title": str(manifest.get("title") or "未命名书目"),
            "author": str(manifest.get("author") or ""),
            "work_id": str(manifest.get("workId") or ""),
            "book_source_sha256": str(manifest.get("bookSourceSha256") or ""),
            "map_count": int(manifest.get("mapCount") or 0),
            "note_count": int(manifest.get("noteCount") or 0),
            "qa_count": int(manifest.get("qaCount") or 0),
            "has_book": bool(book_rel),
            "book_file_name": Path(book_rel).name if book_rel else "",
            "book_rel": book_rel,
            "book_bytes": book_bytes,
            "package_file": "",
            "imported_to_inbox": "",
        }
        _atomic_write_json(item_dir / "meta.json", meta)
        return {"ok": True, "item": meta}

    def register_uploaded_share_package(self, package_path: Path, original_name: str) -> dict[str, Any]:
        """将手机浏览器上传的 `.bcnote` / `.bcshare` 登记到 `mobile_inbox` 并提取清单元数据。"""
        info = _inspect_share_zip(package_path)
        manifest = info["manifest"]
        book_entry = str(info["book_entry"] or "")
        item_dir = package_path.parent
        item_id = item_dir.name
        meta = {
            "item_id": item_id,
            "source": "web_upload",
            "source_url": "",
            "received_at": _now_iso(),
            "title": str(manifest.get("title") or Path(original_name).stem),
            "author": str(manifest.get("author") or ""),
            "work_id": str(manifest.get("workId") or ""),
            "book_source_sha256": str(manifest.get("bookSourceSha256") or ""),
            "map_count": int(manifest.get("mapCount") or 0),
            "note_count": int(manifest.get("noteCount") or 0),
            "qa_count": int(manifest.get("qaCount") or 0),
            "has_book": bool(book_entry),
            "book_file_name": Path(book_entry).name if book_entry else "",
            "book_rel": book_entry,
            "book_bytes": int(manifest.get("bookSizeBytes") or 0),
            "package_file": package_path.name,
            "package_bytes": package_path.stat().st_size,
            "imported_to_inbox": "",
        }
        _atomic_write_json(item_dir / "meta.json", meta)
        return meta

    def list_mobile_inbox(self) -> list[dict[str, Any]]:
        """列出 `工作区/阅读/mobile_inbox/` 中已接收的分享资料包，按时间倒序。"""
        if not self.mobile_inbox_dir.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for entry in sorted(self.mobile_inbox_dir.iterdir(), reverse=True):
            if not entry.is_dir() or not _SAFE_ITEM_ID_RE.match(entry.name):
                continue
            meta_path = entry / "meta.json"
            if not meta_path.is_file():
                continue
            try:
                doc = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(doc, dict):
                items.append(doc)
        items.sort(key=lambda row: str(row.get("received_at") or ""), reverse=True)
        return items

    def send_inbox_book_to_import(self, item_id: str) -> dict[str, Any]:
        """将已接收分享包（`.bcshare` 或局域网拉取）中的原书原子投递到 `books/_收件箱/`。"""
        clean_id = str(item_id or "").strip()
        if not _SAFE_ITEM_ID_RE.match(clean_id):
            raise ValueError("非法 item_id")
        item_dir = (self.mobile_inbox_dir / clean_id).resolve()
        inbox_root = self.mobile_inbox_dir.resolve()
        if not str(item_dir).startswith(f"{inbox_root}{os.sep}") or not item_dir.is_dir():
            raise FileNotFoundError(f"未找到接收记录：{clean_id}")
        meta_path = item_dir / "meta.json"
        if not meta_path.is_file():
            raise FileNotFoundError(f"接收记录缺少元数据：{clean_id}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict) or not meta.get("has_book"):
            raise ValueError("该接收记录不含原书文件（仅伴读资料）")

        book_file_name = sanitize_upload_filename(str(meta.get("book_file_name") or ""), fallback="")
        ext = Path(book_file_name).suffix.lower()
        if ext not in ACCEPT_EXTS:
            raise ValueError(f"原书格式 {ext or '无后缀'} 不在入库收件箱白名单内")

        self.books_inbox_dir.mkdir(parents=True, exist_ok=True)
        target = self.books_inbox_dir / book_file_name
        stem = target.stem
        idx = 2
        while target.exists():
            target = self.books_inbox_dir / f"{stem}-{idx}{ext}"
            idx += 1

        part = target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}.part")
        try:
            package_name = str(meta.get("package_file") or "")
            book_rel = str(meta.get("book_rel") or "")
            if package_name:
                pkg_path = item_dir / package_name
                if not pkg_path.is_file():
                    raise FileNotFoundError("原始分享包文件已不存在")
                with zipfile.ZipFile(pkg_path, "r") as zf:
                    if book_rel not in zf.namelist():
                        raise FileNotFoundError("分享包内找不到原书条目")
                    with zf.open(book_rel, "r") as src_fh, part.open("wb") as dst_fh:
                        shutil.copyfileobj(src_fh, dst_fh, length=64 * 1024)
            else:
                src_book = (item_dir / book_rel).resolve()
                if not str(src_book).startswith(f"{item_dir}{os.sep}") or not src_book.is_file():
                    raise FileNotFoundError("已拉取的原书文件不存在")
                shutil.copyfile(src_book, part)
            size = part.stat().st_size
            part.replace(target)
        except Exception:
            part.unlink(missing_ok=True)
            raise

        meta["imported_to_inbox"] = target.name
        _atomic_write_json(meta_path, meta)
        return {"ok": True, "name": target.name, "bytes": size, "item": meta}
