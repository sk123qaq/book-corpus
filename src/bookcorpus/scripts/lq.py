#!/usr/bin/env python3
"""只读查询 目录/library.db。默认每行一条记录，字段用制表符分隔。

用法:
  python3 lq.py "SELECT uid,title,path FROM book WHERE title LIKE '%乡土中国%'"
  python3 lq.py -H "SELECT subject1,count(*) FROM book GROUP BY subject1"
  echo "SELECT count(*) FROM book" | python3 lq.py

选项:
  -H, --header   第一行打印列名
  -j, --json     输出 JSON 数组（每行一个对象）
  -s, --sep      字段分隔符（默认制表符）

库以只读模式打开（file:...?mode=ro），查询语句不会写库。
"""
import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L  # noqa: E402


def unique_names(names):
    out = []
    seen = {}
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else "%s#%d" % (n, seen[n]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-H", "--header", action="store_true")
    ap.add_argument("-j", "--json", action="store_true")
    ap.add_argument("-s", "--sep", default="\t")
    ap.add_argument("sql", nargs="?", default="")
    args = ap.parse_args()
    sql = args.sql or sys.stdin.read()
    if not sql.strip():
        raise SystemExit("没有 SQL（放参数里或从标准输入读）")
    db = L.PATHS.library_db
    if not os.path.isfile(db):
        raise SystemExit("没有 %s，先跑 build_library.py" % db)
    con = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
    try:
        cur = con.execute(sql)
    except sqlite3.Error as e:
        raise SystemExit("SQL 错误: %s" % e)
    names = [d[0] for d in (cur.description or [])]
    rows = cur.fetchall()
    con.close()
    if args.json:
        keys = unique_names(names)
        print(json.dumps([dict(zip(keys, r)) for r in rows], ensure_ascii=False, indent=1))
        return
    if args.header:
        print(args.sep.join(names))
    for r in rows:
        print(args.sep.join("" if v is None else str(v) for v in r))


if __name__ == "__main__":
    main()
