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

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / ".ruff-legacy-files.txt"
CONFIG = ROOT / "ruff-strict.toml"
# 与 pyproject.toml 的 exclude 对齐：不带参数扫全仓时不该把依赖和语料算进来。
SKIP_DIRS = {".git", ".venv", "__pycache__", "books", "build", "dist", "工作区", "缓存", ".agents"}
# 读法包只许 model.py 直接碰网络：分类、重试、超时、事件账全挂在那个客户端上，
# 别处自己 import 就是绕开这四样，出事只剩一句 str(exc) 可以猜。
NETWORK_SCOPE = "src/bookcorpus/reading/"
SINGLE_CLIENT = "src/bookcorpus/reading/model.py"
NETWORK_MODULES = (
    "urllib.request",
    "urllib.error",
    "http.client",
    "socket",
    "ssl",
    "requests",
    "httpx",
    "aiohttp",
)


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


def _imported_modules(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module


def check_network_imports(files: list[str]) -> int:
    """禁止读法包里除 model.py 之外的文件直接 import 网络库。"""
    offenders = []
    for rel in files:
        if not rel.startswith(NETWORK_SCOPE) or rel == SINGLE_CLIENT or not rel.endswith(".py"):
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for lineno, module in _imported_modules(tree):
            if any(module == banned or module.startswith(banned + ".") for banned in NETWORK_MODULES):
                offenders.append(f"  {rel}:{lineno}  import {module}")
    if not offenders:
        return 0
    print("\n[ruff-strict] 读法包只允许 src/bookcorpus/reading/model.py 直接访问网络：")
    print("\n".join(offenders))
    print("把请求交给 ModelClient.complete_json / _request，别再自己开连接。")
    return 1


def main(argv: list[str]) -> int:
    baseline = load_baseline()
    candidates = [normalize(a) for a in argv] if argv else [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.py")]
    candidates = [c for c in candidates if not SKIP_DIRS & set(Path(c).parts)]
    targets = [c for c in candidates if c.endswith(".py") and c not in baseline]
    code = 0
    if targets:
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
            code = 1
    else:
        print("[ruff-strict] 本次改动的文件全在存量清单里，跳过 ruff 检查。")
    # 网络入口检查对存量文件也生效：它就是防止有人在 service.py 里再开一条通路。
    return check_network_imports(candidates) or code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
