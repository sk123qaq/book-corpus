#!/usr/bin/env python3
"""提交级代码→文档耦合提醒（G4）。

检查本次暂存文件（或 `--since` / `--files` 指定的文件）是否命中了任意活文档、
应用手册、验证地图或已知红表的 `失效源：` 声明，而对应文档未一并修改。

默认只打印警告、退出码 0；加 `--strict` 时返回退出码 1。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from bookcorpus import docfacts

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git_files(repo: Path, args: list[str]) -> set[str]:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        return set()
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def collect_changed(repo: Path, since: str | None = None, files: list[str] | None = None) -> set[str]:
    if files:
        return {f.strip().lstrip("./") for f in files if f.strip()}
    if since:
        return _git_files(repo, ["diff", "--name-only", since, "--"])
    return _git_files(repo, ["diff", "--cached", "--name-only", "--"])


def run(repo: Path = REPO_ROOT, since: str | None = None, files: list[str] | None = None, strict: bool = False) -> int:
    changed = collect_changed(repo, since=since, files=files)
    warns = docfacts.check_changed_files(repo, changed)
    if not warns:
        return 0
    tag = "fail" if strict else "warn"
    for item in warns:
        print(f"[{tag}] [docs-coupling] {item}")
    if strict:
        print(f"不过：{len(warns)} 份关联文档未随代码同步更新")
        return 1
    print(f"提示：{len(warns)} 份关联文档的失效源有改动，请确认是否需同步更新（当前仅提醒，不阻断提交）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查本次改动是否命中未同步更新的失效源文档")
    parser.add_argument("--repo", default=str(REPO_ROOT), help="仓库根目录")
    parser.add_argument("--since", default=None, help="用 git diff --name-only <rev> 代替暂存区")
    parser.add_argument("--files", nargs="*", default=None, help="显式指定改动文件路径（测试用）")
    parser.add_argument("--strict", action="store_true", help="有警告时返回退出码 1")
    args = parser.parse_args(argv)
    return run(Path(args.repo), since=args.since, files=args.files, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
