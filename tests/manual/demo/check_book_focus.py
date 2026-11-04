#!/usr/bin/env python3
"""Live acceptance for an explicitly named book (focus) and the anchored answer.

Usage: BOOKS_ROOT=... python tests/manual/demo/check_book_focus.py [base-url]
The server must already be running and the data/index must contain B0019.
This calls the model for /api/run (term extraction) and /api/answer (primary +
supplement), so it is a manual acceptance, not part of pytest.
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765").rstrip("/")
QUESTION = "《万历十五年》里万历为什么对张居正的态度转变非常大"
FOCUS_BOOK = "B0019"
FRONT_MATTER = ("05_", "22_", "23_", "04_", "16_", "17_", "24_", "25_", "26_", "27_")
fails = []


def check(name, ok, detail=""):
    print("[%s] %s%s" % ("过" if ok else "败", name, (" · " + detail) if detail else ""))
    if not ok:
        fails.append(name)


def post(path, obj, timeout=300):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def source(row, title=""):
    path = row.get("best_path") or row.get("path") or ""
    return {
        "book": row.get("book") or "",
        "title": row.get("title") or row.get("book_title") or title,
        "path": path,
        "line": int(row.get("best_line") or row.get("line") or 1),
    }


status, trace = post("/api/run", {"question": QUESTION})
check("run 200", status == 200)
focus = trace.get("focus") or {}
plan = trace.get("plan") or {}
ranking = plan.get("ranking") or []
result = trace.get("result") or {}
cards = trace.get("cards") or {}

check("focus exact", focus.get("status") == "exact", json.dumps(focus, ensure_ascii=False)[:300])
check("focus book B0019", focus.get("book") == FOCUS_BOOK, str(focus.get("book")))
check(
    "ranking[0] pinned focus",
    bool(ranking) and ranking[0].get("book") == FOCUS_BOOK and bool(ranking[0].get("pinned")),
    str(ranking[0].get("book") if ranking else None),
)
check("result focus", result.get("book") == FOCUS_BOOK, str(result.get("book")))
check("cards not triggered", cards.get("trigger") is False)
result_path = os.path.basename(result.get("path") or "")
check("result is body chapter", bool(result_path) and not result_path.startswith(FRONT_MATTER), result_path)

sources = []
if result.get("path"):
    sources.append(source(result, focus.get("title") or "万历十五年"))
for row in ranking:
    if len(sources) >= 4:
        break
    if row.get("book") == FOCUS_BOOK or not row.get("best_path"):
        continue
    sources.append(source(row))
check(
    "sources focus first", bool(sources) and sources[0].get("book") == FOCUS_BOOK, str([s.get("book") for s in sources])
)

if len(sources) < 2:
    check("supplement available", False, "no other book with best_path")
else:
    body = {
        "question": QUESTION,
        "book": sources[0]["book"],
        "path": sources[0]["path"],
        "line": sources[0]["line"],
        "terms": (trace.get("extract") or {}).get("terms") or [],
        "groups": (trace.get("extract") or {}).get("groups") or [],
        "sources": sources,
        "focus": focus,
    }
    status, answer = post("/api/answer", body)
    check("answer 200", status == 200)
    parts = answer.get("parts") or []
    check("mode anchored", answer.get("mode") == "anchored", str(answer.get("mode")))
    check("two parts", len(parts) == 2, str([p.get("kind") for p in parts]))
    if len(parts) >= 2:
        primary, supplement = parts[0], parts[1]
        check(
            "primary kind/title",
            primary.get("kind") == "primary" and (primary.get("title") or "").find("万历十五年") >= 0,
            str(primary.get("title")),
        )
        primary_cites = primary.get("citations") or []
        check(
            "primary citations all focus",
            bool(primary_cites) and all(c.get("book") == FOCUS_BOOK for c in primary_cites),
            str([c.get("book") for c in primary_cites]),
        )
        check(
            "primary citations have book_title",
            all(c.get("book_title") == "万历十五年" for c in primary_cites),
            str([c.get("book_title") for c in primary_cites]),
        )
        supplement_cites = supplement.get("citations") or []
        check(
            "supplement excludes focus",
            all(c.get("book") != FOCUS_BOOK for c in supplement_cites),
            str([c.get("book") for c in supplement_cites]),
        )
        check(
            "supplement citations have book_title",
            all(c.get("book_title") for c in supplement_cites),
            str([c.get("book_title") for c in supplement_cites]),
        )
    flat = answer.get("citations") or []
    check(
        "flat citations focus first",
        bool(flat) and flat[0].get("book") == FOCUS_BOOK,
        str([c.get("book") for c in flat[:4]]),
    )
    check(
        "flat citation has book_title",
        bool(flat) and flat[0].get("book_title") == "万历十五年",
        str([c.get("book_title") for c in flat[:4]]),
    )
    text = answer.get("answer") or ""
    check("answer has supplement heading", "其他书的补充" in text, text[:120])
    check("answer no wrong-book disclaimer", "这本书不是《万历十五年》" not in text)
    check("answer lead not wrong book", "这本书不是" not in (answer.get("lead") or ""))
    if answer.get("ok"):
        status, cached = post("/api/answer", body)
        check(
            "second answer cached",
            cached.get("cached") is True and (cached.get("mode") == "anchored"),
            str(cached.get("cached")),
        )

print("\nALL PASS" if not fails else "\n失败 %d 项：%s" % (len(fails), ", ".join(fails)))
sys.exit(0 if not fails else 1)
