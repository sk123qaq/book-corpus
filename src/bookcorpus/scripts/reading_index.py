#!/usr/bin/env python3
"""分层读的章表索引：看状态或整库重建。

书架只需要每本多少章、多少字。索引把逐文件读全文换成一次 scandir + stat，
改一章只重读那个文件。库在 build/阅读/shelf-index.db，删掉可重建。

用法:
  book-corpus run reading_index --status
  book-corpus run reading_index --rebuild
  book-corpus run reading_index            # 增量核对（同服务启动时的动作）
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

from bookcorpus.reading.shelf_index import PARSER_VERSION, SCHEMA_VERSION, ShelfIndex


def main(argv=None):
    parser = argparse.ArgumentParser(description="分层读章表索引")
    parser.add_argument("--status", action="store_true", help="只打印索引状态，不建库")
    parser.add_argument("--rebuild", action="store_true", help="删掉现有索引，从磁盘整库重建")
    args = parser.parse_args(argv)

    index = ShelfIndex(L.PATHS)
    if args.status:
        stats = index.stats()
        meta = stats["meta"]
        print(f"索引：{stats['path']}")
        if stats.get("missing"):
            print("状态：未建（首次打开书架时自动建，或跑 --rebuild）")
            return 0
        size_mb = stats["bytes"] / 1048576.0
        print(f"规模：{stats['books']} 本 / {stats['chapters']} 章，{size_mb:.1f} MB")
        print(
            f"版本：schema={meta.get('schema_version', '?')} "
            f"parser={meta.get('parser_version', '?')}，建库时间 {meta.get('built_at', '?')}"
        )
        return 0

    if args.rebuild:
        print("整库重建中…")
        result = index.rebuild()
        verb = "重建"
    else:
        result = index.ensure()
        verb = "增量核对"
    print(
        f"{verb}完成：{result['mode']}，解析 {result['parsed']} 个文件，"
        f"删除 {result['removed']} 条，{result['books']} 本 / {result['chapters']} 章，"
        f"{result['elapsed']:.1f} 秒"
    )
    print(f"口径版本：schema={SCHEMA_VERSION} parser={PARSER_VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
