#!/usr/bin/env python3
"""书名解析：问句里点到哪本书，给出 exact / ambiguous / missing。

  from book_title import resolve_title
  resolve_title("《万历十五年》里万历为什么对张居正态度转变非常大",
                book_cards.load_books(), book_score.load_meta())

匹配优先级：引号《》 > 归一化全名相等 > 分隔符前的主标题相等 > 唯一前缀。
同名同作者的多版本取章数最多的那本，算 exact；同名不同作者、不同卷次算
ambiguous 并给出候选。不做任意 2 字子串匹配，避免「中国」这类词把整库点着。

`books` 是 {uid: {"title","author"}}（book_cards.load_books），`meta` 是
{uid: book_score.Meta}（可为 None）。返回只含 JSON 安全类型。
"""
import re

SEP_RE = re.compile(r"[：:—–\-（(]")
QUOTE_RE = re.compile(r"《([^》]{2,})》")
NORM_RE = re.compile(r"[\s《》〈〉“”\"'「」『』（）()【】\[\]、，,。.！!？?：:；;·—\-]+")
SHORT_TAIL = "里中内的讲说是这那"


def normalize(text):
    return NORM_RE.sub("", text or "")


def main_title(title):
    return SEP_RE.split(title or "", 1)[0]


def detect_title(query, books):
    """没给 hint 时自己找：最长已知书名命中，其次引号里的书名。"""
    q = normalize(query)
    best = ""
    for rec in books.values():
        t = normalize(rec.get("title"))
        if not t:
            continue
        hit = (len(t) >= 3 and t in q) or \
            (len(t) == 2 and (q == t or (q.startswith(t) and q[2:3] in SHORT_TAIL)))
        if hit and len(t) > len(best):
            best = t
    quoted = QUOTE_RE.search(query or "")
    if quoted:
        qt = normalize(quoted.group(1))
        if len(qt) > len(best):
            best = qt
    return best


def _chapters(meta, uid):
    m = (meta or {}).get(uid)
    if isinstance(m, dict):
        return int(m.get("chapters") or 0)
    return int(getattr(m, "chapters", 0) or 0)


def _candidates(uids, books, meta):
    out = []
    for uid in uids:
        rec = books.get(uid) or {}
        out.append({"book": uid, "title": rec.get("title") or "",
                    "author": rec.get("author") or "",
                    "chapters": _chapters(meta, uid)})
    out.sort(key=lambda c: (-c["chapters"], c["book"]))
    return out


def _match(title, books):
    """返回 (匹配方式, uid 列表)。匹配方式是 title / main / prefix，空串表示没匹配。"""
    exact, main = {}, {}
    for uid, rec in books.items():
        full = normalize(rec.get("title"))
        if not full:
            continue
        exact.setdefault(full, []).append(uid)
        short = normalize(main_title(rec.get("title")))
        if short:
            main.setdefault(short, []).append(uid)
    if title in exact:
        return "title", list(exact[title])
    if title in main:
        return "main", list(main[title])
    if len(title) >= 2:
        hits = []
        for uid, rec in books.items():
            if normalize(rec.get("title")).startswith(title) or \
                    normalize(main_title(rec.get("title"))).startswith(title):
                hits.append(uid)
        if hits:
            return "prefix", hits
    return "", []


def _author_names(text):
    return {normalize(a) for a in re.split(r"[、，,;；/\s]+", text or "") if normalize(a)}


def _decide(candidates):
    """同全名同作者取章数最多的一本；作者缺一个也算不同作者，交给用户选。"""
    titles = {normalize(c["title"]) for c in candidates}
    authors = [_author_names(c["author"]) for c in candidates]
    same_author = all(not a for a in authors) or \
        (all(authors) and len({frozenset(a) for a in authors}) == 1)
    if len(titles) == 1 and same_author:
        return "exact", candidates[0]
    return "ambiguous", None


def _result(status, query_title, matched_by="", book="", books=None, meta=None,
            candidates=None):
    rec = (books or {}).get(book) if book else {}
    rec = rec or {}
    return {
        "status": status,
        "query_title": query_title or "",
        "book": book or None,
        "title": rec.get("title") or "",
        "author": rec.get("author") or "",
        "chapters": _chapters(meta, book) if book else 0,
        "matched_by": matched_by,
        "best_path": "",
        "best_line": 0,
        "candidates": candidates or [],
    }


def resolve_title(query, books, meta=None, hint=""):
    books = books or {}
    quote = QUOTE_RE.search(query or "")
    quote_title = normalize(quote.group(1)) if quote else ""
    if quote_title:
        rule, uids = _match(quote_title, books)
        if not rule:
            return _result("missing", quote_title, "", "", books, meta)
    sources = []
    if quote_title:
        sources.append(("quoted", quote_title))
    if hint:
        sources.append(("hint", normalize(hint)))
    sources.append(("detect", detect_title(query, books)))
    seen, query_title = set(), ""
    for tag, title in sources:
        if not title or title in seen:
            continue
        seen.add(title)
        if not query_title:
            query_title = title
        rule, uids = _match(title, books)
        if not rule:
            continue
        candidates = _candidates(list(dict.fromkeys(uids)), books, meta)
        status, best = _decide(candidates)
        matched_by = "quoted" if tag == "quoted" and rule == "title" else rule
        if status == "exact":
            return _result("exact", query_title, matched_by, best["book"], books, meta)
        return _result("ambiguous", query_title, matched_by, "", books, meta, candidates)
    return _result("missing", query_title, "", "", books, meta)
