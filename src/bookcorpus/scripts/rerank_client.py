#!/usr/bin/env python3
"""交叉编码器精排 HTTP 客户端（纯标准库，主环境零 torch 依赖）。

默认连 http://127.0.0.1:18081（可通过 BOOKS_RERANK_URL 覆盖，设为 off 关闭）。
服务离线、超时或未部署时平滑返回 None，让上层自动降级回 BM25+向量 RRF。
"""

import atexit
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import lib_common

DEFAULT_URL = "http://127.0.0.1:18081"
DEFAULT_PORT = 18081
_UNHEALTHY_COOLDOWN_S = 5.0
_DISABLED_VALUES = frozenset({"off", "0", "false", "no", "none", "disabled"})

_LOCK = threading.Lock()
_UNHEALTHY_UNTIL: dict[str, float] = {}


def _rerank_url(url=None):
    raw = url if url is not None else os.environ.get("BOOKS_RERANK_URL", DEFAULT_URL)
    val = str(raw or "").strip()
    if not val or val.lower() in _DISABLED_VALUES:
        return ""
    return val.rstrip("/")


def clear_cache():
    """清空健康探测冷却状态（测试用）。"""
    with _LOCK:
        _UNHEALTHY_UNTIL.clear()


def server_healthy(url=None, timeout=0.35):
    """非阻塞探活本机精排服务；失败进入短冷却期，避免反复拖慢查询。"""
    u = _rerank_url(url)
    if not u:
        return False
    now = time.monotonic()
    with _LOCK:
        if now < _UNHEALTHY_UNTIL.get(u, 0.0):
            return False
    req = urllib.request.Request(u + "/health", method="GET")  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            ok = resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        ok = False
    with _LOCK:
        if ok:
            _UNHEALTHY_UNTIL.pop(u, None)
        else:
            _UNHEALTHY_UNTIL[u] = time.monotonic() + _UNHEALTHY_COOLDOWN_S
    return ok


def rerank_scores(query, texts, url=None, timeout=2.5):
    """调用常驻精排服务对文本按入参顺序打分；不可用时返回 None。"""
    q = str(query or "").strip()
    if not q or not texts:
        return None
    u = _rerank_url(url)
    if not u:
        return None
    with _LOCK:
        if time.monotonic() < _UNHEALTHY_UNTIL.get(u, 0.0):
            return None
    clean = [str(t or "") for t in texts]
    body = json.dumps({"query": q, "texts": clean}).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310
        u + "/v1/rerank",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        scores = data.get("scores")
        if not isinstance(scores, list) or len(scores) != len(clean):
            return None
        out = [float(x) for x in scores]
        if not all(math.isfinite(x) for x in out):
            return None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError):
        with _LOCK:
            _UNHEALTHY_UNTIL[u] = time.monotonic() + _UNHEALTHY_COOLDOWN_S
        return None
    with _LOCK:
        _UNHEALTHY_UNTIL.pop(u, None)
    return out


def extract_centered_text(lines, line, max_chars=700):
    """以命中行为中心对称抽 ~700 字（匹配 bge-reranker-v2-m3 512 token 窗）并保留近处标题。"""
    if not lines:
        return ""
    total_lines = len(lines)
    idx = max(0, min(total_lines - 1, int(line or 1) - 1))
    heading = ""
    for i in range(idx - 1, max(-1, idx - 41), -1):
        s = lines[i].strip()
        if s.startswith("#"):
            heading = s
            break
    center = lines[idx].strip()
    head_len = (len(heading) + 1) if heading and heading != center else 0
    budget = max(80, int(max_chars) - head_len)

    up_idx = idx - 1
    down_idx = idx + 1
    up_parts = []
    down_parts = []
    used = len(center)
    while used < budget and (up_idx >= 0 or down_idx < total_lines):
        progressed = False
        if down_idx < total_lines and used < budget:
            s = lines[down_idx].strip()
            down_idx += 1
            if s:
                if used + len(s) + 1 <= budget or used < 120:
                    down_parts.append(s)
                    used += len(s) + 1
                    progressed = True
                else:
                    down_idx = total_lines
        if up_idx >= 0 and used < budget:
            s = lines[up_idx].strip()
            up_idx -= 1
            if s:
                if used + len(s) + 1 <= budget or used < 120:
                    up_parts.append(s)
                    used += len(s) + 1
                    progressed = True
                else:
                    up_idx = -1
        if not progressed and down_idx >= total_lines and up_idx < 0:
            break

    up_parts.reverse()
    parts = [*up_parts, center, *down_parts]
    parts = [p for p in parts if p]
    if heading and (not parts or parts[0] != heading):
        parts.insert(0, heading)
    return "\n".join(parts)[:max_chars].strip()


def read_centered_text(rel_path, line, max_chars=700, root=None):
    if not rel_path:
        return ""
    base = Path(root or lib_common.ROOT)
    full = base / rel_path
    try:
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return extract_centered_text(lines, line, max_chars=max_chars)


def health_info(root=None, url=None):
    """返回精排服务与模型状态，供 /api/health 与诊断检查。"""
    from bookcorpus import rerankenv

    base = Path(root or lib_common.ROOT)
    u = _rerank_url(url)
    has_model = rerankenv.model_ready(base)
    if not u:
        return {
            "model": "ok" if has_model else "missing",
            "server": "disabled",
            "ready": False,
            "url": "",
        }
    srv_ok = server_healthy(url=u, timeout=0.35)
    return {
        "model": "ok" if has_model else "missing",
        "server": "ok" if srv_ok else "offline",
        "ready": bool(srv_ok),
        "url": u,
    }


def ensure_server(root=None, url=None, keep=False, timeout=90):
    """若本机精排服务未起且环境/GPU/模型就绪，拉起常驻 HTTP 服务。"""
    from bookcorpus import rerankenv

    base = Path(root or lib_common.ROOT)
    u = _rerank_url(url) or DEFAULT_URL
    if server_healthy(url=u, timeout=0.5):
        return None
    gpu = rerankenv.detect_gpu()
    if not gpu.available:
        raise SystemExit(f"精排服务未启动：{gpu.reason}")
    ok_v, det_v = rerankenv.versions(base)
    if not ok_v:
        raise SystemExit(f"精排环境未就绪：{det_v}（先跑 book-corpus rerank-setup）")
    if not rerankenv.model_ready(base):
        raise SystemExit(f"精排模型缺失：{rerankenv.model_dir(base)}（先跑 book-corpus rerank-setup）")

    host, port = "127.0.0.1", str(DEFAULT_PORT)
    m = re.match(r"http://([^:/]+)(?::(\d+))?", u)
    if m:
        host, port = m.group(1), m.group(2) or port

    log = base / "工作区" / "中间态" / "rerank-server.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    py = rerankenv.python(base)
    script = str(HERE / "rerank.py")
    env = dict(os.environ, BOOKS_ROOT=str(base))
    with log.open("ab") as fh:
        proc = subprocess.Popen(
            [
                py,
                script,
                "--serve",
                "--host",
                host,
                "--port",
                port,
                "--model-dir",
                rerankenv.model_dir(base),
            ],
            cwd=str(HERE),
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=bool(keep),
            env=env,
        )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"rerank-server 退出（{proc.returncode}），看日志 {log}")
        if server_healthy(url=u, timeout=0.5):
            if not keep:
                atexit.register(lambda: proc.terminate() if proc.poll() is None else None)
            return proc
        time.sleep(0.5)
    proc.terminate()
    raise SystemExit(f"rerank-server {timeout} 秒没起来，看日志 {log}")


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description="精排常驻 HTTP 服务启停与探活")
    ap.add_argument("--serve", action="store_true", help="前台启动精排 HTTP 服务（阻塞）")
    ap.add_argument("--daemon", action="store_true", help="后台拉起精排常驻 HTTP 服务并等就绪")
    ap.add_argument("--status", action="store_true", help="查看精排服务与模型状态")
    ap.add_argument("--url", default=None)
    a = ap.parse_args(argv)

    if a.status:
        print(json.dumps(health_info(url=a.url), ensure_ascii=False, indent=2))
        return 0

    if a.daemon:
        u = _rerank_url(a.url) or DEFAULT_URL
        if server_healthy(url=u, timeout=0.5):
            print(f"rerank-server 已在运行：{u}")
            return 0
        proc = ensure_server(url=u, keep=True)
        print(f"rerank-server 已启动（pid={proc.pid}）：{u}")
        return 0

    if a.serve:
        from bookcorpus import rerankenv

        base = Path(lib_common.ROOT)
        gpu = rerankenv.detect_gpu()
        if not gpu.available:
            raise SystemExit(f"无法启动精排服务：{gpu.reason}")
        ok_v, det_v = rerankenv.versions(base)
        if not ok_v:
            raise SystemExit(f"精排环境未就绪：{det_v}")
        if not rerankenv.model_ready(base):
            raise SystemExit(f"精排模型缺失：{rerankenv.model_dir(base)}")
        u = _rerank_url(a.url) or DEFAULT_URL
        host, port = "127.0.0.1", str(DEFAULT_PORT)
        m = re.match(r"http://([^:/]+)(?::(\d+))?", u)
        if m:
            host, port = m.group(1), m.group(2) or port
        os.execv(  # noqa: S606  # 调用受控 venv 解释器与仓内 rerank.py 服务入口
            rerankenv.python(base),
            [
                rerankenv.python(base),
                str(HERE / "rerank.py"),
                "--serve",
                "--host",
                host,
                "--port",
                port,
                "--model-dir",
                rerankenv.model_dir(base),
            ],
        )
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
