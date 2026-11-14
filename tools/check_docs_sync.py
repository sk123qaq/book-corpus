#!/usr/bin/env python3
"""补齐 `book-corpus docs check` 之外的活文档一致性校验。

既有 `src/bookcorpus/docfacts.py` 已负责 `docs/reference/facts.md`、`docs/INDEX.md`、
`docs/{adr,design,briefs}` 状态头与 `docs/` 相对链接，但明确不管 `.agents/`，
也不校验正文中的反引号仓库路径、技能清单与 `apps/ops/CONTRACT.md` 接口契约。

用法：
    python3 tools/check_docs_sync.py [--strict]
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

FENCE_RE = re.compile(r"^\s*```")
BACKTICK_RE = re.compile(r"`([^`\n]+)`")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)")
PORT_RE = re.compile(r"127\.0\.0\.1:(\d{2,5})")
CLI_CMD_RE = re.compile(r"\bbook-corpus\s+([a-z][a-z0-9_-]*)\b")
CONTRACT_ROUTE_RE = re.compile(r"^(?:GET|POST|PUT|DELETE|PATCH)\s+(/api/[a-zA-Z0-9_/-]+)", re.MULTILINE)
SERVER_ROUTE_RE = re.compile(r"""["'](/api/[a-zA-Z0-9_/-]+)["']""")

REPO_PREFIXES = (
    "src/",
    "apps/",
    "tests/",
    "tools/",
    ".agents/",
    "docs/",
    "showcase/",
    "pyproject.toml",
    "ruff-strict.toml",
    ".pre-commit-config.yaml",
    ".ruff-legacy-files.txt",
    ".github/",
    ".env.example",
)

# 仅作示例或运行期 gitignore 产物的路径，不要求在工具仓磁盘存在
IGNORED_PATH_TOKENS = {
    "src/bookcorpus/scripts/xxx.py",
    "src/<context>/",
    ".agents/skills/typesafe-ai/.env",
    # 运行期生成、被 .gitignore 排除的水位线文件
    "docs/.last-doc-sync",
    # 文档明确写了「已删」的历史测试：正文保留路径是为了说明别再按它打命令
    "tests/test_reading_citations.py",
    "tests/acceptance/demo/test_answer_anchored.py",
}
IGNORED_PATH_PREFIXES = (
    "apps/demo/缓存/",
    "apps/demo/运行记录/",
    "artifacts/",
)

# verify-book-corpus 约定的私有验证端口与 embed 默认端口
EXTRA_ALLOWED_PORTS = {18080, 18765, 18770, 18771, 18772, 18773, 18774, 18775, 18780}

# cli.py 内置顶层子命令（非 ALIASES 映射表中的分发命令）
BUILTIN_SUBCOMMANDS = {
    "init",
    "sample",
    "doctor",
    "venv",
    "model",
    "daily",
    "list",
    "embed",
    "docs",
    "run",
    "help",
}

# 历史文档中提及已下线的旧命令（不作为报错）
HISTORICAL_SUBCOMMANDS = {"migrate", "remap"}


def _is_historical(text: str) -> bool:
    head = "\n".join(text.splitlines()[:35])
    if re.search(r"^状态：\s*historical\b", head, re.MULTILINE):
        return True
    return bool(re.search(r"布局(说明|迁移)", head))


def iter_living_docs(repo: Path) -> list[Path]:
    docs: list[Path] = []
    for p in sorted(repo.glob("*.md")):
        if p.is_file():
            docs.append(p)
    for folder in ("docs", "apps", ".agents/skills"):
        base = repo / folder
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.md")):
            if p.name in {"facts.md", "INDEX.md"} and p.parent.name in {"reference", "docs"}:
                continue
            docs.append(p)
    return docs


def discover_skills(repo: Path) -> list[str]:
    skills_dir = repo / ".agents/skills"
    if not skills_dir.exists():
        return []
    return sorted(p.parent.name for p in skills_dir.glob("*/SKILL.md"))


def check_skills_inventory(repo: Path) -> list[str]:
    """核对 .agents/skills/*/SKILL.md 与 AGENTS.md / README.md 的技能清单是否一致。"""
    actual = set(discover_skills(repo))
    if not actual:
        return []
    problems: list[str] = []

    agents_md = repo / "AGENTS.md"
    if agents_md.exists():
        for line in agents_md.read_text(encoding="utf-8").splitlines():
            if "`.agents/skills/`" in line:
                listed = set(re.findall(r"`([a-z0-9-]+)`", line))
                missing = sorted(actual - listed)
                extra = sorted(listed - actual)
                if missing:
                    problems.append(f"AGENTS.md 文件地图 `.agents/skills/` 行漏列技能：{', '.join(missing)}")
                if extra:
                    problems.append(f"AGENTS.md 文件地图 `.agents/skills/` 行包含不存在技能：{', '.join(extra)}")
                break

    readme_md = repo / "README.md"
    if readme_md.exists():
        text = readme_md.read_text(encoding="utf-8")
        listed_in_readme = set(re.findall(r"\|\s*`([a-z0-9-]+)`\s*\|\s*`\.agents/skills/", text))
        if listed_in_readme:
            missing = sorted(actual - listed_in_readme)
            extra = sorted(listed_in_readme - actual)
            if missing:
                problems.append(f"README.md「技能」表漏列技能：{', '.join(missing)}")
            if extra:
                problems.append(f"README.md「技能」表包含不存在技能：{', '.join(extra)}")

    return problems


def _looks_like_repo_path(token: str) -> str | None:
    cand = token.strip().split(":", 1)[0].split("#", 1)[0]
    if not cand.startswith(REPO_PREFIXES):
        return None
    if cand in IGNORED_PATH_TOKENS or cand.startswith(IGNORED_PATH_PREFIXES):
        return None
    if any(ch in cand for ch in ("*", "<", ">", "{", "}", "$", " ", "…")):
        return None
    return cand


def check_backtick_paths(repo: Path) -> list[str]:
    """校验活文档中反引号包裹的仓库相对路径是否真实存在。"""
    problems: list[str] = []
    for doc in iter_living_docs(repo):
        rel = doc.relative_to(repo).as_posix()
        text = doc.read_text(encoding="utf-8")
        if _is_historical(text):
            continue
        in_fence = False
        for lineno, line in enumerate(text.splitlines(), 1):
            if FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for raw in BACKTICK_RE.findall(line):
                target = _looks_like_repo_path(raw)
                if target is None:
                    continue
                if not (repo / target).exists():
                    problems.append(f"{rel}:{lineno} 引用了不存在的仓库路径 `{target}`")
    return problems


def _allowed_ports(repo: Path) -> set[int]:
    ports = set(EXTRA_ALLOWED_PORTS)
    for server in (repo / "apps").glob("*/server.py"):
        tree = ast.parse(server.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "add_argument":
                continue
            flags = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if "--port" not in flags:
                continue
            for kw in node.keywords:
                if kw.arg == "default" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, int):
                    ports.add(kw.value.value)
    return ports


def check_skill_links_and_ports(repo: Path) -> list[str]:
    """补齐 docfacts.py 跳过的 .agents/skills/ 相对链接与本地端口检查。"""
    skills_dir = repo / ".agents/skills"
    if not skills_dir.exists():
        return []
    allowed_ports = _allowed_ports(repo)
    problems: list[str] = []

    for doc in sorted(skills_dir.rglob("*.md")):
        rel = doc.relative_to(repo).as_posix()
        in_fence = False
        for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            if FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            cleaned = re.sub(r"`[^`]*`", "", line)
            for target in LINK_RE.findall(cleaned):
                if target.startswith(("http://", "https://", "mailto:", "#", "/", "~")):
                    continue
                clean_target = target.split("#", 1)[0].split("?", 1)[0]
                if not clean_target or not (doc.parent / clean_target).exists():
                    problems.append(f"{rel}:{lineno} 技能相对链接不存在：{target}")
            for port_str in PORT_RE.findall(line):
                port = int(port_str)
                if port not in allowed_ports:
                    problems.append(f"{rel}:{lineno} 出现未登记端口 127.0.0.1:{port}")
    return problems


def check_ops_contract(repo: Path) -> list[str]:
    """核对 apps/ops/CONTRACT.md 声明的 /api/* 路由是否在 apps/ops/server.py 中存在。"""
    contract = repo / "apps/ops/CONTRACT.md"
    server = repo / "apps/ops/server.py"
    if not contract.exists() or not server.exists():
        return []
    contract_routes = {r.rstrip("/") for r in CONTRACT_ROUTE_RE.findall(contract.read_text(encoding="utf-8"))}
    server_routes = {r.rstrip("/") for r in SERVER_ROUTE_RE.findall(server.read_text(encoding="utf-8"))}
    return [
        f"apps/ops/CONTRACT.md 声明了路由 `{route}`，但 apps/ops/server.py 中未找到"
        for route in sorted(contract_routes - server_routes)
    ]


def _valid_cli_subcommands(repo: Path) -> set[str]:
    valid = set(BUILTIN_SUBCOMMANDS)
    cli = repo / "src/bookcorpus/cli.py"
    if not cli.exists():
        return valid
    tree = ast.parse(cli.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ALIASES" for t in node.targets):
            try:
                aliases = ast.literal_eval(node.value)
            except ValueError:
                aliases = {}
            if isinstance(aliases, dict):
                valid.update(str(k) for k in aliases)
    return valid


def check_cli_commands(repo: Path) -> list[str]:
    """核对活文档中的 `book-corpus <subcommand>` 是否均为有效子命令。"""
    valid = _valid_cli_subcommands(repo)
    problems: list[str] = []
    for doc in iter_living_docs(repo):
        rel = doc.relative_to(repo).as_posix()
        text = doc.read_text(encoding="utf-8")
        if _is_historical(text):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for cmd in CLI_CMD_RE.findall(line):
                if cmd in valid or cmd in HISTORICAL_SUBCOMMANDS:
                    continue
                problems.append(f"{rel}:{lineno} 出现未知 CLI 子命令 `book-corpus {cmd}`")
    return problems


def run(repo: Path = REPO_ROOT, strict: bool = False) -> int:
    checks = (
        ("skills", check_skills_inventory(repo)),
        ("paths", check_backtick_paths(repo)),
        ("skill-links", check_skill_links_and_ports(repo)),
        ("ops-contract", check_ops_contract(repo)),
        ("cli", check_cli_commands(repo)),
    )
    issues = [f"[{label}] {prob}" for label, probs in checks for prob in probs]

    if not issues:
        print("全过：skills/paths/skill-links/ops-contract/cli")
        return 0

    tag = "fail" if strict else "warn"
    for item in issues:
        print(f"[{tag}] {item}")
    if strict:
        print(f"不过：{len(issues)} 项文档漂移")
        return 1
    print(f"提示：发现 {len(issues)} 项文档漂移（加 --strict 阻断）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查仓库活文档与代码/技能目录的一致性")
    parser.add_argument("--strict", action="store_true", help="发现任何不一致时返回退出码 1")
    args = parser.parse_args(argv)
    return run(REPO_ROOT, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
