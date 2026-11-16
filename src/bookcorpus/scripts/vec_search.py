#!/usr/bin/env python3
"""全库与单书向量检索（基于 emb.db + vectors.f16 的 memmap 混合加速）。

默认在 emb.db / vectors.f16 / llama-server 不可用时平滑降级返回空列表，
让上层 book_query、book_cards 与 answer.pick_passages 回退到纯 BM25，不阻塞流程。
"""

import json
import math
import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

HERE = str(Path(__file__).resolve().parent)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import embed_corpus
import lib_common
import rerank_client

PATHS = lib_common.PATHS
DEFAULT_DIM = embed_corpus.DEFAULT_DIM
DEFAULT_W_VEC = 1.35
BOOK_RRF_K = 30
CHAP_RRF_K = 5
CHAP_POOL_LIMIT = 10
CHAP_W_RERANK = 2.0
PASSAGE_RRF_K = 5
PASSAGE_POOL_LIMIT = 30
PASSAGE_LINE_SPAN = 30

_QUERY_INSTRUCT = "Given a web search query, retrieve relevant passages that answer the query"
_BATCH_SIZE = 32768
_MAX_WORKERS = min(8, os.cpu_count() or 4)
_UNHEALTHY_COOLDOWN_S = 5.0
_QVEC_CACHE_MAX = 128

_LOCK = threading.Lock()
_STORE_CACHE: dict[tuple[str, str, int], "VectorStore"] = {}
_QVEC_CACHE: dict[tuple[str, str, int, str], np.ndarray] = {}
_SEARCH_CACHE: dict[tuple, list[dict]] = {}
_UNHEALTHY_UNTIL: dict[str, float] = {}
_SEARCH_CACHE_MAX = 16


def _embed_url(url=None):
    if url:
        return url.rstrip("/")
    return (os.environ.get("BOOKS_EMBED_URL") or embed_corpus.DEFAULT_URL).rstrip("/")


def _embed_model(model=None):
    if model:
        return model
    return os.environ.get("BOOKS_EMBED_MODEL") or embed_corpus.DEFAULT_MODEL


def _resolve_paths(embed_db=None, vec_path=None):
    return (
        embed_db or PATHS.embed_db,
        vec_path or PATHS.embed_vectors,
    )


def format_query(question):
    """按 Qwen3-Embedding 规范构造检索侧查询前缀。"""
    q = (question or "").strip()
    if not q:
        return ""
    return f"Instruct: {_QUERY_INSTRUCT}\nQuery: {q}"


def clear_cache():
    """清空内存里的向量索引、查询向量缓存与健康冷却状态（测试用）。"""
    with _LOCK:
        _STORE_CACHE.clear()
        _QVEC_CACHE.clear()
        _SEARCH_CACHE.clear()
        _UNHEALTHY_UNTIL.clear()


def available(embed_db=None, vec_path=None):
    """仅判断向量库文件是否存在且非空。"""
    db_p, vec_p = _resolve_paths(embed_db, vec_path)
    try:
        return Path(db_p).is_file() and Path(vec_p).is_file() and Path(vec_p).stat().st_size > 0
    except OSError:
        return False


def server_healthy(url=None, timeout=0.5):
    """非阻塞探活本机 llama-server；失败走短冷却，不反复拖慢请求。"""
    u = _embed_url(url)
    now = time.monotonic()
    with _LOCK:
        until = _UNHEALTHY_UNTIL.get(u, 0.0)
        if now < until:
            return False
    req = urllib.request.Request(u + "/health", method="GET")  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            ok = resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        ok = False
    with _LOCK:
        if ok:
            _UNHEALTHY_UNTIL.pop(u, None)
        else:
            _UNHEALTHY_UNTIL[u] = time.monotonic() + _UNHEALTHY_COOLDOWN_S
    return ok


def embed_query(question, url=None, model=None, dim=DEFAULT_DIM, timeout=5.0):
    """获取单条查询的 L2 归一化 float32 向量；不可用时返回 None。"""
    prompt = format_query(question)
    if not prompt:
        return None
    u = _embed_url(url)
    m = _embed_model(model)
    ckey = (u, m, dim, prompt)
    with _LOCK:
        cached = _QVEC_CACHE.get(ckey)
        if cached is not None:
            return cached
        until = _UNHEALTHY_UNTIL.get(u, 0.0)
        if time.monotonic() < until:
            return None

    body = json.dumps({"model": m, "input": [prompt]}).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310
        u + "/v1/embeddings",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        items = sorted(data.get("data") or [], key=lambda x: x.get("index", 0))
        if not items:
            return None
        vec = np.asarray(items[0]["embedding"], dtype=np.float32)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, IndexError):
        with _LOCK:
            _UNHEALTHY_UNTIL[u] = time.monotonic() + _UNHEALTHY_COOLDOWN_S
        return None

    if vec.ndim != 1 or vec.shape[0] != dim:
        return None
    norm = float(np.linalg.norm(vec))
    if norm <= 1e-12:
        return None
    vec = vec / norm

    with _LOCK:
        _UNHEALTHY_UNTIL.pop(u, None)
        if len(_QVEC_CACHE) >= _QVEC_CACHE_MAX:
            _QVEC_CACHE.pop(next(iter(_QVEC_CACHE)))
        _QVEC_CACHE[ckey] = vec
    return vec


class VectorStore:
    """用 memmap 只读流式扫描 vectors.f16，仅在内存保留紧凑的 chunk->book 索引。"""

    def __init__(self, embed_db, vec_path, dim=DEFAULT_DIM):
        self.embed_db = embed_db
        self.vec_path = vec_path
        self.dim = dim
        self._load()

    def _load(self):
        vec_bytes = Path(self.vec_path).stat().st_size
        n_total = vec_bytes // (self.dim * 2)
        if n_total <= 0:
            self._mm = None
            self._row_ids = np.zeros(0, dtype=np.int64)
            self._chunk_ids = np.zeros(0, dtype=np.int64)
            self._book_idx = np.zeros(0, dtype=np.int32)
            self._book_names = []
            self._book_to_id = {}
            return

        con = sqlite3.connect(f"file:{self.embed_db}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT c.id, c.book FROM chunk c JOIN book b ON c.book=b.uid "
                "WHERE c.active=1 AND b.status='done' ORDER BY c.id"
            ).fetchall()
        finally:
            con.close()

        valid = [(cid, bk) for cid, bk in rows if 1 <= cid <= n_total]
        n = len(valid)
        self._mm = np.memmap(self.vec_path, dtype=np.float16, mode="r", shape=(n_total, self.dim))
        self._chunk_ids = np.empty(n, dtype=np.int64)
        self._row_ids = np.empty(n, dtype=np.int64)
        self._book_idx = np.empty(n, dtype=np.int32)
        book_to_id = {}
        book_names = []
        for i, (cid, bk) in enumerate(valid):
            self._chunk_ids[i] = cid
            self._row_ids[i] = cid - 1
            bi = book_to_id.get(bk)
            if bi is None:
                bi = len(book_names)
                book_to_id[bk] = bi
                book_names.append(bk)
            self._book_idx[i] = bi
        self._book_names = book_names
        self._book_to_id = book_to_id

    @property
    def chunk_count(self):
        return len(self._row_ids)

    @property
    def book_count(self):
        return len(self._book_names)

    def _fetch_chunk_meta(self, chunk_ids):
        if not chunk_ids:
            return {}
        con = sqlite3.connect(f"file:{self.embed_db}?mode=ro", uri=True)
        try:
            ph = ",".join("?" * len(chunk_ids))
            rows = con.execute(
                f"SELECT id, book, path, line FROM chunk WHERE id IN ({ph})",  # noqa: S608
                chunk_ids,
            ).fetchall()
        finally:
            con.close()
        return {int(cid): (bk, path, int(line or 1)) for cid, bk, path, line in rows}

    def _score_batch(self, start, end, qvec, top_k, excl_mask=None):
        rids = self._row_ids[start:end]
        if int(rids[-1] - rids[0]) == (end - start - 1):
            mat = self._mm[int(rids[0]) : int(rids[-1]) + 1].astype(np.float32)
        else:
            mat = self._mm[rids].astype(np.float32)
        sims = mat @ qvec
        if excl_mask is not None:
            sub_mask = excl_mask[start:end]
            if np.any(sub_mask):
                sims[sub_mask] = -1e9
        k = min(top_k, len(sims))
        if k <= 0:
            return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.float32)
        idx = np.argpartition(sims, -k)[-k:]
        return idx + start, sims[idx]

    def search(self, qvec, top_k=200, exclude=None):
        """全库流式向量近邻检索，返回降序命中列表。"""
        n = len(self._row_ids)
        if n == 0 or self._mm is None or qvec is None:
            return []
        qvec = np.asarray(qvec, dtype=np.float32)
        excl_mask = None
        if exclude:
            ex_ids = [self._book_to_id[b] for b in exclude if b in self._book_to_id]
            if ex_ids:
                excl_mask = np.isin(self._book_idx, ex_ids)

        k = min(top_k, n)
        ranges = [(s, min(s + _BATCH_SIZE, n)) for s in range(0, n, _BATCH_SIZE)]
        if len(ranges) == 1:
            g_idx, g_sc = self._score_batch(ranges[0][0], ranges[0][1], qvec, k, excl_mask)
        else:
            all_idx = []
            all_sc = []
            with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
                for b_idx, b_sc in pool.map(
                    lambda ab: self._score_batch(ab[0], ab[1], qvec, k, excl_mask),
                    ranges,
                ):
                    all_idx.append(b_idx)
                    all_sc.append(b_sc)
            g_idx = np.concatenate(all_idx)
            g_sc = np.concatenate(all_sc)
            if len(g_sc) > k:
                keep = np.argpartition(g_sc, -k)[-k:]
                g_idx = g_idx[keep]
                g_sc = g_sc[keep]

        order = np.argsort(-g_sc)
        g_idx = g_idx[order]
        g_sc = g_sc[order]

        chosen_pos = [int(p) for p, sc in zip(g_idx, g_sc, strict=True) if sc > -1e8]
        chosen_sc = [float(sc) for sc in g_sc if sc > -1e8]
        cids = [int(self._chunk_ids[p]) for p in chosen_pos]
        meta = self._fetch_chunk_meta(cids)
        out = []
        for cid, sc in zip(cids, chosen_sc, strict=True):
            row = meta.get(cid)
            if row:
                bk, path, line = row
                out.append(
                    {
                        "chunk_id": cid,
                        "book": bk,
                        "path": path,
                        "line": line,
                        "score": round(sc, 6),
                    }
                )
        return out

    def search_book(self, book, qvec, top_k=30):
        """单书切片向量近邻检索，只读该书对应的行。"""
        if self._mm is None or qvec is None:
            return []
        bi = self._book_to_id.get(book)
        if bi is None:
            return []
        sel = np.flatnonzero(self._book_idx == bi)
        if len(sel) == 0:
            return []
        qvec = np.asarray(qvec, dtype=np.float32)
        mat = self._mm[self._row_ids[sel]].astype(np.float32)
        sims = mat @ qvec
        k = min(top_k, len(sims))
        idx = np.argpartition(sims, -k)[-k:]
        idx = idx[np.argsort(-sims[idx])]
        cids = [int(self._chunk_ids[sel[i]]) for i in idx]
        meta = self._fetch_chunk_meta(cids)
        out = []
        for cid, sc in zip(cids, sims[idx], strict=True):
            row = meta.get(cid)
            if row:
                bk, path, line = row
                out.append(
                    {
                        "chunk_id": cid,
                        "book": bk,
                        "path": path,
                        "line": line,
                        "score": round(float(sc), 6),
                    }
                )
        return out


def get_store(embed_db=None, vec_path=None, dim=DEFAULT_DIM):
    """按文件快照缓存加载 VectorStore；索引不可用时返回 None。"""
    db_p, vec_p = _resolve_paths(embed_db, vec_path)
    if not available(db_p, vec_p):
        return None
    try:
        st_db = Path(db_p).stat()
        st_vec = Path(vec_p).stat()
    except OSError:
        return None
    sig = (
        str(Path(db_p).resolve()),
        str(Path(vec_p).resolve()),
        dim,
        st_db.st_mtime_ns,
        st_vec.st_size,
        st_vec.st_mtime_ns,
    )
    with _LOCK:
        store = _STORE_CACHE.get((sig[0], sig[1], dim))
        if store is not None and getattr(store, "_sig", None) == sig:
            return store
    try:
        store = VectorStore(db_p, vec_p, dim=dim)
        store._sig = sig
    except (OSError, sqlite3.Error, ValueError):
        return None
    with _LOCK:
        _STORE_CACHE[(sig[0], sig[1], dim)] = store
    return store


def health_info(embed_db=None, vec_path=None, url=None):
    """返回向量检索组件状态，供 /api/health 与诊断检查。"""
    db_p, vec_p = _resolve_paths(embed_db, vec_path)
    files_ok = available(db_p, vec_p)
    chunks = 0
    books = 0
    if files_ok:
        try:
            con = sqlite3.connect(f"file:{db_p}?mode=ro", uri=True)
            try:
                chunks = int(con.execute("SELECT COUNT(*) FROM chunk WHERE active=1").fetchone()[0])
                books = int(con.execute("SELECT COUNT(*) FROM book WHERE status='done'").fetchone()[0])
            finally:
                con.close()
        except sqlite3.Error:
            files_ok = False
    srv_ok = server_healthy(url=url, timeout=0.35) if files_ok else False
    return {
        "vectors": "ok" if files_ok else "missing",
        "server": "ok" if srv_ok else "offline",
        "ready": bool(files_ok and srv_ok),
        "chunks": chunks,
        "books": books,
        "url": _embed_url(url),
    }


def vec_book_scores(vhits):
    """把块级向量命中汇聚成书级排序：[(book, score, hit_count), ...]。"""
    by_b = {}
    for h in vhits or ():
        b = h.get("book")
        if not b:
            continue
        sc = float(h.get("score") or 0.0)
        cur = by_b.get(b)
        if cur is None:
            by_b[b] = [sc, 1]
        else:
            if sc > cur[0]:
                cur[0] = sc
            cur[1] += 1
    ranked = [(b, round(best + 0.02 * math.log1p(cnt), 6), cnt) for b, (best, cnt) in by_b.items()]
    ranked.sort(key=lambda item: -item[1])
    return ranked


def fuse_book_scores(bm25_books, vec_books, k=BOOK_RRF_K, w_bm25=1.0, w_vec=DEFAULT_W_VEC):
    """书级 RRF 融合：合并 BM25 书序与向量书序，返回 {book: fused_score}。"""
    scores = {}
    for rk, b in enumerate((bm25_books or ())[:30], 1):
        scores[b] = scores.get(b, 0.0) + w_bm25 / (k + rk)
    for rk, b in enumerate((vec_books or ())[:30], 1):
        scores[b] = scores.get(b, 0.0) + w_vec / (k + rk)
    return scores


def read_snippet(rel_path, line, max_chars=150):
    """读取章节目标行附近的短预览，供纯向量新命中的章卡或选段展示。"""
    if not rel_path:
        return ""
    full = Path(PATHS.root) / rel_path
    try:
        with full.open(encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return ""
    if not lines:
        return ""
    idx = max(0, min(len(lines) - 1, int(line or 1) - 1))
    text = " ".join(" ".join(lines[idx : idx + 3]).split())
    return text[:max_chars]


def fetch_chunk_texts(chunk_ids, embed_db=None):
    """按 chunk_id 批量读取 emb.db 里存好的约 700 字原文块。"""
    ids = sorted({int(c) for c in (chunk_ids or ()) if c is not None})
    if not ids:
        return {}
    db_p, _ = _resolve_paths(embed_db, None)
    if not Path(db_p).is_file():
        return {}
    out = {}
    try:
        con = sqlite3.connect(f"file:{db_p}?mode=ro", uri=True)
        try:
            for i in range(0, len(ids), 400):
                batch = ids[i : i + 400]
                ph = ",".join("?" * len(batch))
                for cid, txt in con.execute(
                    f"SELECT id, text FROM chunk WHERE id IN ({ph})",  # noqa: S608
                    batch,
                ):
                    if txt:
                        out[int(cid)] = txt
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    return out


def _score_chapter_rerank(
    bm_list,
    v_list,
    question,
    rerank_fn=None,
    chapter_text_fn=None,
    embed_db=None,
    max_chars=700,
):
    """对候选章的代表命中跑交叉编码器精排，返回 {chapter_path: max_score} 或 None。"""
    q = (question or "").strip()
    if not q or (not bm_list and not v_list):
        return None
    if rerank_fn is None and not rerank_client.server_healthy():
        return None

    cands = []
    seen = []  # (cp, line)
    for r in bm_list:
        cp = r.get("chapter_path")
        if cp:
            ln = int(r.get("line") or 1)
            cands.append((cp, ln, None, r.get("snippet") or ""))
            seen.append((cp, ln))
    for vr in v_list:
        cp = vr["chapter_path"]
        ln = int(vr.get("line") or 1)
        if any(scp == cp and abs(sln - ln) <= PASSAGE_LINE_SPAN for scp, sln in seen):
            continue
        cands.append((cp, ln, vr.get("chunk_id"), ""))
        seen.append((cp, ln))

    if not cands:
        return None

    chunk_texts = fetch_chunk_texts([cid for _, _, cid, _ in cands if cid is not None], embed_db=embed_db)
    texts = []
    for cp, ln, cid, fallback in cands:
        txt = ""
        if chapter_text_fn is not None:
            txt = chapter_text_fn(cp, ln, cid, fallback) or ""
        if not txt and cid is not None:
            txt = chunk_texts.get(int(cid), "")[:max_chars]
        if not txt:
            txt = rerank_client.read_centered_text(cp, ln, max_chars=max_chars) or fallback
        texts.append(txt[:max_chars])

    valid_idx = [i for i, t in enumerate(texts) if t.strip()]
    if not valid_idx:
        return None
    scores = (
        rerank_fn(q, [texts[i] for i in valid_idx])
        if rerank_fn is not None
        else rerank_client.rerank_scores(q, [texts[i] for i in valid_idx])
    )
    if not scores or len(scores) != len(valid_idx):
        return None
    chap_best = {}
    for i, sc in zip(valid_idx, scores, strict=True):
        cp = cands[i][0]
        fsc = float(sc)
        if cp not in chap_best or fsc > chap_best[cp]:
            chap_best[cp] = fsc
    return chap_best


def fuse_chapters(
    bm25_rows,
    vec_hits,
    max_chapters=3,
    k=CHAP_RRF_K,
    w_vec=DEFAULT_W_VEC,
    max_vec=CHAP_POOL_LIMIT,
    title_fn=None,
    snippet_fn=read_snippet,
    question="",
    rerank_fn=None,
    w_rerank=CHAP_W_RERANK,
    chapter_text_fn=None,
    embed_db=None,
):
    """章级 RRF 融合：合并 BM25、单书向量与可选交叉编码器精排。"""
    v_chaps = {}
    for h in vec_hits or ():
        cp = h.get("path")
        if not cp:
            continue
        sc = float(h.get("score") or 0.0)
        if cp not in v_chaps or sc > v_chaps[cp]["vscore"]:
            v_chaps[cp] = {
                "chapter_path": cp,
                "line": int(h.get("line") or 1),
                "vscore": sc,
                "chunk_id": h.get("chunk_id"),
            }
    v_sorted = sorted(v_chaps.values(), key=lambda x: -x["vscore"])
    bm_list = [r for r in (bm25_rows or ())[:CHAP_POOL_LIMIT] if r.get("chapter_path")]
    v_list = v_sorted[:max_vec]
    if not bm_list and not v_list:
        return []

    chap_rr = _score_chapter_rerank(
        bm_list,
        v_list,
        question,
        rerank_fn=rerank_fn,
        chapter_text_fn=chapter_text_fn,
        embed_db=embed_db,
    )
    if not v_list and chap_rr is None:
        return list(bm25_rows)[:max_chapters]

    scores = {}
    meta = {}
    for rk, r in enumerate(bm_list, 1):
        cp = r["chapter_path"]
        scores[cp] = scores.get(cp, 0.0) + 1.0 / (k + rk)
        meta[cp] = dict(r)
    for rk, vr in enumerate(v_list, 1):
        cp = vr["chapter_path"]
        scores[cp] = scores.get(cp, 0.0) + w_vec / (k + rk)
        if cp not in meta:
            title = title_fn(cp) if title_fn else Path(cp).name
            snip = snippet_fn(cp, vr["line"]) if snippet_fn else ""
            meta[cp] = {
                "chapter_path": cp,
                "chapter_title": title,
                "line": vr["line"],
                "score": round(vr["vscore"], 4),
                "snippet": snip,
                "hits": 0,
            }
        else:
            meta[cp]["vec_score"] = round(vr["vscore"], 4)

    if chap_rr:
        rr_sorted = sorted(chap_rr.items(), key=lambda kv: -kv[1])
        for rk, (cp, rsc) in enumerate(rr_sorted, 1):
            if cp in scores:
                scores[cp] += float(w_rerank) / (k + rk)
                meta[cp]["rerank_score"] = round(rsc, 4)

    ordered = sorted(scores.items(), key=lambda kv: -kv[1])[:max_chapters]
    out = []
    for cp, fsc in ordered:
        row = meta[cp]
        row["fused_score"] = round(fsc, 6)
        out.append(row)
    return out


def fuse_passages(
    bm25_hits,
    vec_hits,
    k=PASSAGE_RRF_K,
    w_vec=DEFAULT_W_VEC,
    max_bm25=PASSAGE_POOL_LIMIT,
    max_vec=PASSAGE_POOL_LIMIT,
    line_span=PASSAGE_LINE_SPAN,
):
    """书内段落级 RRF 融合：对齐同一章节邻近 line_span 行内的 BM25 与向量命中。"""
    if not vec_hits:
        return [dict(h) for h in (bm25_hits or ())]
    scores = {}
    meta = {}
    span = max(1, int(line_span))
    b_sorted = sorted(bm25_hits or (), key=lambda h: -float(h.get("score") or 0.0))
    for rk, h in enumerate(b_sorted[:max_bm25], 1):
        p = h.get("path")
        if not p:
            continue
        lno = int(h.get("line") or 1)
        key = (p, lno // span)
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rk)
        if key not in meta:
            meta[key] = dict(h)

    v_sorted = sorted(vec_hits, key=lambda h: -float(h.get("score") or 0.0))
    for rk, h in enumerate(v_sorted[:max_vec], 1):
        p = h.get("path")
        if not p:
            continue
        lno = int(h.get("line") or 1)
        match_key = None
        for ex_key, ex_h in meta.items():
            if ex_key[0] == p and abs(int(ex_h.get("line") or 1) - lno) <= span:
                match_key = ex_key
                break
        key = match_key or (p, lno // span)
        scores[key] = scores.get(key, 0.0) + w_vec / (k + rk)
        if key not in meta:
            meta[key] = {
                "book": h.get("book", ""),
                "path": p,
                "line": lno,
                "score": float(h.get("score") or 0.0),
                "hits": 0,
            }

    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    out = []
    for key, fsc in ordered:
        row = dict(meta[key])
        row["rrf_score"] = round(fsc, 6)
        out.append(row)
    return out


def search_text(question, top_k=200, exclude=None, embed_db=None, vec_path=None, url=None):
    """全库查询向量检索总入口，返回 (hits, meta_dict)。"""
    t0 = time.time()
    q = (question or "").strip()
    if not q:
        return [], {"used": False, "ms": 0.0, "hits": 0, "qvec": None, "reason": "empty_query"}
    store = get_store(embed_db=embed_db, vec_path=vec_path)
    if store is None:
        return [], {
            "used": False,
            "ms": round((time.time() - t0) * 1000, 1),
            "hits": 0,
            "qvec": None,
            "reason": "no_vectors",
        }
    qvec = embed_query(q, url=url)
    if qvec is None:
        return [], {
            "used": False,
            "ms": round((time.time() - t0) * 1000, 1),
            "hits": 0,
            "qvec": None,
            "reason": "no_server",
        }
    skey = (
        getattr(store, "_sig", (store.embed_db, store.vec_path)),
        top_k,
        tuple(sorted(exclude or ())),
        format_query(q),
    )
    with _LOCK:
        cached_hits = _SEARCH_CACHE.get(skey)
    if cached_hits is not None:
        return [dict(h) for h in cached_hits], {
            "used": True,
            "ms": round((time.time() - t0) * 1000, 1),
            "hits": len(cached_hits),
            "qvec": qvec,
            "reason": "cache",
        }
    hits = store.search(qvec, top_k=top_k, exclude=exclude)
    with _LOCK:
        if len(_SEARCH_CACHE) >= _SEARCH_CACHE_MAX:
            _SEARCH_CACHE.pop(next(iter(_SEARCH_CACHE)))
        _SEARCH_CACHE[skey] = [dict(h) for h in hits]
    return hits, {
        "used": True,
        "ms": round((time.time() - t0) * 1000, 1),
        "hits": len(hits),
        "qvec": qvec,
        "reason": "ok",
    }


def search_book_text(book, question, top_k=30, embed_db=None, vec_path=None, url=None, qvec=None):
    """单书向量检索入口；不可用时平滑返回 []。"""
    if not book:
        return []
    store = get_store(embed_db=embed_db, vec_path=vec_path)
    if store is None:
        return []
    qv = qvec if qvec is not None else embed_query(question, url=url)
    if qv is None:
        return []
    return store.search_book(book, qv, top_k=top_k)
