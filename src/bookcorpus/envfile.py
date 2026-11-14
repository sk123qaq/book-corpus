"""项目配置：仓库根的 ``.env`` 是主入口。

真实环境变量永远优先：这里只补没设过的键（``os.environ.setdefault``），
不会覆盖 shell 里已经导出的值。查找顺序：

1. 显式传入的路径
2. ``BOOK_CORPUS_ENV``（指了就不回退，便于测试与隔离运行）
3. 仓库根 ``.env``
4. 当前目录 ``.env``（兼容在工作区里直接跑脚本）
5. ``~/.config/book-corpus/.env``（旧位置，仍然认）

``.env`` 格式按常见约定：``KEY=value``、可写 ``export`` 前缀、值可用单双引号
包起来并跨行。解析失败的行忽略，不让配置错误挡住启动。

写侧只有一个入口 :func:`set_value`：``book-corpus init`` 用它把 ``BOOKS_ROOT``
记进来，让之后的命令不必再带 ``--root``。写盘只改那一行，注释与顺序保留。
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = ".env"
ENV_OVERRIDE = "BOOK_CORPUS_ENV"
HOME_ENV_DIR = Path(".config") / "book-corpus"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def project_env_path(repo_root: str | Path | None = None) -> Path:
    """仓库根 ``.env`` 的路径（不保证存在）。"""

    return Path(repo_root or PROJECT_ROOT) / ENV_FILE


def candidates(repo_root: str | Path | None = None) -> list[Path]:
    """按优先级列出现场可能生效的配置文件。"""

    return [
        project_env_path(repo_root),
        Path.cwd() / ENV_FILE,
        Path.home() / HOME_ENV_DIR / ENV_FILE,
    ]


def path(explicit: str | Path | None = None, repo_root: str | Path | None = None) -> Path | None:
    """找到第一个可读的配置文件；显式路径与 ``BOOK_CORPUS_ENV`` 不回退。"""

    if explicit:
        target = Path(explicit).expanduser()
        return target if target.is_file() else None
    override = (os.environ.get(ENV_OVERRIDE) or "").strip()
    if override:
        target = Path(override).expanduser()
        return target if target.is_file() else None
    seen: set[Path] = set()
    for candidate in candidates(repo_root):
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            return candidate
    return None


def parse(text: str) -> dict[str, str]:
    """把 ``.env`` 文本解析成键值对；后出现的键覆盖先出现的。"""

    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] not in ("'", '"') and " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = value.replace("\\n", "\n").replace('\\"', '"')
        if key:
            values[key] = value
    return values


def read(target: str | Path) -> dict[str, str]:
    try:
        return parse(Path(target).read_text(encoding="utf-8-sig"))
    except OSError:
        return {}


def load(explicit: str | Path | None = None, repo_root: str | Path | None = None) -> Path | None:
    """读入配置文件，返回实际生效的路径（没有则 ``None``）。"""

    target = path(explicit, repo_root)
    if target is None:
        return None
    for key, value in read(target).items():
        os.environ.setdefault(key, value)
    return target


def writable_target(explicit: str | Path | None = None, repo_root: str | Path | None = None) -> Path:
    """写侧的目标文件：与读同一套优先级，但允许文件还不存在。

    1. 显式传入的路径
    2. ``BOOK_CORPUS_ENV``：指了就用它，没有就建一个（测试靠这条隔离）
    3. 已经存在的那份配置（cwd / 用户目录）
    4. 仓库根 ``.env``：兜底，init 之后新建的就是它
    """

    if explicit:
        return Path(explicit).expanduser()
    override = (os.environ.get(ENV_OVERRIDE) or "").strip()
    if override:
        return Path(override).expanduser()
    existing = path(repo_root=repo_root)
    return existing if existing is not None else project_env_path(repo_root)


def _line(key: str, value: str) -> str:
    """一行 ``KEY=value``；有空白、引号或 ``#`` 时加双引号，与 :func:`parse` 对齐。"""

    if not value:
        return "%s=" % key
    if any(ch.isspace() or ch in "\"'#" for ch in value):
        return '%s="%s"' % (key, value.replace('"', '\\"'))
    return "%s=%s" % (key, value)


def _name_of(raw: str) -> str:
    """这一行是哪个键；注释行与不是 ``KEY=value`` 的行返回空串。"""

    body = raw.strip()
    if body.startswith("export "):
        body = body[7:].lstrip()
    if "=" not in body or body.startswith("#"):
        return ""
    return body.split("=", 1)[0].strip()


def set_value(key: str, value: str, target: str | Path | None = None, repo_root: str | Path | None = None) -> Path:
    """把 ``key=value`` 写进配置文件，返回实际写入的路径。

    已有该键就原地替换，没有就追加到末尾；其余行（注释、顺序、其它键）原样保留。
    写盘走临时文件加 ``os.replace``，与仓库其余写文件的规矩一致。
    """

    dest = writable_target(target, repo_root)
    lines = dest.read_text(encoding="utf-8-sig").splitlines() if dest.is_file() else []
    new = _line(key, value)
    out, done = [], False
    for raw in lines:
        if not done and _name_of(raw) == key:
            out.append(new)
            done = True
            continue
        out.append(raw)
    if not done:
        out.append(new)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.replace(dest)
    return dest
