#!/usr/bin/env python3
"""B5 书卡与用户反馈：首轮之后按触发规则弹 5 到 8 张卡，用户选书/排除类型/换一批。

  book_cards.py 礼治 秩序 法律
  book_cards.py 礼治 秩序 法律 --question "礼治社会靠什么维持秩序" --json
  book_cards.py 礼治 秩序 法律 --select 1,3 --json
  book_cards.py 石昊 天人族 收拾 --exclude-type 网文/类型 --json
  book_cards.py 礼治 秩序 法律 --refresh --json
  book_cards.py --gate        # 60 题 gold 上的触发率与门槛
  book_cards.py --perf        # 20 题，开卡/不开卡交错

触发（任一成立，且不是书名/作者/原句查询）：
  1 top1 与 top2 的分差 < 10%
  2 top10 跨至少三个类型
  3 top10 里有 >=50 章的书
  4 概念型开放问题（概念表命中或问句词）且没有书名、作者提示

非交互模式返回 JSON：ask_user=false，默认前三本，用户选择留空，不确定性写进 JSON。
反馈只追加到 book_feedback.tsv，只用于当前会话，不更新全局权重。
书卡只在首轮之后弹；用户选中后才给下一步章节，--expand 才跑 B4（默认每本最佳章）。

只读 目录/检索/index.db 与 目录/library.db。
"""
import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import threading
import time
from dataclasses import asdict, dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib_common import ROOT  # noqa: E402
sys.path.insert(0, os.path.join(HERE))
import book_query  # noqa: E402
import book_readme  # noqa: E402
import book_score  # noqa: E402
import lib_fts_cjk as cjk  # noqa: E402

DB = book_score.DB
LIBDB = book_score.LIBDB
BATCH = os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-22-书级融合")
FEEDBACK = os.path.join(BATCH, "book_feedback.tsv")
GOLD = os.path.join(BATCH, "book_gold.tsv")
LONG_TRIGGER = 50      # 触发规则 3 的章数
LONG_SHARE = 500       # 长书占比口径，与 B2 一致
MIN_CARDS, MAX_CARDS = 5, 8
DEFAULT_CARDS = 6
QUESTION_WORDS = ("为什么", "为何", "怎么", "怎样", "如何", "是什么", "什么", "哪",
                  "谁", "多少", "多大", "多久", "哪年", "几年", "几个人", "什么时候",
                  "多长", "多高", "多重", "几种", "几次", "吗", "呢", "靠什么", "凭什么",
                  "有何", "是否", "能否")
FEEDBACK_COLS = ["ts", "query", "shown", "selected", "rejected", "types", "chapters",
                 "answer_found"]


@dataclass
class BookCard:
    book: str
    rank: int
    title: str
    type: str
    chapters: int
    best_chapter: str
    best_chapter_title: str
    snippet: str
    matched_groups: list
    score: float
    reason: str
    readme_status: str


@dataclass
class CardSet:
    query: str
    terms: list
    trigger: bool
    reasons: list
    hints: dict
    cards: list = field(default_factory=list)
    ask_user: bool = False
    default_selection: list = field(default_factory=list)
    uncertainty: dict = field(default_factory=dict)
    next: list = field(default_factory=list)
    feedback: dict = field(default_factory=dict)
    ms: float = 0.0
    model_calls: int = 0


def normalize(text):
    return re.sub(r"[\s《》〈〉“”\"'「」『』（）()【】\[\]、，,。.！!？?：:；;·—\-]+", "",
                  text or "")


def load_books(libdb=LIBDB):
    """path → title/author，只读 library.db。"""
    con = sqlite3.connect("file:%s?mode=ro" % libdb, uri=True)
    try:
        out = {}
        for path, title, author in con.execute("SELECT path,title,author FROM book"):
            out[path] = {"title": title or "", "author": author or ""}
        return out
    finally:
        con.close()


def load_concepts():
    return book_query.read_table(book_query.CONCEPTS)


def query_hints(query, books, concepts, terms=None):
    """书名/作者/原句/概念/问句提示。只做词法判断。"""
    q = normalize(query)
    qw = [w for w in QUESTION_WORDS if w in q]
    title = ""
    for rec in books.values():
        t = normalize(rec["title"])
        if not t:
            continue
        hit = (len(t) >= 3 and t in q) or \
            (len(t) == 2 and (q == t or (q.startswith(t) and q[2:3] in "里中内的讲说是这那")))
        if hit and len(t) > len(title):
            title = t
    quoted_title = re.search(r"《([^》]{2,})》", query or "")
    if quoted_title:
        qt = normalize(quoted_title.group(1))
        if len(qt) > len(title):
            title = qt
    author = ""
    for rec in books.values():
        for a in re.split(r"[、，,;；/\s]+", rec["author"] or ""):
            a = normalize(a)
            if len(a) < 2 or a not in q:
                continue
            if not qw or len(a) >= max(2, len(q) // 2):
                if len(a) > len(author):
                    author = a
    quoted = re.search(r"[“\"「『](.{8,})[”\"」』]", query or "")
    verbatim = bool(quoted) or (len(q) >= 12 and not qw)
    terms = terms or cjk.split_groups(query or "")
    concept = any(book_query.resolve(t, concepts) for t in terms)
    if not concept:
        for key, rec in concepts.items():
            for al in [key] + rec["aliases"]:
                a = normalize(al)
                if len(a) >= 2 and a in q:
                    concept = True
                    break
            if concept:
                break
    return {"title": title, "author": author, "verbatim": verbatim,
            "concept": concept, "question": bool(qw), "qw": qw}


def trigger(query, ranking, hints, cards=DEFAULT_CARDS):
    """返回 (是否弹卡, 触发原因)。书名/作者/原句查询直接不弹。"""
    if not ranking:
        return False, []
    if hints.get("title") or hints.get("author") or hints.get("verbatim"):
        return False, []
    reasons = []
    if len(ranking) > 1 and ranking[0].score > 0:
        margin = (ranking[0].score - ranking[1].score) / ranking[0].score
        if margin < 0.10:
            reasons.append("margin")
    if len({b.type for b in ranking[:10]}) >= 3:
        reasons.append("types")
    if any(b.chapters >= LONG_TRIGGER for b in ranking[:10]):
        reasons.append("long")
    if hints.get("concept") or hints.get("question"):
        reasons.append("concept")
    return bool(reasons), reasons


def _clean_text(text):
    """去掉段首的章标题与脚注标记，留下正文。"""
    t = text.strip()
    if t.startswith("#"):
        t = re.sub(r"^#+\s*[^\s【（(]+\s*", "", t, count=1)
    t = re.sub(r"\[\[\d+\]\]\([^)]*\)", "", t)
    return t.strip()


def _clean_para(rows, fallback):
    """卡片片段优先取正文段，跳过脚注/表格/引用块。"""
    good = []
    for r in rows:
        if re.match(r"^\s*(\[|\||>|\*\*|\*)", r["text"]):
            continue
        t = _clean_text(r["text"])
        if len(t) >= 40 and not re.match(r"^\s*(\[|\||>|\*)", t):
            good.append((r, t))
    if good:
        r, t = max(good, key=lambda x: (x[0]["score"], -x[0]["line"]))
    elif rows:
        r, t = max(rows, key=lambda r: (r["score"], -r["line"])), None
    else:
        return fallback
    out = dict(fallback)
    out["line"] = r["line"]
    out["snippet"] = " ".join((t or r["text"]).split())[:150]
    return out


def _chapter_rows(hits, book, terms, toc):
    rows = book_readme.select([r for r in hits if r["book"] == book], terms, terms,
                              toc, 1, 0.0)
    if rows:
        return _clean_para([r for r in hits if r["path"] == rows[0]["chapter_path"]],
                           rows[0])
    own = book_query.chapter_order(hits, book)
    if not own:
        return None
    r = own[0]
    return {"chapter_path": r["path"], "chapter_title": os.path.basename(r["path"]),
            "line": r["line"], "snippet": " ".join(r["text"].split())[:150],
            "score": r["score"]}


def build_cards(ranking, hits, terms, n=DEFAULT_CARDS, toc_fn=None, select_fn=None):
    """把首轮 top n 变成卡片。toc_fn/select_fn 可注入，单测不碰库。"""
    toc_fn = toc_fn or book_readme.readme_toc
    out = []
    for i, b in enumerate(ranking[:n], 1):
        toc, status = toc_fn(b.book)
        c = _chapter_rows(hits, b.book, terms, toc)
        out.append(BookCard(
            book=b.book, rank=i, title=b.title, type=b.type, chapters=b.chapters,
            best_chapter=c["chapter_path"] if c else "",
            best_chapter_title=c["chapter_title"] if c else "",
            snippet=c["snippet"] if c else "", matched_groups=list(b.groups),
            score=round(b.score, 4),
            reason="第%d名 best=%.2f support=%d cov=%.2f 组=%s"
                   % (i, b.best, b.support, b.coverage, ",".join(b.groups) or "-"),
            readme_status=status))
    return out


def uncertainty(ranking, hints, reasons, cards):
    margin = 0.0
    if len(ranking) > 1 and ranking[0].score > 0:
        margin = round((ranking[0].score - ranking[1].score) / ranking[0].score, 4)
    return {"margin": margin, "types": len({b.type for b in ranking[:10]}),
            "long10": sum(1 for b in ranking[:10] if b.chapters >= LONG_TRIGGER),
            "reasons": list(reasons), "hints": dict(hints),
            "user_choice_unknown": True,
            "note": "非交互模式不猜用户选择；default_selection 只是默认前三本"}


def append_feedback(path, row):
    """只追加，不改全局权重。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FEEDBACK_COLS, delimiter="\t",
                           lineterminator="\n", extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


def feedback_row(query, cards, selected=(), rejected="", answer_found="", ts=None):
    return {"ts": ts or time.strftime("%Y-%m-%dT%H:%M:%S%z"), "query": query,
            "shown": "|".join(c.title for c in cards),
            "selected": "|".join(c.title for c in selected),
            "rejected": rejected,
            "types": "|".join(sorted({c.type for c in cards})),
            "chapters": "|".join(str(c.chapters) for c in cards),
            "answer_found": answer_found}


def pick(cards, spec):
    """--select 1,3 或 1-3，最多三本。越界忽略。"""
    idx = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if a.isdigit() and b.isdigit():
                idx.extend(range(int(a), int(b) + 1))
        elif part.isdigit():
            idx.append(int(part))
    out, seen = [], set()
    for i in idx:
        if 1 <= i <= len(cards) and cards[i - 1].book not in seen:
            seen.add(cards[i - 1].book)
            out.append(cards[i - 1])
    return out[:3]


def exclude_type(cards, type_name):
    return [c for c in cards if c.type != type_name]


def apply_action(cards, select="", exclude="", refresh_cards=None):
    """返回 (展示的卡, 展开的下一步（全部卡，至多六本）, 用户显式选中的书, rejected)。

    「下一步」与 default_selection（默认前三本）口径不同：不带动作时把
    展示的卡整批展开进 next（至多六本，test: 空选择展开全部卡）。
    """
    if exclude:
        shown = exclude_type(cards, exclude)
        return shown, shown[:6], [], "type:" + exclude
    if select:
        chosen = pick(cards, select)
        return cards, chosen, chosen, "|".join(c.title for c in cards if c not in chosen)
    if refresh_cards is not None:
        return refresh_cards, refresh_cards[:6], [], "refresh"
    return cards, cards[:6], [], ""


def gate_verdict(auto_hit, card_hit, kept_hit, long_auto, long_kept,
                 long10_auto=None, long_display=None):
    """B5 门槛：命中 +10 点，或命中不降且展示列表长书占比下降。

    长书占比的基线取自动 top10（与 B2 的 long_share@10 同单位）；展示列表在触发的题
    上是卡片，未触发的题上是自动 top10。long_kept 是排除类型后的诊断，不单独当门槛，
    因为排除类型会连目标书一起排掉。
    """
    gain = round(card_hit - auto_hit, 3)
    pass_gain = gain >= 0.10
    base = long10_auto if long10_auto is not None else long_auto
    shown = long_display if long_display is not None else long_kept
    pass_long = card_hit >= auto_hit and shown < base
    return {"gain": gain, "pass_gain": pass_gain, "pass_long": pass_long,
            "pass": pass_gain or pass_long}


def next_books(cards, hits, terms, max_chapters=3, toc_fn=None, select_fn=None):
    """选中的书 → 每本最多三章。只含选中的书。"""
    toc_fn = toc_fn or book_readme.readme_toc
    select_fn = select_fn or book_readme.select
    out = []
    for c in cards:
        toc, status = toc_fn(c.book)
        rows = select_fn([r for r in hits if r["book"] == c.book], terms, terms,
                         toc, max_chapters, 0.0)
        out.append({"book": c.book, "title": c.title, "chapters": c.chapters,
                    "book_score": c.score, "readme_status": status,
                    "selected": [{"chapter_path": r["chapter_path"],
                                  "chapter_title": r["chapter_title"],
                                  "line": r["line"], "score": r["score"]}
                                 for r in rows]})
    return out


def cards_for(planner, terms, question, n=DEFAULT_CARDS, books=None, concepts=None,
              toc_fn=None, select_fn=None):
    """用已有 Planner 的首轮缓存建卡，不重复检索。

    返回 (CardSet, hits, nouns, ranking)。book_query 默认开卡时复用它，避免再查一遍。
    """
    terms = [t for t in (terms or []) if t]
    if not terms:
        terms = cjk.split_groups(question or "")
    nouns = book_query.split_terms(terms, planner.actions)
    noun_hits, _ = planner.search(nouns)
    ranking = book_score.rank(noun_hits, nouns, "best_support",
                              getattr(planner, "exclude", []), planner.meta,
                              planner.expand)
    # 选章永远用全词集（B2.5）；卡片排序仍用名词集。
    hits, _ = planner.search(terms) if terms != nouns else (noun_hits, 0.0)
    books = load_books() if books is None else books
    concepts = load_concepts() if concepts is None else concepts
    hints = query_hints(question, books, concepts, terms)
    fire, reasons = trigger(question, ranking, hints, n)
    cards = build_cards(ranking, hits, terms, n, toc_fn, select_fn) if fire else []
    res = CardSet(query=question, terms=terms, trigger=fire, reasons=reasons,
                  hints=hints, cards=cards, ask_user=False)
    res.uncertainty = uncertainty(ranking, hints, reasons, cards)
    res.default_selection = [c.book for c in cards[:3]]
    return res, hits, nouns, ranking


def expand_selected(question, books, expand_fn=None):
    """选中后跑 B4：每本只扩最佳章。返回实际调用次数。"""
    if not books:
        return 0
    if expand_fn is None:
        import book_expand
        cfg = book_expand.MODES["default"]
        deadline = time.monotonic() + cfg["budget"]
        counter = {"used": 0, "cap": cfg["calls"], "lock": threading.Lock()}

        def expand_fn(question, book, path, title, line, mode, status="", nbrs=None):
            out = book_expand.run_chapter(
                question, book, path, title, line, mode, book_expand.default_call,
                deadline, status, True, counter, nbrs or {}, book_expand.NEIGHBORS)
            return asdict(out)
    calls = 0
    for b in books:
        if not b.get("selected"):
            continue
        r = b["selected"][0]
        out = expand_fn(question, b["book"], r["chapter_path"], r["chapter_title"],
                        r["line"], "default", b.get("readme_status", ""))
        b["expand"] = out if isinstance(out, dict) else asdict(out)
        calls += 1
    return calls


def run(query, terms=None, question=None, n=DEFAULT_CARDS, select="", exclude="",
        refresh=False, answer_found="", feedback=FEEDBACK, con=None, meta=None,
        expand=None, books=None, concepts=None, toc_fn=None, select_fn=None,
        expand_fn=None):
    """首轮 → 触发 → 卡片 → 动作 → 下一步。不调模型（--expand 除外）。"""
    t0 = time.time()
    terms = terms or cjk.split_groups(query or "")
    question = question or query or ""
    own = con is None
    if con is None:
        con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    try:
        meta = book_score.load_meta() if meta is None else meta
        expand = book_score.load_expand(con) if expand is None else expand
        books = load_books() if books is None else books
        concepts = load_concepts() if concepts is None else concepts
        planner = book_query.Planner(con, meta, expand, limit=max(n, 10))
        res, hits, nouns, ranking = cards_for(planner, terms, question, n, books,
                                              concepts, toc_fn, select_fn)
        res.query = query
        direct = []
        if not res.trigger:
            direct = build_cards(ranking[:1], hits, terms, 1, toc_fn, select_fn)
            res.default_selection = [c.book for c in direct[:3]]
        if res.trigger:
            refresh_cards = None
            if refresh:
                start = n
                # 没有下一批候选时保持现有卡，不把展示清空
                if len(ranking) > start:
                    refresh_cards = build_cards(ranking[start:start + n], hits, terms,
                                                n, toc_fn, select_fn)
                    for i, c in enumerate(refresh_cards, 1):
                        c.rank = start + i
            shown, chosen, picked, rejected = apply_action(res.cards, select, exclude,
                                                           refresh_cards)
            res.cards = shown
            res.next = next_books(chosen, hits, terms, 3, toc_fn, select_fn) if chosen else []
        else:
            shown, chosen, picked, rejected = [], [], [], ""
            res.next = next_books(direct, hits, terms, 3, toc_fn, select_fn) if direct else []
        if expand and res.next:
            res.model_calls += expand_selected(question, res.next, expand_fn)
        res.feedback = feedback_row(question, shown, picked, rejected, answer_found)
        if feedback:
            append_feedback(feedback, res.feedback)
        res.ms = round((time.time() - t0) * 1000, 1)
        return res
    finally:
        if own:
            con.close()


def gold_rows(path=GOLD):
    with open(path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    out = []
    for r in rows:
        books = r["target_book"].split("|")
        chaps = set(r["target_chapters"].split("|"))
        per_book = {b: {c for c in chaps if c.startswith("库/%s/" % b)} for b in books}
        out.append({"qid": r["qid"], "stratum": r["stratum"], "question": r["question"],
                    "terms": [t.strip() for t in r["terms"].split(",") if t.strip()],
                    "books": books, "chaps": chaps, "per_book": per_book})
    return out


def _hit(book, own, hits, terms, max_chapters=3):
    if not own:
        return 0
    rows = book_query.chapter_order(hits, book)[:max_chapters]
    return int(any(r["path"] in own for r in rows))


def write_tsv(path, rows, cols):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t", lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(path + ".tmp", path)


def _hit_any(ranking, per_book, hits, terms, k):
    """前 k 本里有没有任意一本的目标章命中。"""
    for b in ranking[:k]:
        if _hit(b.book, per_book.get(b.book, set()), hits, terms):
            return 1
    return 0


def _long_share(ranking, k):
    xs = ranking[:k]
    return sum(1 for b in xs if b.chapters >= LONG_SHARE) / len(xs) if xs else 0.0


def _card_long(cards, k):
    xs = cards[:k]
    return sum(1 for c in xs if c.chapters >= LONG_SHARE) / len(xs) if xs else 0.0


def gate(con=None, meta=None, expand=None, books=None, concepts=None, n=DEFAULT_CARDS,
         path=GOLD, out=None):
    """60 题：触发率、等宽命中、长书占比、排除类型后的读数。

    等宽口径（2026-10-23 审计后）：
      K=1  首轮/循环第 1 本              vs 卡片第 1 张
      K=3  循环前 3 本                   vs 卡片前三本（默认展开的章节）
      K=5/6 循环前 5 本                  vs 卡片前六张（展示上限）
    默认入口未触发走循环、触发走卡片前三本，所以主判据是 K=3；K=1 与 K=5/6 只作诊断。
    旧报告的 card 6 本 vs loop top1 不是等宽，+19 是宽度差。
    """
    own = con is None
    if con is None:
        con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    try:
        meta = book_score.load_meta() if meta is None else meta
        expand = book_score.load_expand(con) if expand is None else expand
        books = load_books() if books is None else books
        concepts = load_concepts() if concepts is None else concepts
        rows, out_rows = gold_rows(path), []
        for row in rows:
            planner = book_query.Planner(con, meta, expand, limit=max(n, 10))
            nouns = book_query.split_terms(row["terms"], planner.actions)
            hits, _ = planner.search(nouns)
            ranking = book_score.rank(hits, nouns, "best_support", [], meta, expand)
            hints = query_hints(row["question"], books, concepts, row["terms"])
            fire, reasons = trigger(row["question"], ranking, hints, n)
            cards = build_cards(ranking, hits, nouns, n) if fire else []
            # 自动基线：首轮 top1 / top5
            auto_hit = _hit_any(ranking, row["per_book"], hits, nouns, 1)
            auto_top5 = _hit_any(ranking, row["per_book"], hits, nouns, 5)
            long_auto = int(bool(ranking) and ranking[0].chapters >= LONG_SHARE)
            long10 = _long_share(ranking, 10)
            # B2.5 循环：top1 / top3 / top5
            plan = planner.plan(row["terms"], chapters=False)
            loop_hits, _ = planner.search(row["terms"])
            loop_rank = plan.ranking
            loop_hit = _hit_any(loop_rank, row["per_book"], loop_hits, row["terms"], 1)
            loop_top3 = _hit_any(loop_rank, row["per_book"], loop_hits, row["terms"], 3)
            loop_top5 = _hit_any(loop_rank, row["per_book"], loop_hits, row["terms"], 5)
            loop_long = _long_share(loop_rank, 10)
            long_loop_top3 = _long_share(loop_rank, 3)
            # 卡片：top1 / 前三（默认展开）/ 前六（展示上限）
            if fire:
                card_top1 = _hit_any(cards, row["per_book"], loop_hits, row["terms"], 1)
                card_top3 = _hit_any(cards, row["per_book"], loop_hits, row["terms"], 3)
                card_top6 = _hit_any(cards, row["per_book"], loop_hits, row["terms"], 6)
                shown = {c.book for c in cards}
                tgt = next((b for b in row["books"] if b in shown), "")
                long_cards = _card_long(cards, 6)
                long_card_top3 = _card_long(cards, 3)
                by_type = {}
                for c in cards:
                    if c.chapters >= LONG_SHARE:
                        by_type[c.type] = by_type.get(c.type, 0) + 1
                excl = max(by_type, key=by_type.get) if by_type else ""
                kept = exclude_type(cards, excl) if excl else cards
                kept_tgt = next((b for b in row["books"]
                                 if b in {c.book for c in kept}), "")
                kept_hit = _hit(kept_tgt, row["per_book"].get(kept_tgt, set()),
                                loop_hits, row["terms"]) if kept_tgt else 0
                kept_long = _card_long(kept, 6)
            else:
                tgt, excl, kept = "", "", cards
                card_top1 = card_top3 = card_top6 = 0
                kept_hit = loop_top3
                long_cards = float(long10)
                long_card_top3 = long_loop_top3
                kept_long = float(long10)
            # 默认路径：触发卡前三本；未触发循环前三本（主判据 K=3）
            eff_k1 = card_top1 if fire else loop_hit
            eff_k3 = card_top3 if fire else loop_top3
            eff_k5 = card_top6 if fire else loop_top5
            long_eff = long_card_top3 if fire else long_loop_top3
            out_rows.append({"qid": row["qid"], "stratum": row["stratum"],
                             "trigger": int(fire), "reasons": ",".join(reasons),
                             "auto_hit": auto_hit, "auto_top5": auto_top5,
                             "loop_top1": loop_hit, "loop_top3": loop_top3,
                             "loop_top5": loop_top5,
                             "card_top1": card_top1, "card_top3": card_top3,
                             "card_top6": card_top6,
                             "effective_k1": eff_k1, "effective_k3": eff_k3,
                             "effective_k5": eff_k5,
                             "effective_default": card_top6 if fire else loop_top5,
                             "long_auto": long_auto, "long10_auto": round(long10, 3),
                             "long_loop": round(loop_long, 3),
                             "long_loop_top3": round(long_loop_top3, 3),
                             "long_cards": round(long_cards, 3),
                             "long_card_top3": round(long_card_top3, 3),
                             "long_effective": round(long_eff, 3),
                             "excluded_type": excl, "kept_hit": kept_hit,
                             "long_kept": round(kept_long, 3),
                             "target_shown": int(bool(tgt)), "cards": len(cards)})
        nq = len(out_rows)
        trig = [r for r in out_rows if r["trigger"]]
        mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
        auto = mean([r["auto_hit"] for r in out_rows])
        auto5 = mean([r["auto_top5"] for r in out_rows])
        loop = mean([r["loop_top1"] for r in out_rows])
        loop3 = mean([r["loop_top3"] for r in out_rows])
        loop5 = mean([r["loop_top5"] for r in out_rows])
        eff1 = mean([r["effective_k1"] for r in out_rows])
        eff = mean([r["effective_k3"] for r in out_rows])
        eff5 = mean([r["effective_k5"] for r in out_rows])
        eff_def = mean([r["effective_default"] for r in out_rows])
        kept = mean([r["kept_hit"] for r in out_rows])
        la = mean([r["long_auto"] for r in out_rows])
        l10 = mean([r["long10_auto"] for r in out_rows])
        ll = mean([r["long_loop"] for r in out_rows])
        ll3 = mean([r["long_loop_top3"] for r in out_rows])
        le = mean([r["long_effective"] for r in out_rows])
        lk = mean([r["long_kept"] for r in out_rows])
        auto_t = mean([r["auto_hit"] for r in trig])
        loop_t = mean([r["loop_top1"] for r in trig])
        loop3_t = mean([r["loop_top3"] for r in trig])
        loop5_t = mean([r["loop_top5"] for r in trig])
        card_t = mean([r["card_top3"] for r in trig])
        card6_t = mean([r["card_top6"] for r in trig])
        kept_t = mean([r["kept_hit"] for r in trig])
        ll_t = mean([r["long_loop"] for r in trig])
        ll3_t = mean([r["long_loop_top3"] for r in trig])
        le_t = mean([r["long_cards"] for r in trig])
        lc3_t = mean([r["long_card_top3"] for r in trig])
        lk_t = mean([r["long_kept"] for r in trig])
        # 主判据：K=3 等宽。未触发用循环前三本，触发用卡片前三本；基线同为循环前三本。
        v_all = gate_verdict(loop3, eff, kept, ll3, lk, ll3, le)
        v_trig = gate_verdict(loop3_t, card_t, kept_t, ll3_t, lk_t, ll3_t, lc3_t)
        summary = {"n": nq, "trigger": len(trig), "triggered_n": len(trig),
                   "auto_hit": round(auto, 3), "auto_top5": round(auto5, 3),
                   "loop_hit": round(loop, 3), "loop_top3": round(loop3, 3),
                   "loop_top5": round(loop5, 3),
                   "card_hit_triggered": round(card6_t, 3),
                   "card_top3_triggered": round(card_t, 3),
                   "card_top6_triggered": round(card6_t, 3),
                   "auto_hit_triggered": round(auto_t, 3),
                   "loop_hit_triggered": round(loop_t, 3),
                   "loop_top3_triggered": round(loop3_t, 3),
                   "effective_hit": round(eff, 3), "effective_hit_top1": round(eff1, 3),
                   "effective_hit_top5": round(eff5, 3),
                   "effective_default": round(eff_def, 3),
                   "gain_vs_loop_top5_default": round(eff_def - loop5, 3),
                   "kept_hit": round(kept, 3),
                   "gain_vs_loop": round(eff - loop, 3),
                   "gain_vs_loop_top3": round(eff - loop3, 3),
                   "gain_vs_loop_triggered": round(card_t - loop3_t, 3),
                   "gain_vs_loop_top1_triggered": round(card_t - loop_t, 3),
                   "gain_triggered": round(card_t - auto_t, 3),
                   "long_auto": round(la, 3), "long10_auto": round(l10, 3),
                   "long_loop": round(ll, 3), "long_loop_top3": round(ll3, 3),
                   "long_effective": round(le, 3),
                   "long_cards_triggered": round(le_t, 3),
                   "long_card_top3_triggered": round(lc3_t, 3),
                   "long_kept": round(lk, 3),
                   "target_shown": sum(r["target_shown"] for r in out_rows)}
        summary.update({"pass_gain_overall": v_all["pass_gain"],
                        "pass_long_overall": v_all["pass_long"],
                        "pass_overall": v_all["pass"],
                        "pass_gain_triggered": v_trig["pass_gain"],
                        "pass_long_triggered": v_trig["pass_long"],
                        "pass_triggered": v_trig["pass"]})
        summary["recommend"] = "opt-in" if v_trig["pass"] else "drop"
        summary["decision_note"] = "operator 2026-10-23: default display; evidence gate: " + summary["recommend"]
        if out:
            write_tsv(out, out_rows,
                      ["qid", "stratum", "trigger", "reasons", "auto_hit", "auto_top5",
                       "loop_top1", "loop_top3", "loop_top5", "card_top1", "card_top3",
                       "card_top6", "effective_k1", "effective_k3", "effective_k5",
                       "effective_default", "long_auto", "long10_auto", "long_loop", "long_loop_top3",
                       "long_cards", "long_card_top3", "long_effective",
                       "excluded_type", "kept_hit", "long_kept", "target_shown", "cards"])
        return {"summary": summary, "rows": out_rows}
    finally:
        if own:
            con.close()


def perf(con=None, meta=None, expand=None, n=20, cards=DEFAULT_CARDS, passes=2):
    """20 题开卡/不开卡交错，p95。卡片生成不调模型。"""
    own = con is None
    if con is None:
        con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    try:
        meta = book_score.load_meta() if meta is None else meta
        expand = book_score.load_expand(con) if expand is None else expand
        books, concepts = load_books(), load_concepts()
        rows = gold_rows()[:n]
        out = []
        for p in range(passes):
            for row in rows:
                for mode in ("off", "cards"):
                    t0 = time.time()
                    planner = book_query.Planner(con, meta, expand, limit=10)
                    nouns = book_query.split_terms(row["terms"], planner.actions)
                    hits, _ = planner.search(nouns)
                    ranking = book_score.rank(hits, nouns, "best_support", [], meta, expand)
                    if mode == "cards":
                        hints = query_hints(row["question"], books, concepts, row["terms"])
                        fire, _ = trigger(row["question"], ranking, hints, cards)
                        if fire:
                            build_cards(ranking, hits, nouns, cards)
                    else:
                        if not ranking:
                            # 空题没有可看章，跳过；不补这刀整个 --perf 会中断
                            out.append({"pass": p + 1, "qid": row["qid"], "mode": mode,
                                        "ms": round((time.time() - t0) * 1000, 1)})
                            continue
                        toc, _ = book_readme.readme_toc(ranking[0].book)
                        _chapter_rows(hits, ranking[0].book, nouns, toc)
                    out.append({"pass": p + 1, "qid": row["qid"], "mode": mode,
                                "ms": round((time.time() - t0) * 1000, 1)})
        print("model_calls 0")
        for p in range(passes):
            for mode in ("off", "cards"):
                xs = sorted(r["ms"] for r in out if r["pass"] == p + 1 and r["mode"] == mode)
                p95 = xs[max(0, min(len(xs) - 1, int(round(0.95 * (len(xs) - 1)))))]
                print("pass=%d mode=%-5s n=%d p50 %.0f ms p95 %.0f ms max %.0f ms"
                      % (p + 1, mode, len(xs), xs[len(xs) // 2], p95, xs[-1]))
        return out
    finally:
        if own:
            con.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("terms", nargs="*")
    ap.add_argument("--question", help="自然语言问句，用于书名/作者/原句/概念判断")
    ap.add_argument("--cards", type=int, default=DEFAULT_CARDS, choices=range(MIN_CARDS, MAX_CARDS + 1))
    ap.add_argument("--select", default="", help="1,3 或 1-3，最多三本")
    ap.add_argument("--exclude-type", default="", help="排除一个类型，如 网文/类型")
    ap.add_argument("--refresh", action="store_true", help="换一批")
    ap.add_argument("--answer-found", default="", choices=["", "yes", "no"])
    ap.add_argument("--feedback", default=FEEDBACK, help="反馈 TSV；空串表示不写")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--expand", action="store_true", help="选中后跑 B4，默认每本最佳章")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--gate-out", default=os.path.join(BATCH, "live", "B5", "gate.tsv"),
                    help="--gate 的 TSV 输出路径")
    ap.add_argument("--perf", action="store_true")
    a = ap.parse_args(argv)
    if a.gate:
        res = gate(n=a.cards, out=a.gate_out)
        print(json.dumps(res["summary"], ensure_ascii=False, indent=1))
        for r in res["rows"]:
            print("%-4s %-8s trigger=%d %-22s auto=%d card=%d shown=%d long=%.2f/%.2f kept=%d"
                  % (r["qid"], r["stratum"], r["trigger"], r["reasons"], r["auto_hit"],
                     r["card_hit"], r["target_shown"], r["long_auto"], r["long_cards"],
                     r["kept_hit"]))
        return 0
    if a.perf:
        perf(n=20, cards=a.cards)
        return 0
    query = " ".join(a.terms)
    if not query:
        ap.error("要给查询词，或用 --gate / --perf")
    interactive = sys.stdin.isatty() and sys.stdout.isatty() and not a.json
    if interactive and not (a.select or a.exclude_type or a.refresh):
        preview = run(query, terms=a.terms or None, question=a.question, n=a.cards,
                      feedback=None)
        for i, c in enumerate(preview.cards, 1):
            print("%d. %-40s %-8s %5d章  %s" % (i, c.title, c.type, c.chapters,
                                                c.readme_status))
            print("   %s" % c.reason)
            print("   %s" % c.snippet)
        if preview.trigger:
            ans = input("选书 1,3 / 排除类型 x 类型 / 换一批 r / 默认回车：").strip()
            if ans.lower().startswith("x"):
                a.exclude_type = ans[1:].strip()
            elif ans.lower() == "r":
                a.refresh = True
            elif ans:
                a.select = ans
    res = run(query, terms=a.terms or None, question=a.question, n=a.cards,
              select=a.select, exclude=a.exclude_type, refresh=a.refresh,
              answer_found=a.answer_found, feedback=a.feedback or None,
              expand=a.expand)
    if a.json or not sys.stdout.isatty():
        print(json.dumps(asdict(res), ensure_ascii=False, indent=1, default=str))
        return 0
    print("%s  %s  %s  %d 张卡  %.0f ms" % (
        res.query, "弹卡" if res.trigger else "不弹卡",
        ",".join(res.reasons) or "-", len(res.cards), res.ms), file=sys.stderr)
    for i, c in enumerate(res.cards, 1):
        print("%d. %-40s %-8s %5d章  %s" % (i, c.title, c.type, c.chapters,
                                            c.readme_status))
        print("   %s:%s" % (c.best_chapter_title, c.best_chapter))
        print("   %s" % c.reason)
        print("   %s" % c.snippet)
    if res.next:
        print("下一步（只含选中的书）：", file=sys.stderr)
        for b in res.next:
            print("  %s" % b["title"])
            for r in b["selected"]:
                print("    [%s] %s:%d" % (r["chapter_title"], r["chapter_path"], r["line"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
