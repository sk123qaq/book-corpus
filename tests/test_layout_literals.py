"""树形只能由 layout.py 命名。脚本里再拼 库/、目录/、视图/ 就是第二条隐式约定。

也不许写死别人的家目录：跑测试的机器不叫 shike，别人的机器也不叫。
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = sorted((ROOT / "src" / "bookcorpus").rglob("*.py"))
TARGETS.append(ROOT / "apps" / "ops" / "server.py")
# migrate.py 的职责就是读 v1 树，必须认识旧名字。
# check_p1.py 也要认旧 P1 gold 的 库/ 路径，但只读并映射到 books/，不写旧树。
# docfacts.py 是文档闸门的旧路径清单，同样只做检测。
SKIP = {
    ROOT / "src" / "bookcorpus" / "migrate.py",
    ROOT / "src" / "bookcorpus" / "scripts" / "check_p1.py",
    ROOT / "src" / "bookcorpus" / "docfacts.py",
}
OLD = {"目录", "库", "视图"}
HOME_PTR = re.compile(r"(?<![\w.])/(?:home|Users)/[A-Za-z0-9._-]+/")


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
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("join", "exists", "walk", "isdir", "listdir", "makedirs", "unlink", "replace")
        ):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value in OLD:
                    found.append((node.lineno, arg.value))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
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


def test_no_script_hardcodes_a_home_directory():
    bad = {}
    for path in sorted(set(TARGETS) | set((ROOT / "tests").rglob("*.py")) | set((ROOT / "apps").rglob("*.py"))):
        found = HOME_PTR.findall(path.read_text(encoding="utf-8"))
        if found:
            bad[str(path.relative_to(ROOT))] = sorted(set(found))
    assert not bad, bad
