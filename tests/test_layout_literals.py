"""树形只能由 layout.py 命名。脚本里再拼 库/、目录/、视图/ 就是第二条隐式约定。"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = sorted((ROOT / "src" / "bookcorpus").rglob("*.py"))
TARGETS.append(ROOT / "工作区" / "运维" / "server.py")
# migrate.py 的职责就是读 v1 树，必须认识旧名字。
SKIP = {ROOT / "src" / "bookcorpus" / "migrate.py"}
OLD = {"目录", "库", "视图"}


def docstrings(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def offenders(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = docstrings(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("join", "exists", "walk", "isdir", "listdir",
                                  "makedirs", "unlink", "replace"):
                for arg in node.args:
                    if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                            and arg.value in OLD):
                        found.append((node.lineno, arg.value))
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in skip):
            for old in OLD:
                if node.value.startswith(old + "/"):
                    found.append((node.lineno, node.value[:60]))
                    break
    return found


def test_no_script_names_the_old_tree():
    bad = {}
    for path in TARGETS:
        if path in SKIP:
            continue
        hits = offenders(path)
        if hits:
            bad[str(path.relative_to(ROOT))] = hits
    assert not bad, bad
