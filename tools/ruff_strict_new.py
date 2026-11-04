#!/usr/bin/env python3
"""pre-commit 的「新代码严格档」闸门。

存量代码有 4000+ 处 PTH（os.path 拼接）/ UP031（% 格式化），一次性全改风险太大，
所以按文件粒度做增量治理：在 .ruff-legacy-files.txt 里的老文件放行，
不在清单里的文件（＝新加的文件，或已迁移干净的文件）必须通过 ruff-strict.toml。

迁移某个老文件时：改干净，然后把它从 .ruff-legacy-files.txt 里删掉。
想临时跳过：SKIP=ruff-strict git commit

用法（pre-commit 会把暂存的文件作为参数传进来）：
    python tools/ruff_strict_new.py [文件...]
不带参数时检查整个仓库里所有不在清单中的文件。
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / ".ruff-legacy-files.txt"
CONFIG = ROOT / "ruff-strict.toml"
# 与 pyproject.toml 的 exclude 对齐：不带参数扫全仓时不该把依赖和语料算进来。
SKIP_DIRS = {".git", ".venv", "__pycache__", "books", "build", "dist", "工作区", "缓存", ".agents"}


def load_baseline() -> set[str]:
    if not BASELINE.exists():
        return set()
    return {line.strip() for line in BASELINE.read_text(encoding="utf-8").splitlines() if line.strip()}


def normalize(path: str) -> str:
    """把 pre-commit 传来的路径统一成仓库相对的 posix 路径。"""
    p = Path(path)
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix().lstrip("./")


def main(argv: list[str]) -> int:
    baseline = load_baseline()
    candidates = [normalize(a) for a in argv] if argv else [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.py")]
    candidates = [c for c in candidates if not SKIP_DIRS & set(Path(c).parts)]
    targets = [c for c in candidates if c.endswith(".py") and c not in baseline]
    if not targets:
        print("[ruff-strict] 本次改动全在存量清单里，跳过。")
        return 0
    print(f"[ruff-strict] 严格档检查 {len(targets)} 个文件：")
    for t in targets:
        print(f"  {t}")
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--config", str(CONFIG), "--force-exclude", *targets],
        cwd=ROOT,
    )
    if proc.returncode != 0:
        print(
            "\n[ruff-strict] 新代码不允许 os.path 拼接（用 pathlib）和 % 格式化（用 f-string）。\n"
            "确有必要时加 `# noqa: PTH118 理由`。"
        )
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
