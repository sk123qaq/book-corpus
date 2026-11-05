"""Word / Excel / PPT 侧的 Python 环境：<root>/工作区/.venv-docs。

docs_to_md.py 的 worker 在这个解释器里跑 markitdown，不碰系统 Python。
requirements-docs.txt 是同一份清单的人读副本；tests/test_docenv.py 对着它查漂移。
"""

import os
import subprocess
import sys
from pathlib import Path

REQUIREMENTS = ("markitdown[docx,xlsx,xls,pptx]==0.1.9",)

DISABLE_ENV = "BOOKS_NO_DOCS_VENV"
TIMEOUT = 900
_OFF = ("", "0", "false", "no")


def venv_dir(root):
    return str(Path(root) / "工作区" / ".venv-docs")


def python(root):
    return str(Path(venv_dir(root)) / "bin" / "python")


def disabled():
    """BOOKS_NO_DOCS_VENV=1 时 init 不建 venv，给离线或测试用。"""
    return os.environ.get(DISABLE_ENV, "").strip().lower() not in _OFF


def _tail(proc):
    lines = [x.strip() for x in ((proc.stderr or "") + (proc.stdout or "")).splitlines() if x.strip()]
    return lines[-1] if lines else f"退出码 {proc.returncode}"


def create(root, packages=REQUIREMENTS, timeout=TIMEOUT):
    """建 venv 并装包。已在就只补装，成功即幂等。返回 (ok, detail)。"""
    d = venv_dir(root)
    py = python(root)
    if not Path(py).exists():
        Path(d).parent.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run([sys.executable, "-m", "venv", d], capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"建 venv 失败：{exc}"
        if proc.returncode != 0:
            return False, f"建 venv 失败：{_tail(proc)}"
    if not packages:
        return True, f"{d}（未装包）"
    try:
        proc = subprocess.run(
            [py, "-m", "pip", "install", "--disable-pip-version-check", "-q", *packages],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"pip 装包失败：{exc}"
    if proc.returncode != 0:
        return False, f"pip 装包失败：{_tail(proc)}"
    return True, f"{d}（{len(packages)} 个包）"


def versions(root, timeout=120):
    """在 venv 里导入一遍 markitdown，返回 (ok, detail)。不装包，只验。"""
    py = python(root)
    if not Path(py).exists():
        return False, f"没有 {venv_dir(root)}"
    code = "import markitdown;print(getattr(markitdown, '__version__', '?'))"
    try:
        proc = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"导入失败：{exc}"
    if proc.returncode != 0:
        return False, f"导入失败：{_tail(proc)}"
    return True, f"markitdown {proc.stdout.strip() or '?'}"


def ensure(root):
    """venv 在且能 import 就用，缺了才建（建 venv 900s 上限）。返回 (ok, detail)。"""
    if Path(python(root)).exists():
        ok, _detail = versions(root)
        if ok:
            return True, f"{venv_dir(root)} 就绪"
    if disabled():
        return False, f"没有 {venv_dir(root)}，且 {DISABLE_ENV} 已禁用"
    ok, detail = create(root)
    if not ok:
        return False, detail
    return versions(root)
