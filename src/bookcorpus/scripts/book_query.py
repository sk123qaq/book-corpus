#!/usr/bin/env python3
"""查询规划：两套词、两段检索、最多 5 轮词法循环（B2.5）。

  book_query.py 礼治 秩序 法律
  book_query.py 石昊 天人族 收拾 --explain
  book_query.py 权力 日常生活 --broad
  book_query.py 礼治,秩序,法律 --json

一轮 = 一次书级查询（用本轮词集）。全词集只在最后给选定书选章时查一次，两段共用。
循环按「够不够好」决定要不要换一套词再查，判据纯词法：

  A  top1 书的命中段并起来覆盖全部名词组，且至少两段各覆盖 min(2, 名词组数) 个组
  B  top1 与 top2 的书级分差 >= 10%
  C  top1 书里名词组全覆盖的段落数 >= 3

  A 且（B 或 C）成立即停。C 成立时 A 必然成立，所以实际是 C 或 A 且 B。
  A 的「各覆盖 min(2, n) 组」是 2026-10-23 的放宽：原口径要求两段各自覆盖全部组，
  三段式在自然文本里几乎不可能到线（同一段很少同时出现三个名词组）。

换词顺序（方案 B2.5-方案.md）：
  1 名词集          2 全词集
  3 名词 + 概念表同义组（能解析到概念键时）
  4 全词 + 宽词组     （只在 --broad 或计划显式给出时）
  5 去掉最泛的一个词  （书级 df 最高者）

两段：书级排序用本轮词集，选章始终用全词集。最多 5 轮，到线上限记 stop=cap，
词集用尽记 stop=exhausted，返回质量最好的一轮（见 quality()）。

词性划分：名词 = 概念表命中 + 实体；动作词见 目录/概念表-动作词.tsv。名词集为空时退回全词集。

只读 目录/检索/index.db 与 目录/library.db。
"""
import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib_common import ROOT  # noqa: E402
sys.path.insert(0, HERE)
import book_readme  # noqa: E402
import book_score  # noqa: E402
import lib_fts_cjk as cjk  # noqa: E402
import qsearch  # noqa: E402

ACTIONS = os.path.join(ROOT, "目录", "概念表-动作词.tsv")
CONCEPTS = os.path.join(ROOT, "目录", "概念表.tsv")
BROAD = os.path.join(ROOT, "目录", "概念表-broad.tsv")
MAX_ROUNDS = 5
MARGIN = 0.10          # 判据 B 的 10%
# B6 验收后的默认值。qsearch --book 仍是 mix 旧口径，不在这里。
DEFAULTS = {"book_score": "best_support", "cards": True,
            "expand_on_demand": False, "max_rounds": MAX_ROUNDS}
MIN_CHARS = 40
POOL = 2000


def read_table(path):
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            key = (row.get("key") or "").strip()
            if not key:
                continue
            aliases = [a.strip() for a in (row.get("aliases") or "").split(",") if a.strip()]
            exclude = [a.strip() for a in (row.get("exclude") or "").split(",")
                       if a.strip() and a.strip() != "无"]
            out[key] = {"aliases": aliases, "exclude": exclude}
    return out


def load_actions(path=ACTIONS):
    out = set()
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            term = (row.get("term") or "").strip()
            if term:
                out.add(term)
    return out


def split_terms(terms, actions):
    """名词集 = 全词集去掉动作词。全被去掉时退回全词集。"""
    nouns = [t for t in terms if t not in actions]
    return nouns if nouns else list(terms)


def resolve(term, concepts):
    """认概念键：只认键与别名精确相等。

    不做子串包含：主表里「爱」会被包含关系认成「自由邦」、「人」认成「他人即地狱」、
    「幸福」认成「功利主义」、「政治」认成「承认」。那是查询期扩词的噪声源。
    """
    if term in concepts:
        return term
    for key, rec in concepts.items():
        if term in rec["aliases"]:
            return key
    return None


def dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


@dataclass
class Round:
    n: int
    kind: str
    terms: list
    groups: list
    hits: int
    top1: str
    top1_score: float
    top2_score: float
    margin: float
    cover_paras: int
    A: bool
    B: bool
    C: bool
    stop: bool
    ms: float
    skipped: str = ""


@dataclass
class Plan:
    query: str
    terms: list
    nouns: list
    action_dropped: list
    rounds: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    stop: str = ""
    best_round: int = 0
    quality: tuple = ()
    ranking: list = field(default_factory=list)
    chapters: list = field(default_factory=list)
    fetches: int = 0
    model_calls: int = 0
    ms: float = 0.0
    timings: dict = field(default_factory=dict)


def quality(r):
    """挑「最好的一轮」：先看到线，再到线里的 C 与 A且B，同分取更早。

    没到线的一轮不跨轮比较：换词后候选集与覆盖组都变了，去掉一个词几乎总能
    把覆盖与分差抬上去，拿它当「更好」会把书名次换差（O04 第 2 到 155，
    旧 gold q019 第 3 到 22、q035 第 10 到 29）。到线的一轮才有正向证据。
    一轮都没到线时返回第一轮（词最全、没被换词削过）；第一轮空则取最早非空轮。
    """
    if r.stop:
        return (2, int(r.C), int(r.A and r.B), -r.n)
    if r.top1:
        return (1, 0, 0, -r.n)
    return (0, 0, 0, -r.n)


def hit_rounds(hits, ranking, groups, margin=MARGIN, exclude=()):
    """一轮的停止判据。返回 (A, B, C, cover_paras)。

    A（2026-10-23 放宽）：top1 书的命中段并起来覆盖全部名词组，且至少两段各覆盖
    min(2, n) 个组。原口径要求两段各自覆盖全部组；三段式里三个词往往分散在相邻段，
    原口径 60 题只有 34 题到线。放宽后 45 题到线，轮数 1.72 到 1.40，书序读数不变。
    C 不变：覆盖全部组的段落数 >= 3。C 仍蕴含 A（全组覆盖的两段一定满足 A）。
    """
    top1 = ranking[0] if ranking else None
    if top1 is None:
        return False, False, False, 0
    need = min(2, len(groups))
    full = part = 0
    seen = set()
    for r in hits:
        if r["book"] != top1.book:
            continue
        cov = book_score.matched_groups(r["text"], groups, exclude)
        if len(cov) == len(groups):
            full += 1
        if len(cov) >= need:
            part += 1
        seen.update(cov)
    top2 = ranking[1].score if len(ranking) > 1 else 0.0
    m = (top1.score - top2) / top1.score if top1.score > 0 else 0.0
    A = len(seen) == len(groups) and part >= 2
    C = full >= 3
    B = m >= margin
    return A, B, C, full


class Planner:
    def __init__(self, con, meta=None, expand=None, actions=None, concepts=None,
                 broad=None, scheme="best_support", pool=POOL, min_chars=MIN_CHARS,
                 search_fn=None, df_fn=None, limit=10, exclude=None):
        self.con = con
        self.meta = book_score.load_meta() if meta is None else meta
        self.expand = book_score.load_expand(con) if expand is None else expand
        self.actions = load_actions() if actions is None else set(actions)
        self.concepts = read_table(CONCEPTS) if concepts is None else concepts
        self.broad = read_table(BROAD) if broad is None else broad
        self.scheme = scheme
        self.pool = pool
        self.min_chars = min_chars
        self.limit = limit
        self.exclude = list(exclude or [])
        self._search_fn = search_fn
        self._df_fn = df_fn
        self._hits = {}
        self._df = {}
        self._toc = {}
        self.fetches = 0

    # ---- 检索 -------------------------------------------------------
    def search(self, terms):
        key = tuple(dedup(terms))
        if key in self._hits:
            return self._hits[key]
        if self._search_fn is not None:
            hits, ms = self._search_fn(list(key))
        else:
            expr = " OR ".join("(%s)" % cjk.query_expr(t) for t in key if cjk.query_expr(t))
            hits, ms = qsearch.search(self.con, expr, list(key), self.exclude, 2000,
                                      self.min_chars, False, self.pool)
        self.fetches += 1
        self._hits[key] = (hits, ms)
        return hits, ms

    def df(self, term):
        """书级 df：命中这本书的书数。只给第 5 轮挑「最泛的词」用。"""
        if term in self._df:
            return self._df[term]
        if self._df_fn is not None:
            val = self._df_fn(term)
        else:
            expr = cjk.query_expr(term)
            if not expr:
                val = (0, 0)
            else:
                para = self.con.execute(
                    "SELECT count(*) FROM para_fts WHERE para_fts MATCH ?", (expr,)).fetchone()[0]
                books = self.con.execute(
                    "SELECT count(DISTINCT p.book) FROM para_fts f JOIN para p ON p.id=f.rowid "
                    "WHERE para_fts MATCH ?", (expr,)).fetchone()[0]
                val = (books, para)
        self._df[term] = val
        return val

    def toc(self, book):
        if book not in self._toc:
            self._toc[book] = book_readme.readme_toc(book)
        return self._toc[book]

    def _one(self, n, kind, query_terms, groups):
        hits, ms = self.search(query_terms)
        ranking = book_score.rank(hits, query_terms, self.scheme, self.exclude,
                                  self.meta, self.expand)
        A, B, C, cover = hit_rounds(hits, ranking, groups, exclude=self.exclude)
        top1 = ranking[0] if ranking else None
        return Round(
            n=n, kind=kind, terms=list(query_terms), groups=list(groups), hits=len(hits),
            top1=top1.book if top1 else "", top1_score=round(top1.score, 4) if top1 else 0.0,
            top2_score=round(ranking[1].score, 4) if len(ranking) > 1 else 0.0,
            margin=round((top1.score - ranking[1].score) / top1.score, 4)
            if top1 and top1.score and len(ranking) > 1 else 0.0,
            cover_paras=cover, A=A, B=B, C=C, stop=bool(A and (B or C)), ms=round(ms, 1))

    # ---- 换词 -------------------------------------------------------
    def term_sets(self, terms, nouns, allow_broad):
        """按方案顺序给出 (kind, query_terms, coverage_groups)，第 5 轮延迟生成。"""
        keys = dedup([k for k in (resolve(t, self.concepts) for t in nouns) if k])
        syn = []
        for key in keys:
            syn.extend([key] + self.concepts[key]["aliases"])
        broad = []
        if allow_broad:
            bkeys = dedup([k for k in (resolve(t, self.broad) for t in terms) if k])
            for key in bkeys:
                broad.extend([key] + self.broad[key]["aliases"])
        yield "noun", nouns, nouns
        yield "full", list(terms), nouns
        if [t for t in syn if t not in nouns]:
            yield "noun+concept", dedup(nouns + syn), nouns
        if broad:
            yield "full+broad", dedup(list(terms) + broad), nouns
        if len(terms) > 1:
            dfs = [(self.df(t), i, t) for i, t in enumerate(terms)]
            drop = max(dfs, key=lambda x: (x[0][0], x[0][1], x[1]))[2]
            rest = [t for t in terms if t != drop]
            groups = [t for t in nouns if t in rest] or rest
            yield "drop-generic", rest, groups

    def plan(self, terms, broad=False, max_rounds=MAX_ROUNDS, chapters=True,
             chapter_selector="score", max_chapters=3):
        t0 = time.time()
        terms = dedup(terms)
        nouns = split_terms(terms, self.actions)
        dropped = [t for t in terms if t not in nouns]
        res = Plan(query=",".join(terms), terms=terms, nouns=nouns, action_dropped=dropped)
        seen = set()
        for kind, qterms, groups in self.term_sets(terms, nouns, broad):
            if len(res.rounds) >= max_rounds:
                break
            key = tuple(dedup(qterms))
            if not key:
                res.skipped.append({"kind": kind, "reason": "empty"})
                continue
            if key in seen:
                res.skipped.append({"kind": kind, "reason": "duplicate", "terms": list(key)})
                continue
            seen.add(key)
            r = self._one(len(res.rounds) + 1, kind, key, groups)
            res.rounds.append(r)
            if r.stop:
                break
        if res.rounds and res.rounds[-1].stop:
            last = res.rounds[-1]
            res.stop = "C" if last.C else "A+B"
            res.best_round = last.n
        elif len(res.rounds) >= max_rounds:
            res.stop = "cap"
        else:
            res.stop = "exhausted"
        if not res.rounds:
            res.quality = ()
            res.ms = round((time.time() - t0) * 1000, 1)
            return res
        best = res.rounds[0]
        for r in res.rounds[1:]:
            if quality(r) > quality(best):
                best = r
        res.best_round = best.n
        res.quality = quality(best)
        res.ranking = book_score.rank(self.search(best.terms)[0], best.terms,
                                      self.scheme, self.exclude, self.meta, self.expand)
        if chapters:
            hits, _ = self.search(terms)          # 选章永远用全词集
            for b in res.ranking[:self.limit]:
                toc, status = self.toc(b.book)
                rows = select_chapters(hits, b.book, terms, toc, max_chapters,
                                       chapter_selector)
                for r in rows:
                    r["readme_status"] = status
                res.chapters.append({"book": b.book, "title": b.title,
                                     "book_score": round(b.score, 4), "selected": rows})
        res.fetches = self.fetches
        res.ms = round((time.time() - t0) * 1000, 1)
        return res


def chapter_order(hits, book):
    """章内排序：同章去重取最好段，按 (-score, path)。与 B2 的证据章口径一致。"""
    best = {}
    for r in hits:
        if r["book"] != book:
            continue
        cur = best.get(r["path"])
        if cur is None or (-r["score"], r["path"], r["line"]) < \
                (-cur["score"], cur["path"], cur["line"]):
            best[r["path"]] = r
    return sorted(best.values(), key=lambda r: (-r["score"], r["path"], r["line"]))


def select_chapters(hits, book, terms, toc=None, max_chapters=3, selector="score"):
    if selector == "readme" and toc:
        rows = book_readme.select(hits, terms, terms, toc, max_chapters, 0.0)
        return [{"chapter_path": r["chapter_path"], "chapter_title": r["chapter_title"],
                 "line": r["line"], "score": r["score"], "snippet": r["snippet"]}
                for r in rows]
    titles = {e["path"]: e["title"] for e in (toc or [])}
    rows = []
    for r in chapter_order(hits, book)[:max_chapters]:
        rows.append({"chapter_path": r["path"],
                     "chapter_title": titles.get(r["path"]) or os.path.basename(r["path"]),
                     "line": r["line"], "score": r["score"],
                     "snippet": " ".join(r["text"].split())[:150]})
    return rows


def run(terms, con=None, broad=False, scheme="best_support", limit=10, max_chapters=3,
        chapter_selector="score", planner=None):
    own = con is None
    if con is None:
        con = sqlite3.connect("file:%s?mode=ro" % book_score.DB, uri=True)
    try:
        p = planner or Planner(con, scheme=scheme, limit=limit)
        return p.plan(terms, broad=broad, chapters=True, chapter_selector=chapter_selector,
                      max_chapters=max_chapters)
    finally:
        if own:
            con.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="*")
    ap.add_argument("--concept")
    ap.add_argument("-n", "--limit", type=int, default=5)
    ap.add_argument("--max-chapters", type=int, default=3)
    ap.add_argument("--book-score", default=DEFAULTS["book_score"],
                    choices=list(book_score.SCHEMES),
                    help="书级打分（默认 %s）" % DEFAULTS["book_score"])
    ap.add_argument("--question", help="自然语言问句，用于书卡触发判断")
    ap.add_argument("--cards", dest="cards", action="store_true",
                    default=DEFAULTS["cards"], help="首轮后弹书卡（默认开）")
    ap.add_argument("--no-cards", dest="cards", action="store_false", help="关闭书卡")
    ap.add_argument("--expand-on-demand", action="store_true",
                    default=DEFAULTS["expand_on_demand"],
                    help="选中后跑 B4 按需扩展（默认关，会调模型）")
    ap.add_argument("--broad", action="store_true", help="第 4 轮加宽词组（默认不给）")
    ap.add_argument("--max-rounds", type=int, default=DEFAULTS["max_rounds"],
                    help="最多几轮（默认 %d）" % DEFAULTS["max_rounds"])
    ap.add_argument("--chapter-selector", default="score", choices=["score", "readme"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--explain", action="store_true")
    a = ap.parse_args(argv)
    exclude = []
    if a.concept:
        groups, exclude = qsearch.load_concept(a.concept)
        terms = groups
    elif a.query:
        terms = cjk.split_groups(" ".join(a.query))
    else:
        ap.error("要给查询词或 --concept")
    con = sqlite3.connect("file:%s?mode=ro" % book_score.DB, uri=True)
    p = Planner(con, scheme=a.book_score, limit=a.limit, exclude=exclude)
    question = a.question or " ".join(terms)
    res = p.plan(terms, broad=a.broad, max_rounds=a.max_rounds,
                 chapter_selector=a.chapter_selector, max_chapters=a.max_chapters)
    cardset = None
    if a.cards:
        import book_cards
        cardset, hits, nouns, ranking = book_cards.cards_for(p, terms, question, 6)
        if cardset.trigger:
            # 2026-10-23 操作人定：书卡展开六张卡（不再只展开前三本）。
            keep = {c.book for c in cardset.cards}
            res.chapters = [b for b in res.chapters if b["book"] in keep]
            have = {b["book"] for b in res.chapters}
            missing = [c for c in cardset.cards if c.book in keep and c.book not in have]
            if missing:
                full_hits, _ = p.search(terms)
                res.chapters += book_cards.next_books(missing, full_hits, terms, 3)
            order = {c.book: i for i, c in enumerate(cardset.cards)}
            res.chapters.sort(key=lambda b: order.get(b["book"], 99))
    if a.expand_on_demand and res.chapters:
        import book_cards
        res.model_calls = book_cards.expand_selected(question, res.chapters[:3], None)
    if a.json:
        payload = asdict(res)
        if cardset is not None:
            payload["cards"] = asdict(cardset)
        print(json.dumps(payload, ensure_ascii=False, indent=1, default=str))
        return 0
    print("%s  名词=%s  动作=%s  %s  第 %d 轮  %.0f ms  %d 次查询"
          % (res.query, ",".join(res.nouns), ",".join(res.action_dropped) or "-",
             res.stop, res.best_round, res.ms, res.fetches), file=sys.stderr)
    if cardset is not None:
        if cardset.trigger:
            print("书卡：%d 张，原因 %s" % (len(cardset.cards),
                                            ",".join(cardset.reasons)), file=sys.stderr)
            for i, c in enumerate(cardset.cards, 1):
                print("%d. %-40s %-8s %5d章  [%s]" % (i, c.title, c.type, c.chapters,
                                                      c.best_chapter_title))
                print("   %s" % c.reason)
                print("   %s" % c.snippet)
        else:
            print("不弹卡：书名/作者/原句提示", file=sys.stderr)
    if a.explain:
        for r in res.rounds:
            print("  R%d %-12s A=%d B=%d C=%d stop=%d top1=%s %.2f margin=%.2f cover=%d %s"
                  % (r.n, r.kind, r.A, r.B, r.C, r.stop, r.top1, r.top1_score, r.margin,
                     r.cover_paras, ",".join(r.terms)), file=sys.stderr)
    for i, b in enumerate(res.chapters, 1):
        print("%2d %-46s %.2f" % (i, b["book"], b.get("book_score", 0.0)))
        for r in b["selected"]:
            print("   [%s] %s:%d  %.2f" % (r["chapter_title"], r["chapter_path"],
                                           r["line"], r["score"]))
            if a.explain:
                print("      " + r["snippet"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
