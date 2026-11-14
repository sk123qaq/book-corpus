"""文档与代码的事实对账。

  book-corpus docs write   重写 docs/reference/facts.md 与 docs/INDEX.md
  book-corpus docs check   只读对账：生成块、状态头、旧路径、相对链接、端口
                           给了 --data-root 再比 AGENTS「现状」数字

唯一约定的一处事实在这里生成：脚本清单、别名、端口、版本都从代码读，
文档只引用，不手抄。历史文档（状态：historical）或带「布局说明」的文件
跳过旧路径检查，正文不改。`.agents/` 不归本模块管。
"""

from __future__ import annotations

import ast
import fnmatch
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

FACTS = Path("docs/reference/facts.md")
INDEX = Path("docs/INDEX.md")
FACTS_START = "<!-- facts:start -->"
FACTS_END = "<!-- facts:end -->"
INDEX_START = "<!-- docs:index:start -->"
INDEX_END = "<!-- docs:index:end -->"

STATUS_VALUES = ("draft", "accepted", "proposed", "implemented", "superseded", "historical")
STATUS_RE = re.compile(
    r"^状态：\s*(draft|accepted|proposed|implemented|superseded|historical)\b(?:\s*[·・]\s*(\d{4}-\d{2}-\d{2}))?"
)
VERIFY_RE = re.compile(r"^验收：\s*(.+)$", re.MULTILINE)
REPLACE_RE = re.compile(r"^(?:替代|取代)：\s*(.+)$", re.MULTILINE)
SOURCE_RE = re.compile(r"^失效源：\s*(.+)$", re.MULTILINE)
TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)")
PORT_RE = re.compile(r"127\.0\.0\.1:(\d{2,5})")
LEGACY_PATH_RE = re.compile(r"(?<!书)库/(?!索引)[0-9A-Za-z_\u4e00-\u9fff]")
FENCE_RE = re.compile(r"^\s*```")

#: v2 已不存在的路径。分类表.csv / 概念表.tsv 仍作兜底种子存在，不算退役。
RETIRED_TOKENS = (
    ("目录/书目总表", "v2 是 build/书目总表.*"),
    ("目录/分类预填", "v2 是 tables/分类预填.csv"),
    ("目录/library.db", "v2 是 build/library.db"),
    ("目录/检索", "v2 是 build/检索"),
    ("工作区/脚本/", "脚本在 src/bookcorpus/scripts/"),
    ("~/文档/book-corpus-data", "旧数据仓已迁到 ~/书库"),
    ("book-corpus-data/", "旧数据仓已迁到 ~/书库"),
    ("人账 `tables/分类表.csv`", "活账是 tables/ledger.db"),
)
#: AGENTS 的迁移史段落按设计保留旧数据仓路径，其余文件不豁免。
TOKEN_ALLOW = {"AGENTS.md": {"~/文档/book-corpus-data", "book-corpus-data/"}}
#: 这些前缀在工具仓里没有、在实例里有，链接检查不追。
WORKSPACE_PREFIXES = ("工作区/", "books/", "build/", "tables/", "collections/", "archive/", "日课/", "缓存/")
STATUS_ORDER = {"implemented": 0, "accepted": 1, "proposed": 2, "draft": 3, "superseded": 4, "historical": 5}


@dataclass
class Report:
    fails: list[str] = field(default_factory=list)
    warns: list[str] = field(default_factory=list)
    skips: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.fails


@dataclass
class DocStatus:
    path: str
    status: str | None = None
    date: str | None = None
    verify: str | None = None
    replaces: str | None = None
    sources: list[str] = field(default_factory=list)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _first_line(path: Path) -> str:
    try:
        doc = ast.get_docstring(ast.parse(_read(path))) or ""
    except SyntaxError:
        return ""
    stripped = doc.strip()
    return stripped.splitlines()[0].strip() if stripped else ""


def _cell(text: str, limit: int = 90) -> str:
    text = " ".join(text.split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _project(repo: Path) -> tuple[str, str]:
    data = tomllib.loads(_read(repo / "pyproject.toml"))
    requires = str(data.get("project", {}).get("requires-python", ""))
    version = ""
    init = repo / "src/bookcorpus/__init__.py"
    if init.exists():
        for node in ast.parse(_read(init)).body:
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)
                and isinstance(node.value, ast.Constant)
            ):
                version = str(node.value.value)
    return requires, version


def _aliases(repo: Path) -> dict[str, str]:
    cli = repo / "src/bookcorpus/cli.py"
    if not cli.exists():
        return {}
    for node in ast.parse(_read(cli)).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ALIASES" for t in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return {}
            return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}
    return {}


def script_names(repo: Path) -> list[str]:
    scripts = repo / "src/bookcorpus/scripts"
    return sorted(p.stem for p in scripts.glob("*.py") if p.stem not in {"__init__", "lib_common"})


def app_ports(repo: Path) -> dict[str, int]:
    ports: dict[str, int] = {}
    for server in sorted((repo / "apps").glob("*/server.py")):
        port: int | None = None
        for node in ast.walk(ast.parse(_read(server))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "add_argument":
                continue
            flags = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if "--port" not in flags:
                continue
            for kw in node.keywords:
                if kw.arg == "default" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, int):
                    port = kw.value.value
        if port is not None:
            ports[server.parent.name] = port
    return ports


def render_facts(repo: Path) -> str:
    requires, version = _project(repo)
    aliases = _aliases(repo)
    reverse: dict[str, list[str]] = {}
    for alias, target in sorted(aliases.items()):
        reverse.setdefault(target, []).append(alias)
    lines = [
        FACTS_START,
        f"- `book-corpus` 版本：`{version}`",
        f"- Python：`{requires}`",
        "- 本块由 `book-corpus docs write` 生成，`docs check` 对账；不要手改。",
        "",
        "## 应用与默认端口",
        "",
        "| 应用 | 默认端口 | 入口 |",
        "|---|---|---|",
    ]
    for app, port in app_ports(repo).items():
        lines.append(f"| `{app}` | {port} | `python3 apps/{app}/server.py` |")
    lines.extend(["", "## 脚本与别名", "", "| 脚本 | 别名 | 说明 |", "|---|---|---|"])
    for name in script_names(repo):
        aliases_for = " ".join(f"`{a}`" for a in reverse.get(name, [])) or "—"
        summary = _cell(_first_line(repo / "src/bookcorpus/scripts" / f"{name}.py"))
        lines.append(f"| `{name}.py` | {aliases_for} | {summary or '—'} |")
    lines.append(FACTS_END)
    return "\n".join(lines) + "\n"


def status_docs(repo: Path) -> list[Path]:
    docs: list[Path] = []
    for folder in ("adr", "design", "briefs"):
        docs.extend(sorted((repo / "docs" / folder).glob("*.md")))
    return docs


def parse_status(text: str, rel: str) -> DocStatus:
    doc = DocStatus(path=rel)
    for line in text.splitlines()[:24]:
        stripped = line.strip()
        match = STATUS_RE.match(stripped)
        if match:
            doc.status, doc.date = match.group(1), match.group(2)
            break
        if stripped.startswith("状态：") and doc.status is None:
            doc.status = "未知"
            break
    verify = VERIFY_RE.search(text)
    doc.verify = verify.group(1).strip() if verify else None
    replace = REPLACE_RE.search(text)
    doc.replaces = replace.group(1).strip() if replace else None
    source = SOURCE_RE.search(text)
    if source:
        doc.sources = [p.strip("` ") for p in re.split(r"[,，、\s]+", source.group(1)) if p.strip(" `")]
    return doc


def _exists_any(repo: Path, doc: Path, token: str) -> bool:
    token = token.strip("`'\" ")
    if not token or token.startswith(("http://", "https://")):
        return True
    return any((base / token).exists() for base in (repo, doc.parent))


def check_facts(repo: Path) -> list[str]:
    path = repo / FACTS
    if not path.exists():
        return [f"缺 {FACTS}（跑 `book-corpus docs write`）"]
    text = _read(path)
    start, end = text.find(FACTS_START), text.find(FACTS_END)
    if start < 0 or end < start:
        return [f"{FACTS} 的生成块标记丢了（{FACTS_START} … {FACTS_END}）"]
    got = text[start : end + len(FACTS_END)] + "\n"
    want = render_facts(repo)
    if got == want:
        return []
    got_lines, want_lines = got.splitlines(), want.splitlines()
    for i, (a, b) in enumerate(zip(got_lines, want_lines, strict=True), 1):
        if a != b:
            return [f"{FACTS}:{i} 与代码不一致：文档写 `{a}`，代码是 `{b}`（跑 `book-corpus docs write`）"]
    return [f"{FACTS} 行数与代码不一致（{len(got_lines)} vs {len(want_lines)}）"]


def render_index(repo: Path) -> str:
    rows = []
    for path in status_docs(repo):
        rel = path.relative_to(repo).as_posix()
        text = _read(path)
        doc = parse_status(text, rel)
        found = TITLE_RE.search(text)
        rows.append((rel, doc.status or "缺", doc.date or "", found.group(1).strip() if found else path.stem))
    rows.sort(key=lambda r: (STATUS_ORDER.get(r[1], 9), r[2], r[0]))
    lines = [INDEX_START, "| 文档 | 状态 | 日期 | 标题 |", "|---|---|---|---|"]
    for rel, status, date, title in rows:
        link = Path(rel).relative_to("docs").as_posix()
        lines.append(f"| [{rel}]({link}) | {status} | {date} | {_cell(title)} |")
    lines.append(INDEX_END)
    return "\n".join(lines) + "\n"


def check_index(repo: Path) -> list[str]:
    path = repo / INDEX
    if not path.exists():
        return [f"缺 {INDEX}（跑 `book-corpus docs write`）"]
    text = _read(path)
    start, end = text.find(INDEX_START), text.find(INDEX_END)
    if start < 0 or end < start:
        return [f"{INDEX} 的索引块标记丢了"]
    got = text[start : end + len(INDEX_END)] + "\n"
    if got == render_index(repo):
        return []
    return [f"{INDEX} 与状态头不一致（跑 `book-corpus docs write`）"]


def check_status(repo: Path) -> list[str]:
    problems: list[str] = []
    for path in status_docs(repo):
        rel = path.relative_to(repo).as_posix()
        doc = parse_status(_read(path), rel)
        if doc.status == "未知":
            line = next((ln.strip() for ln in _read(path).splitlines() if ln.strip().startswith("状态：")), "")
            problems.append(f"{rel}: 状态值不在枚举里（{line}）；用 {'|'.join(STATUS_VALUES)}")
            continue
        if doc.status is None:
            problems.append(f"{rel}: 缺 `状态：<{'|'.join(STATUS_VALUES)}> · YYYY-MM-DD`")
            continue
        if doc.date is None:
            problems.append(f"{rel}: 状态行缺日期（`状态：{doc.status} · YYYY-MM-DD`）")
        if doc.status == "superseded":
            if not doc.replaces:
                problems.append(f"{rel}: superseded 必须写 `替代：<路径>`")
            else:
                refs = re.findall(r"`([^`]+)`", doc.replaces) or [doc.replaces]
                for ref in refs:
                    if not _exists_any(repo, path, ref):
                        problems.append(f"{rel}: 替代目标不存在：{ref}")
        if doc.status == "implemented" and not doc.verify:
            problems.append(f"{rel}: implemented 必须写 `验收：<命令或测试路径>`")
    return problems


def _md_files(repo: Path) -> list[Path]:
    files = sorted(p for p in repo.glob("*.md") if p.is_file())
    files.extend(sorted((repo / "docs").rglob("*.md")))
    files.extend(sorted((repo / "apps").glob("*/README.md")))
    files.extend(sorted((repo / "src/bookcorpus/templates").rglob("*.md")))
    return files


def _generated(repo: Path, path: Path) -> bool:
    return path in {repo / FACTS, repo / INDEX}


def check_tokens(repo: Path) -> list[str]:
    problems: list[str] = []
    for path in _md_files(repo):
        if _generated(repo, path):
            continue
        rel = path.relative_to(repo).as_posix()
        text = _read(path)
        head = "\n".join(text.splitlines()[:40])
        doc = parse_status(text, rel)
        if doc.status == "historical" or re.search(r"布局(说明|迁移)", head):
            continue
        allowed = TOKEN_ALLOW.get(rel, set())
        for i, line in enumerate(text.splitlines(), 1):
            for token, hint in RETIRED_TOKENS:
                if token in line and token not in allowed:
                    problems.append(f"{rel}:{i} 还在用 `{token}`：{hint}")
            if LEGACY_PATH_RE.search(line):
                problems.append(f"{rel}:{i} 出现 v1 路径 `库/…`：v2 是 `books/<uid>/`")
    return problems


def check_links(repo: Path) -> list[str]:
    problems: list[str] = []
    for path in _md_files(repo):
        if _generated(repo, path):
            continue
        rel = path.relative_to(repo).as_posix()
        in_fence = False
        for i, line in enumerate(_read(path).splitlines(), 1):
            if FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for target in LINK_RE.findall(re.sub(r"`[^`]*`", "", line)):
                if target.startswith(("http://", "https://", "mailto:", "data:", "#", "/", "~")):
                    continue
                if target.startswith(WORKSPACE_PREFIXES):
                    continue
                clean = target.split("#", 1)[0].split("?", 1)[0]
                if not clean or not (path.parent / clean).exists():
                    problems.append(f"{rel}:{i} 链接目标不存在：{target}")
    return problems


def check_ports(repo: Path) -> list[str]:
    allowed = set(app_ports(repo).values())
    problems: list[str] = []
    for path in _md_files(repo):
        if _generated(repo, path):
            continue
        rel = path.relative_to(repo).as_posix()
        in_fence = False
        for i, line in enumerate(_read(path).splitlines(), 1):
            if FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for port in PORT_RE.findall(line):
                if int(port) not in allowed:
                    problems.append(
                        f"{rel}:{i} 写了 127.0.0.1:{port}，代码里没有这个默认端口（默认：{sorted(allowed)}）"
                    )
    return problems


def check_numbers(repo: Path, data_root: Path) -> list[str]:
    try:
        from .scripts import doc_numbers
    except Exception as exc:  # pragma: no cover - 只在安装破损时发生
        return [f"导不进 doc_numbers：{exc}"]
    doc = repo / "AGENTS.md"
    if not doc.exists():
        return ["缺 AGENTS.md"]
    block = doc_numbers.read_block(_read(doc))
    if block is None:
        return ["AGENTS.md 缺「现状」numbers 块"]
    try:
        got = doc_numbers.collect(root=str(data_root))
    except Exception as exc:
        return [f"读不到现库数字（{data_root}）：{exc}"]
    if doc_numbers.same_numbers(doc_numbers.render(got), block):
        return []
    return [f"AGENTS.md「现状」数字与现库不一致：{doc_numbers.render(got).splitlines()[0]}"]


def _matches(rel: str, pattern: str) -> bool:
    pattern = pattern.strip("` ")
    return fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, pattern.replace("**", "*"))


def check_since(repo: Path, rev: str) -> tuple[list[str], list[str]]:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "diff", "--name-only", rev, "--"], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        return [], [f"git 不可用，跳过 --since：{exc}"]
    if out.returncode != 0:
        return [], [f"git diff {rev} 失败，跳过 --since：{out.stderr.strip()[:160]}"]
    changed = {line.strip() for line in out.stdout.splitlines() if line.strip()}
    warns: list[str] = []
    for path in status_docs(repo):
        rel = path.relative_to(repo).as_posix()
        if rel in changed:
            continue
        doc = parse_status(_read(path), rel)
        for pattern in doc.sources:
            hits = [c for c in changed if _matches(c, pattern)]
            if hits:
                warns.append(f"{rel}: 失效源 {pattern} 有改动（{hits[0]} 等 {len(hits)} 个），复核后更新状态日期")
    return warns, []


def check_repo(repo: Path = REPO_ROOT, data_root: Path | None = None, since: str | None = None) -> Report:
    repo = Path(repo)
    data_root = Path(data_root) if data_root else None
    report = Report()
    for label, problems in (
        ("facts", check_facts(repo)),
        ("status", check_status(repo)),
        ("index", check_index(repo)),
        ("tokens", check_tokens(repo)),
        ("links", check_links(repo)),
        ("ports", check_ports(repo)),
    ):
        report.fails.extend(f"[{label}] {p}" for p in problems)
    if data_root is None:
        report.skips.append("numbers: 没给 --data-root，跳过现库数字")
    else:
        report.fails.extend(f"[numbers] {p}" for p in check_numbers(repo, data_root))
    if since:
        warns, skips = check_since(repo, since)
        report.warns.extend(f"[since] {w}" for w in warns)
        report.skips.extend(f"[since] {s}" for s in skips)
    return report


def _replace_block(path: Path, block: str, intro: str, start: str, end: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        text = _read(path)
        at, et = text.find(start), text.find(end)
        if at >= 0 and et > at:
            text = text[:at] + block + text[et + len(end) + 1 :]
        else:
            text = text.rstrip("\n") + "\n\n" + block
    else:
        text = intro + "\n" + block
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write(repo: Path = REPO_ROOT) -> list[str]:
    repo = Path(repo)
    facts_intro = "# 事实（生成）\n\n脚本、别名、端口与版本都从代码读；本文件由 `book-corpus docs write` 重写。\n\n"
    index_intro = (
        "# 文档索引\n\n`docs/adr`、`docs/design`、`docs/briefs` 的状态与日期；\n由 `book-corpus docs write` 重写。\n\n"
    )
    _replace_block(repo / FACTS, render_facts(repo), facts_intro, FACTS_START, FACTS_END)
    _replace_block(repo / INDEX, render_index(repo), index_intro, INDEX_START, INDEX_END)
    return [FACTS.as_posix(), INDEX.as_posix()]


def run_check(repo: Path = REPO_ROOT, data_root: Path | None = None, since: str | None = None) -> int:
    report = check_repo(repo, data_root, since)
    for message in report.fails:
        print(f"[fail] {message}")
    for message in report.warns:
        print(f"[warn] {message}")
    for message in report.skips:
        print(f"[skip] {message}")
    if report.fails:
        print(f"不过：{len(report.fails)} 项失败")
        return 1
    print(f"全过：facts/status/index/tokens/links/ports{'/numbers' if data_root else ''}")
    return 0
