#!/usr/bin/env python3
"""Phase 4 小样本 A/B 检索评测工具：对比上下文前缀、L0 规范化与水章/注释过滤。

支持在独立临时或 --work-dir 目录构建隔离样本库（绝不写入生产 ~/书库 或 build/检索/）：
  1. 离线 BM25（index.db）：原始未过滤（A 组）vs L0 规范化 + 水章/注释过滤（B 组）；
  2. 离线切块与 Token 开销分析：A / B（--context-header）/ C（叠加 --skip-notes）的块数、
     字符/Token 增幅百分比、污染清零对比及全库重嵌时间估算；
  3. 向量与 Hybrid RRF 对比：当 embedding HTTP 服务可达时，在隔离 --emb-dir 分别建库评测；
     当服务不可达时，优雅输出 embed_ab: skipped_unreachable 与一行补跑命令。

用法：
  book-corpus eval-ab --root ~/书库 --uids B0169,B0301 --work-dir /tmp/eval-ab
  book-corpus eval-ab --root ~/书库 --sample-size 8 --json
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import math
import os
import re
import shlex
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build_para_index
import embed_corpus
import eval_retrieval
import lib_fts_cjk as cjk
import qsearch
import vec_search
from layout import Layout, find_root

from bookcorpus.md import classify_chapter_role, normalize_book_md, parse_markdown

CAP = eval_retrieval.CAP
HTML_TAG_RE = re.compile(r"<(?:table|tr|td|th|div)\b", re.IGNORECASE)
ATTR_RESIDUE_RE = re.compile(r"\{\.[a-zA-Z0-9_-]+|\{#[a-zA-Z0-9_-]+")
FOOTNOTE_RESIDUE_RE = re.compile(r"note\d*\.png|\[\[\d+\]\]\([^)]*\)")
CJK_PHRASE_RE = re.compile(r"[\u4e00-\u9fff]{2,8}")

DEFAULT_OVERHEAD_GATE_PCT = 15.0
DEFAULT_THROUGHPUT_TOK_S = 24800.0
DEFAULT_MRR_GAIN = 0.02
DEFAULT_RECALL10_GAIN = 0.03
MAX_REGRESSION = -0.01


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _validate_isolation(source_root: Path, work_dir: Path) -> None:
    src = source_root.resolve()
    wd = work_dir.resolve()
    if wd == src or _is_within(wd, src / "books") or _is_within(wd, src / "build"):
        raise SystemExit(f"安全护栏拒绝：--work-dir ({wd}) 不能与源工作区 {src} 重合或位于其 books/build 下")


def _extract_uid_from_path(raw_path: str) -> str:
    parts = Path(raw_path.replace("\\", "/")).parts
    if len(parts) >= 2 and parts[0] == "books":
        return parts[1]
    return ""


def _build_title_to_uid_map(source_root: Path) -> dict[str, str]:
    lib_db = source_root / "build" / "library.db"
    if not lib_db.is_file():
        return {}
    out: dict[str, str] = {}
    con = sqlite3.connect(f"file:{lib_db}?mode=ro", uri=True)
    try:
        for uid, title in con.execute("SELECT uid, title FROM book"):
            if title:
                out.setdefault(str(title).strip(), str(uid))
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    return out


def _normalize_gold_path(raw_path: str, uid: str) -> str:
    norm = raw_path.replace("\\", "/").strip()
    if norm.startswith("books/"):
        return norm
    name = Path(norm).name
    if uid and name:
        return f"books/{uid}/md/{name}"
    return norm


def _auto_gold_path(source_root: Path) -> Path | None:
    candidates = (
        source_root / "工作区" / "批次" / "2026-11-15-hybrid-eval" / "gold-120.tsv",
        source_root / "工作区" / "批次" / "qa-检索-2026-10-19" / "gold.tsv",
        source_root / "工作区" / "批次" / "qa-检索-2026-10-22-书级融合" / "book_gold.tsv",
    )
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def load_gold_queries(gold_path: Path, source_root: Path) -> list[dict[str, str]]:
    """读取评测 TSV（兼容 gold.tsv / gold-120.tsv / book_gold.tsv 口径）。"""
    title_map = _build_title_to_uid_map(source_root)
    rows: list[dict[str, str]] = []
    with gold_path.open(encoding="utf-8") as fh:
        for idx, raw in enumerate(csv.DictReader(fh, delimiter="\t"), 1):
            raw_path = (raw.get("path") or "").split("|", 1)[0].strip()
            uid = (raw.get("uid") or "").strip() or _extract_uid_from_path(raw_path)
            if not uid and raw.get("target_book"):
                first_book = raw["target_book"].split("|", 1)[0].strip()
                book_name = Path(first_book).name
                uid = title_map.get(book_name, "")
            if not uid and raw_path:
                # 兼容旧 库/.../书名/md/章.md 路径
                parts = raw_path.replace("\\", "/").split("/")
                if "md" in parts:
                    md_idx = parts.index("md")
                    if md_idx >= 1:
                        uid = title_map.get(parts[md_idx - 1], "")
            terms = (raw.get("terms") or "").strip()
            question = (raw.get("question") or "").strip() or terms.replace(",", " ")
            if not (terms or question):
                continue
            rows.append(
                {
                    "qid": (raw.get("qid") or f"Q{idx:03d}").strip(),
                    "category": (raw.get("category") or raw.get("type") or "默认").strip(),
                    "question": question,
                    "terms": terms or ",".join(cjk.split_groups(question)),
                    "uid": uid,
                    "path": _normalize_gold_path(raw_path, uid),
                    "evidence": (raw.get("evidence") or "").strip(),
                }
            )
    return rows


def select_sample_books(
    source_root: Path,
    *,
    uids: list[str] | None = None,
    sample_size: int = 8,
    gold_rows: list[dict[str, str]] | None = None,
) -> list[tuple[str, str, str]]:
    """从只读源工作区选择待测样本书 [(uid, path, title), ...]。"""
    available = embed_corpus.md_books(source_root)
    by_uid = {u: (u, p, t) for u, p, t in available}
    if not by_uid:
        raise SystemExit(f"源工作区没有可评测的 md 书：{source_root / 'books'}")

    if uids:
        selected = []
        missing = []
        for u in uids:
            if u in by_uid:
                selected.append(by_uid[u])
            else:
                missing.append(u)
        if missing:
            raise SystemExit(f"指定 uid 在源库没有 md 正文：{', '.join(missing)}")
        return selected

    limit = max(1, sample_size)
    selected_uids: list[str] = []
    seen: set[str] = set()
    if gold_rows:
        for row in gold_rows:
            u = row.get("uid", "")
            if u and u in by_uid and u not in seen:
                seen.add(u)
                selected_uids.append(u)
                if len(selected_uids) >= limit:
                    break
    for u in by_uid:
        if len(selected_uids) >= limit:
            break
        if u not in seen:
            seen.add(u)
            selected_uids.append(u)
    return [by_uid[u] for u in selected_uids]


def _write_isolated_library_db(ws_root: Path, books: list[tuple[str, str, str]]) -> None:
    """为隔离工作区构建最小只读元数据 library.db（仅含样本书，不读生产库）。"""
    db_path = ws_root / "build" / "library.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "CREATE TABLE book(uid TEXT PRIMARY KEY, path TEXT NOT NULL, title TEXT, "
            "author TEXT, subject1 TEXT, subject2 TEXT)"
        )
        con.execute("CREATE TABLE derived(uid TEXT NOT NULL, kind TEXT NOT NULL, status TEXT, chapters INTEGER)")
        for uid, rel_path, title in books:
            bdir = ws_root / "books" / rel_path
            bj = bdir / "book.json"
            s1 = s2 = author = ""
            btitle = title
            if bj.is_file():
                try:
                    data = json.loads(bj.read_text(encoding="utf-8", errors="replace"))
                    btitle = str(data.get("title") or btitle)
                    author = str(data.get("author") or "")
                    subs = data.get("subjects") or []
                    if isinstance(subs, list):
                        s1 = str(subs[0]) if len(subs) > 0 else ""
                        s2 = str(subs[1]) if len(subs) > 1 else ""
                except (OSError, ValueError):
                    pass
            md_dir = bdir / "md"
            chaps = (
                sum(1 for p in md_dir.iterdir() if p.is_file() and p.suffix == ".md" and p.name.lower() != "readme.md")
                if md_dir.is_dir()
                else 0
            )
            con.execute(
                "INSERT INTO book(uid, path, title, author, subject1, subject2) VALUES(?,?,?,?,?,?)",
                (uid, rel_path, btitle, author, s1, s2),
            )
            con.execute("INSERT INTO derived(uid, kind, status, chapters) VALUES(?,?,?,?)", (uid, "md", "done", chaps))
        con.commit()
    finally:
        con.close()


def _copy_book_md(src_book_dir: Path, dst_book_dir: Path) -> None:
    dst_book_dir.mkdir(parents=True, exist_ok=True)
    bj = src_book_dir / "book.json"
    if bj.is_file():
        shutil.copy2(bj, dst_book_dir / "book.json")
    src_md = src_book_dir / "md"
    dst_md = dst_book_dir / "md"
    dst_md.mkdir(parents=True, exist_ok=True)
    for item in sorted(src_md.iterdir()):
        if item.is_file() and (item.suffix == ".md" or item.name == ".manifest.json"):
            shutil.copy2(item, dst_md / item.name)


def prepare_isolated_workspaces(
    source_root: Path,
    work_dir: Path,
    books: list[tuple[str, str, str]],
) -> tuple[Path, Path, dict[str, Any]]:
    """复制样本书到隔离 ws_raw 与 ws_norm，并在 ws_norm 跑 L0 规范化。"""
    ws_raw = work_dir / "ws_raw"
    ws_norm = work_dir / "ws_norm"
    for ws in (ws_raw, ws_norm):
        if ws.exists():
            shutil.rmtree(ws)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "books.toml").write_text("schema = 2\n", encoding="utf-8")

    norm_summary = {
        "books": len(books),
        "split_chapters": 0,
        "tables_converted": 0,
        "footnotes_normalized": 0,
        "front_back_chapters": 0,
        "notes_chapters": 0,
        "body_chapters": 0,
    }

    for _uid, rel_path, _title in books:
        src_book_dir = source_root / "books" / rel_path
        _copy_book_md(src_book_dir, ws_raw / "books" / rel_path)
        _copy_book_md(src_book_dir, ws_norm / "books" / rel_path)
        res = normalize_book_md(ws_norm / "books" / rel_path / "md", dry_run=False)
        stats = res.get("stats") or {}
        norm_summary["split_chapters"] += max(
            0, int(stats.get("chapters_after", 0)) - int(stats.get("chapters_before", 0))
        )
        norm_summary["tables_converted"] += int(stats.get("html_tables_converted", 0))
        norm_summary["footnotes_normalized"] += int(stats.get("footnotes_converted", 0))
        roles = stats.get("roles") or {}
        norm_summary["front_back_chapters"] += int(roles.get("front_back", 0))
        norm_summary["notes_chapters"] += int(roles.get("notes", 0))
        norm_summary["body_chapters"] += int(roles.get("body", 0))

    _write_isolated_library_db(ws_raw, books)
    _write_isolated_library_db(ws_norm, books)
    return ws_raw, ws_norm, norm_summary


def _derive_sample_queries(ws_norm: Path, books: list[tuple[str, str, str]], max_per_book: int = 2) -> list[dict]:
    """当缺少匹配的 gold TSV 时，从样本正文自动提取可重复的章级定位问句。"""
    queries: list[dict] = []
    qid_num = 1
    for uid, rel_path, title in books:
        md_dir = ws_norm / "books" / rel_path / "md"
        roles = embed_corpus.load_manifest_chapter_roles(md_dir)
        picked = 0
        for p in sorted(md_dir.iterdir()):
            if not p.is_file() or p.suffix != ".md" or p.name.lower() == "readme.md":
                continue
            raw = p.read_text(encoding="utf-8", errors="replace")
            parsed = parse_markdown(raw, stem=p.stem, base_dir=md_dir, manifest_roles=roles, file_name=p.name)
            if (parsed.get("chapter_role") or "body") != "body":
                continue
            paras = [
                str(b.get("clean_text") or "").strip()
                for b in parsed["blocks"]
                if b.get("kind") == "paragraph" and len(str(b.get("clean_text") or "").strip()) >= 40
            ]
            if not paras:
                continue
            body_text = paras[0]
            phrases = [m.group(0) for m in CJK_PHRASE_RE.finditer(body_text)]
            # 取两个较有区分度的短语（长度 2-6）
            terms: list[str] = []
            for ph in sorted(set(phrases), key=lambda s: (-min(len(s), 4), body_text.find(s))):
                if 2 <= len(ph) <= 6:
                    terms.append(ph)
                if len(terms) >= 2:
                    break
            if not terms:
                words = list(re.findall(r"[A-Za-z]{3,}", body_text))
                terms = words[:2]
            if not terms:
                continue
            ch_title = str(parsed.get("title") or p.stem)
            evidence = body_text[:24]
            queries.append(
                {
                    "qid": f"AUTO{qid_num:03d}",
                    "category": "自动抽样",
                    "question": f"《{title}》{ch_title}里如何论述{'、'.join(terms)}？",
                    "terms": ",".join(terms),
                    "uid": uid,
                    "path": f"books/{rel_path}/md/{p.name}",
                    "evidence": evidence,
                }
            )
            qid_num += 1
            picked += 1
            if picked >= max_per_book:
                break
    return queries


def _resolve_targets_for_ws(ws_root: Path, query: dict[str, str]) -> set[str]:
    """解析某条评测题在给定工作区（原始或 L0 拆章后）的可接受目标章路径集合。"""
    target_path = query["path"]
    uid = query["uid"]
    evidence = query.get("evidence", "")
    exact = ws_root / target_path
    if exact.is_file():
        return {target_path}
    md_dir = ws_root / "books" / uid / "md"
    if not md_dir.is_dir():
        return {target_path}
    matched: set[str] = set()
    stem_prefix = Path(target_path).stem
    ev_compact = re.sub(r"\s+", "", evidence)
    for p in sorted(md_dir.iterdir()):
        if not p.is_file() or p.suffix != ".md" or p.name.lower() == "readme.md":
            continue
        rel = f"books/{uid}/md/{p.name}"
        if ev_compact:
            txt = p.read_text(encoding="utf-8", errors="replace")
            if ev_compact in re.sub(r"\s+", "", txt):
                matched.add(rel)
                continue
        if stem_prefix and p.stem.startswith(stem_prefix):
            matched.add(rel)
    return matched or {target_path}


def _chapter_role_lookup(ws_root: Path, fallback_roles: dict[str, str] | None = None) -> dict[str, str]:
    roles_by_path: dict[str, str] = {}
    books_dir = ws_root / "books"
    if not books_dir.is_dir():
        return roles_by_path
    for bdir in sorted(books_dir.iterdir()):
        if not bdir.is_dir() or bdir.name.startswith("_"):
            continue
        md_dir = bdir / "md"
        if not md_dir.is_dir():
            continue
        m_roles = embed_corpus.load_manifest_chapter_roles(md_dir)
        for p in sorted(md_dir.iterdir()):
            if not p.is_file() or p.suffix != ".md" or p.name.lower() == "readme.md":
                continue
            raw = p.read_text(encoding="utf-8", errors="replace")
            parsed = parse_markdown(raw, stem=p.stem, base_dir=md_dir, manifest_roles=m_roles, file_name=p.name)
            rel = p.relative_to(ws_root).as_posix()
            role = str(parsed.get("chapter_role") or "body")
            if role == "body":
                if fallback_roles and rel in fallback_roles:
                    role = fallback_roles[rel]
                else:
                    role = classify_chapter_role(str(parsed.get("title") or ""), raw, file_name=p.name)
            roles_by_path[rel] = role
    return roles_by_path


def _has_dirty_residue(text: str) -> bool:
    return bool(HTML_TAG_RE.search(text) or ATTR_RESIDUE_RE.search(text) or FOOTNOTE_RESIDUE_RE.search(text))


def _scan_disk_md_residue(ws_root: Path) -> dict[str, int]:
    """统计盘上 md 文件中的 HTML 表、Pandoc 属性与脚注图标残渣文件数。"""
    counts = {"md_files": 0, "html_residue_files": 0, "attr_residue_files": 0, "footnote_residue_files": 0}
    books_dir = ws_root / "books"
    if not books_dir.is_dir():
        return counts
    for bdir in sorted(books_dir.iterdir()):
        if not bdir.is_dir() or bdir.name.startswith("_"):
            continue
        md_dir = bdir / "md"
        if not md_dir.is_dir():
            continue
        for p in sorted(md_dir.iterdir()):
            if not p.is_file() or p.suffix != ".md" or p.name.lower() == "readme.md":
                continue
            counts["md_files"] += 1
            txt = p.read_text(encoding="utf-8", errors="replace")
            if HTML_TAG_RE.search(txt):
                counts["html_residue_files"] += 1
            if ATTR_RESIDUE_RE.search(txt):
                counts["attr_residue_files"] += 1
            if FOOTNOTE_RESIDUE_RE.search(txt):
                counts["footnote_residue_files"] += 1
    return counts


def inspect_index_pollution(
    index_db: Path, ws_root: Path, *, roles_by_path: dict[str, str] | None = None
) -> dict[str, int]:
    """统计 index.db 段落中的角色污染与盘上/索引残渣条目数。"""
    roles = roles_by_path if roles_by_path is not None else _chapter_role_lookup(ws_root)
    disk = _scan_disk_md_residue(ws_root)
    con = sqlite3.connect(f"file:{index_db}?mode=ro", uri=True)
    counts = {
        "paras": 0,
        "chapters": 0,
        "front_back_paras": 0,
        "notes_paras": 0,
        "html_residue_paras": 0,
        "attr_residue_paras": 0,
        "footnote_residue_paras": 0,
        **disk,
    }
    try:
        paths: set[str] = set()
        for path, text in con.execute("SELECT path, text FROM para"):
            counts["paras"] += 1
            paths.add(path)
            role = roles.get(path, "body")
            if role == "front_back":
                counts["front_back_paras"] += 1
            elif role == "notes":
                counts["notes_paras"] += 1
            if HTML_TAG_RE.search(text):
                counts["html_residue_paras"] += 1
            if ATTR_RESIDUE_RE.search(text):
                counts["attr_residue_paras"] += 1
            if FOOTNOTE_RESIDUE_RE.search(text):
                counts["footnote_residue_paras"] += 1
        counts["chapters"] = len(paths)
    finally:
        con.close()
    return counts


def _chunk_arm_stats(
    ws_root: Path,
    books: list[tuple[str, str, str]],
    *,
    chunk_chars: int,
    overlap: int,
    context_header: bool,
    include_front_back: bool,
    skip_notes: bool,
    roles_by_path: dict[str, str] | None = None,
) -> dict[str, Any]:
    roles_map = roles_by_path if roles_by_path is not None else _chapter_role_lookup(ws_root)
    skip_roles = embed_corpus.skip_roles_for(include_front_back=include_front_back, skip_notes=skip_notes)
    chunks_total = 0
    text_chars = 0
    input_chars = 0
    fb_chunks = 0
    notes_chunks = 0
    residue_chunks = 0
    for _uid, rel_path, title in books:
        chunks = embed_corpus.book_chunks(
            ws_root,
            rel_path,
            chunk_chars,
            overlap,
            context_header=context_header,
            book_title=title,
            skip_roles=skip_roles,
        )
        for c in chunks:
            chunks_total += 1
            text_chars += int(c["chars"])
            in_txt = c.get("embed_text") if context_header else c["text"]
            input_chars += len(in_txt or "")
            role = roles_map.get(c["path"], "body")
            if role == "front_back":
                fb_chunks += 1
            elif role == "notes":
                notes_chunks += 1
            if _has_dirty_residue(c["text"]):
                residue_chunks += 1
    header_chars = max(0, input_chars - text_chars)
    header_pct = round((header_chars / text_chars * 100.0) if text_chars else 0.0, 2)
    return {
        "context_header": context_header,
        "include_front_back": include_front_back,
        "skip_notes": skip_notes,
        "chunks": chunks_total,
        "text_chars": text_chars,
        "input_chars": input_chars,
        "header_overhead_chars": header_chars,
        "header_overhead_pct": header_pct,
        "est_input_tokens": round(input_chars * embed_corpus.TOKENS_PER_CHAR),
        "est_text_tokens": round(text_chars * embed_corpus.TOKENS_PER_CHAR),
        "front_back_chunks": fb_chunks,
        "notes_chunks": notes_chunks,
        "residue_chunks": residue_chunks,
    }


def _estimate_corpus_tokens(source_root: Path, explicit_tokens: int | None) -> tuple[int | None, str]:
    if explicit_tokens and explicit_tokens > 0:
        return explicit_tokens, "cli"
    emb_db = source_root / "build" / "检索" / "embeddings" / "emb.db"
    if emb_db.is_file():
        try:
            con = sqlite3.connect(f"file:{emb_db}?mode=ro", uri=True)
            try:
                row = con.execute("SELECT coalesce(sum(chars), 0) FROM chunk WHERE active=1").fetchone()
                chars = int(row[0] or 0) if row else 0
                if chars > 0:
                    return round(chars * embed_corpus.TOKENS_PER_CHAR), "source_emb_db"
            finally:
                con.close()
        except sqlite3.Error:
            pass
    idx_db = source_root / "build" / "检索" / "index.db"
    if idx_db.is_file():
        try:
            con = sqlite3.connect(f"file:{idx_db}?mode=ro", uri=True)
            try:
                row = con.execute("SELECT value FROM meta WHERE key='chars'").fetchone()
                chars = int(row[0] or 0) if row else 0
                if chars > 0:
                    return round(chars * embed_corpus.TOKENS_PER_CHAR), "source_index_db"
            finally:
                con.close()
        except sqlite3.Error:
            pass
    return None, "sample_only"


def _evaluate_rankings(
    queries: list[dict[str, str]],
    target_sets: list[set[str]],
    rank_fn,
    ks: list[int],
    roles_by_path: dict[str, str],
) -> dict[str, Any]:
    n = len(queries)
    if n == 0:
        return {"n": 0, "mrr": 0.0, "recall": {}, "hit": {}, "ndcg": {}, "pollution_at_10": 0.0}
    max_k = max(ks)
    ranks: list[int] = []
    recall_acc = dict.fromkeys(ks, 0.0)
    hit_acc = dict.fromkeys(ks, 0.0)
    ndcg_acc = dict.fromkeys(ks, 0.0)
    polluted_10 = 0.0
    per_query: list[dict[str, Any]] = []

    for q, targets in zip(queries, target_sets, strict=True):
        cands = rank_fn(q)
        pos = next((i for i, p in enumerate(cands, 1) if p in targets), None)
        rank = min(pos, CAP) if pos is not None else CAP
        ranks.append(rank)

        for k in ks:
            top_k = cands[:k]
            hits_in_k = sum(1 for p in top_k if p in targets)
            recall_acc[k] += hits_in_k / max(1, len(targets))
            hit_acc[k] += 1.0 if hits_in_k > 0 else 0.0
            dcg = sum(1.0 / math.log2(i + 1) for i, p in enumerate(top_k, 1) if p in targets)
            idcg = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(targets), k) + 1))
            ndcg_acc[k] += (dcg / idcg) if idcg > 0 else 0.0

        top10 = cands[:10]
        if top10:
            bad = sum(1 for p in top10 if roles_by_path.get(p, "body") in ("front_back", "notes"))
            polluted_10 += bad / len(top10)

        per_query.append(
            {
                "qid": q["qid"],
                "uid": q["uid"],
                "rank": rank,
                "found": pos is not None,
                f"hit@{max_k}": int(rank <= max_k),
            }
        )

    mrr = sum(1.0 / r for r in ranks if r < CAP) / n
    return {
        "n": n,
        "mrr": round(mrr, 4),
        "recall": {str(k): round(recall_acc[k] / n, 4) for k in ks},
        "hit": {str(k): round(hit_acc[k] / n, 4) for k in ks},
        "ndcg": {str(k): round(ndcg_acc[k] / n, 4) for k in ks},
        "pollution_at_10": round(polluted_10 / n, 4),
        "details": per_query,
    }


def _qsearch_candidates(index_db: Path, q: dict[str, str], limit: int = 100) -> list[str]:
    groups = [t for t in q["terms"].split(",") if t] or cjk.split_groups(q["question"])
    expr = " OR ".join(f"({cjk.query_expr(g)})" for g in groups if cjk.query_expr(g))
    if not expr:
        return []
    con = sqlite3.connect(f"file:{index_db}?mode=ro", uri=True)
    try:
        hits, _ms = qsearch.search(con, expr, groups, [], 2000, 40, False, 2000)
    finally:
        con.close()
    seen: set[str] = set()
    order: list[str] = []
    for h in hits:
        p = h["path"]
        if p not in seen:
            seen.add(p)
            order.append(p)
            if len(order) >= limit:
                break
    return order


def _embed_arm(
    ws_root: Path,
    emb_dir: Path,
    books: list[tuple[str, str, str]],
    *,
    server_url: str,
    dim: int,
    chunk_chars: int,
    overlap: int,
    context_header: bool,
    include_front_back: bool,
    skip_notes: bool,
) -> dict[str, Any]:
    if emb_dir.exists():
        shutil.rmtree(emb_dir)
    emb_dir.mkdir(parents=True, exist_ok=True)
    db_path = emb_dir / "emb.db"
    vec_path = emb_dir / "vectors.f16"
    store = embed_corpus.Store(
        db_path,
        vec_path,
        dim=dim,
        model=embed_corpus.DEFAULT_MODEL,
        chunk_chars=chunk_chars,
        overlap=overlap,
        context_header=context_header,
        include_front_back=include_front_back,
        skip_notes=skip_notes,
    )
    try:
        args = SimpleNamespace(
            sync=False,
            books=",".join(u for u, _, _ in books),
            force=True,
            context_header=context_header,
            include_front_back=include_front_back,
            skip_notes=skip_notes,
            chunk_chars=chunk_chars,
            overlap=overlap,
            batch=32,
            timeout=120,
            server=server_url,
            keep_server=True,
            allow_missing_engine=False,
        )
        with contextlib.redirect_stdout(sys.stderr):
            rc = embed_corpus.run_embedding(ws_root, store, books, args)
        if rc != 0:
            raise RuntimeError(f"隔离 embedding 建库失败（rc={rc}）：{emb_dir}")
        stats = store.stats()
    finally:
        store.close()
    return stats


def _decision_gate(
    *,
    chunk_b: dict[str, Any],
    embed_ab: dict[str, Any],
    max_overhead_pct: float,
) -> dict[str, Any]:
    overhead_pct = float(chunk_b.get("header_overhead_pct") or 0.0)
    overhead_ok = overhead_pct <= max_overhead_pct
    pollution_ok = int(chunk_b.get("front_back_chunks", 0)) == 0 and int(chunk_b.get("residue_chunks", 0)) == 0

    if embed_ab.get("status") != "completed":
        return {
            "recommendation": "PENDING_EMBED_AB",
            "reason": (
                "embedding HTTP 服务不可达，已完成离线 BM25 与 Token 开销预检；"
                "待接通服务补跑向量 A/B 后再决定全库重嵌。"
            ),
            "thresholds": {
                "min_mrr_gain": DEFAULT_MRR_GAIN,
                "min_recall10_gain": DEFAULT_RECALL10_GAIN,
                "max_regression": MAX_REGRESSION,
                "max_token_overhead_pct": max_overhead_pct,
                "require_zero_front_back_pollution": True,
            },
            "checks": {
                "overhead_within_budget": overhead_ok,
                "pollution_cleared": pollution_ok,
                "vector_quality_gate": None,
            },
        }

    hyb_a = embed_ab["arms"]["A_baseline"]["hybrid"]
    hyb_b = embed_ab["arms"]["B_treatment"]["hybrid"]
    vec_a = embed_ab["arms"]["A_baseline"]["vector"]
    vec_b = embed_ab["arms"]["B_treatment"]["vector"]

    hyb_mrr_delta = round(hyb_b["mrr"] - hyb_a["mrr"], 4)
    hyb_r10_delta = round(hyb_b["recall"].get("10", 0.0) - hyb_a["recall"].get("10", 0.0), 4)
    vec_mrr_delta = round(vec_b["mrr"] - vec_a["mrr"], 4)
    vec_r10_delta = round(vec_b["recall"].get("10", 0.0) - vec_a["recall"].get("10", 0.0), 4)

    gain_ok = (
        hyb_mrr_delta >= DEFAULT_MRR_GAIN
        or hyb_r10_delta >= DEFAULT_RECALL10_GAIN
        or vec_mrr_delta >= DEFAULT_MRR_GAIN
        or vec_r10_delta >= DEFAULT_RECALL10_GAIN
    )
    no_reg = (
        hyb_mrr_delta >= MAX_REGRESSION
        and hyb_r10_delta >= MAX_REGRESSION
        and vec_mrr_delta >= MAX_REGRESSION
        and vec_r10_delta >= MAX_REGRESSION
    )
    quality_ok = gain_ok and no_reg
    passed = overhead_ok and pollution_ok and quality_ok
    if passed:
        rec = "RECOMMEND_REEMBED"
        reason = "向量/混合检索指标达标且无显著退化，Token 增幅与水章污染清零均满足全库重嵌门槛。"
    else:
        rec = "HOLD_REEMBED"
        fails = []
        if not quality_ok:
            fails.append(f"检索增量未达门槛（hybrid ΔMRR={hyb_mrr_delta:+.4f}, ΔR@10={hyb_r10_delta:+.4f}）")
        if not overhead_ok:
            fails.append(f"Token 增幅超限（{overhead_pct:.2f}% > {max_overhead_pct:.2f}%）")
        if not pollution_ok:
            fails.append("B 组仍存在水章或残渣块")
        reason = "暂不建议全库重嵌：" + "；".join(fails)

    return {
        "recommendation": rec,
        "reason": reason,
        "deltas_b_vs_a": {
            "vector_mrr": vec_mrr_delta,
            "vector_recall_10": vec_r10_delta,
            "hybrid_mrr": hyb_mrr_delta,
            "hybrid_recall_10": hyb_r10_delta,
        },
        "thresholds": {
            "min_mrr_gain": DEFAULT_MRR_GAIN,
            "min_recall10_gain": DEFAULT_RECALL10_GAIN,
            "max_regression": MAX_REGRESSION,
            "max_token_overhead_pct": max_overhead_pct,
            "require_zero_front_back_pollution": True,
        },
        "checks": {
            "overhead_within_budget": overhead_ok,
            "pollution_cleared": pollution_ok,
            "vector_quality_gate": quality_ok,
        },
    }


def _build_rerun_command(
    source_root: Path,
    books: list[tuple[str, str, str]],
    work_dir_arg: str | None,
    gold_arg: str | None,
    server_url: str,
) -> str:
    parts = [
        f"BOOKS_EMBED_URL={shlex.quote(server_url)}",
        "uv",
        "run",
        "--frozen",
        "book-corpus",
        "eval-ab",
        "--root",
        shlex.quote(str(source_root)),
        "--uids",
        shlex.quote(",".join(u for u, _, _ in books)),
    ]
    if gold_arg:
        parts.extend(["--gold", shlex.quote(gold_arg)])
    if work_dir_arg:
        parts.extend(["--work-dir", shlex.quote(work_dir_arg)])
    return " ".join(parts)


def render_markdown_report(report: dict[str, Any]) -> str:
    """渲染人类可读的 Markdown 决策报告。"""
    ks = [str(k) for k in report["ks"]]
    lines = [
        "# Phase 4 Embedding 与清洗策略 A/B 评测报告",
        "",
        f"- 源工作区：`{report['source_root']}`（只读）",
        f"- 样本书数：{len(report['sample_books'])} 本（{', '.join(b['uid'] for b in report['sample_books'])}）",
        f"- 评测题数：{report['query_count']} 题（来源：`{report['query_source']}`）",
        f"- 决策结论：**{report['decision']['recommendation']}** — {report['decision']['reason']}",
        "",
        "## 1. 切块、Token 开销与块污染对比（离线实测）",
        "",
        (
            "| 实验组 | 上下文前缀 | 跳过 front_back | 跳过 notes | 块数 | 正文字数 | 入模字数"
            " | 前缀开销增幅 | 估算 Token | 水章块 | 注释块 | 残渣块 |"
        ),
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm_key, label in (
        ("A_baseline", "A（基线未过滤）"),
        ("A_filtered", "A'（过滤无前缀）"),
        ("B_treatment", "B（前缀+滤水章）"),
        ("C_skip_notes", "C（前缀+滤水章+滤注释）"),
    ):
        arm = report["chunk_analysis"].get(arm_key)
        if not arm:
            continue
        lines.append(
            f"| {label} | {'开' if arm['context_header'] else '关'} | "
            f"{'否' if arm['include_front_back'] else '是'} | "
            f"{'是' if arm['skip_notes'] else '否'} | "
            f"{arm['chunks']} | {arm['text_chars']:,} | {arm['input_chars']:,} | "
            f"+{arm['header_overhead_pct']:.2f}% | {arm['est_input_tokens']:,} | "
            f"{arm['front_back_chunks']} | {arm['notes_chunks']} | {arm['residue_chunks']} |"
        )

    est = report["reembed_estimate"]
    lines.extend(
        [
            "",
            f"- `--context-header` 纯前缀字/Token 增幅（B 组同块对比）：**+{est['header_overhead_pct']:.2f}%**"
            f"（+{est['sample_header_chars']:,} 字）",
            f"- 样本总入模字数净变化（B 组 vs A 组含去水章）：**{est['net_b_vs_a_pct']:+.2f}%**",
        ]
    )
    if est.get("corpus_baseline_tokens"):
        lines.append(
            f"- 全库重嵌耗时估算（基准 {est['corpus_baseline_tokens']:,} tok，"
            f"按 {est['throughput_tok_s']:,.0f} tok/s 含 25% 开销）："
            f"预计 **{est['corpus_reembed_tokens']:,} tok**，约 **{est['corpus_reembed_hms']}**"
        )
    else:
        lines.append(
            "- 全库重嵌耗时估算：源库未提供全库 `emb.db`/`index.db` 字符基准，可用 `--corpus-tokens` 指定估算。"
        )

    bm25 = report["bm25_ab"]
    pol_a = bm25["pollution"]["A_raw"]
    pol_b = bm25["pollution"]["B_norm"]
    lines.extend(
        [
            "",
            "## 2. 离线 BM25 / FTS 对比（原始未过滤 vs L0 规范化 + 角色过滤）",
            "",
            (
                f"- 索引与盘面污染清零：`front_back` 索引段落 `{pol_a['front_back_paras']} -> "
                f"{pol_b['front_back_paras']}`，"
                f"`notes` 索引段落 `{pol_a['notes_paras']} -> {pol_b['notes_paras']}`，"
                f"盘面 HTML 表文件 `{pol_a['html_residue_files']} -> {pol_b['html_residue_files']}`，"
                f"盘面 Pandoc 属性文件 `{pol_a['attr_residue_files']} -> {pol_b['attr_residue_files']}`，"
                f"盘面脚注图标残渣文件 `{pol_a['footnote_residue_files']} -> {pol_b['footnote_residue_files']}`"
            ),
            "",
            "| 路径 | MRR | "
            + " | ".join(f"Recall@{k}" for k in ks)
            + " | "
            + " | ".join(f"NDCG@{k}" for k in ks)
            + " | Top10 水章/注释污染率 |",
            "|---|---:|" + "---:|" * (len(ks) * 2 + 1),
        ]
    )
    for key, label in (
        ("A_qsearch", "A 组 `qsearch`（原始未过滤）"),
        ("B_qsearch", "B 组 `qsearch`（L0+角色过滤）"),
        ("A_book_query", "A 组 `book_query` BM25（原始未过滤）"),
        ("B_book_query", "B 组 `book_query` BM25（L0+角色过滤）"),
    ):
        m = bm25["metrics"][key]
        r_cells = " | ".join(f"{m['recall'][k]:.3f}" for k in ks)
        n_cells = " | ".join(f"{m['ndcg'][k]:.3f}" for k in ks)
        lines.append(f"| {label} | {m['mrr']:.3f} | {r_cells} | {n_cells} | {m['pollution_at_10']:.3f} |")

    emb = report["embed_ab"]
    lines.extend(["", "## 3. Embedding 向量与 Hybrid RRF A/B 对比", ""])
    if emb["status"] != "completed":
        lines.extend(
            [
                f"- 状态：`embed_ab: {emb['status']}`（服务端 `{emb['server']}` 不可达，已优雅跳过在线推理）",
                "- 一行补跑命令：",
                "",
                "```bash",
                emb["rerun_command"],
                "```",
            ]
        )
    else:
        lines.extend(
            [
                f"- 状态：`completed`（服务端 `{emb['server']}`）",
                "",
                "| 实验组 / 模式 | MRR | "
                + " | ".join(f"Recall@{k}" for k in ks)
                + " | "
                + " | ".join(f"NDCG@{k}" for k in ks)
                + " | Top10 污染率 |",
                "|---|---:|" + "---:|" * (len(ks) * 2 + 1),
            ]
        )
        for arm_key, arm_label in (
            ("A_baseline", "A（无前缀+含水章）"),
            ("B_treatment", "B（前缀+滤水章）"),
            ("C_skip_notes", "C（前缀+滤水章+滤注释）"),
        ):
            arm_data = emb["arms"].get(arm_key)
            if not arm_data:
                continue
            for mode_key, mode_label in (("vector", "纯向量"), ("hybrid", "Hybrid RRF")):
                m = arm_data[mode_key]
                r_cells = " | ".join(f"{m['recall'][k]:.3f}" for k in ks)
                n_cells = " | ".join(f"{m['ndcg'][k]:.3f}" for k in ks)
                pol_10 = m["pollution_at_10"]
                lines.append(f"| {arm_label} · {mode_label} | {m['mrr']:.3f} | {r_cells} | {n_cells} | {pol_10:.3f} |")

    th = report["decision"]["thresholds"]
    lines.extend(
        [
            "",
            "## 4. 全库重嵌量化门槛",
            "",
            f"- 质量增益：`MRR >= +{th['min_mrr_gain']:.2f}` 或 `Recall@10 >= +{th['min_recall10_gain']:.2f}`"
            f"（且任一核心指标退化不低于 `{th['max_regression']:.2f}`）",
            f"- 算力/Token 预算：`--context-header` 输入 Token 增幅 `<= +{th['max_token_overhead_pct']:.1f}%`",
            "- 洁净度门控：Treatment 组 `front_back` 块数 `== 0` 且 HTML/属性/脚注残渣块 `== 0`",
            "",
        ]
    )
    return "\n".join(lines)


def run_ab(
    *,
    source_root: Path,
    work_dir: Path,
    uids: list[str] | None = None,
    sample_size: int = 8,
    gold_path: Path | None = None,
    ks: list[int] | None = None,
    chunk_chars: int = 700,
    overlap: int = 0,
    server_url: str | None = None,
    dim: int = embed_corpus.DEFAULT_DIM,
    max_overhead_pct: float = DEFAULT_OVERHEAD_GATE_PCT,
    corpus_tokens: int | None = None,
    throughput_tok_s: float = DEFAULT_THROUGHPUT_TOK_S,
    with_skip_notes: bool = True,
    work_dir_arg: str | None = None,
    gold_arg: str | None = None,
) -> dict[str, Any]:
    """在隔离工作区执行完整的 Phase 4 A/B 评测，绝不修改 source_root。"""
    source_root = source_root.resolve()
    work_dir = work_dir.resolve()
    _validate_isolation(source_root, work_dir)
    ks = sorted(set(ks or [5, 10, 20]))
    srv_url = (server_url or os.environ.get("BOOKS_EMBED_URL") or embed_corpus.DEFAULT_URL).rstrip("/")

    resolved_gold = gold_path
    if resolved_gold is None:
        resolved_gold = _auto_gold_path(source_root)
    gold_rows = load_gold_queries(resolved_gold, source_root) if resolved_gold else []

    books = select_sample_books(source_root, uids=uids, sample_size=sample_size, gold_rows=gold_rows)
    book_uid_set = {u for u, _, _ in books}

    ws_raw, ws_norm, norm_summary = prepare_isolated_workspaces(source_root, work_dir, books)

    matched_gold = [r for r in gold_rows if r.get("uid") in book_uid_set]
    if matched_gold:
        queries = matched_gold
        query_source = f"gold:{resolved_gold}"
    else:
        queries = _derive_sample_queries(ws_norm, books)
        query_source = "auto_chapter"

    targets_raw = [_resolve_targets_for_ws(ws_raw, q) for q in queries]
    targets_norm = [_resolve_targets_for_ws(ws_norm, q) for q in queries]
    roles_norm = _chapter_role_lookup(ws_norm)
    roles_raw = _chapter_role_lookup(ws_raw, fallback_roles=roles_norm)

    # 1. 离线切块与 Token 开销分析
    chunk_a = _chunk_arm_stats(
        ws_raw,
        books,
        chunk_chars=chunk_chars,
        overlap=overlap,
        context_header=False,
        include_front_back=True,
        skip_notes=False,
        roles_by_path=roles_raw,
    )
    chunk_a_filtered = _chunk_arm_stats(
        ws_norm,
        books,
        chunk_chars=chunk_chars,
        overlap=overlap,
        context_header=False,
        include_front_back=False,
        skip_notes=False,
        roles_by_path=roles_norm,
    )
    chunk_b = _chunk_arm_stats(
        ws_norm,
        books,
        chunk_chars=chunk_chars,
        overlap=overlap,
        context_header=True,
        include_front_back=False,
        skip_notes=False,
        roles_by_path=roles_norm,
    )
    chunk_analysis: dict[str, Any] = {
        "A_baseline": chunk_a,
        "A_filtered": chunk_a_filtered,
        "B_treatment": chunk_b,
    }
    if with_skip_notes:
        chunk_analysis["C_skip_notes"] = _chunk_arm_stats(
            ws_norm,
            books,
            chunk_chars=chunk_chars,
            overlap=overlap,
            context_header=True,
            include_front_back=False,
            skip_notes=True,
            roles_by_path=roles_norm,
        )

    net_b_vs_a_pct = (
        round((chunk_b["input_chars"] - chunk_a["input_chars"]) / chunk_a["input_chars"] * 100.0, 2)
        if chunk_a["input_chars"]
        else 0.0
    )
    base_corpus_tok, tok_source = _estimate_corpus_tokens(source_root, corpus_tokens)
    reembed_tok = round(base_corpus_tok * (1.0 + chunk_b["header_overhead_pct"] / 100.0)) if base_corpus_tok else None
    reembed_secs = (reembed_tok / throughput_tok_s * 1.25) if (reembed_tok and throughput_tok_s > 0) else None
    reembed_estimate = {
        "header_overhead_pct": chunk_b["header_overhead_pct"],
        "sample_header_chars": chunk_b["header_overhead_chars"],
        "net_b_vs_a_pct": net_b_vs_a_pct,
        "corpus_baseline_tokens": base_corpus_tok,
        "corpus_tokens_source": tok_source,
        "corpus_reembed_tokens": reembed_tok,
        "throughput_tok_s": throughput_tok_s,
        "corpus_reembed_seconds": round(reembed_secs, 1) if reembed_secs is not None else None,
        "corpus_reembed_hms": embed_corpus.hms(reembed_secs) if reembed_secs is not None else None,
    }

    # 2. 离线 BM25 索引构建与对比
    idx_raw = ws_raw / "build" / "检索" / "index.db"
    idx_norm = ws_norm / "build" / "检索" / "index.db"
    with contextlib.redirect_stdout(sys.stderr):
        con_raw = build_para_index.build(str(idx_raw), root=str(ws_raw), include_notes=True, include_front_back=True)
        con_raw.close()
        con_norm = build_para_index.build(
            str(idx_norm), root=str(ws_norm), include_notes=False, include_front_back=False
        )
        con_norm.close()

    pollution_raw = inspect_index_pollution(idx_raw, ws_raw, roles_by_path=roles_raw)
    pollution_norm = inspect_index_pollution(idx_norm, ws_norm, roles_by_path=roles_norm)

    bm25_metrics = {
        "A_qsearch": _evaluate_rankings(queries, targets_raw, lambda q: _qsearch_candidates(idx_raw, q), ks, roles_raw),
        "B_qsearch": _evaluate_rankings(
            queries, targets_norm, lambda q: _qsearch_candidates(idx_norm, q), ks, roles_norm
        ),
        "A_book_query": _evaluate_rankings(
            queries,
            targets_raw,
            lambda q: eval_retrieval.book_query_files(
                [t for t in q["terms"].split(",") if t],
                q["question"],
                index_db=str(idx_raw),
                lib_db=str(ws_raw / "build" / "library.db"),
                root=str(ws_raw),
                hybrid=False,
            ),
            ks,
            roles_raw,
        ),
        "B_book_query": _evaluate_rankings(
            queries,
            targets_norm,
            lambda q: eval_retrieval.book_query_files(
                [t for t in q["terms"].split(",") if t],
                q["question"],
                index_db=str(idx_norm),
                lib_db=str(ws_norm / "build" / "library.db"),
                root=str(ws_norm),
                hybrid=False,
            ),
            ks,
            roles_norm,
        ),
    }

    # 3. 向量 & Hybrid RRF 对比（服务不可达时优雅降级）
    rerun_cmd = _build_rerun_command(source_root, books, work_dir_arg, gold_arg, srv_url)
    vec_search.clear_cache()
    if not embed_corpus.healthy(srv_url):
        embed_ab: dict[str, Any] = {
            "status": "skipped_unreachable",
            "server": srv_url,
            "reason": "embedding HTTP 服务不可达，跳过向量建库与在线评测",
            "rerun_command": rerun_cmd,
            "arms": {},
        }
    else:
        arms_cfg = [
            ("A_baseline", ws_raw, idx_raw, targets_raw, roles_raw, False, True, False),
            ("B_treatment", ws_norm, idx_norm, targets_norm, roles_norm, True, False, False),
        ]
        if with_skip_notes:
            arms_cfg.append(("C_skip_notes", ws_norm, idx_norm, targets_norm, roles_norm, True, False, True))
        arms_out: dict[str, Any] = {}
        for arm_key, ws_p, idx_p, tgts, r_map, use_ctx, inc_fb, sk_notes in arms_cfg:
            emb_dir = work_dir / f"emb_{arm_key}"
            store_stats = _embed_arm(
                ws_p,
                emb_dir,
                books,
                server_url=srv_url,
                dim=dim,
                chunk_chars=chunk_chars,
                overlap=overlap,
                context_header=use_ctx,
                include_front_back=inc_fb,
                skip_notes=sk_notes,
            )
            vec_search.clear_cache()
            vec_metrics = _evaluate_rankings(
                queries,
                tgts,
                lambda q, ed=emb_dir: eval_retrieval.vec_files(q["question"], emb_dir=str(ed), url=srv_url),
                ks,
                r_map,
            )
            hyb_metrics = _evaluate_rankings(
                queries,
                tgts,
                lambda q, ed=emb_dir, ip=idx_p, wp=ws_p: eval_retrieval.book_query_files(
                    [t for t in q["terms"].split(",") if t],
                    q["question"],
                    index_db=str(ip),
                    lib_db=str(wp / "build" / "library.db"),
                    root=str(wp),
                    emb_dir=str(ed),
                    url=srv_url,
                    hybrid=True,
                ),
                ks,
                r_map,
            )
            arms_out[arm_key] = {
                "emb_dir": str(emb_dir),
                "store_stats": store_stats,
                "vector": vec_metrics,
                "hybrid": hyb_metrics,
            }
        embed_ab = {
            "status": "completed",
            "server": srv_url,
            "rerun_command": rerun_cmd,
            "arms": arms_out,
        }

    decision = _decision_gate(chunk_b=chunk_b, embed_ab=embed_ab, max_overhead_pct=max_overhead_pct)

    return {
        "source_root": str(source_root),
        "work_dir": str(work_dir),
        "ks": ks,
        "sample_books": [{"uid": u, "path": p, "title": t} for u, p, t in books],
        "query_count": len(queries),
        "query_source": query_source,
        "l0_normalization": norm_summary,
        "chunk_analysis": chunk_analysis,
        "reembed_estimate": reembed_estimate,
        "bm25_ab": {
            "pollution": {"A_raw": pollution_raw, "B_norm": pollution_norm},
            "metrics": bm25_metrics,
        },
        "embed_ab": embed_ab,
        "decision": decision,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", help="只读源工作区根（默认取 BOOKS_ROOT 或当前目录上层 books.toml）")
    ap.add_argument("--work-dir", help="隔离评测输出与临时库目录（默认创建临时目录）")
    ap.add_argument("--uids", help="逗号分隔的样本书 uid（如 B0169,B0301）")
    ap.add_argument("--sample-size", type=int, default=8, help="未传 --uids 时的抽样书数（默认 8）")
    ap.add_argument("--gold", help="评测 gold TSV 路径（可选，未传时自动探测或从正文抽样）")
    ap.add_argument("--k", default="5,10,20", help="逗号分隔的指标 K 值（默认 5,10,20）")
    ap.add_argument("--chunk-chars", type=int, default=700, help="embedding 切块目标字符数")
    ap.add_argument("--overlap", type=int, default=0, help="embedding 块间重叠段数")
    ap.add_argument("--server", help="embedding HTTP 服务地址（默认 BOOKS_EMBED_URL）")
    ap.add_argument("--dim", type=int, default=embed_corpus.DEFAULT_DIM, help="向量维度")
    ap.add_argument("--max-token-overhead-pct", type=float, default=DEFAULT_OVERHEAD_GATE_PCT)
    ap.add_argument("--corpus-tokens", type=int, default=None, help="显式指定全库基线 token 数用于重嵌耗时估算")
    ap.add_argument("--throughput-tok-s", type=float, default=DEFAULT_THROUGHPUT_TOK_S)
    ap.add_argument("--no-skip-notes", action="store_true", help="跳过 C 组（--skip-notes）对照")
    ap.add_argument("--out-json", help="保存 JSON 报告路径")
    ap.add_argument("--out-md", help="保存 Markdown 决策报告路径")
    ap.add_argument("--json", action="store_true", help="标准输出打印 JSON 报告")
    args = ap.parse_args(argv)

    resolved_root = args.root or os.environ.get("BOOKS_ROOT") or find_root()
    if not resolved_root:
        ap.error("请通过 --root 或 BOOKS_ROOT 指定只读源工作区")
    source_root = Path(resolved_root).expanduser().resolve()
    Layout(str(source_root))

    try:
        ks = eval_retrieval.parse_ks(args.k)
    except ValueError as exc:
        ap.error(str(exc))

    uids = [u.strip() for u in (args.uids or "").split(",") if u.strip()] or None
    gold_path = Path(args.gold).expanduser().resolve() if args.gold else None

    tmp_ctx: tempfile.TemporaryDirectory[str] | None = None
    if args.work_dir:
        work_dir = Path(args.work_dir).expanduser().resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="eval-embed-ab-")
        work_dir = Path(tmp_ctx.name)

    try:
        report = run_ab(
            source_root=source_root,
            work_dir=work_dir,
            uids=uids,
            sample_size=args.sample_size,
            gold_path=gold_path,
            ks=ks,
            chunk_chars=args.chunk_chars,
            overlap=args.overlap,
            server_url=args.server,
            dim=args.dim,
            max_overhead_pct=args.max_token_overhead_pct,
            corpus_tokens=args.corpus_tokens,
            throughput_tok_s=args.throughput_tok_s,
            with_skip_notes=not args.no_skip_notes,
            work_dir_arg=args.work_dir,
            gold_arg=args.gold,
        )
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()

    md_text = render_markdown_report(report)
    json_text = json.dumps(report, ensure_ascii=False, indent=2)

    out_json = (
        Path(args.out_json).expanduser().resolve()
        if args.out_json
        else (work_dir / "eval-ab-report.json" if args.work_dir else None)
    )
    out_md = (
        Path(args.out_md).expanduser().resolve()
        if args.out_md
        else (work_dir / "eval-ab-report.md" if args.work_dir else None)
    )
    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json_text + "\n", encoding="utf-8")
    if out_md is not None:
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(md_text + "\n", encoding="utf-8")

    if args.json:
        print(json_text)
    else:
        print(f"embed_ab: {report['embed_ab']['status']}")
        print(md_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
