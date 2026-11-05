#!/usr/bin/env python3
"""日课：每天从书库里挑一章，生成一期 300 到 500 字的稿子。

  daily.py [--date YYYY-MM-DD] [--book B0003] [--chapter 2] [--dry-run] [--force]
  daily.py install-timer [--at 07:00] [--print]

数据写到 <根>/日课/YYYY-MM-DD.json，日志写到 <根>/日课/日志/YYYY-MM-DD.log。
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import lib_common as L

from bookcorpus import daily as D
from bookcorpus import pimodel

REPO = HERE.parents[2]


def parse_day(value):
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("日期要写成 YYYY-MM-DD") from None


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="book-corpus daily",
        description="每天从书库里挑一章，写一期日课。",
    )
    parser.add_argument("--date", type=parse_day, default=None, help="补哪一天，默认今天")
    parser.add_argument("--book", help="指定书 uid，例如 B0003")
    parser.add_argument("--chapter", type=int, help="指定章号，例如 2")
    parser.add_argument("--model", help="覆盖 books.toml 的模型")
    parser.add_argument("--thinking", choices=pimodel.LEVELS, help="覆盖思考等级")
    parser.add_argument("--force", action="store_true", help="当天已有也重写")
    parser.add_argument("--dry-run", action="store_true", help="只打印选择和提示词，不调模型")

    sub = parser.add_subparsers(dest="cmd")
    timer = sub.add_parser("install-timer", help="装 systemd 用户定时器")
    timer.add_argument("--at", default=D.DEFAULT_AT, help="每天几点，默认 07:00")
    timer.add_argument("--print", dest="print_only", action="store_true", help="只打印单元文件")

    args = parser.parse_args(argv)
    root = Path(L.ROOT)

    if args.cmd == "install-timer":
        try:
            units, paths = D.install_timer(REPO, root, args.at, args.print_only)
        except D.DailyError as exc:
            print(f"日课定时器：{exc}", file=sys.stderr)
            return 1
        if args.print_only:
            print(units["service"])
            print(units["timer"])
            return 0
        print(f"已装：{'、'.join(str(path) for path in paths)}")
        print(f"每天 {args.at} 生成；看状态：systemctl --user list-timers book-corpus-daily.timer")
        return 0

    try:
        path = D.generate(
            root,
            day=args.date,
            book_uid=args.book,
            chapter_no=args.chapter,
            model=args.model,
            thinking=args.thinking,
            force=args.force,
            dry_run=args.dry_run,
            log=print,
        )
    except D.DailyError as exc:
        print(f"日课没写成：{exc}", file=sys.stderr)
        return 1
    if path:
        print(f"日课：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
