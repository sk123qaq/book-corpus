#!/usr/bin/env python3
"""查询规划：词法循环 + 向量三层混合检索（B2.5）。

  book_query.py 礼治 秩序 法律
  book_query.py 石昊 天人族 收拾 --explain
  book_query.py 礼治,秩序,法律 --json

一轮 = 一次书级查询（用本轮词集）。全词集只在最后给选定书选章时查一次，两段共用。
循环按「够不够好」决定要不要换一套词再查，判据纯词法：

  A  top1 书的命中段并起来覆盖全部名词组，且至少两段各覆盖 min(2, 名词组数) 个组
  B  top1 与 top2 的书级分差 >= 10%
  C  top1 书里名词组全覆盖的段落数 >= 3

  A 且（B 或 C）成立即停。C 成立时 A 必然成立，所以实际是 C 或 A 且 B。

换词顺序：
  1 全词集
  2 去掉最泛的一个词（书级 df 最高者）

两段：书级排序用本轮词集，选章始终用全词集。最多 5 轮，到线上限记 stop=cap，
词集用尽记 stop=exhausted，返回质量最好的一轮（见 quality()）。

只读 目录/检索/index.db 与 目录/library.db。
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

sys.path.insert(0, HERE)
import book_readme
import book_score
import lib_fts_cjk as cjk
import qsearch
import vec_search

MAX_ROUNDS = 5
MARGIN = 0.10  # 判据 B 的 10%
# B6 验收后的默认值。qsearch --book 仍是 mix 旧口径，不在这里。
DEFAULTS = {"book_score": "best_support", "cards": True, "expand_on_demand": False, "max_rounds": MAX_ROUNDS}
MIN_CHARS = 40
POOL = 2000


def dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


_NORM_STRIP_RE = re.compile(r"[\s`*_>#\[\]()（）「」『』“”\"'‘’.,，。；;：:！!？?、…—\-]+")


def _norm_passage(text):
    """压平空白与标点，供跨书去重比较使用。"""
    return _NORM_STRIP_RE.sub("", str(text or "")).lower()


def is_duplicate_passage(hit_text, source_text):
    """命中段落与来路选段正文几乎相同时判为重复（避免查出原文自身/整段复本）。"""
    src = _norm_passage(source_text)
    hit = _norm_passage(hit_text)
    if not src or not hit:
        return False
    if len(src) >= 30:
        if src in hit and len(src) / max(1, len(hit)) >= 0.78:
            return True
        if hit in src and len(hit) / max(1, len(src)) >= 0.78:
            return True
    return len(src) >= 12 and hit == src


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
    fusion: str = "bm25_only"
    vec_used: bool = False
    vec_ms: float = 0.0
    vec_hits: int = 0
    rerank_used: bool = False
    rerank_ms: float = 0.0


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
    ok_a = len(seen) == len(groups) and part >= 2
    ok_c = full >= 3
    ok_b = m >= margin
    return ok_a, ok_b, ok_c, full


class Planner:
    def __init__(
        self,
        con,
        meta=None,
        expand=None,
        scheme="best_support",
        pool=POOL,
        min_chars=MIN_CHARS,
        search_fn=None,
        df_fn=None,
        limit=10,
        exclude=None,
        vec_fn=None,
        book_vec_fn=None,
        rerank_fn=None,
        scope=None,
        exclude_books=None,
        exclude_spots=None,
        exclude_text="",
        root=None,
        emb_dir=None,
        embed_db=None,
        vec_path=None,
        embed_url=None,
    ):
        self.con = con
        self.meta = book_score.load_meta() if meta is None else meta
        self.expand = book_score.load_expand(con) if expand is None else expand
        self.scheme = scheme
        self.pool = pool
        self.min_chars = min_chars
        self.limit = limit
        self.exclude = list(exclude or [])
        self.scope = set(scope or ())
        self.exclude_books = set(exclude_books or ())
        self.exclude_spots = {(str(p), int(ln or 0)) for p, ln in (exclude_spots or ()) if p}
        self.exclude_text = str(exclude_text or "").strip()
        self.root = root
        self.emb_dir = emb_dir
        self.embed_db = embed_db
        self.vec_path = vec_path
        self.embed_url = embed_url
        self._search_fn = search_fn
        self._df_fn = df_fn
        self._vec_fn = vec_fn
        self._book_vec_fn = book_vec_fn
        self._rerank_fn = rerank_fn
        self._hits = {}
        self._df = {}
        self._toc = {}
        self.fetches = 0

    def _filter_hits(self, hits, check_text=True):
        out = []
        for h in hits or ():
            bk = h.get("book")
            if self.scope and bk not in self.scope:
                continue
            if self.exclude_books and bk in self.exclude_books:
                continue
            if self.exclude_spots and (h.get("path"), int(h.get("line") or 0)) in self.exclude_spots:
                continue
            if check_text and self.exclude_text and is_duplicate_passage(h.get("text") or "", self.exclude_text):
                continue
            out.append(h)
        return out

    # ---- 检索 -------------------------------------------------------
    def search(self, terms):
        key = tuple(dedup(terms))
        if key in self._hits:
            return self._hits[key]
        if self._search_fn is not None:
            hits, ms = self._search_fn(list(key))
        else:
            expr = " OR ".join("(%s)" % cjk.query_expr(t) for t in key if cjk.query_expr(t))
            hits, ms = qsearch.search(self.con, expr, list(key), self.exclude, 2000, self.min_chars, False, self.pool)
        self.fetches += 1
        hits = self._filter_hits(hits, check_text=True)
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
                para = self.con.execute("SELECT count(*) FROM para_fts WHERE para_fts MATCH ?", (expr,)).fetchone()[0]
                books = self.con.execute(
                    "SELECT count(DISTINCT p.book) FROM para_fts f JOIN para p ON p.id=f.rowid WHERE para_fts MATCH ?",
                    (expr,),
                ).fetchone()[0]
                val = (books, para)
        self._df[term] = val
        return val

    def toc(self, book):
        if book not in self._toc:
            self._toc[book] = book_readme.readme_toc(book, root=self.root)
        return self._toc[book]

    def _one(self, n, kind, query_terms, groups):
        hits, ms = self.search(query_terms)
        ranking = book_score.rank(hits, query_terms, self.scheme, self.exclude, self.meta, self.expand)
        ok_a, ok_b, ok_c, cover = hit_rounds(hits, ranking, groups, exclude=self.exclude)
        top1 = ranking[0] if ranking else None
        return Round(
            n=n,
            kind=kind,
            terms=list(query_terms),
            groups=list(groups),
            hits=len(hits),
            top1=top1.book if top1 else "",
            top1_score=round(top1.score, 4) if top1 else 0.0,
            top2_score=round(ranking[1].score, 4) if len(ranking) > 1 else 0.0,
            margin=round((top1.score - ranking[1].score) / top1.score, 4)
            if top1 and top1.score and len(ranking) > 1
            else 0.0,
            cover_paras=cover,
            A=ok_a,
            B=ok_b,
            C=ok_c,
            stop=bool(ok_a and (ok_b or ok_c)),
            ms=round(ms, 1),
        )

    # ---- 换词 -------------------------------------------------------
    def term_sets(self, terms):
        """给出 (kind, query_terms, coverage_groups)：先全词检索，不够好再去掉最泛一词。"""
        yield "full", list(terms), list(terms)
        if len(terms) > 1:
            dfs = [(self.df(t), i, t) for i, t in enumerate(terms)]
            drop = max(dfs, key=lambda x: (x[0][0], x[0][1], x[1]))[2]
            rest = [t for t in terms if t != drop]
            yield "drop-generic", rest, rest

    def _vec_search(self, query_text):
        vec_excl = sorted(self.exclude_books) if self.exclude_books else self.exclude
        if self._vec_fn is not None:
            res = self._vec_fn(query_text, vec_excl)
            if isinstance(res, tuple):
                hits, meta = res
                return list(hits or []), dict(meta or {})
            hits = list(res or [])
            return hits, {"used": True, "ms": 0.0, "hits": len(hits), "qvec": None}
        if self._search_fn is not None:
            return [], {"used": False, "ms": 0.0, "hits": 0, "qvec": None}
        return vec_search.search_text(
            query_text,
            top_k=200,
            exclude=vec_excl,
            embed_db=self.embed_db,
            vec_path=self.vec_path,
            url=self.embed_url,
            emb_dir=self.emb_dir,
        )

    def _book_vec_hits(self, book, query_text, vhits, qvec):
        vh_b = [h for h in (vhits or []) if h.get("book") == book]
        if len(vh_b) >= 8:
            return vh_b
        if self._book_vec_fn is not None:
            rows = list(self._book_vec_fn(book, query_text, 20) or vh_b)
        elif self._search_fn is not None or qvec is None:
            rows = vh_b
        else:
            rows = (
                vec_search.search_book_text(
                    book,
                    query_text,
                    top_k=20,
                    embed_db=self.embed_db,
                    vec_path=self.vec_path,
                    url=self.embed_url,
                    qvec=qvec,
                    emb_dir=self.emb_dir,
                )
                or vh_b
            )
        return self._filter_hits(rows, check_text=False)

    def plan(
        self,
        terms,
        max_rounds=MAX_ROUNDS,
        chapters=True,
        chapter_selector="score",
        max_chapters=3,
        focus=None,
        question="",
    ):
        t0 = time.time()
        terms = dedup(terms)
        query_text = (question or "").strip() or " ".join(terms)
        res = Plan(query=",".join(terms), terms=terms, nouns=list(terms), action_dropped=[])
        seen = set()
        for kind, qterms, groups in self.term_sets(terms):
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

        vhits, vmeta = self._vec_search(query_text)
        vhits = self._filter_hits(vhits, check_text=False)
        qvec = vmeta.get("qvec")
        if vmeta.get("used"):
            res.vec_used = True
            res.vec_ms = float(vmeta.get("ms") or 0.0)
            res.vec_hits = len(vhits)
            res.fusion = "hybrid"

        if not res.rounds and not vhits:
            res.quality = ()
            if focus:
                pin_focus(
                    res,
                    focus,
                    terms,
                    None,
                    terms,
                    limit=self.limit,
                    chapters=chapters,
                    max_chapters=max_chapters,
                    toc_fn=self.toc,
                    scheme=self.scheme,
                    exclude=self.exclude,
                    meta=self.meta,
                    expand=self.expand,
                )
            res.ms = round((time.time() - t0) * 1000, 1)
            return res

        if res.rounds:
            best = res.rounds[0]
            for r in res.rounds[1:]:
                if quality(r) > quality(best):
                    best = r
            res.best_round = best.n
            res.quality = quality(best)
            best_terms = best.terms
            res.ranking = book_score.rank(
                self.search(best_terms)[0], best_terms, self.scheme, self.exclude, self.meta, self.expand
            )
        else:
            res.quality = ()
            best_terms = terms
            res.ranking = []

        if vhits:
            res.ranking = apply_hybrid_ranking(res.ranking, vhits, self.meta, root=self.root)

        hits = None
        if chapters:
            import rerank_client

            rerank_ready = bool(query_text) and (
                self._rerank_fn is not None or (self._search_fn is None and rerank_client.server_healthy())
            )
            focus_book = focus.get("book") if focus and focus.get("status") == "exact" else None
            hits, _ = self.search(terms) if terms else ([], 0.0)  # 选章永远用全词集
            for idx, b in enumerate(res.ranking[: self.limit]):
                toc, status = self.toc(b.book)
                titles = {e["path"]: e["title"] for e in (toc or [])}
                use_rr = rerank_ready and (idx < 3 or b.book == focus_book)
                need_pool = res.vec_used or use_rr
                bm25_rows = (
                    select_chapters(
                        hits,
                        b.book,
                        terms,
                        toc,
                        vec_search.CHAP_POOL_LIMIT if need_pool else max_chapters,
                        chapter_selector,
                    )
                    if hits
                    else []
                )
                if need_pool:
                    vh_b = self._book_vec_hits(b.book, query_text, vhits, qvec) if res.vec_used else []
                    t_rr = time.time()
                    rows = vec_search.fuse_chapters(
                        bm25_rows,
                        vh_b,
                        max_chapters=max_chapters,
                        title_fn=lambda cp, t=titles: t.get(cp) or os.path.basename(cp),
                        snippet_fn=lambda cp, ln, rt=self.root: vec_search.read_snippet(cp, ln, root=rt),
                        question=query_text if use_rr else "",
                        rerank_fn=self._rerank_fn if use_rr else None,
                        embed_db=self.embed_db,
                        emb_dir=self.emb_dir,
                    )
                    if use_rr and any("rerank_score" in row for row in rows):
                        res.rerank_used = True
                        res.rerank_ms = round(res.rerank_ms + (time.time() - t_rr) * 1000, 1)
                else:
                    rows = bm25_rows[:max_chapters]
                for r in rows:
                    r["readme_status"] = status
                res.chapters.append(
                    {"book": b.book, "title": b.title, "book_score": round(b.score, 4), "selected": rows}
                )
        if focus:
            vec_cb = (lambda bk: self._book_vec_hits(bk, query_text, vhits, qvec)) if res.vec_used else None
            pin_focus(
                res,
                focus,
                terms,
                hits if hits is not None else (self.search(best_terms)[0] if best_terms else []),
                best_terms,
                limit=self.limit,
                chapters=chapters,
                max_chapters=max_chapters,
                toc_fn=self.toc,
                scheme=self.scheme,
                exclude=self.exclude,
                meta=self.meta,
                expand=self.expand,
                vec_hits_fn=vec_cb,
                question=query_text,
                rerank_fn=self._rerank_fn,
                embed_db=self.embed_db,
                emb_dir=self.emb_dir,
                root=self.root,
            )
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
        if cur is None or (-r["score"], r["path"], r["line"]) < (-cur["score"], cur["path"], cur["line"]):
            best[r["path"]] = r
    return sorted(best.values(), key=lambda r: (-r["score"], r["path"], r["line"]))


def select_chapters(hits, book, terms, toc=None, max_chapters=3, selector="score"):
    if selector == "readme" and toc:
        rows = book_readme.select(hits, terms, terms, toc, max_chapters, 0.0)
        return [
            {
                "chapter_path": r["chapter_path"],
                "chapter_title": r["chapter_title"],
                "line": r["line"],
                "score": r["score"],
                "snippet": r["snippet"],
            }
            for r in rows
        ]
    titles = {e["path"]: e["title"] for e in (toc or [])}
    rows = []
    for r in chapter_order(hits, book)[:max_chapters]:
        rows.append(
            {
                "chapter_path": r["path"],
                "chapter_title": titles.get(r["path"]) or os.path.basename(r["path"]),
                "line": r["line"],
                "score": r["score"],
                "snippet": " ".join(r["text"].split())[:150],
            }
        )
    return rows


def focus_score(book, focus, rows, groups, scheme, exclude, meta, expand):
    """focus 书在给定命中里的 BookScore；无命中时给一个 0 分占位，不让位给别的书。"""
    own = [r for r in (rows or []) if r["book"] == book]
    if own:
        ranked = book_score.rank(own, groups, scheme, exclude, meta, expand)
        if ranked:
            return ranked[0]
    m = (meta or {}).get(book)
    return book_score.BookScore(
        book=book,
        title=(getattr(m, "title", "") or focus.get("title") or book),
        type=book_score.genre(meta, book),
        chapters=int(getattr(m, "chapters", 0) or 0),
        best=0.0,
        best_path="",
        best_line=0,
        best_text="",
        support=0,
        topk=0.0,
        coverage=0.0,
        score=0.0,
        groups=[],
        has_prefix=False,
        has_queries=False,
        scores=[],
        hits=0,
        mean=0.0,
        total_groups=len(groups or []),
    )


def focus_rows(hits, book, terms, toc, max_chapters):
    """focus 书的选章：先滤卷首卷尾；README 不认这些路径时退回打分序再滤一次。"""
    rows = book_readme.select(hits, terms, terms, toc, max_chapters, 0.0, skip_front=True)
    if rows:
        return rows
    rows = select_chapters(hits, book, terms, toc, max_chapters, "score")
    kept = [r for r in rows if not book_readme.is_front_matter(r["chapter_title"])]
    return kept or rows


def apply_hybrid_ranking(ranking, vhits, meta, root=None):
    """用 RRF 把 BM25 书级排序与向量书级命中融合。"""
    v_scores = vec_search.vec_book_scores(vhits)
    if not v_scores:
        return list(ranking)
    v_books = [b for b, _, _ in v_scores]
    v_best_map = {}
    v_top = {}
    for h in vhits:
        bk = h.get("book")
        if bk:
            sc = float(h.get("score") or 0.0)
            if sc > v_best_map.get(bk, 0.0):
                v_best_map[bk] = sc
        if bk and bk not in v_top:
            v_top[bk] = h
    v_cnt = {b: cnt for b, _, cnt in v_scores}
    fused = vec_search.fuse_book_scores([b.book for b in ranking], v_books)

    by_book = {b.book: b for b in ranking}
    out = []
    for b in ranking:
        b.score = round(fused.get(b.book, 0.0), 6)
        b.vec_best = round(v_best_map.get(b.book, 0.0), 4)
        out.append(b)
    for bk in v_books:
        if bk in by_book:
            continue
        info = meta.get(bk) if meta else None
        title = getattr(info, "title", bk) or bk
        chaps = int(getattr(info, "chapters", 0) or 0)
        btype = "long" if chaps >= 500 else "ordinary"
        top_h = v_top.get(bk, {})
        bp = top_h.get("path", "")
        bl = int(top_h.get("line") or 1)
        bsc = float(top_h.get("score") or 0.0)
        out.append(
            book_score.BookScore(
                book=bk,
                title=title,
                type=btype,
                chapters=chaps,
                best=round(bsc, 4),
                best_path=bp,
                best_line=bl,
                best_text=vec_search.read_snippet(bp, bl, root=root),
                support=v_cnt.get(bk, 1),
                topk=round(bsc, 4),
                coverage=0.0,
                score=round(fused.get(bk, 0.0), 6),
                groups=[],
                has_prefix=False,
                has_queries=False,
                scores=[round(bsc, 4)],
                hits=v_cnt.get(bk, 1),
                mean=round(bsc, 4),
                total_groups=0,
                vec_best=round(bsc, 4),
            )
        )
    out.sort(key=lambda b: (-b.score, -b.best))
    return out


def pin_focus(
    plan,
    focus,
    terms,
    hits,
    best_terms,
    limit=10,
    chapters=True,
    max_chapters=3,
    toc_fn=None,
    scheme="best_support",
    exclude=(),
    meta=None,
    expand=None,
    vec_hits_fn=None,
    question="",
    rerank_fn=None,
    embed_db=None,
    emb_dir=None,
    root=None,
):
    """精确书名钉到 ranking[0] 与 chapters[0]，回写 focus 的首章定位。"""
    if not focus or focus.get("status") != "exact" or not focus.get("book"):
        return plan
    book = focus["book"]
    focus_hits = [r for r in (hits or []) if r["book"] == book]
    pinned = next((b for b in plan.ranking if b.book == book), None)
    if pinned is None:
        pinned = focus_score(book, focus, focus_hits or hits, terms, scheme, exclude, meta, expand)
    pinned.pinned = True
    plan.ranking = ([pinned] + [b for b in plan.ranking if b.book != book])[:limit]
    if chapters:
        import rerank_client

        toc, status = toc_fn(book) if toc_fn else ([], "")
        titles = {e["path"]: e["title"] for e in (toc or [])}
        vh_b = vec_hits_fn(book) if vec_hits_fn is not None else []
        use_rr = bool(question) and (rerank_fn is not None or rerank_client.server_healthy())
        bm25_rows = focus_rows(
            focus_hits, book, terms, toc, vec_search.CHAP_POOL_LIMIT if (vh_b or use_rr) else max_chapters
        )
        if vh_b or use_rr:
            rows = vec_search.fuse_chapters(
                bm25_rows,
                vh_b,
                max_chapters=max_chapters,
                title_fn=lambda cp, t=titles: t.get(cp) or os.path.basename(cp),
                snippet_fn=lambda cp, ln, rt=root: vec_search.read_snippet(cp, ln, root=rt),
                question=question,
                rerank_fn=rerank_fn,
                embed_db=embed_db,
                emb_dir=emb_dir,
            )
            if any("rerank_score" in row for row in rows):
                plan.rerank_used = True
        else:
            rows = bm25_rows[:max_chapters]
        for r in rows:
            r["readme_status"] = status
        entry = {
            "book": book,
            "title": pinned.title,
            "book_score": round(pinned.score, 4),
            "selected": rows,
            "pinned": True,
        }
        plan.chapters = ([entry] + [c for c in plan.chapters if c["book"] != book])[:limit]
    if plan.chapters and plan.chapters[0]["book"] == book and plan.chapters[0]["selected"]:
        top = plan.chapters[0]["selected"][0]
        focus["best_path"], focus["best_line"] = top["chapter_path"], top["line"]
    if not focus.get("best_path") and pinned.best_path:
        focus["best_path"], focus["best_line"] = pinned.best_path, pinned.best_line
    return plan


def run(
    terms,
    con=None,
    scheme="best_support",
    limit=10,
    max_chapters=3,
    chapter_selector="score",
    planner=None,
    focus=None,
    question="",
    scope=None,
    exclude_books=None,
    exclude_spots=None,
    exclude_text="",
):
    own = con is None
    if con is None:
        con = sqlite3.connect("file:%s?mode=ro" % book_score.DB, uri=True)
    try:
        p = planner or Planner(
            con,
            scheme=scheme,
            limit=limit,
            scope=scope,
            exclude_books=exclude_books,
            exclude_spots=exclude_spots,
            exclude_text=exclude_text,
        )
        return p.plan(
            terms,
            chapters=True,
            chapter_selector=chapter_selector,
            max_chapters=max_chapters,
            focus=focus,
            question=question,
        )
    finally:
        if own:
            con.close()


def _import_book_cards():
    """延迟导入 book_cards，避免与 book_cards 顶层 import book_query 形成循环导入。"""
    if __package__:
        from . import book_cards

        return book_cards
    import book_cards

    return book_cards


def main(argv=None):
    book_cards = _import_book_cards()
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("-n", "--limit", type=int, default=book_cards.DEFAULT_CARDS)
    ap.add_argument("--card-count", type=int, default=None, help="弹卡张数（默认跟随 -n/--limit）")
    ap.add_argument("--max-chapters", type=int, default=3)
    ap.add_argument(
        "--book-score",
        default=DEFAULTS["book_score"],
        choices=list(book_score.SCHEMES),
        help="书级打分（默认 %s）" % DEFAULTS["book_score"],
    )
    ap.add_argument("--question", help="自然语言问句，用于书卡触发判断")
    ap.add_argument(
        "--cards", dest="cards", action="store_true", default=DEFAULTS["cards"], help="首轮后弹书卡（默认开）"
    )
    ap.add_argument("--no-cards", dest="cards", action="store_false", help="关闭书卡")
    ap.add_argument(
        "--expand-on-demand",
        action="store_true",
        default=DEFAULTS["expand_on_demand"],
        help="选中后跑 B4 按需扩展（默认关，会调模型）",
    )
    ap.add_argument(
        "--max-rounds", type=int, default=DEFAULTS["max_rounds"], help="最多几轮（默认 %d）" % DEFAULTS["max_rounds"]
    )
    ap.add_argument("--chapter-selector", default="score", choices=["score", "readme"])
    ap.add_argument("--root", help="隔离工作区根（推导 index.db / library.db / embeddings）")
    ap.add_argument("--index-db", help="显式 index.db 路径")
    ap.add_argument("--lib-db", help="显式 library.db 路径")
    ap.add_argument("--emb-dir", help="隔离向量库目录（包含 emb.db 与 vectors.f16）")
    ap.add_argument("--embed-url", help="embedding HTTP 服务地址（默认 BOOKS_EMBED_URL）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--explain", action="store_true")
    a = ap.parse_args(argv)
    if a.limit <= 0:
        ap.error("-n/--limit 必须为正整数")
    if a.card_count is not None and a.card_count <= 0:
        ap.error("--card-count 必须为正整数")
    if a.max_chapters <= 0:
        ap.error("--max-chapters 必须为正整数")
    terms = cjk.split_groups(" ".join(a.query))
    idx_db = a.index_db or (os.path.join(a.root, "build", "检索", "index.db") if a.root else book_score.DB)
    lib_db = a.lib_db or (os.path.join(a.root, "build", "library.db") if a.root else None)
    emb_dir = a.emb_dir or (os.path.join(a.root, "build", "检索", "embeddings") if a.root else None)
    con = sqlite3.connect("file:%s?mode=ro" % idx_db, uri=True)
    has_iso = any((a.root, a.index_db, a.lib_db, a.emb_dir, a.embed_url))
    if has_iso:
        meta = book_score.load_meta(lib_db) if (lib_db and os.path.isfile(lib_db)) else {}
        p = Planner(
            con,
            meta=meta,
            scheme=a.book_score,
            limit=a.limit,
            root=a.root,
            emb_dir=emb_dir,
            embed_url=a.embed_url,
        )
    else:
        p = Planner(con, scheme=a.book_score, limit=a.limit)
    question = a.question or " ".join(terms)
    plan_kw = {"max_rounds": a.max_rounds, "chapter_selector": a.chapter_selector, "max_chapters": a.max_chapters}
    if a.question:
        plan_kw["question"] = a.question
    res = p.plan(terms, **plan_kw)
    cardset = None
    if a.cards:
        card_count = a.limit if a.card_count is None else a.card_count
        cardset, _hits, _nouns, _ranking = book_cards.cards_for(p, terms, question, card_count)
        if cardset.trigger:
            keep = {c.book for c in cardset.cards}
            res.chapters = [b for b in res.chapters if b["book"] in keep]
            have = {b["book"] for b in res.chapters}
            missing = [c for c in cardset.cards if c.book in keep and c.book not in have]
            if missing:
                full_hits, _ = p.search(terms)
                res.chapters += book_cards.next_books(missing, full_hits, terms, a.max_chapters)
            order = {c.book: i for i, c in enumerate(cardset.cards)}
            res.chapters.sort(key=lambda b: order.get(b["book"], len(cardset.cards)))
    if a.expand_on_demand and res.chapters:
        res.model_calls = book_cards.expand_selected(question, res.chapters[:3], None)
    if a.json:
        payload = asdict(res)
        if cardset is not None:
            payload["cards"] = asdict(cardset)
        print(json.dumps(payload, ensure_ascii=False, indent=1, default=str))
        return 0
    print(
        "%s  名词=%s  动作=%s  %s  第 %d 轮  %.0f ms  %d 次查询"
        % (
            res.query,
            ",".join(res.nouns),
            ",".join(res.action_dropped) or "-",
            res.stop,
            res.best_round,
            res.ms,
            res.fetches,
        ),
        file=sys.stderr,
    )
    if cardset is not None:
        if cardset.trigger:
            print("书卡：%d 张，原因 %s" % (len(cardset.cards), ",".join(cardset.reasons)), file=sys.stderr)
            for i, c in enumerate(cardset.cards, 1):
                print("%d. %-40s %-8s %5d章  [%s]" % (i, c.title, c.type, c.chapters, c.best_chapter_title))
                print("   %s" % c.reason)
                print("   %s" % c.snippet)
        else:
            print("不弹卡：书名/作者/原句提示", file=sys.stderr)
    if a.explain:
        for r in res.rounds:
            print(
                "  R%d %-12s A=%d B=%d C=%d stop=%d top1=%s %.2f margin=%.2f cover=%d %s"
                % (
                    r.n,
                    r.kind,
                    r.A,
                    r.B,
                    r.C,
                    r.stop,
                    r.top1,
                    r.top1_score,
                    r.margin,
                    r.cover_paras,
                    ",".join(r.terms),
                ),
                file=sys.stderr,
            )
    for i, b in enumerate(res.chapters, 1):
        print("%2d %-46s %.2f" % (i, b["book"], b.get("book_score", 0.0)))
        for r in b["selected"]:
            print("   [%s] %s:%d  %.2f" % (r["chapter_title"], r["chapter_path"], r["line"], r["score"]))
            if a.explain:
                print("      " + r["snippet"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
