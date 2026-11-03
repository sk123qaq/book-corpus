"""运维与演示的离线验收脚本，接进 pytest。

这些脚本原来是独立入口，夹具按 v1 的 库/ 目录/ 写死。迁移后它们全部改用 v2 临时根，
这里只负责逐个跑、把末行 ALL PASS 当通过。需要 Chrome、真实模型或真实会话的脚本
（check_demo、check_session*、check_result_ms、shot_ops、repro-*）不在这里跑，仍是手工验收。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
OPS = REPO / "tests" / "acceptance" / "ops"
DEMO = REPO / "tests" / "acceptance" / "demo"
INSTANCE = Path(os.environ.get("BOOKS_ROOT") or (Path.home() / "文档" / "book-corpus-local"))

OFFLINE = [
    (OPS / "check_ops.py", 300),
    (OPS / "test_status.py", 180),
    (OPS / "test_classify.py", 180),
    (OPS / "test_doc_numbers.py", 180),
    (OPS / "test_split.py", 180),
    (OPS / "test_retire.py", 180),
    (OPS / "test_pdf.py", 300),
    (DEMO / "test_answer_retry.py", 180),
]


def pdf_python():
    """带 pymupdf 的解释器。没有就跳过 test_pdf，不伪装成通过。"""
    candidates = []
    if os.environ.get("BOOKS_PDF_PY"):
        candidates.append(os.environ["BOOKS_PDF_PY"])
    candidates += [str(INSTANCE / "工作区" / ".venv-pdf" / "bin" / "python"),
                   str(Path.home() / "teleocr" / ".venv" / "bin" / "python"),
                   sys.executable]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            ok = subprocess.run([path, "-c", "import pymupdf"],
                                capture_output=True, timeout=30).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            return path
    return ""


def acceptance_env():
    env = dict(os.environ)
    env["BOOKS_ROOT"] = str(INSTANCE)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    pdf = pdf_python()
    if pdf:
        env["BOOKS_PDF_PY"] = pdf
    return env


@pytest.mark.parametrize("script,timeout", OFFLINE, ids=[p.name for p, _ in OFFLINE])
def test_offline_acceptance(script, timeout):
    if not script.is_file():
        pytest.skip("缺验收脚本 %s" % script)
    if script.name == "test_status.py" and not INSTANCE.is_dir():
        pytest.skip("没有实例 %s，跳过真库只读段" % INSTANCE)
    if script.name == "test_answer_retry.py":
        chapter = INSTANCE / "books" / "B0210" / "md" / "05_序幕黑洞之旅.md"
        if not chapter.is_file():
            pytest.skip("实例里没有 B0210 的章节，跳过演示离线验收")
    if script.name == "test_pdf.py" and not pdf_python():
        pytest.skip("没有带 pymupdf 的解释器；设 BOOKS_PDF_PY 后重跑")
    proc = subprocess.run([sys.executable, str(script)], cwd=str(REPO),
                          env=acceptance_env(), capture_output=True, text=True,
                          timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stdout + "\n" + proc.stderr)[-4000:]
        pytest.fail("%s 退出码 %d\n%s" % (script.name, proc.returncode, tail))
