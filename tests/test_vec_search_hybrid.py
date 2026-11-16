"""混合检索（vec_search + book_query + answer.pick_passages）的单元测试与降级契约。"""

import http.server
import json
import sqlite3
import sys
import threading
from pathlib import Path

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1] / "src" / "bookcorpus" / "scripts"
DEMO = Path(__file__).resolve().parents[1] / "apps" / "demo"
for _p in (str(SCRIPTS), str(DEMO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import answer
import book_query
import book_score
import qsearch
import vec_search


def _make_synthetic_vectors(tmp_path: Path):
    db_path = tmp_path / "emb.db"
    vec_path = tmp_path / "vectors.f16"
    dim = vec_search.DEFAULT_DIM

    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE book(uid TEXT PRIMARY KEY, status TEXT)")
    con.execute(
        "CREATE TABLE chunk("
        "id INTEGER PRIMARY KEY, book TEXT, path TEXT, seq INTEGER, "
        "line INTEGER, chars INTEGER, text TEXT, active INTEGER)"
    )
    con.executemany(
        "INSERT INTO book VALUES (?, ?)",
        [("B001", "done"), ("B002", "done"), ("B003", "pending")],
    )
    con.executemany(
        "INSERT INTO chunk VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "B001", "books/B001/md/01.md", 0, 12, 100, "a", 1),
            (2, "B002", "books/B002/md/01.md", 0, 25, 100, "b", 1),
            (3, "B002", "books/B002/md/02.md", 1, 48, 100, "c", 1),
            (4, "B002", "books/B002/md/03.md", 2, 70, 100, "trash", 0),  # inactive chunk
            (5, "B003", "books/B003/md/01.md", 0, 10, 100, "pend", 1),  # pending book
        ],
    )
    con.commit()
    con.close()

    vecs = np.zeros((5, dim), dtype=np.float32)
    vecs[0, 0] = 0.2
    vecs[0, 1] = 0.98
    vecs[1, 0] = 0.6
    vecs[1, 1] = 0.8
    vecs[2, 0] = 0.99
    vecs[2, 1] = 0.14
    vecs[3, 0] = 1.0  # inactive, must be filtered
    vecs[4, 0] = 1.0  # pending book, must be filtered
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs.astype(np.float16).tofile(vec_path)
    return str(db_path), str(vec_path), dim


def test_vector_store_filtering_exclude_and_inbook(tmp_path):
    vec_search.clear_cache()
    db_p, vec_p, dim = _make_synthetic_vectors(tmp_path)
    store = vec_search.get_store(embed_db=db_p, vec_path=vec_p, dim=dim)
    assert store is not None
    assert store.chunk_count == 3
    assert store.book_count == 2

    q = np.zeros(dim, dtype=np.float32)
    q[0] = 1.0

    hits = store.search(q, top_k=5)
    assert [h["chunk_id"] for h in hits] == [3, 2, 1]
    assert hits[0]["book"] == "B002"
    assert hits[0]["line"] == 48

    filtered = store.search(q, top_k=5, exclude=["B002"])
    assert [h["book"] for h in filtered] == ["B001"]

    inbook = store.search_book("B002", q, top_k=5)
    assert [h["chunk_id"] for h in inbook] == [3, 2]
    assert store.search_book("B999", q, top_k=5) == []


def test_embed_query_http_and_graceful_fallback(tmp_path):
    vec_search.clear_cache()
    dim = vec_search.DEFAULT_DIM
    expected = np.zeros(dim, dtype=np.float32)
    expected[0] = 3.0
    expected[1] = 4.0

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            assert payload["input"][0].startswith("Instruct:")
            body = json.dumps({"data": [{"index": 0, "embedding": expected.tolist()}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{port}"
        assert vec_search.server_healthy(url=url)
        qv = vec_search.embed_query("为什么离开老家以后亲戚关系就淡了", url=url)
        assert qv is not None
        assert abs(float(np.linalg.norm(qv)) - 1.0) < 1e-5
        assert abs(float(qv[0]) - 0.6) < 1e-5
    finally:
        srv.shutdown()
        srv.server_close()

    # Closed port degrades cleanly to None without raising
    vec_search.clear_cache()
    closed_url = f"http://127.0.0.1:{port}"
    assert vec_search.embed_query("测试降级", url=closed_url, timeout=0.3) is None

    # Missing embedding files degrade cleanly
    hits, meta = vec_search.search_text(
        "测试",
        embed_db=str(tmp_path / "missing.db"),
        vec_path=str(tmp_path / "missing.f16"),
    )
    assert hits == []
    assert meta["used"] is False
    assert meta["reason"] == "no_vectors"


def test_rrf_fusion_book_chapter_passage():
    vec_scores = vec_search.vec_book_scores(
        [
            {"book": "B2", "score": 0.72},
            {"book": "B1", "score": 0.69},
            {"book": "B2", "score": 0.65},
        ]
    )
    assert vec_scores[0][0] == "B2"

    fused = vec_search.fuse_book_scores(["B1", "B3"], ["B2", "B1"], k=30, w_bm25=1.0, w_vec=1.35)
    assert fused["B1"] > fused["B2"] > fused["B3"]

    chaps = vec_search.fuse_chapters(
        [{"chapter_path": "books/B1/md/01.md", "chapter_title": "ch1", "line": 10, "score": 5.0}],
        [
            {"path": "books/B1/md/02.md", "line": 30, "score": 0.85},
            {"path": "books/B1/md/01.md", "line": 12, "score": 0.72},
        ],
        max_chapters=2,
        snippet_fn=None,
    )
    assert chaps[0]["chapter_path"] == "books/B1/md/01.md"
    assert chaps[1]["chapter_path"] == "books/B1/md/02.md"

    passages = vec_search.fuse_passages(
        [{"book": "B1", "path": "books/B1/md/01.md", "line": 100, "score": 4.0}],
        [
            {"book": "B1", "path": "books/B1/md/02.md", "line": 50, "score": 0.90},
            {"book": "B1", "path": "books/B1/md/01.md", "line": 108, "score": 0.80},
        ],
    )
    # Near line 100/108 matches both BM25 rank 1 and Vector rank 2 -> boosted top
    assert passages[0]["path"] == "books/B1/md/01.md"
    assert abs(passages[0]["line"] - 100) <= 10


def test_planner_hybrid_rescue_and_fallback():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE expand(path TEXT, kind TEXT)")
    meta = {
        "B1": book_score.Meta(title="书一", subject1="文学", subject2="", chapters=10),
        "B2": book_score.Meta(title="书二", subject1="人文", subject2="", chapters=20),
    }

    def fake_search(terms):
        if "虚无词" in terms:
            return [], 1.0
        return [
            {
                "id": 1,
                "path": "books/B1/md/01.md",
                "book": "B1",
                "line": 10,
                "score": 8.0,
                "text": "原文命中一段话",
                "chars": 100,
                "hits": 1,
                "groups": ["测试"],
            }
        ], 1.0

    def fake_vec(query_text, exclude):
        return [
            {"chunk_id": 2, "book": "B2", "path": "books/B2/md/05.md", "line": 42, "score": 0.88},
        ], {"used": True, "ms": 12.3, "hits": 1, "qvec": np.ones(4, dtype=np.float32)}

    def fake_book_vec(book, query_text, top_k):
        return [{"chunk_id": 2, "book": book, "path": f"books/{book}/md/05.md", "line": 42, "score": 0.88}]

    p = book_query.Planner(
        con,
        meta=meta,
        expand={},
        search_fn=fake_search,
        df_fn=lambda t: 1,
        vec_fn=fake_vec,
        book_vec_fn=fake_book_vec,
        limit=5,
    )
    plan = p.plan(["虚无词"], question="换一种说法问B2")
    assert plan.fusion == "hybrid"
    assert plan.vec_used is True
    assert [b.book for b in plan.ranking] == ["B2"]
    assert plan.chapters[0]["selected"][0]["chapter_path"] == "books/B2/md/05.md"

    # When vec_fn degrades -> falls back cleanly to BM25-only
    p_deg = book_query.Planner(
        con,
        meta=meta,
        expand={},
        search_fn=fake_search,
        df_fn=lambda t: 1,
        vec_fn=lambda q, ex: ([], {"used": False, "ms": 0.0, "hits": 0, "qvec": None}),
        limit=5,
    )
    plan_deg = p_deg.plan(["测试"])
    assert plan_deg.fusion == "bm25_only"
    assert plan_deg.vec_used is False
    assert [b.book for b in plan_deg.ranking] == ["B1"]


def test_pick_passages_hybrid_and_fallback(monkeypatch, tmp_path):
    book_dir = tmp_path / "books" / "B1" / "md"
    book_dir.mkdir(parents=True)
    md1 = book_dir / "01.md"
    md2 = book_dir / "02.md"
    md1.write_text("# 第一章\n\n第一行正文\n" * 20, encoding="utf-8")
    md2.write_text("# 第二章\n\n这里用换一种说法讲清楚了因果逻辑。\n" * 20, encoding="utf-8")

    monkeypatch.setattr(answer, "ROOT", str(tmp_path))
    monkeypatch.setattr(answer, "_chapter_titles", lambda b: ({}, "missing"))
    monkeypatch.setattr(
        qsearch,
        "search",
        lambda *a, **kw: (
            [{"book": "B1", "path": "books/B1/md/01.md", "line": 3, "score": 3.0, "coverage": 1}],
            1.0,
        ),
    )

    picked, chaps = answer.pick_passages(
        None,
        "B1",
        "books/B1/md/01.md",
        3,
        ["因果"],
        [],
        question="为什么会出现这种结果",
        vec_fn=lambda b, q, k: [{"book": "B1", "path": "books/B1/md/02.md", "line": 12, "score": 0.91}],
    )
    assert picked[0]["path"] == "books/B1/md/01.md"
    assert any(p["path"] == "books/B1/md/02.md" and p["line"] == 12 for p in picked)
    assert any(c["path"] == "books/B1/md/02.md" for c in chaps)

    # Graceful BM25 fallback when vec_fn returns []
    picked_bm25, _ = answer.pick_passages(
        None,
        "B1",
        "books/B1/md/01.md",
        3,
        ["因果"],
        [],
        question="为什么会出现这种结果",
        vec_fn=lambda b, q, k: [],
    )
    assert picked_bm25[0]["path"] == "books/B1/md/01.md"
