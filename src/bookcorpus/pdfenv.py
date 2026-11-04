"""PDF 侧的 Python 环境：<root>/工作区/.venv-pdf。

PDF 脚本和操作台都用这个解释器，不碰系统 Python。requirements.txt 是同一份
清单的人读副本；tests/test_pdfenv.py 对着它查漂移。
"""

import os
import subprocess
import sys

REQUIREMENTS = (
    "pymupdf>=1.24,<2",
    "pypdf>=4",
    "requests>=2.31",
    "numpy>=1.26",
    "pillow>=10",
    "beautifulsoup4>=4.12",
)

DISABLE_ENV = "BOOKS_NO_PDF_VENV"
TIMEOUT = 900
_OFF = ("", "0", "false", "no")


def venv_dir(root):
    return os.path.join(root, "工作区", ".venv-pdf")


def python(root):
    return os.path.join(venv_dir(root), "bin", "python")


def disabled():
    """BOOKS_NO_PDF_VENV=1 时 init 不建 venv，给离线或测试用。"""
    return os.environ.get(DISABLE_ENV, "").strip().lower() not in _OFF


def _tail(proc):
    lines = [x.strip() for x in ((proc.stderr or "") + (proc.stdout or "")).splitlines() if x.strip()]
    return lines[-1] if lines else "退出码 %d" % proc.returncode


def create(root, packages=REQUIREMENTS, timeout=TIMEOUT):
    """建 venv 并装包。已在就只补装，成功即幂等。返回 (ok, detail)。"""
    d = venv_dir(root)
    py = python(root)
    if not os.path.exists(py):
        os.makedirs(os.path.dirname(d), exist_ok=True)
        try:
            proc = subprocess.run([sys.executable, "-m", "venv", d], capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, "建 venv 失败：%s" % exc
        if proc.returncode != 0:
            return False, "建 venv 失败：%s" % _tail(proc)
    if not packages:
        return True, "%s（未装包）" % d
    try:
        proc = subprocess.run(
            [py, "-m", "pip", "install", "--disable-pip-version-check", "-q", *packages],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, "pip 装包失败：%s" % exc
    if proc.returncode != 0:
        return False, "pip 装包失败：%s" % _tail(proc)
    return True, "%s（%d 个包）" % (d, len(packages))


def versions(root, timeout=120):
    """在 venv 里导入一遍依赖，返回 (ok, detail)。不装包，只验。"""
    py = python(root)
    if not os.path.exists(py):
        return False, "没有 %s" % venv_dir(root)
    code = (
        "import pymupdf, pypdf, numpy, PIL, bs4, requests;"
        "print(pymupdf.__version__, pypdf.__version__, numpy.__version__)"
    )
    try:
        proc = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, "导入失败：%s" % exc
    if proc.returncode != 0:
        return False, "导入失败：%s" % _tail(proc)
    return True, "pymupdf %s" % (proc.stdout.strip() or "?")
