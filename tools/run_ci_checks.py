#!/usr/bin/env python3
"""不短路跑完 CI 全部静态、文档与前端 JS 语法检查，最后一次性列出全部失败项。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run_cmd(name: str, argv: list[str]) -> bool:
    t0 = time.monotonic()
    proc = subprocess.run(argv, cwd=ROOT)
    dt = time.monotonic() - t0
    tag = "PASS" if proc.returncode == 0 else "FAIL"
    print(f"[{tag}] {name} ({dt:.2f}s)", flush=True)
    return proc.returncode == 0


def _check_js_syntax() -> bool:
    t0 = time.monotonic()
    node = shutil.which("node")
    if not node:
        print("[FAIL] js syntax (未找到 node 可执行文件)", flush=True)
        return False
    js_files = sorted(
        [
            *(ROOT / "apps").rglob("*.js"),
            *(ROOT / "tests" / "acceptance" / "ops").rglob("*.js"),
        ]
    )
    failed: list[str] = []
    for path in js_files:
        proc = subprocess.run([node, "--check", str(path)], cwd=ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            rel = path.relative_to(ROOT).as_posix()
            failed.append(rel)
            detail = (proc.stderr or proc.stdout).strip()
            print(f"  [JS 语法错] {rel}\n{detail}", flush=True)
    dt = time.monotonic() - t0
    if failed:
        print(f"[FAIL] js syntax ({len(failed)}/{len(js_files)} 个文件失败, {dt:.2f}s)", flush=True)
        return False
    print(f"[PASS] js syntax ({len(js_files)} 个文件, {dt:.2f}s)", flush=True)
    return True


def main() -> int:
    is_ci = bool(os.environ.get("GITHUB_ACTIONS"))
    ruff_args = [sys.executable, "-m", "ruff", "check", *(("--output-format=github",) if is_ci else ()), "."]
    checks: list[tuple[str, list[str]]] = [
        ("ruff check", ruff_args),
        ("ruff format", [sys.executable, "-m", "ruff", "format", "--check", "."]),
        ("ruff strict", [sys.executable, str(ROOT / "tools" / "ruff_strict_new.py")]),
        ("mypy (src/bookcorpus)", [sys.executable, "-m", "mypy"]),
        ("docs check", [sys.executable, "-m", "bookcorpus", "docs", "check"]),
        ("living docs sync", [sys.executable, str(ROOT / "tools" / "check_docs_sync.py"), "--strict"]),
    ]

    failures: list[str] = []
    for name, argv in checks:
        if not _run_cmd(name, argv):
            failures.append(name)
    if not _check_js_syntax():
        failures.append("js syntax")

    if failures:
        print(f"\n汇总：{len(failures)} 项检查未通过 -> {', '.join(failures)}", flush=True)
        return 1
    print("\n汇总：全部 7 项检查通过", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
