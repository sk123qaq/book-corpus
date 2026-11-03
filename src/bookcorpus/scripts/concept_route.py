#!/usr/bin/env python3
"""把一句人话路由到 概念表.tsv 的键，再交给 qsearch。

  concept_route.py "打工的人为什么被换掉"            # 只打印路由结果
  concept_route.py "打工的人为什么被换掉" -n 10      # 路由 + 段落检索
  concept_route.py --json "..." 

为什么需要它：`qsearch --auto-concept` 做的是字符串包含，用户问句里没有概念表的词时返回 None
（60 题 gold 只认出 10 题）。这一步改成让模型从概念表的键里挑 3 个，代价一次调用。
缓存落 目录/检索/route_cache/<sha1>.json。
"""
import argparse, hashlib, json, os, re, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib_common import ROOT, PATHS  # noqa: E402
sys.path.insert(0, HERE)
import build_expand
import lib_fts_cjk as cjk
import qsearch

DB = PATHS.index_db
CTAB = PATHS.concepts
CACHE = os.path.join(PATHS.index_dir, "route_cache")

PROMPT = """下面是书库的概念表，一行一个键，冒号后是它的同义说法。

{table}

用户的问题：{q}

从上面挑出最可能讲这件事的最多 3 个键，按相关度从高到低，一行一个，只输出键名，不要编号，不要解释。

两条硬规矩：
1. 只能输出上表里存在的键，一个都不许编；
2. 如果上表里没有一个键真的讲这件事，就只输出「无」这一个字，不要硬挑。硬挑会把读者带去看不相干的书。

一个正例：
概念表有「异化：劳动异化,物化」「内卷：过密化,精细化竞争」「自由：消极自由,积极自由」
用户的问题：为什么打工的人觉得自己随时可以被换掉
输出：
异化
内卷

一个反例（表里没有合适的键，就必须说无）：
用户的问题：这本书的译者是谁
输出：
无"""


def table_keys():
    import csv
    with open(CTAB, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def live_keys(db=DB):
    """book_concept 里有落点的键。索引没建时返回空集，调用方不要过滤。"""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        try:
            return {k for (k,) in con.execute("SELECT DISTINCT key FROM book_concept")}
        finally:
            con.close()
    except sqlite3.Error as exc:
        sys.stderr.write("live_keys 读 %s 失败，概念表这次不做过滤：%s\n" % (db, exc))
        return set()


def drop_dead(rows, db=DB):
    """book_concept 零行的键不进提示词：模型选了也是空转。"""
    live = live_keys(db)
    if not live:
        return rows, []
    dead = [r["key"] for r in rows if r["key"] not in live]
    return [r for r in rows if r["key"] in live], dead


def table_fingerprint():
    """概念表.tsv 的内容哈希前 8 位。它进缓存键，概念表改了就不命中旧缓存。"""
    with open(CTAB, "rb") as fh:
        return hashlib.sha1(fh.read()).hexdigest()[:8]


def compact_table(rows):
    out = []
    for r in rows:
        al = ",".join(a for a in r["aliases"].split(",") if a)
        out.append("%s：%s" % (r["key"], al))
    return "\n".join(out)


NONE = "无"

# 提示词内容指纹进缓存键：改 PROMPT 后旧缓存自然失效
PROMPT_VERSION = hashlib.sha1(PROMPT.encode()).hexdigest()[:8]


def route(q, rows=None, effort="low", max_out=12000, timeout=180, tag="", db=DB):
    """返回最多 3 个概念键；一个都不合适时返回空列表，让调用方退回段落档。

    空列表是合法答案，不是失败，所以调用方不要把它当异常。
    """
    rows = rows or table_keys()
    rows, skipped = drop_dead(rows, db)
    keys = [r["key"] for r in rows]
    os.makedirs(CACHE, exist_ok=True)
    h = hashlib.sha1((tag + "|v3|" + PROMPT_VERSION + "|" + effort + "|"
                      + table_fingerprint() + "|" + q).encode()).hexdigest()[:16]
    p = os.path.join(CACHE, h + ".json")
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as fh:
                cached = json.load(fh)
            return cached.get("picked") or []
        except (OSError, ValueError):
            pass  # 缓存坏了当未命中，重算后覆盖
    picked, txt = [], ""
    for cap in (max_out, max_out * 2):
        ns = argparse.Namespace(effort=effort, max_out=cap, provider="commandcode")
        txt, usage = build_expand.call(PROMPT.format(table=compact_table(rows), q=q), ns,
                                       timeout=timeout)
        for line in txt.splitlines():
            line = re.sub(r"^\s*[\d.、)\-]+\s*", "", line).strip()
            if line == NONE:
                picked = []
                break
            if line in keys and line not in picked:
                picked.append(line)
        picked = picked[:3]
        if picked or NONE in txt:
            break
    if not picked and NONE not in txt:
        raise RuntimeError("路由回包既没有合法键也没有「%s」（%d 字）" % (NONE, len(txt)))
    with open(p + ".tmp", "w", encoding="utf-8") as fh:
        json.dump({"q": q, "picked": picked, "skipped": skipped, "usage": usage}, fh,
                  ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(p + ".tmp", p)
    return picked


def search_keys(con, keys, n, min_chars=40, pool=2000):
    groups, exclude = [], []
    for k in keys:
        g, ex = qsearch.load_concept(k)
        for x in g:
            if x not in groups:
                groups.append(x)
        exclude += [e for e in ex if e not in exclude]
    expr = " OR ".join("(%s)" % cjk.query_expr(g) for g in groups if cjk.query_expr(g))
    if not expr:
        return [], 0.0
    hits, ms = qsearch.search(con, expr, groups, exclude, n, min_chars, False, pool)
    return hits, ms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("-n", "--limit", type=int, default=10)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    keys = route(a.query, effort=a.effort, db=a.db)
    if a.limit <= 0:
        print(json.dumps(keys, ensure_ascii=False))
        return
    con = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True)
    if not keys:
        print("%s → 无合适概念键，请改用段落档" % a.query, file=sys.stderr)
        if a.json:
            print(json.dumps({"query": a.query, "keys": [], "ms": 0.0, "hits": []},
                             ensure_ascii=False, indent=1))
        return
    hits, ms = search_keys(con, keys, a.limit)
    if a.json:
        print(json.dumps({"query": a.query, "keys": keys, "ms": round(ms, 1),
                          "hits": hits}, ensure_ascii=False, indent=1))
        return
    print("%s → %s  %d 条  %.0f ms" % (a.query, " / ".join(keys), len(hits), ms),
          file=sys.stderr)
    for i, r in enumerate(hits, 1):
        print("%2d %-66s:%d  %.2f" % (i, r["path"], r["line"], r["score"]))
        print("   " + re.sub(r"\s+", " ", r["text"])[:150])


if __name__ == "__main__":
    main()
