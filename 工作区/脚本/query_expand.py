#!/usr/bin/env python3
"""查询侧扩展：把用户的口语问法换成 4 条书里可能出现的话，再并回段落倒排。

  query_expand.py "人怎么会变成只会执行的机器" -n 20
  query_expand.py --phrases-only "打工的人为什么被换掉"
  query_expand.py "..." --json --no-cache

与 build_expand 的区别：build_expand 把 8.7 万章全扩一遍（约 $15、数小时），
本脚本只在查询时扩 4 条短语（约 $0.0003、1 次调用），用于补「一个原文词都没有」的召回缺口。
缓存落 目录/检索/expand_queries/<sha1前16位>.json，同一问法第二次不再调用模型。
"""
import argparse, hashlib, json, os, re, sqlite3, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import build_expand
import lib_fts_cjk as cjk
import qsearch

DB = os.path.join(ROOT, "目录", "检索", "index.db")
CACHE = os.path.join(ROOT, "目录", "检索", "expand_queries")

PROMPT = """读者在中文书库里找答案，但他打的词原文里一个都没有。请给出 4 条检索式，每条不超过 14 字，用于在书里找到讲这件事的段落。

硬性要求：
1. 每条至少含 1 个读者没打过、但正文学者会写的词（学术用语、译名、上位概念）；
2. 至少 2 条是大白话，读者真的会这么打字；
3. 不要写「为什……？」这种考卷问句，不要引号，不要编号之外的符号；
4. 只输出 4 行，一行一条。

正例。读者问：「人怎么会变成只会听话的工具」
竞品条目：1. 官僚制把人变成零件 2. 异化劳动 3. 工具理性 4. 服从权威的实验

读者问：{q}"""


def phrases(q, effort="low", max_out=12000, timeout=300, provider="commandcode"):
    os.makedirs(CACHE, exist_ok=True)
    h = hashlib.sha1((provider + "|" + effort + "|" + q).encode()).hexdigest()[:16]
    p = os.path.join(CACHE, h + ".json")
    if os.path.isfile(p):
        cached = json.load(open(p, encoding="utf-8"))["phrases"]
        if len(cached) >= 3:
            return cached
    last = ""
    for attempt in (1, 2):
        cap = max_out if attempt == 1 else max_out * 2
        ns = argparse.Namespace(provider=provider, effort=effort, max_out=cap)
        txt, usage = build_expand.call(PROMPT.format(q=q), ns, timeout=timeout)
        out = []
        for line in txt.splitlines():
            line = re.sub(r"^\s*[\d.、)\-]+\s*", "", line).strip().strip("「」\"'")
            if 2 <= len(line) <= 24:
                out.append(line)
        out = out[:4]
        if len(out) >= 3:
            with open(p + ".tmp", "w", encoding="utf-8") as fh:
                json.dump({"q": q, "phrases": out, "usage": usage}, fh,
                          ensure_ascii=False, indent=1)
                fh.write("\n")
            os.replace(p + ".tmp", p)
            return out
        last = txt
    raise RuntimeError("模型两次都没给可用检索式（末次回包 %d 字）" % len(last))


def run(con, text, n, min_chars=40, rerank=False):
    groups = [g for g in re.split(r"[\s,]+", text) if g]
    expr = " OR ".join("(%s)" % cjk.query_expr(g) for g in groups if cjk.query_expr(g))
    hits, ms = qsearch.search(con, expr, groups, [], n, min_chars, rerank, pool=2000)
    return hits, ms


def rrf(lists, k=60, n=20, grain="para"):
    if grain == "chapter":
        score, best = {}, {}
        for hits, w in lists:
            for i, r in enumerate(hits, 1):
                score[r["path"]] = score.get(r["path"], 0.0) + w / (k + i)
                if r["path"] not in best:
                    best[r["path"]] = dict(r)
        out = []
        for p in sorted(score, key=lambda p: (-score[p], p))[:n]:
            r = best[p]
            r["score"] = round(score[p], 6)
            out.append(r)
        return out
    score, by_key = {}, {}
    for hits, w in lists:
        for i, r in enumerate(hits, 1):
            key = (r["path"], r["line"])
            score[key] = score.get(key, 0.0) + w / (k + i)
            by_key.setdefault(key, r)
    order = sorted(score, key=lambda key: (-score[key], key[0], key[1]))
    out = []
    for key in order[:n]:
        r = dict(by_key[key])
        r["score"] = round(score[key], 6)
        out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", help="基底检索词（空格分隔），也作为默认的问法")
    ap.add_argument("--question", help="给人看的原始问法；模型按它扩展，基底仍用 query")
    ap.add_argument("-n", "--limit", type=int, default=20)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--provider", default="commandcode",
                    help="扩展用的模型提供方，默认 commandcode；mimo 有内容风控，opencode 走本机 key")
    ap.add_argument("--phrases-only", action="store_true")
    ap.add_argument("--min-chars", type=int, default=40)
    ap.add_argument("--rerank", action="store_true")
    ap.add_argument("--grain", default="para", choices=["para", "chapter"],
                    help="融合粒度：para 章内段落级（旧口径）/ chapter 章级名次，累加式 w/(k+i) 同 qsearch --route-mode rrf；但 qsearch 两路等权 1:1，这里基底权重看 --base-weight")
    ap.add_argument("--base-weight", type=float, default=3.0,
                    help="基底列表的 RRF 权重（original-copies）；扩展短语恒为 1.0。旧口径是 3，章级实验测 1 与 2")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    q = a.question or a.query
    try:
        ps = phrases(q, a.effort, provider=a.provider)
    except Exception as exc:
        if a.phrases_only:
            raise
        print("扩展失败，退回原问法: %s" % exc, file=sys.stderr)
        ps = []
    if a.phrases_only:
        print(json.dumps(ps, ensure_ascii=False))
        return
    con = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True)
    base, _ = run(con, a.query, 200, a.min_chars, a.rerank)
    lists = [(base, a.base_weight)]
    for p in ps:
        lists.append((run(con, p, 20, a.min_chars, a.rerank)[0], 1.0))
    out = rrf(lists, 60, a.limit, a.grain)
    if a.json:
        print(json.dumps({"query": q, "phrases": ps,
                          "ms": round((time.time() - t0) * 1000, 1), "hits": out},
                         ensure_ascii=False, indent=1))
        return
    print("%s  %d 条  %.1f s  扩展 %s" % (q, len(out), time.time() - t0,
                                        " / ".join(ps)), file=sys.stderr)
    for i, r in enumerate(out, 1):
        print("%2d %-66s:%d  %.4f" % (i, r["path"], r["line"], r["score"]))
        print("   " + re.sub(r"\s+", " ", r["text"])[:150])


if __name__ == "__main__":
    main()
