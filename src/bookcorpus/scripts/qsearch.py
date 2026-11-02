#!/usr/bin/env python3
"""词法检索：段落倒排 + 组覆盖度重排，书级聚合。

  qsearch.py 自由 -n 20
  qsearch.py --concept 自由 -n 20
  qsearch.py 自由 --book -n 20
  qsearch.py 自由 --json

概念模式把 目录/概念表.tsv 的同义词当 OR 组展开，排除词只在重排时扣分，不在召回时过滤。
"""
import argparse, csv, json, math, os, re, sqlite3, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib_common import PATHS  # noqa: E402
sys.path.insert(0, HERE)
import lib_fts_cjk as cjk

DB = PATHS.index_db
CONCEPTS = PATHS.concepts


def all_concepts():
    out = {}
    with open(CONCEPTS, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            out[r["key"]] = [a for a in r["aliases"].split(",") if a]
    return out


def resolve(text):
    """把用户打的词认成概念键：键精确 > 别名精确 > 最长的别名包含关系。"""
    table = all_concepts()
    if text in table:
        return text
    best = None
    for key, aliases in table.items():
        for al in aliases:
            if al == text:
                return key
            if len(al) >= 2 and (al in text or text in al):
                if best is None or len(al) > len(best[1]):
                    best = (key, al)
    return best[0] if best else None


def load_concept(key):
    with open(CONCEPTS, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r["key"] == key:
                aliases = [a for a in r["aliases"].split(",") if a]
                exclude = [x for x in r["exclude"].split(",") if x and x != "无"]
                return [key] + aliases, exclude
    sys.exit("概念表里没有 " + key)


ASCII_ALPHA = re.compile(r"[A-Za-z]")


def group_in(g, low):
    """组词是否算命中（low 是已 lower 的正文）。

    ASCII 与 FTS5 的 unicode61 折叠对齐，折小写再比；多词别名（如
    learned helplessness）在 expr 里是各词 AND，可不相邻，这里同口径：
    各词都在即算命中，不要求字面连续。
    """
    if " " in g:
        return all(w in low for w in g.split())
    return g in low


def group_hits(text, groups, exclude):
    """单段组命中数。search 的热循环自己内联了这段（带预计算），这里留给外部调用。"""
    if ASCII_ALPHA.search("".join(groups)):
        body = text
        if exclude:
            body = re.sub("|".join(map(re.escape, exclude)), "", body)
        low = body.lower()
        return sum(1 for g in groups if group_in(g.lower(), low))
    body = text
    if exclude:
        body = re.sub("|".join(map(re.escape, exclude)), "", body)
    return sum(1 for g in groups if g in body)


DEF_CUE = re.compile(r"所谓|指的是|也就是说|本质上|可以定义为|这意味着|换句话说")


def rerank_bonus(text, line, groups):
    """模型无关的重排特征：定义式措辞加分，引号/对白/卷首页减分。"""
    bonus = 0.0
    if DEF_CUE.search(text):
        bonus += 0.6
    quotes = text.count("“") + text.count("”") + text.count('"')
    if quotes / max(1, len(text)) > 0.02:
        bonus -= 0.4
    if re.match(r"^\s*(#|\*\*|\||>)", text):
        bonus -= 0.5
    if line <= 3:
        bonus -= 0.5
    if any(group_in(g.lower(), text[:40].lower()) for g in groups):
        bonus += 0.3
    return bonus


def search(con, expr, groups, exclude, limit, min_chars, rerank=False, pool=400):
    """pool 是取多少段进打分。以前写死 400，导致 --book 只能在 190 本书里挑。

    pool <= 0 表示不设上限（全取）。
    """
    t0 = time.time()
    if not expr:
        # 查询词全是不可索引字符（全角符号/假名等）时 expr 为空，
        # MATCH '' 是 FTS5 语法错；这里返回空结果，不炸。
        return [], 0.0
    sql = ("SELECT p.id, p.path, p.book, p.line, p.text, bm25(para_fts) AS score "
           "FROM para_fts f JOIN para p ON p.id=f.rowid "
           "WHERE para_fts MATCH ? AND p.chars >= ? ORDER BY score")
    if pool and pool > 0:
        rows = con.execute(sql + " LIMIT ?", (expr, min_chars, pool)).fetchall()
    else:
        rows = con.execute(sql, (expr, min_chars)).fetchall()
    cand = []
    # 大小写判定与组词 lower 都只做一次；纯 CJK 查询的逐段路径与
    # 不折大小写的旧版完全同价
    ascii_groups = bool(ASCII_ALPHA.search("".join(groups)))
    low_groups = [g.lower() for g in groups] if ascii_groups else None
    for pid, path, book, line, text, score in rows:
        body = text
        if exclude:
            body = re.sub("|".join(map(re.escape, exclude)), "", body)
        if ascii_groups:
            low = body.lower()
            cov = sum(1 for g in low_groups if group_in(g, low))
        else:
            cov = sum(1 for g in groups if g in body)
        if cov == 0 and exclude:
            continue
        kana = sum(1 for ch in text if "\u3040" <= ch <= "\u30ff")
        penalty = 0.55 if kana > 0.1 * len(text) else 1.0
        rank_score = (-score + 0.8 * cov + 0.15 * math.log1p(len(text))) * penalty
        if rerank:
            rank_score += rerank_bonus(text, line, groups)
        cand.append({"id": pid, "path": path, "book": book, "line": line,
                     "text": text, "bm25": round(score, 4), "kana": round(kana / len(text), 2) if text else 0.0,
                     "coverage": cov, "score": round(rank_score, 4)})
    cand.sort(key=lambda r: (-r["score"], r["path"], r["line"]))
    ms = (time.time() - t0) * 1000
    return cand[:limit], ms


def search_expand(con, expr, kind="both", limit=200):
    if not expr:
        return []
    cond = "" if kind == "both" else " AND e.kind = ?"
    args = (expr, limit) if kind == "both" else (expr, kind, limit)
    try:
        rows = con.execute(
            "SELECT e.path, e.text, bm25(expand_fts) AS score FROM expand_fts "
            "JOIN expand e ON e.rowid = expand_fts.rowid "
            "WHERE expand_fts MATCH ?" + cond + " ORDER BY score LIMIT ?", args).fetchall()
    except sqlite3.OperationalError as exc:
        print("expand 档不可用（%s），只跑段落档" % exc, file=sys.stderr)
        return []
    return [{"path": p, "text": t, "bm25": round(sc, 4)} for p, t, sc in rows]


def fuse(para_cands, exp_cands):
    """RRF：段落档按章去重后与扩展档各出一份章级名次。"""
    scores, order = {}, []
    for i, r in enumerate(para_cands):
        if r["path"] not in scores:
            scores[r["path"]] = 0.0
            order.append(r["path"])
        scores[r["path"]] += 1.0 / (60 + i)
    for i, r in enumerate(exp_cands):
        if r["path"] not in scores:
            scores[r["path"]] = 0.0
            order.append(r["path"])
        scores[r["path"]] += 1.0 / (60 + i)
    return sorted(order, key=lambda p: (-scores[p], p)), scores


def rollup_by_book(cand, con, limit, mode="mix"):
    """把段落命中聚到书上。

    mode=mix（默认）按 0.7*最好段 + 0.3*均值，长选集不会因为段数多就压过讲透的一本。
    mode=sum 保留旧的求和口径，只是给对照留个后路。
    """
    best = {}
    for r in cand:
        b = best.setdefault(r["book"], {"book": r["book"], "paras": 0, "score": 0.0,
                                        "best": r})
        b["paras"] += 1
        b["score"] += r["score"]
        if r["score"] > b["best"]["score"]:
            b["best"] = r
    for b in best.values():
        if mode == "mix":
            b["score"] = 0.7 * b["best"]["score"] + 0.3 * b["score"] / max(1, b["paras"])
    rows = sorted(best.values(), key=lambda b: (-b["score"], b["book"]))[:limit]
    for b in rows:
        b["score"] = round(b["score"], 3)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?")
    ap.add_argument("--concept")
    ap.add_argument("--auto-concept", action="store_true", help="把查询词认成概念键再展开")
    ap.add_argument("-n", "--limit", type=int, default=20)
    ap.add_argument("--book", action="store_true")
    ap.add_argument("--book-score", default="mix", choices=["mix", "sum"],
                    help="qsearch 旧书级口径：mix=0.7*最好段+0.3*段落均值（默认）；"
                         "sum=段落求和（旧口径）。新书级默认 best_support 见 book_query.py")
    ap.add_argument("--expand", action="store_true", help="并上 build/检索/expand 的扩展文本档")
    ap.add_argument("--expand-kind", default="both", choices=["both", "prefix", "queries"])
    ap.add_argument("--rerank", action="store_true", help="加模型无关的定义式/引号/卷首页特征")
    ap.add_argument("--route-mode", default="append", choices=["append", "rrf"],
                    help="默认 append，保留基底前 10 名再把路由结果接在后面；rrf 为等权融合")
    ap.add_argument("--route", action="store_true",
                    help="模型把问句路由到概念表 3 个键，再与段落档等权 RRF（需联网，结果有缓存）")
    ap.add_argument("--pool", default="medium",
                    help="取多少段进打分：low=400 / medium=2000 / all=不限 / 也可给数字")
    ap.add_argument("--min-chars", type=int, default=40)
    ap.add_argument("--db", default=DB)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-exclude", action="store_true")
    a = ap.parse_args()
    if not (a.query or a.concept):
        ap.error("要给查询词或 --concept")
    if not a.concept and a.auto_concept and a.query:
        key = resolve(a.query.strip())
        if key:
            print("查询词认成概念 %s" % key, file=sys.stderr)
            a.concept = key
    if a.concept:
        groups, exclude = load_concept(a.concept)
        if a.no_exclude:
            exclude = []
        expr = " OR ".join("(%s)" % cjk.query_expr(g) for g in groups if cjk.query_expr(g))
        label = a.concept
    else:
        groups = cjk.split_groups(a.query)
        exclude = []
        expr = " OR ".join("(%s)" % cjk.query_expr(g) for g in groups if cjk.query_expr(g))
        label = a.query
    con = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True)
    pool = {"low": 400, "medium": 2000, "all": 0}.get(a.pool, None)
    if pool is None:
        try:
            pool = int(a.pool)
        except ValueError:
            ap.error("--pool 只认 low / medium / all 或整数")
    hits, ms = search(con, expr, groups, exclude,
                      max(a.limit, 2000) if a.book else a.limit,
                      a.min_chars, a.rerank, pool)
    if a.route and a.book:
        print("--book 是书级聚合，不支持问句路由；本次忽略 --route", file=sys.stderr)
    if a.route and not a.book:
        try:
            import concept_route
            keys = concept_route.route(a.query or label)
            second, ms2 = concept_route.search_keys(con, keys, 200, a.min_chars,
                                                    pool=pool)
        except Exception as exc:
            print("路由失败，退回段落档: %s" % exc, file=sys.stderr)
            keys, second = [], []
        if second and a.route_mode == "append":
            head = max(1, min(10, a.limit // 2))
            base = hits
            seen, out = set(), []
            for r in base[:head]:
                seen.add(r["path"])
                out.append(r)
            for r in second:
                if len(out) >= a.limit:
                    break
                if r["path"] not in seen:
                    seen.add(r["path"])
                    out.append(r)
            for r in base[head:]:
                if len(out) >= a.limit:
                    break
                if r["path"] not in seen:
                    seen.add(r["path"])
                    out.append(r)
            hits = out
        elif second:
            base = hits
            order, scores = fuse(base, second)
            best = {r["path"]: r for r in reversed(base)}
            best.update({r["path"]: r for r in reversed(second)})
            hits = []
            for pth in order[: a.limit]:
                r = dict(best.get(pth) or {"path": pth, "line": 0, "text": "",
                                           "book": pth.split("/md/")[0][2:], "coverage": 0})
                r["score"] = round(scores[pth], 6)
                hits.append(r)
            ms += ms2
    if a.expand:
        exp = search_expand(con, expr, a.expand_kind,
                            max(400, pool) if pool > 0 else -1)
        order, scores = fuse(hits, exp)
        best = {r["path"]: r for r in reversed(hits)}
        expmap = {r["path"]: r for r in exp}
        out = []
        for p in order[: a.limit] if not a.book else order[:400]:
            r = best.get(p) or {"path": p, "book": p.split("/md/")[0][2:], "line": 0,
                                "text": "", "score": 0.0, "coverage": 0}
            if p in expmap:
                r = dict(r)
                r["expand"] = expmap[p]["text"]
            r["score"] = round(scores[p], 6)
            out.append(r)
        if a.book:
            out = rollup_by_book(out, con, a.limit, a.book_score)
    elif a.book:
        out = rollup_by_book(hits, con, a.limit, a.book_score)
    else:
        out = hits
    if a.json:
        print(json.dumps({"query": label, "ms": round(ms, 1), "hits": out},
                         ensure_ascii=False, indent=1))
        return
    print("%s  %d 条  %.0f ms" % (label, len(out), ms), file=sys.stderr)
    for i, r in enumerate(out, 1):
        if a.book:
            b = r["best"]
            print("%2d %-58s +%d 段  %.2f  %s:%d" % (
                i, r["book"], r["paras"], r["score"], b["path"], b["line"]))
            print("   " + re.sub(r"\s+", " ", b["text"])[:150])
        else:
            print("%2d %-66s:%d  %.2f" % (i, r["path"], r["line"], r["score"]))
            print("   " + re.sub(r"\s+", " ", r["text"])[:150])


if __name__ == "__main__":
    main()
