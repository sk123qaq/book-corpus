#!/usr/bin/env python3
"""幂等 L0 Markdown 规范化脚本：单书、按 UID 或全库批量规范化 md/ 目录。

用法：
  book-corpus normalize --md-dir /tmp/test/md
  book-corpus normalize --root /tmp/ws --uid B0537 --backup-dir /tmp/backup
  book-corpus normalize --root /tmp/ws --all --backup-dir /tmp/backup --workers 4 --json
  book-corpus normalize --root /tmp/ws --uids B0130,B0537 --dry-run --json
  book-corpus normalize --root /tmp/ws --all --reclassify-roles --json
  book-corpus normalize --root /tmp/ws --books B0980,B1106 --reclassify-roles
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookcorpus import workspace
from bookcorpus.md.normalize import (
    DEFAULT_SPLIT_BLOCKS,
    DEFAULT_SPLIT_CHARS,
    normalize_book_md,
)


def _collect_targets(
    root: Path | None,
    *,
    md_dir: str | None = None,
    uids: list[str] | None = None,
    run_all: bool = False,
) -> list[Path]:
    if md_dir:
        return [Path(md_dir)]
    if root is None:
        raise ValueError("必须指定 --md-dir 或 --root/--uid/--all")
    books_dir = root / "books"
    if uids:
        return [books_dir / uid / "md" for uid in uids]
    if run_all:
        targets: list[Path] = []
        if books_dir.is_dir():
            for entry in sorted(books_dir.iterdir()):
                if not entry.is_dir() or entry.name.startswith("_"):
                    continue
                cand = entry / "md"
                if cand.is_dir():
                    targets.append(cand)
        return targets
    raise ValueError("请指定 --md-dir、--uid/--uids 或 --all")


def _run_one(
    md_path: Path,
    *,
    backup_dir: Path | None,
    dry_run: bool,
    split_chars: int,
    split_blocks: int,
    reclassify_roles_only: bool = False,
) -> dict[str, Any]:
    try:
        res = normalize_book_md(
            md_path,
            backup_dir=backup_dir,
            dry_run=dry_run,
            split_chars=split_chars,
            split_blocks=split_blocks,
            reclassify_roles_only=reclassify_roles_only,
        )
        res["ok"] = True
        return res
    except Exception as exc:
        return {
            "md_dir": str(md_path),
            "uid": md_path.parent.name,
            "ok": False,
            "changed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="幂等 L0 Markdown 规范化（支持单书与离线批量）。")
    ap.add_argument("--root", help="工作区根，默认按环境变量或当前目录查找 books.toml")
    ap.add_argument("--md-dir", help="直接指定单本书的 md/ 目录路径")
    ap.add_argument("--uid", help="指定单本书 UID（如 B0537）")
    ap.add_argument("--uids", help="逗号分隔的 UID 列表（如 B0130,B0537）")
    ap.add_argument("--books", help="逗号分隔的 UID 列表（同 --uids，如 B0980,B1106）")
    ap.add_argument("--all", action="store_true", help="扫描工作区 books/*/md 全部非冷宫书籍")
    ap.add_argument("--backup-dir", help="写前强制备份原 md/ 到该目录；备份失败则中止且绝不改写原盘")
    ap.add_argument("--dry-run", action="store_true", help="只统计拟变更，不写盘、不建备份")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出规范化统计")
    ap.add_argument("--workers", type=int, default=4, help="并发数（默认 4）")
    ap.add_argument("--split-chars", type=int, default=DEFAULT_SPLIT_CHARS, help="单章整书拆章字符阈值")
    ap.add_argument("--split-blocks", type=int, default=DEFAULT_SPLIT_BLOCKS, help="单章整书拆章 L1 块阈值")
    ap.add_argument(
        "--reclassify-roles",
        action="store_true",
        help="仅无损重标 chapter_role（更新 .manifest.json 与 front matter，不改正文、不拆章）",
    )
    args = ap.parse_args(argv)

    uid_list: list[str] | None = None
    if args.uid or args.uids or args.books:
        raw = ",".join(x for x in (args.uid, args.uids, args.books) if x)
        uid_list = [u.strip() for u in raw.split(",") if u.strip()]

    root: Path | None = None
    if not args.md_dir:
        root = Path(workspace.resolve(args.root))

    try:
        targets = _collect_targets(root, md_dir=args.md_dir, uids=uid_list, run_all=args.all)
    except ValueError as exc:
        ap.error(str(exc))

    backup_dir = Path(args.backup_dir) if args.backup_dir else None
    results: list[dict[str, Any]] = []
    workers = max(1, min(args.workers, max(1, len(targets))))
    if workers == 1 or len(targets) <= 1:
        for target in targets:
            results.append(
                _run_one(
                    target,
                    backup_dir=backup_dir,
                    dry_run=args.dry_run,
                    split_chars=args.split_chars,
                    split_blocks=args.split_blocks,
                    reclassify_roles_only=args.reclassify_roles,
                )
            )
    else:
        with futures.ThreadPoolExecutor(max_workers=workers) as pool:
            jobs = [
                pool.submit(
                    _run_one,
                    target,
                    backup_dir=backup_dir,
                    dry_run=args.dry_run,
                    split_chars=args.split_chars,
                    split_blocks=args.split_blocks,
                    reclassify_roles_only=args.reclassify_roles,
                )
                for target in targets
            ]
            for job in jobs:
                results.append(job.result())

    failed = [r for r in results if not r.get("ok")]
    changed = [r for r in results if r.get("changed")]
    summary = {
        "total": len(results),
        "changed": len(changed),
        "unchanged": len(results) - len(changed) - len(failed),
        "failed": len(failed),
        "dry_run": args.dry_run,
        "reclassify_roles_only": args.reclassify_roles,
        "results": results,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        action = "角色重标" if args.reclassify_roles else "规范化"
        mode = f"{action}演练" if args.dry_run else action
        print(
            f"{mode}完成：共 {summary['total']} 本，变更 {summary['changed']} 本，"
            f"无变更 {summary['unchanged']} 本，失败 {summary['failed']} 本"
        )
        for r in results:
            if not r.get("ok"):
                print(f"  FAIL {r['uid']}: {r.get('error', '')}", file=sys.stderr)
            elif args.reclassify_roles:
                roles = (r.get("stats") or {}).get("roles") or {}
                print(
                    f"  {'~' if r.get('changed') else '='} {r['uid']} "
                    f"文件更新={r.get('files_updated', 0)} "
                    f"roles=body:{roles.get('body', 0)}/notes:{roles.get('notes', 0)}/fb:{roles.get('front_back', 0)}"
                )
            else:
                st = r.get("stats") or {}
                print(
                    f"  {'~' if r.get('changed') else '='} {r['uid']} "
                    f"章 {st.get('chapters_before')}->{st.get('chapters_after')} "
                    f"html={st.get('html_tables_converted', 0)}表/{st.get('html_bytes_cleaned', 0)}B "
                    f"页码={st.get('page_lines_removed', 0)} 缝合={st.get('sentences_stitched', 0)} "
                    f"脚注={st.get('footnotes_converted', 0)} 删图={st.get('orphan_icons_removed', 0)}"
                )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
