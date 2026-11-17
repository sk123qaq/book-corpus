"""源码路径 / 行号 / 引文探针到分层读规范章节与原文块的解析器。"""

from __future__ import annotations

import posixpath
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from .store import UID_RE

_PATH_UID_RE = re.compile(r"(?:^|/)books/([A-Za-z0-9_-]+)/md/[^/]+$")
_NON_WORD_RE = re.compile(r"[\W_]+")


@dataclass(frozen=True)
class OpenTarget:
    """``GET /open`` 的解析结果。"""

    kind: Literal["chapter", "book", "not_found"]
    uid: str
    n: int | None = None
    block_ids: tuple[str, ...] = ()
    highlight: str = ""
    notice: str = ""


def extract_uid_from_path(rel_path: str) -> str:
    """从 ``books/<uid>/md/<file>.md`` 推导合法 ``uid``，不合法返回空串。"""
    clean = (rel_path or "").replace("\\", "/").strip()
    match = _PATH_UID_RE.search(clean)
    if match and UID_RE.match(match.group(1)):
        return match.group(1)
    parts = [part for part in clean.split("/") if part]
    if len(parts) >= 3 and parts[1] == "md" and UID_RE.match(parts[0]):
        return parts[0]
    return ""


def resolve_chapter_by_path(chapters: list[dict[str, Any]], rel_path: str) -> dict[str, Any] | None:
    """按文件名将原始 ``.md`` 路径解析到规范章节（兼容前置页合并）。"""
    name = posixpath.basename((rel_path or "").replace("\\", "/").strip())
    if not name:
        return None
    for ch in chapters:
        sources = ch.get("source_files") or [ch.get("file", "")]
        if name in sources:
            return ch
    return None


def normalize_probe(text: str) -> str:
    """归一化引文探针：NFKC + 去空白与标点（若全为标点则退回折叠空白）。"""
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    compact = _NON_WORD_RE.sub("", normalized)
    if compact:
        return compact
    return "".join(normalized.split())


def block_searchable_text(block: dict[str, Any]) -> str:
    """提取块内可匹配原文，兼容段落、列表与表格。"""
    parts: list[str] = []
    text = str(block.get("text") or "").strip()
    if text:
        parts.append(text)
    items = block.get("items")
    if isinstance(items, list):
        parts.extend(str(item) for item in items if item)
    head = block.get("head")
    if isinstance(head, list):
        parts.extend(str(cell) for cell in head if cell)
    rows = block.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, list):
                parts.extend(str(cell) for cell in row if cell)
    return " ".join(parts)


def _nearest_line_index(file_blocks: list[dict[str, Any]], line: int) -> int:
    first_start = int(file_blocks[0].get("start_line") or 0)
    if first_start and line <= first_start:
        return 0
    for idx, block in enumerate(file_blocks):
        start = int(block.get("start_line") or 0)
        end = int(block.get("end_line") or start)
        if start and start <= line <= end:
            return idx
    best_idx = 0
    best_dist: int | None = None
    for idx, block in enumerate(file_blocks):
        start = int(block.get("start_line") or 0)
        end = int(block.get("end_line") or start)
        if not start:
            continue
        dist = 0 if start <= line <= end else min(abs(line - start), abs(line - end))
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_idx = idx
    return best_idx


def _probe_variants(quote: str) -> list[str]:
    norm = normalize_probe(quote)
    if not norm:
        return []
    variants = [norm]
    if len(norm) > 28:
        variants.append(norm[:28])
    if len(norm) > 16:
        variants.append(norm[:16])
    return variants


def _match_blocks_by_quote(
    file_blocks: list[dict[str, Any]],
    anchor_idx: int,
    quote: str,
) -> tuple[str, ...]:
    probes = _probe_variants(quote)
    if not probes:
        return ()

    norm_texts = [normalize_probe(block_searchable_text(b)) for b in file_blocks]
    indices = list(range(len(file_blocks)))
    indices.sort(key=lambda idx: abs(idx - anchor_idx))

    for probe in probes:
        for idx in indices:
            if probe and probe in norm_texts[idx]:
                return (str(file_blocks[idx]["id"]),)
        for idx in indices:
            if idx + 1 >= len(file_blocks):
                continue
            combined = norm_texts[idx] + norm_texts[idx + 1]
            if probe and probe in combined and probe not in norm_texts[idx] and probe not in norm_texts[idx + 1]:
                return (str(file_blocks[idx]["id"]), str(file_blocks[idx + 1]["id"]))
    return ()


def locate_blocks(
    blocks: list[dict[str, Any]],
    source_file: str,
    line: int | None = None,
    quote: str = "",
) -> tuple[str, ...]:
    """按 ``(source_file, line)`` 主定位与引文校验，返回命中的 ``block_id`` 元组。"""
    if not blocks:
        return ()
    file_blocks = [b for b in blocks if b.get("source_file") == source_file] if source_file else []
    if not file_blocks:
        file_blocks = list(blocks)

    anchor_idx = 0
    has_line = line is not None and line > 0
    if has_line and line is not None:
        anchor_idx = _nearest_line_index(file_blocks, line)

    if quote:
        matched = _match_blocks_by_quote(file_blocks, anchor_idx, quote)
        if matched:
            return matched

    return (str(file_blocks[anchor_idx]["id"]),)


def resolve_open_target(
    library: Any,
    *,
    uid: str = "",
    path: str = "",
    line: int | None = None,
    quote: str = "",
) -> OpenTarget:
    """将外部来源的 ``(uid, path, line, quote)`` 解析为分层读规范跳转目标。"""
    clean_uid = (uid or "").strip() or extract_uid_from_path(path)
    if not clean_uid or not UID_RE.match(clean_uid):
        return OpenTarget(kind="not_found", uid="", notice="缺少或非法的书目编号。")

    book = library.book(clean_uid)
    if not book:
        return OpenTarget(
            kind="not_found",
            uid=clean_uid,
            notice=f"书库中未找到 {clean_uid} 的可读正文（可能尚未拆章或已移出流程）。",
        )

    chapters = library.chapters(clean_uid)
    if not chapters:
        return OpenTarget(kind="book", uid=clean_uid, notice="该书暂无可展示的章节。")

    clean_path = (path or "").replace("\\", "/").strip()
    if clean_path:
        chapter = resolve_chapter_by_path(chapters, clean_path)
        if chapter is None:
            name = posixpath.basename(clean_path)
            return OpenTarget(
                kind="book",
                uid=clean_uid,
                notice=f"「{name}」不是正文章节或章节已重排，已为你定位到本书目录。",
            )
    else:
        chapter = chapters[0]

    n = int(chapter["n"])
    chapter_data = library.read_chapter(clean_uid, n)
    if not chapter_data:
        return OpenTarget(kind="book", uid=clean_uid, notice="未能读取该章正文，已退回本书目录。")

    source_file = posixpath.basename(clean_path) if clean_path else str(chapter.get("file") or "")
    block_ids = locate_blocks(chapter_data.get("blocks") or [], source_file, line=line, quote=quote)
    clean_hl = " ".join((quote or "").split())[:120]
    return OpenTarget(
        kind="chapter",
        uid=clean_uid,
        n=n,
        block_ids=block_ids,
        highlight=clean_hl,
    )
