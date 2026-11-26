"""幂等 L0 Markdown 规范化器：HTML/表格清洗、页码去噪与断句缝合、脚注归一、三级拆章与无损角色标注。

只清洗转档噪声与合理拆章，绝不改写或删减正文句子；对已规范化书目二次运行必须 0 变更。
"""

from __future__ import annotations

import datetime
import hashlib
import html
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .footnotes import (
    NOTE_CHAPTER_TITLE_RE,
    NOTE_HEADING_RE,
    _match_tier2_prefix,
    normalize_marker,
    parse_explicit_footnote_def,
)
from .parse import (
    CODE_FENCE_RE,
    FOOTNOTE_RE,
    FRONT_MATTER_LIMIT,
    HEADING_RE,
    HR_RE,
    IMAGE_RE,
    LIST_RE,
    QUOTE_RE,
    TABLE_ROW_RE,
    _image_kind,
    clean_inline,
    non_space_count,
    parse_markdown,
)
from .roles import CHAPTER_ROLES, ChapterRole, load_manifest_chapter_roles, normalize_chapter_role

CANONICAL_VERSION = 1
DEFAULT_SPLIT_CHARS = 24_000
DEFAULT_SPLIT_BLOCKS = 800
TARGET_CHAPTER_MIN_CHARS = 8_000
TARGET_CHAPTER_MAX_CHARS = 18_000
TARGET_CHAPTER_MAX_BLOCKS = 650

_IMG_REF_RE = re.compile(r"!\[[^\]]*\]\((?:<([^>]+)>|([^)]+))\)")
_LINK_REF_RE = re.compile(r"(?<![!\\])\[[^\]]*\]\((?:<([^>]+)>|([^)]+))\)")
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".jpx", ".gif", ".svg", ".webp", ".bmp")

_HTML_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table\s*>", re.I | re.S)
_HTML_IMG_RE = re.compile(r"<img\b([^>]*)/?>", re.I)
_HTML_IMG_SRC_RE = re.compile(r"""\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
_HTML_IMG_ALT_RE = re.compile(r"""\balt\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
_HTML_LINK_RE = re.compile(r"""<a\b([^>]*)>(.*?)</a>""", re.I | re.S)
_HTML_HREF_RE = re.compile(r"""\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
_HTML_BR_RE = re.compile(r"<br\s*/?>", re.I)
_HTML_HR_RE = re.compile(r"<hr\b[^>]*/?>", re.I)
_UNCLOSED_CELL_END_RE = re.compile(r"</(?:td|th)\s*>", re.I)
_UNCLOSED_TR_END_RE = re.compile(r"</tr\s*>", re.I)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_HTML_TAG_RE = re.compile(
    r"</?(?:sup|sub|span|div|a|br|hr|i|b|u|em|strong|small|big|font|p|td|tr|th|tbody|thead|tfoot|"
    r"colgroup|col|caption|table|img|figure|figcaption|blockquote|center|section|article|header|footer|"
    r"ul|ol|li|dl|dt|dd)\b[^>]*>",
    re.I,
)
_PANDOC_ATTR_RE = re.compile(
    r"\s*\{(?:[^{}\n]*\.calibre[^{}\n]*|#[A-Za-z_][A-Za-z0-9_.:-]*(?:\s+[^{}\n]*)?|\.[A-Za-z_][A-Za-z0-9_-]*(?:\s+[^{}\n]*)?)\}"
)
_EMPTY_PANDOC_SPAN_RE = re.compile(r"\[\]\s*\{[^{}\n]*\}")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ZERO_WIDTH_RE = re.compile(r"[\ufeff\u200b-\u200d]")
_PUA_RE = re.compile(r"[\ue000-\uf8ff]")

# 页码与页眉模式
_EXPLICIT_PAGE_RE = re.compile(
    r"^(?:"
    r"[-–—]{1,3}\s*\d{1,4}\s*[-–—]{0,3}"
    r"|\d{1,4}\s*[-–—]{1,3}"
    r"|第\s*\d{1,4}\s*页"
    r"|(?:page|p\.?)\s*\d{1,4}"
    r")$",
    re.I,
)
_BARE_PAGE_NUM_RE = re.compile(r"^\d{1,4}$")
_CJK_BOUNDARY_RE = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")
_SENT_CLOSERS = "\u201d\u2019\"'\u300d\u300f\uff09\u3011\u300b\u3009)]】"
_SENT_ENDERS = "。！？…!?.;:：；"

# 脚注语法识别
_INLINE_ICON_LINK_RE = re.compile(r"(\^?)\[!\[([^\]]*)\]\(([^)\s]+(?:\s+\"[^\"]*\")?)\)\]\(([^)]*)\)(\^?)")
_INLINE_ICON_IMG_RE = re.compile(r"(\^?)!\[([^\]]*)\]\(([^)\s]+(?:\s+\"[^\"]*\")?)\)(\^?)")
_INLINE_DBL_FN_RE = re.compile(r"(\^?)\[\[(?:\*\*|\*)?([^\[\]\n]{1,24}?)(?:\*\*|\*)?\.?\]\]\(([^)\n]*)\)(\^?)")
_STD_FN_LABEL_RE = re.compile(r"^[0-9A-Za-z_-]{1,20}$")
_DIGITS_RE = re.compile(r"(\d{1,4})")

# 章节命名与角色判定
_CHAPTER_FILE_RE = re.compile(r"^(\d+)_(.*)\.md$")
_BAD_FILENAME_RE = re.compile(r'[\\/:*?"<>|\r\n\t]')
_TIER1_HEADING_RE = re.compile(
    r"^(?:"
    r"第\s*[〇零一二三四五六七八九十百千两0-9]+\s*[章篇部卷回集辑幕]"
    r"|(?:chapter|part|book|section|act)\s+(?:\d+|[ivxlcdm]+)\b"
    r"|(?:序言?|自序|代序|前言|引言|导言|导论|绪论|序论|出版说明|内容简介|编者按|译者序|"
    r"致谢|后记|跋|尾声|结语|附录(?:\s*[A-Za-z0-9一二三四五六七八九十]*)?|参考文献|注释|索引|目录|总目录|版权页?)\b"
    r")",
    re.I,
)
_FRONT_BACK_TITLE_RE = re.compile(
    r"^(?:"
    r"(?:前|后)?折页|勒口|书衣|腰封|奥付|版权(?:页|声明|所有|信息)?|图书在版编目|CIP(?:数据)?|"
    r"封面|封底|封二|封三|书脊|书名页|扉页|内封|题名页|"
    r"总目录|简目|详细目录|插图目录|图表目录|目\s*录|"
    r"剧中人物(?:表)?|人物表|序幕人物|登场人物|"
    r"前置页(?:\s*\d+)?|"
    r"cover|back\s*cover|title\s*page|half\s*title|copyright|colophon|imprint|"
    r"table\s*of\s*contents|contents|cast\s*of\s*characters|dramatis\s*personae"
    r")$",
    re.I,
)
_BODY_PROTECTED_TITLE_RE = re.compile(
    r"(?:^第\s*[〇零一二三四五六七八九十百千两0-9]+\s*[章篇部卷回幕场节]|"
    r"^(?:序幕|楔子|尾声|引子|绪论|导论|引言|前言|后记|结语|chapter\s+\d+|part\s+\d+|act\s+\d+))",
    re.I,
)


@dataclass
class NormalizeStats:
    """单本书 L0 规范化统计。"""

    chapters_before: int = 0
    chapters_after: int = 0
    html_bytes_cleaned: int = 0
    html_tables_converted: int = 0
    page_lines_removed: int = 0
    header_lines_removed: int = 0
    sentences_stitched: int = 0
    footnotes_converted: int = 0
    footnote_residues_cleaned: int = 0
    orphan_icons_removed: int = 0
    roles: dict[str, int] = field(default_factory=lambda: {"body": 0, "notes": 0, "front_back": 0})

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapters_before": self.chapters_before,
            "chapters_after": self.chapters_after,
            "html_bytes_cleaned": self.html_bytes_cleaned,
            "html_tables_converted": self.html_tables_converted,
            "page_lines_removed": self.page_lines_removed,
            "header_lines_removed": self.header_lines_removed,
            "sentences_stitched": self.sentences_stitched,
            "footnotes_converted": self.footnotes_converted,
            "footnote_residues_cleaned": self.footnote_residues_cleaned,
            "orphan_icons_removed": self.orphan_icons_removed,
            "roles": dict(self.roles),
        }


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def _link_dest(name: str) -> str:
    return f"<{name}>" if any(c in name for c in "() ") else name


def _sanitize_filename(title: str, fallback: str, *, max_chars: int = 80, max_bytes: int = 200) -> str:
    cleaned = _BAD_FILENAME_RE.sub("", (title or "").strip()).strip(" .")
    cleaned = re.sub(r"\s+", "_", cleaned)[:max_chars].strip("_.")
    raw = cleaned.encode("utf-8")
    if len(raw) > max_bytes:
        cleaned = raw[:max_bytes].decode("utf-8", "ignore").strip("_.")
    return cleaned or fallback


def _split_front_matter_raw(raw: str) -> tuple[list[tuple[str, str]], dict[str, str], str]:
    """保留有序 YAML front matter 键值对，返回 ``(items, mapping, body)``。"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n") if "\r" in raw else raw
    had_bom = text.startswith("\ufeff")
    probe = text[1:] if had_bom else text
    lines = probe.split("\n")
    if len(lines) < 2 or lines[0].strip() != "---":
        return [], {}, text
    items: list[tuple[str, str]] = []
    meta: dict[str, str] = {}
    for idx in range(1, min(len(lines), FRONT_MATTER_LIMIT + 1)):
        if lines[idx].strip() == "---":
            for raw_line in lines[1:idx]:
                if ":" not in raw_line:
                    continue
                k, v = raw_line.split(":", 1)
                k = k.strip()
                v_clean = v.strip()
                if len(v_clean) >= 2 and v_clean[0] == v_clean[-1] and v_clean[0] in "\"'":
                    v_clean = v_clean[1:-1]
                if k:
                    items.append((k, v_clean))
                    meta[k] = v_clean
            body = "\n".join(lines[idx + 1 :])
            return items, meta, body
    return [], {}, text


def _format_front_matter(items: Sequence[tuple[str, str]]) -> str:
    if not items:
        return ""
    lines = ["---"]
    for key, value in items:
        if (key in ("chapter", "page", "pages") and re.fullmatch(r"\d+", str(value))) or key == "chapter_role":
            lines.append(f"{key}: {value}")
        else:
            escaped = str(value).replace('"', "'").replace("\n", " ").strip()
            lines.append(f'{key}: "{escaped}"')
    lines.append("---\n")
    return "\n".join(lines) + "\n"


def _split_code_segments(text: str) -> list[tuple[bool, str]]:
    """把文本切成 ``(is_code, text)`` 段，保护围栏代码块内部不被改写。"""
    lines = text.split("\n")
    segs: list[tuple[bool, str]] = []
    buf: list[str] = []
    in_code = False
    for line in lines:
        if not in_code:
            if CODE_FENCE_RE.match(line.strip()):
                if buf:
                    segs.append((False, "\n".join(buf)))
                    buf = []
                in_code = True
                buf.append(line)
            else:
                buf.append(line)
        else:
            buf.append(line)
            if line.strip().startswith("```"):
                segs.append((True, "\n".join(buf)))
                buf = []
                in_code = False
    if buf:
        segs.append((in_code, "\n".join(buf)))
    return segs


# --------------------------------------------------------------------------- HTML & 表格清洗


class _PipeTableHTMLParser(HTMLParser):
    """解析单个 HTML <table>，展开 rowspan/colspan 为规范二维网格。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[bool, list[str]]] = []
        self._depth = 0
        self._row_cells: list[tuple[int, int, str]] | None = None
        self._row_is_head = False
        self._in_thead = False
        self._cell_parts: list[str] | None = None
        self._cell_colspan = 1
        self._cell_rowspan = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._depth += 1
            return
        if self._depth != 1:
            if self._cell_parts is not None and tag == "img":
                self._append_img(attrs)
            elif self._cell_parts is not None and tag in ("br", "tr", "p", "li"):
                self._cell_parts.append(" ")
            return
        if tag == "thead":
            self._in_thead = True
        elif tag == "tr":
            self._row_cells = []
            self._row_is_head = self._in_thead
        elif tag in ("td", "th"):
            attr_map = {k.lower(): (v or "") for k, v in attrs}
            self._cell_parts = []
            self._cell_colspan = _parse_span(attr_map.get("colspan"))
            self._cell_rowspan = _parse_span(attr_map.get("rowspan"))
            if tag == "th":
                self._row_is_head = True
        elif self._cell_parts is not None:
            if tag == "img":
                self._append_img(attrs)
            elif tag in ("br", "p", "div", "li"):
                self._cell_parts.append(" ")

    def _append_img(self, attrs: list[tuple[str, str | None]]) -> None:
        if self._cell_parts is None:
            return
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        src = attr_map.get("src", "").strip()
        alt = attr_map.get("alt", "").strip()
        if src:
            self._cell_parts.append(f" ![{alt}]({src}) ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "table":
            self._depth = max(0, self._depth - 1)
            return
        if self._depth != 1:
            return
        if tag == "thead":
            self._in_thead = False
        elif tag in ("td", "th") and self._cell_parts is not None and self._row_cells is not None:
            raw = " ".join("".join(self._cell_parts).split())
            self._row_cells.append((self._cell_colspan, self._cell_rowspan, raw))
            self._cell_parts = None
        elif tag == "tr" and self._row_cells is not None:
            self._commit_row(self._row_is_head, self._row_cells)
            self._row_cells = None

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def _commit_row(self, is_head: bool, cells: list[tuple[int, int, str]]) -> None:
        if not cells:
            return
        row_idx = len(self.rows)
        # 跳过已被前面 rowspan 占位的格
        grid_row: list[str] = []
        if not hasattr(self, "_occupied"):
            self._occupied: set[tuple[int, int]] = set()
        col = 0
        for colspan, rowspan, text in cells:
            while (row_idx, col) in self._occupied:
                grid_row.append("")
                col += 1
            for r_off in range(rowspan):
                for c_off in range(colspan):
                    if r_off > 0:
                        self._occupied.add((row_idx + r_off, col + c_off))
            grid_row.append(text)
            for _ in range(1, colspan):
                grid_row.append("")
            col += colspan
        while (row_idx, col) in self._occupied:
            grid_row.append("")
            col += 1
        self.rows.append((is_head, grid_row))


def _parse_span(raw: str | None) -> int:
    if not raw:
        return 1
    try:
        val = int(raw.strip())
    except ValueError:
        return 1
    return max(1, min(val, 20))


def html_table_to_pipe(table_html: str) -> str:
    """把单个 HTML ``<table>...</table>`` 展平为标准 Markdown Pipe 表格。"""
    parser = _PipeTableHTMLParser()
    try:
        parser.feed(table_html)
        parser.close()
    except (ValueError, AssertionError):
        return ""
    if not parser.rows:
        return ""
    ncols = max(len(row) for _, row in parser.rows)
    if ncols == 0:
        return ""
    normalized_rows: list[tuple[bool, list[str]]] = []
    for is_head, row in parser.rows:
        padded = [_escape_pipe_cell(c) for c in row] + [""] * (ncols - len(row))
        if any(padded):
            normalized_rows.append((is_head, padded))
    if not normalized_rows:
        return ""

    if normalized_rows[0][0]:
        head = normalized_rows[0][1]
        data_rows = [row for _, row in normalized_rows[1:]]
    else:
        head = [""] * ncols
        data_rows = [row for _, row in normalized_rows]

    def fmt_row(cells: list[str]) -> str:
        return "| " + " | ".join(cells) + " |"

    lines = [fmt_row(head), "| " + " | ".join("---" for _ in range(ncols)) + " |"]
    lines.extend(fmt_row(row) for row in data_rows)
    return "\n\n" + "\n".join(lines) + "\n\n"


def _escape_pipe_cell(text: str) -> str:
    cleaned = _PANDOC_ATTR_RE.sub("", text or "")
    cleaned = _CONTROL_RE.sub("", cleaned).replace("\ufffd", "").replace("\u00a0", " ")
    cleaned = " ".join(cleaned.split())
    return cleaned.replace("|", "\\|")


def _replace_html_img(match: re.Match[str]) -> str:
    attrs = match.group(1)
    src_m = _HTML_IMG_SRC_RE.search(attrs)
    if not src_m:
        return ""
    src = (src_m.group(1) or src_m.group(2) or src_m.group(3) or "").strip()
    if not src:
        return ""
    alt_m = _HTML_IMG_ALT_RE.search(attrs)
    alt = (alt_m.group(1) or alt_m.group(2) or alt_m.group(3) or "").strip() if alt_m else ""
    dest = f"<{src}>" if any(c in src for c in "() ") else src
    return f"![{alt}]({dest})"


def _replace_html_link(match: re.Match[str]) -> str:
    attrs, inner = match.group(1), match.group(2)
    inner_clean = _HTML_TAG_RE.sub("", inner).strip()
    if not inner_clean:
        return ""
    href_m = _HTML_HREF_RE.search(attrs)
    href = (href_m.group(1) or href_m.group(2) or href_m.group(3) or "").strip() if href_m else ""
    if href.lower().startswith(("http://", "https://", "mailto:")) and "![" not in inner_clean:
        return f"[{inner_clean}]({href})"
    return inner_clean


def clean_html_and_noise(text: str, stats: NormalizeStats) -> str:
    """清洗 HTML 表格、无语义标签、HTML 实体、Pandoc 属性与控制字符/异常 PUA。"""
    if not text:
        return text
    out_segs: list[str] = []
    for is_code, seg in _split_code_segments(text):
        if is_code:
            out_segs.append(seg)
            continue
        before_len = len(seg)
        worked = seg

        if "<table" in worked.lower():

            def _sub_table(m: re.Match[str]) -> str:
                stats.html_tables_converted += 1
                return html_table_to_pipe(m.group(0))

            worked = _HTML_TABLE_RE.sub(_sub_table, worked)

        if "<" in worked:
            worked = _HTML_COMMENT_RE.sub("", worked)
            worked = _HTML_IMG_RE.sub(_replace_html_img, worked)
            worked = _HTML_LINK_RE.sub(_replace_html_link, worked)
            worked = _HTML_BR_RE.sub("\n", worked)
            worked = _HTML_HR_RE.sub("\n---\n", worked)
            worked = _UNCLOSED_CELL_END_RE.sub(" ", worked)
            worked = _UNCLOSED_TR_END_RE.sub("\n", worked)
            worked = _HTML_TAG_RE.sub("", worked)

        worked = _EMPTY_PANDOC_SPAN_RE.sub("", worked)
        worked = _PANDOC_ATTR_RE.sub("", worked)
        if "&" in worked:
            worked = html.unescape(worked)
        worked = worked.replace("\u00a0", " ")
        worked = _ZERO_WIDTH_RE.sub("", worked)
        worked = _CONTROL_RE.sub("", worked)
        worked = _PUA_RE.sub("", worked)
        # 折叠多余连续空行（超过 2 个换行压成 2 个）
        worked = re.sub(r"\n{3,}", "\n\n", worked)

        removed = max(0, before_len - len(worked))
        if worked != seg:
            stats.html_bytes_cleaned += removed or 1 if "<" in seg or "{" in seg or "&" in seg else removed
        out_segs.append(worked)
    return "\n".join(out_segs)


# --------------------------------------------------------------------------- 页码去噪与断句缝合


def _is_structural_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    return bool(
        HEADING_RE.match(s)
        or LIST_RE.match(s)
        or QUOTE_RE.match(s)
        or TABLE_ROW_RE.match(s)
        or HR_RE.match(s)
        or FOOTNOTE_RE.match(s)
        or IMAGE_RE.fullmatch(s)
        or s.startswith("```")
    )


def _header_key(line: str) -> str:
    s = " ".join(line.strip().split())
    # 只把独立在边缘的数字掩码为 #，不误伤「第1节」「1.2」等正文编号
    s = re.sub(r"^\d{1,4}\s+", "# ", s)
    return re.sub(r"\s+\d{1,4}$", " #", s)


def _is_page_number_line(line: str, *, allow_bare: bool) -> bool:
    s = line.strip()
    if not s:
        return False
    if _EXPLICIT_PAGE_RE.match(s):
        return True
    if allow_bare and _BARE_PAGE_NUM_RE.fullmatch(s):
        num = int(s)
        if 1 <= num <= 2500:
            return True
    return False


def _detect_repeated_headers(chunks: list[tuple[bool, str]], book_title: str = "") -> set[str]:
    counts: Counter[str] = Counter()
    near_page_num: set[str] = set()
    norm_title = re.sub(r"\s+", "", book_title or "").lower()

    for idx, (is_code, chunk) in enumerate(chunks):
        if is_code or "\n" in chunk.strip():
            continue
        s = chunk.strip()
        if not s or len(s) > 55 or _is_structural_line(s):
            continue
        if _BARE_PAGE_NUM_RE.fullmatch(s) or _EXPLICIT_PAGE_RE.match(s):
            continue
        if s.endswith(("。", "！", "？", "!", "?", "：", ":", "，", ",")):
            continue
        if s.startswith(("**", "[", "【", "（", "(")):
            continue
        key = _header_key(s)
        counts[key] += 1
        prev_is_pg = idx > 0 and not chunks[idx - 1][0] and _is_page_number_line(chunks[idx - 1][1], allow_bare=True)
        next_is_pg = (
            idx + 1 < len(chunks)
            and not chunks[idx + 1][0]
            and _is_page_number_line(chunks[idx + 1][1], allow_bare=True)
        )
        if prev_is_pg or next_is_pg:
            near_page_num.add(key)

    headers: set[str] = set()
    for key, cnt in counts.items():
        compact = re.sub(r"\s+", "", key).lower()
        title_hit = bool(norm_title and len(norm_title) >= 2 and (compact == norm_title or compact in norm_title))
        if cnt >= 4 or (cnt >= 3 and (key in near_page_num or title_hit)):
            headers.add(key)
    return headers


def _can_stitch_paragraphs(prev_text: str, next_text: str, *, short_run_neighbor: bool = False) -> bool:
    p1 = prev_text.strip()
    p2 = next_text.strip()
    if not p1 or not p2:
        return False
    if _is_structural_line(p1.splitlines()[-1]) or _is_structural_line(p2.splitlines()[0]):
        return False
    if ("\n" in p1 or "\n" in p2) and any(_is_structural_line(ln) for ln in p1.splitlines() + p2.splitlines()):
        # 多行列表/表格/诗行块不轻易缝合
        return False
    if parse_explicit_footnote_def(p1) or parse_explicit_footnote_def(p2):
        return False
    if _match_tier2_prefix(p1) or _match_tier2_prefix(p2):
        return False
    if short_run_neighbor:
        return False

    tail_stripped = p1.rstrip(_SENT_CLOSERS)
    if tail_stripped and tail_stripped[-1] in _SENT_ENDERS:
        return False

    # 保护短小标题 / 献诗行 / 独立题名（例如 "版权声明"）
    if len(p1) <= 10 and p1[-1] not in ",，、—-（([" and not re.match(r"^[a-z]", p2):
        return False
    return not (
        len(p2) <= 10
        and not re.search(r"[。！？.!?]$", p2)
        and p1[-1] not in ",，、—-"
        and len(p1) <= 18
        and not re.match(r"^[a-z]", p2)
    )


def _join_stitched(p1: str, p2: str) -> str:
    left = p1.rstrip()
    right = p2.lstrip()
    if not left:
        return right
    if not right:
        return left
    if left.endswith("-") and len(left) >= 2 and left[-2].isalpha() and right[0].islower():
        return left[:-1] + right
    if _CJK_BOUNDARY_RE.match(left[-1]) and _CJK_BOUNDARY_RE.match(right[0]):
        return left + right
    return left + " " + right


def denoise_pages_and_stitch(text: str, stats: NormalizeStats, *, book_title: str = "") -> str:
    """去除独立页码行与高频重复页眉页脚，并缝合被其打断的相邻段落。"""
    if not text:
        return text

    # 按代码段与空行拆块，保序
    raw_chunks: list[tuple[bool, str]] = []
    for is_code, seg in _split_code_segments(text):
        if is_code:
            raw_chunks.append((True, seg))
            continue
        for block in re.split(r"\n{2,}", seg):
            stripped = block.strip("\n")
            if stripped.strip():
                raw_chunks.append((False, stripped))

    if not raw_chunks:
        return text

    # 先判断裸数字是否成页码形态（多行出现、或明显打断上句、或独立出现）
    bare_indices = [
        i
        for i, (is_code, ch) in enumerate(raw_chunks)
        if not is_code and "\n" not in ch.strip() and _BARE_PAGE_NUM_RE.fullmatch(ch.strip())
    ]
    repeated_headers = _detect_repeated_headers(raw_chunks, book_title=book_title)

    kept: list[tuple[bool, str, bool]] = []  # (is_code, text, had_noise_before)
    pending_noise_gap = False
    removed_here = False
    for _idx, (is_code, ch) in enumerate(raw_chunks):
        if is_code:
            kept.append((True, ch, pending_noise_gap))
            pending_noise_gap = False
            continue
        s = ch.strip()
        is_single = "\n" not in s
        if is_single and _EXPLICIT_PAGE_RE.match(s):
            stats.page_lines_removed += 1
            pending_noise_gap = True
            removed_here = True
            continue
        if is_single and _BARE_PAGE_NUM_RE.fullmatch(s):
            num = int(s)
            breaks_prev = (
                bool(kept)
                and not kept[-1][0]
                and not _is_structural_line(kept[-1][1].splitlines()[-1])
                and (kept[-1][1].strip().rstrip(_SENT_CLOSERS)[-1:] not in _SENT_ENDERS)
            )
            likely_year = 1900 <= num <= 2099 and len(bare_indices) < 2 and not breaks_prev
            if 1 <= num <= 2500 and not likely_year:
                stats.page_lines_removed += 1
                pending_noise_gap = True
                removed_here = True
                continue
        if is_single and _header_key(s) in repeated_headers:
            stats.header_lines_removed += 1
            pending_noise_gap = True
            removed_here = True
            continue

        # 单块内部也可能有多行混着孤立页码行（如 `上句\n12\n下句` 未空行）
        if not is_single and not _is_structural_line(s.splitlines()[0]):
            lines = ch.split("\n")
            new_lines: list[str] = []
            for ln in lines:
                ls = ln.strip()
                if _EXPLICIT_PAGE_RE.match(ls):
                    stats.page_lines_removed += 1
                    removed_here = True
                    continue
                if repeated_headers and _header_key(ls) in repeated_headers:
                    stats.header_lines_removed += 1
                    removed_here = True
                    continue
                new_lines.append(ln)
            ch = "\n".join(new_lines).strip()
            if not ch:
                pending_noise_gap = True
                continue

        kept.append((False, ch, pending_noise_gap))
        pending_noise_gap = False

    if not removed_here:
        return text

    # 去噪后再识别诗歌/戏剧短行连续段（≥3 个连续无句号短行），防止误缝
    is_short_poem_line = [
        (
            not is_code
            and "\n" not in ch.strip()
            and 0 < len(ch.strip()) <= 28
            and not _is_structural_line(ch)
            and ch.strip()[-1] not in "。！？.!?"
        )
        for is_code, ch, _gap in kept
    ]
    short_run_flags = [False] * len(kept)
    run_start = None
    for idx, flag in enumerate([*is_short_poem_line, False]):
        if flag and run_start is None:
            run_start = idx
        elif not flag and run_start is not None:
            if idx - run_start >= 3:
                for k in range(run_start, idx):
                    short_run_flags[k] = True
            run_start = None

    stitched: list[tuple[bool, str]] = []
    for idx, (is_code, ch, had_gap) in enumerate(kept):
        if (
            had_gap
            and not is_code
            and stitched
            and not stitched[-1][0]
            and _can_stitch_paragraphs(stitched[-1][1], ch, short_run_neighbor=short_run_flags[idx])
        ):
            stitched[-1] = (False, _join_stitched(stitched[-1][1], ch))
            stats.sentences_stitched += 1
        else:
            stitched.append((is_code, ch))

    return "\n\n".join(ch for _, ch in stitched) + ("\n" if text.endswith("\n") else "")


# --------------------------------------------------------------------------- 脚注语法归一


def _safe_fn_label(raw_marker: str, used: set[str], counter: list[int]) -> str:
    display, key = normalize_marker(raw_marker)
    cand = re.sub(r"[^0-9A-Za-z_-]", "", display or key)
    if not cand or not _STD_FN_LABEL_RE.fullmatch(cand):
        while str(counter[0]) in used:
            counter[0] += 1
        cand = str(counter[0])
        counter[0] += 1
    base = cand
    suffix = 2
    while cand in used:
        cand = f"{base}_{suffix}"
        suffix += 1
    used.add(cand)
    return cand


def _is_footnote_icon_ref(rel: str, alt: str, md_dir: Path | None) -> bool:
    clean_rel = unquote(rel.split()[0].strip("<>"))
    return _image_kind(clean_rel, md_dir, inline=True, alt=alt) == "footnote"


def _icon_marker_hint(rel: str) -> str:
    clean_rel = unquote(rel.split()[0].strip("<>"))
    stem = clean_rel.rsplit("/", 1)[-1].rsplit(".", 1)[0].strip()
    m = _DIGITS_RE.search(stem)
    return m.group(1) if m else ""


def _split_chunk_with_fences(text: str) -> list[tuple[bool, str]]:
    chunks: list[tuple[bool, str]] = []
    for is_code, seg in _split_code_segments(text):
        if is_code:
            chunks.append((True, seg))
            continue
        for part in re.split(r"\n{2,}", seg):
            if part.strip():
                chunks.append((False, part.strip("\n")))
    return chunks


def _is_preserved_dbl_fn_target(raw_target: str) -> bool:
    """判断 ``[[N]](target)`` 的目标是否为外链或跨文件 `.md` 脚注链接（须原样保留）。"""
    target = (raw_target or "").strip()
    if not target:
        return False
    if target.lower().startswith(("http://", "https://", "mailto:")):
        return True
    if target.startswith("<") and ">" in target:
        inner = target[1 : target.find(">")].strip()
    else:
        inner = re.split(r"""\s+(?=["'])""", target, maxsplit=1)[0].strip()
    path_part = inner.split("#", 1)[0].strip()
    return path_part.lower().endswith(".md")


def normalize_chapter_footnotes(
    text: str,
    md_dir: Path | None,
    stats: NormalizeStats,
    *,
    removed_icon_rels: set[str],
) -> str:
    """将 ``^![note](images/note*.png)^``、``[![](images/note_3.png)]()``、``[[N]]()`` 归一为 ``[^N]`` + ``[^N]:``。"""
    if not text:
        return text

    chunks = _split_chunk_with_fences(text)
    if not chunks:
        return text

    # 1. 识别本章注释区起点与文末注定义块
    notes_start_idx: int | None = None
    for idx, (is_code, ch) in enumerate(chunks):
        if is_code:
            continue
        hm = HEADING_RE.match(ch.strip())
        if hm and "\n" not in ch.strip() and NOTE_HEADING_RE.match(hm.group(2).strip()):
            notes_start_idx = idx
            break

    # 识别文末同族裸段落（至少 3 条）
    trailing_note_indices: set[int] = set()
    tail_family = ""
    for idx in range(len(chunks) - 1, -1, -1):
        is_code, ch = chunks[idx]
        if is_code or _is_structural_line(ch.splitlines()[0]):
            break
        info = _match_tier2_prefix(ch.strip())
        if not info or info[0] not in ("A", "B"):
            break
        if not tail_family:
            tail_family = info[1]
        elif info[1] != tail_family:
            break
        trailing_note_indices.add(idx)
    if len(trailing_note_indices) < 3:
        trailing_note_indices.clear()

    # 文末连续脚注定义块（`[^N]:` 或 `[[N]]() ...`），允许紧邻其前的列表式尾注也视为文末注区
    trailing_def_floor = len(chunks)
    for idx in range(len(chunks) - 1, -1, -1):
        is_code, ch = chunks[idx]
        if is_code:
            break
        s_tail = ch.strip()
        if FOOTNOTE_RE.match(s_tail) and "\n" not in s_tail:
            trailing_def_floor = idx
            continue
        if parse_explicit_footnote_def(" ".join(ln.strip() for ln in s_tail.splitlines())):
            trailing_def_floor = idx
            continue
        break

    # 收集本地可配对的脚注定义条目
    # defs: list of dict(chunk_idx, item_idx, key, display, body, kind, assigned_label)
    defs: list[dict[str, Any]] = []
    used_labels: set[str] = set()
    for idx, (is_code, ch) in enumerate(chunks):
        if is_code:
            continue
        s = ch.strip()
        # 已是标准 [^N]:
        m_std = FOOTNOTE_RE.match(s)
        if m_std and "\n" not in s:
            label = m_std.group(1).strip()
            _, key = normalize_marker(label)
            used_labels.add(label)
            defs.append(
                {
                    "chunk_idx": idx,
                    "item_idx": None,
                    "key": key,
                    "display": label,
                    "body": m_std.group(2).strip(),
                    "kind": "std",
                    "label": label,
                }
            )
            continue
        # 显式定义 [[N]]() ... / [N](ch.md) ...
        exp = parse_explicit_footnote_def(" ".join(ln.strip() for ln in s.splitlines()))
        if exp:
            defs.append(
                {
                    "chunk_idx": idx,
                    "item_idx": None,
                    "key": exp["key"],
                    "display": exp["display"],
                    "body": exp["body"],
                    "kind": "explicit",
                    "def_kind": exp["def_kind"],
                    "label": None,
                }
            )
            continue
        # 列表式尾注（如 B0130: `- [1]...`）
        lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
        if lines and all(LIST_RE.match(ln) for ln in lines):
            parsed_items = []
            matched_cnt = 0
            for ln in lines:
                lm = LIST_RE.match(ln)
                item_txt = lm.group(1).strip() if lm else ln
                info = _match_tier2_prefix(
                    item_txt, allow_dot=bool(notes_start_idx is not None and idx > notes_start_idx)
                )
                exp_i = parse_explicit_footnote_def(item_txt) if not info else None
                if info:
                    matched_cnt += 1
                    parsed_items.append((info[4], info[2], item_txt[info[3] :].strip()))
                elif exp_i:
                    matched_cnt += 1
                    parsed_items.append((exp_i["key"], exp_i["display"], exp_i["body"]))
                else:
                    parsed_items.append(("", "", item_txt))
            if matched_cnt >= max(2, int(len(lines) * 0.6)) and (
                idx + 1 >= trailing_def_floor or (notes_start_idx is not None and idx > notes_start_idx)
            ):
                for item_i, (k, disp, item_body) in enumerate(parsed_items):
                    if k:
                        defs.append(
                            {
                                "chunk_idx": idx,
                                "item_idx": item_i,
                                "key": k,
                                "display": disp,
                                "body": item_body,
                                "kind": "list_item",
                                "label": None,
                            }
                        )
                continue
        # 注释区或文末连续注段落
        if (
            (notes_start_idx is not None and idx > notes_start_idx) or idx in trailing_note_indices
        ) and not _is_structural_line(s.splitlines()[0]):
            info = _match_tier2_prefix(s, allow_dot=bool(notes_start_idx is not None and idx > notes_start_idx))
            if info:
                defs.append(
                    {
                        "chunk_idx": idx,
                        "item_idx": None,
                        "key": info[4],
                        "display": info[2],
                        "body": s[info[3] :].strip(),
                        "kind": "tier2",
                        "label": None,
                    }
                )

    # 记录现有标准引用的 key
    for is_code, ch in chunks:
        if is_code:
            continue
        for m in re.finditer(r"(?<!\[)\[\^([0-9A-Za-z_-]{1,20})\](?![:(])", ch):
            used_labels.add(m.group(1))

    # 预先统计所有通用无号图标出现次数（用于判断 1-to-1 唯一配对）
    def_by_key: dict[str, list[dict[str, Any]]] = {}
    for d in defs:
        if d["key"]:
            def_by_key.setdefault(d["key"], []).append(d)

    generic_icon_spans: list[tuple[int, int, int, str]] = []  # (chunk_idx, start, end, rel)
    numbered_refs_keys: set[str] = set()
    for idx, (is_code, ch) in enumerate(chunks):
        if is_code:
            continue
        # 跳过定义块本身
        if any(d["chunk_idx"] == idx for d in defs):
            continue
        for m in _INLINE_DBL_FN_RE.finditer(ch):
            if _is_preserved_dbl_fn_target(m.group(3) or ""):
                continue
            _, k = normalize_marker(m.group(2))
            if k:
                numbered_refs_keys.add(k)
        for m in _INLINE_ICON_LINK_RE.finditer(ch):
            rel = unquote(m.group(3).split()[0].strip("<>"))
            if _is_footnote_icon_ref(rel, m.group(2), md_dir):
                hint = _icon_marker_hint(rel)
                if hint:
                    numbered_refs_keys.add(normalize_marker(hint)[1])
                else:
                    generic_icon_spans.append((idx, m.start(), m.end(), rel))
        # 单独图片（去掉已被 _INLINE_ICON_LINK_RE 覆盖的区间）
        link_ranges = [m.span() for m in _INLINE_ICON_LINK_RE.finditer(ch)]
        for m in _INLINE_ICON_IMG_RE.finditer(ch):
            if any(a <= m.start() and m.end() <= b for a, b in link_ranges):
                continue
            rel = unquote(m.group(3).split()[0].strip("<>"))
            if _is_footnote_icon_ref(rel, m.group(2), md_dir):
                hint = _icon_marker_hint(rel)
                if hint:
                    numbered_refs_keys.add(normalize_marker(hint)[1])
                else:
                    generic_icon_spans.append((idx, m.start(), m.end(), rel))

    lone_defs = [
        d
        for d in defs
        if d["kind"] != "std"
        and not (d["kind"] == "explicit" and d.get("def_kind") == "dbl_bracket")
        and d["key"] not in numbered_refs_keys
    ]
    generic_pairable = bool(generic_icon_spans and len(generic_icon_spans) == len(lone_defs))

    seq_counter = [1]
    paired_defs: set[int] = set()
    changed = False

    def _assign_label_for_def(d: dict[str, Any]) -> str:
        if d["label"]:
            return d["label"]
        lbl = _safe_fn_label(d["display"] or d["key"], used_labels, seq_counter)
        d["label"] = lbl
        return lbl

    def _sub_dbl_fn(m: re.Match[str]) -> str:
        nonlocal changed
        if _is_preserved_dbl_fn_target(m.group(3) or ""):
            return m.group(0)
        raw_lbl = m.group(2).strip()
        display, k = normalize_marker(raw_lbl)
        if not k:
            return m.group(0)
        cands = def_by_key.get(k) or []
        if cands:
            d = cands[0]
            lbl = _assign_label_for_def(d)
            paired_defs.add(id(d))
            stats.footnotes_converted += 1
            changed = True
            return f"[^{lbl}]"
        cand_std = re.sub(r"[^0-9A-Za-z_-]", "", display or k)
        if cand_std and _STD_FN_LABEL_RE.fullmatch(cand_std) and len(display) <= 20:
            used_labels.add(cand_std)
            stats.footnote_residues_cleaned += 1
            changed = True
            return f"[^{cand_std}]"
        stats.footnote_residues_cleaned += 1
        changed = True
        return f"[{display}]"

    # 执行行内替换
    generic_cursor = 0
    new_chunks: list[tuple[bool, str]] = []
    for idx, (is_code, ch) in enumerate(chunks):
        if is_code or any(d["chunk_idx"] == idx for d in defs):
            new_chunks.append((is_code, ch))
            continue

        updated = ch

        # 先处理 [![alt](icon)]()，再处理 ^![alt](icon)^ / ![alt](icon)
        def _replace_icon(rel: str, alt: str) -> str | None:
            nonlocal generic_cursor, changed
            if not _is_footnote_icon_ref(rel, alt, md_dir):
                return None
            removed_icon_rels.add(rel)
            hint = _icon_marker_hint(rel)
            if hint:
                _, k = normalize_marker(hint)
                cands = def_by_key.get(k) or []
                if cands:
                    d = cands[0]
                    lbl = _assign_label_for_def(d)
                    paired_defs.add(id(d))
                    stats.footnotes_converted += 1
                    changed = True
                    return f"[^{lbl}]"
                # 找不到本地定义但带明确编号：转为标准 [^N]，交由 L1 跨文件配对
                lbl = re.sub(r"[^0-9A-Za-z_-]", "", hint)
                if lbl and _STD_FN_LABEL_RE.fullmatch(lbl):
                    used_labels.add(lbl)
                    stats.footnote_residues_cleaned += 1
                    changed = True
                    return f"[^{lbl}]"
                stats.footnote_residues_cleaned += 1
                changed = True
                return ""
            if generic_pairable and generic_cursor < len(lone_defs):
                d = lone_defs[generic_cursor]
                generic_cursor += 1
                lbl = _assign_label_for_def(d)
                paired_defs.add(id(d))
                stats.footnotes_converted += 1
                changed = True
                return f"[^{lbl}]"
            stats.footnote_residues_cleaned += 1
            changed = True
            return ""

        def _sub_icon_link(m: re.Match[str]) -> str:
            rel = unquote(m.group(3).split()[0].strip("<>"))
            res = _replace_icon(rel, m.group(2))
            return m.group(0) if res is None else res

        updated = _INLINE_ICON_LINK_RE.sub(_sub_icon_link, updated)

        def _sub_icon_img(m: re.Match[str]) -> str:
            rel = unquote(m.group(3).split()[0].strip("<>"))
            res = _replace_icon(rel, m.group(2))
            return m.group(0) if res is None else res

        updated = _INLINE_ICON_IMG_RE.sub(_sub_icon_img, updated)
        updated = _INLINE_DBL_FN_RE.sub(_sub_dbl_fn, updated)
        new_chunks.append((False, updated))

    # 重写已配对的非标准定义块，以及所有残留的双括号显式定义块（`[[N]]() ...`）为 `[^N]: body`
    def _clean_body_dbl_fn(body: str) -> str:
        return _INLINE_DBL_FN_RE.sub(_sub_dbl_fn, body)

    defs_by_chunk: dict[int, list[dict[str, Any]]] = {}
    for d in defs:
        is_unpaired_dbl = d.get("kind") == "explicit" and d.get("def_kind") == "dbl_bracket"
        if (id(d) in paired_defs and d["kind"] != "std") or is_unpaired_dbl:
            if not d["label"]:
                _assign_label_for_def(d)
                stats.footnote_residues_cleaned += 1
            defs_by_chunk.setdefault(d["chunk_idx"], []).append(d)

    if defs_by_chunk:
        rebuilt_chunks: list[tuple[bool, str]] = []
        for idx, (is_code, ch) in enumerate(new_chunks):
            chunk_defs = defs_by_chunk.get(idx)
            if not chunk_defs:
                rebuilt_chunks.append((is_code, ch))
                continue
            changed = True
            if chunk_defs[0]["kind"] == "list_item":
                # 可能列表里全部或部分条目被转为 [^N]:
                lines = [ln.strip() for ln in ch.splitlines() if ln.strip()]
                by_item = {d["item_idx"]: d for d in chunk_defs}
                remaining_list: list[str] = []
                converted_defs: list[str] = []
                for item_i, ln in enumerate(lines):
                    item_d = by_item.get(item_i)
                    if item_d and item_d["label"]:
                        body = _clean_body_dbl_fn(item_d["body"])
                        converted_defs.append(f"[^{item_d['label']}]: {body}")
                    else:
                        remaining_list.append(ln)
                if remaining_list:
                    rebuilt_chunks.append((False, "\n".join(remaining_list)))
                for cd in converted_defs:
                    rebuilt_chunks.append((False, cd))
            else:
                d = chunk_defs[0]
                if d["label"]:
                    body = _clean_body_dbl_fn(d["body"])
                    rebuilt_chunks.append((False, f"[^{d['label']}]: {body}"))
                else:
                    rebuilt_chunks.append((False, ch))
        new_chunks = rebuilt_chunks

    if not changed:
        return text
    return "\n\n".join(ch for _, ch in new_chunks if ch.strip()) + ("\n" if text.endswith("\n") else "")


# --------------------------------------------------------------------------- 章节角色判定


def classify_chapter_role(
    title: str,
    body: str,
    file_name: str = "",
    *,
    book_title: str = "",
    is_only_chapter: bool = False,
) -> ChapterRole:
    """按标题与结构无损判别章节角色：``body`` / ``notes`` / ``front_back``。"""
    if is_only_chapter:
        return "body"
    clean_title = clean_inline(title).strip()
    stem = Path(file_name).stem if file_name else ""
    stem_clean = re.sub(r"^\d+_", "", stem).strip()

    if clean_title and NOTE_CHAPTER_TITLE_RE.match(clean_title):
        return "notes"
    if stem_clean and NOTE_CHAPTER_TITLE_RE.match(stem_clean):
        return "notes"
    # 独立评注章节（含编号逐条释义）
    if clean_title in ("评注", "注释与评注", "校注") and non_space_count(body) < 30_000:
        return "notes"

    # 明确含正文章节前缀（第N章/幕/序幕/尾声等）的章节绝不归为 front_back
    if clean_title and _BODY_PROTECTED_TITLE_RE.match(clean_title):
        return "body"

    if clean_title and _FRONT_BACK_TITLE_RE.match(clean_title):
        return "front_back"
    if stem_clean and _FRONT_BACK_TITLE_RE.match(stem_clean):
        return "front_back"

    # 极短封面/书名页（无实质正文，仅图片或复述书名）
    body_no_h1 = re.sub(r"(?m)^#\s+.*$", "", body).strip()
    body_no_imgs = _IMG_REF_RE.sub("", body_no_h1).strip()
    plain_chars = non_space_count(body_no_imgs)
    if plain_chars < 50:
        norm_bt = re.sub(r"[\W_]+", "", (book_title or "").lower())
        norm_ct = re.sub(r"[\W_]+", "", clean_title.lower())
        if plain_chars == 0 or (norm_bt and norm_ct and (norm_ct in norm_bt or norm_bt in norm_ct)):
            return "front_back"
        if stem_clean.startswith("前置页"):
            return "front_back"

    return "body"


# --------------------------------------------------------------------------- 三级拆章策略


def _unit_chunks(body: str) -> list[tuple[str, int, int, str]]:
    """按空行与代码围栏切出原子语义块：``(chunk_text, chars, est_blocks, heading_info)``。"""
    chunks: list[tuple[str, int, int, str]] = []
    for is_code, seg in _split_code_segments(body):
        if is_code:
            chars = non_space_count(seg)
            chunks.append((seg, chars, 1, ""))
            continue
        for part in re.split(r"\n{2,}", seg):
            s = part.strip("\n")
            if not s.strip():
                continue
            lines = [ln for ln in s.splitlines() if ln.strip()]
            heading_title = ""
            if len(lines) == 1:
                hm = HEADING_RE.match(lines[0].strip())
                if hm:
                    heading_title = hm.group(2).strip()
            # 单块里含多个列表项也视为 1 块，避免拆开列表
            est_blocks = 1
            chunks.append((s, non_space_count(s), est_blocks, heading_title))
    return chunks


def _normalize_chapter_body_h1(title: str, raw_chunks: list[str], *, keep_leading_heading: bool = False) -> str:
    """构造单章正文（含唯一 H1，不丢失正文句子）。"""
    chunks = list(raw_chunks)
    if chunks and not keep_leading_heading:
        first = chunks[0].strip()
        hm = HEADING_RE.match(first)
        if hm and "\n" not in first:
            chunks = chunks[1:]
    demoted: list[str] = []
    for ch in chunks:
        if ch.lstrip().startswith("```"):
            demoted.append(ch)
        else:
            demoted.append(re.sub(r"(?m)^#\s+", "## ", ch))
    body_text = "\n\n".join(c for c in demoted if c.strip()).strip()
    if body_text:
        return f"# {title}\n\n{body_text}\n"
    return f"# {title}\n"


def _split_by_blank_lines(
    chunks: list[tuple[str, int, int, str]],
    base_title: str,
    *,
    target_min: int = TARGET_CHAPTER_MIN_CHARS,
    target_max: int = TARGET_CHAPTER_MAX_CHARS,
    max_blocks: int = TARGET_CHAPTER_MAX_BLOCKS,
) -> list[tuple[str, list[str]]]:
    """第三级策略：无标题长文按语义空行分段切卷（8k–18k 字，≤max_blocks 块）。"""
    units: list[list[tuple[str, int, int, str]]] = []
    cur: list[tuple[str, int, int, str]] = []
    cur_chars = 0
    cur_blocks = 0
    for item in chunks:
        _txt, chars, blk_cnt, heading = item
        # 避免把小标题孤零零留在上一卷末尾
        if cur and heading and cur_chars >= target_min // 2:
            units.append(cur)
            cur = [item]
            cur_chars = chars
            cur_blocks = blk_cnt
            continue
        would_exceed = bool(
            cur
            and (
                (cur_chars >= target_min and cur_chars + chars > target_max)
                or cur_blocks + blk_cnt > max_blocks
                or cur_chars + chars > target_max + 4000
            )
        )
        if would_exceed:
            units.append(cur)
            cur = [item]
            cur_chars = chars
            cur_blocks = blk_cnt
        else:
            cur.append(item)
            cur_chars += chars
            cur_blocks += blk_cnt
    if cur:
        units.append(cur)

    # 若末卷过短且与前一卷合并后不超上限，则并回前一卷
    if len(units) >= 2:
        last_chars = sum(it[1] for it in units[-1])
        last_blocks = sum(it[2] for it in units[-1])
        prev_chars = sum(it[1] for it in units[-2])
        prev_blocks = sum(it[2] for it in units[-2])
        if (
            last_chars < 3000
            and prev_chars + last_chars <= target_max + 4000
            and prev_blocks + last_blocks <= max_blocks
        ):
            units[-2].extend(units[-1])
            units.pop()

    out: list[tuple[str, list[str]]] = []
    for idx, grp in enumerate(units, 1):
        first_heading = next((it[3] for it in grp if it[3]), "")
        title = clean_inline(first_heading) if len(units) == 1 and first_heading else f"{base_title}（第 {idx} 节）"
        out.append((title, [it[0] for it in grp]))
    return out


def _merge_small_heading_groups(
    groups: list[tuple[str, list[tuple[str, int, int, str]]]],
    *,
    target_min: int = TARGET_CHAPTER_MIN_CHARS,
    target_max: int = TARGET_CHAPTER_MAX_CHARS,
    max_blocks: int = TARGET_CHAPTER_MAX_BLOCKS,
) -> list[tuple[str, list[str]]]:
    """第二级辅助：把过小的相邻 ``##`` 小节归并到 8k–18k 字，超大节继续细分。"""
    expanded: list[tuple[str, list[tuple[str, int, int, str]]]] = []
    for title, grp in groups:
        g_chars = sum(it[1] for it in grp)
        g_blocks = sum(it[2] for it in grp)
        if g_chars > target_max + 6000 or g_blocks > max_blocks:
            # 该小节本身超大：先尝试内部 ### 切分，否则退回空行切卷
            sub_groups = _split_chunks_by_heading_predicate(grp, lambda ln: bool(re.match(r"^###\s+", ln)))
            if len(sub_groups) >= 2:
                for sub_t, sub_grp in sub_groups:
                    s_chars = sum(it[1] for it in sub_grp)
                    s_blocks = sum(it[2] for it in sub_grp)
                    if s_chars > target_max + 6000 or s_blocks > max_blocks:
                        for vt, vchunks in _split_by_blank_lines(
                            sub_grp, sub_t or title, target_min=target_min, target_max=target_max, max_blocks=max_blocks
                        ):
                            expanded.append((vt, [(c, non_space_count(c), 1, "") for c in vchunks]))
                    else:
                        expanded.append((sub_t or title, sub_grp))
            else:
                for vt, vchunks in _split_by_blank_lines(
                    grp, title, target_min=target_min, target_max=target_max, max_blocks=max_blocks
                ):
                    expanded.append((vt, [(c, non_space_count(c), 1, "") for c in vchunks]))
        else:
            expanded.append((title, grp))

    merged: list[tuple[str, list[str]]] = []
    cur_title = ""
    cur_items: list[tuple[str, int, int, str]] = []
    cur_chars = 0
    cur_blocks = 0

    for idx, (title, grp) in enumerate(expanded):
        g_chars = sum(it[1] for it in grp)
        g_blocks = sum(it[2] for it in grp)
        if not cur_items:
            cur_title = title
            cur_items = list(grp)
            cur_chars = g_chars
            cur_blocks = g_blocks
            continue
        # 首段卷首若很短且下节大，独立保留卷首或按阈值归并
        if idx == 1 and cur_title == "卷首" and cur_chars >= 80 and g_chars >= target_min // 2:
            merged.append((cur_title, [it[0] for it in cur_items]))
            cur_title = title
            cur_items = list(grp)
            cur_chars = g_chars
            cur_blocks = g_blocks
            continue
        if cur_chars < target_min and cur_chars + g_chars <= target_max and cur_blocks + g_blocks <= max_blocks:
            cur_items.extend(grp)
            cur_chars += g_chars
            cur_blocks += g_blocks
            if cur_title == "卷首" and title:
                cur_title = title
        else:
            merged.append((cur_title, [it[0] for it in cur_items]))
            cur_title = title
            cur_items = list(grp)
            cur_chars = g_chars
            cur_blocks = g_blocks
    if cur_items:
        merged.append((cur_title, [it[0] for it in cur_items]))
    return merged


def _split_chunks_by_heading_predicate(
    chunks: list[tuple[str, int, int, str]],
    pred: Any,
) -> list[tuple[str, list[tuple[str, int, int, str]]]]:
    groups: list[tuple[str, list[tuple[str, int, int, str]]]] = []
    cur_title = "卷首"
    cur_items: list[tuple[str, int, int, str]] = []
    for item in chunks:
        txt, _chars, _blk, h_title = item
        if h_title and pred(txt.strip()):
            if cur_items:
                groups.append((cur_title, cur_items))
            cur_title = clean_inline(h_title)
            cur_items = [item]
        else:
            cur_items.append(item)
    if cur_items:
        groups.append((cur_title, cur_items))
    return groups


def split_single_chapter_body(
    body: str,
    book_title: str,
    *,
    max_chars: int = DEFAULT_SPLIT_CHARS,
    max_blocks: int = DEFAULT_SPLIT_BLOCKS,
) -> list[tuple[str, str]]:
    """三级策略拆章，返回 ``[(chapter_title, full_chapter_body_with_h1), ...]``。"""
    # 剥离顶层全书 H1，保留正文所有其他内容
    lines = body.split("\n")
    start_idx = 0
    while start_idx < len(lines) and not lines[start_idx].strip():
        start_idx += 1
    if start_idx < len(lines) and lines[start_idx].strip().startswith("# "):
        start_idx += 1
    rest_body = "\n".join(lines[start_idx:]).strip()
    if not rest_body:
        return [(book_title, f"# {book_title}\n")]

    chunks = _unit_chunks(rest_body)
    if not chunks:
        return [(book_title, f"# {book_title}\n")]

    # 第一级：已有章级标题（第N章/篇/部/卷/回、前言/后记/附录/参考文献/注释等）
    tier1_groups = _split_chunks_by_heading_predicate(
        chunks,
        lambda ln: bool(
            (m := HEADING_RE.match(ln))
            and len(m.group(1)) <= 4
            and _TIER1_HEADING_RE.match(clean_inline(m.group(2)).strip())
        ),
    )
    tier1_headed = [g for g in tier1_groups if g[0] != "卷首"]
    raw_chapters: list[tuple[str, list[str], bool]] = []  # (title, chunks, keep_leading_heading)

    if len(tier1_headed) >= 2:
        # 合并紧邻无正文的空标题段到下一章
        compact_t1: list[tuple[str, list[tuple[str, int, int, str]]]] = []
        pending_prefix: list[tuple[str, int, int, str]] = []
        pending_title = ""
        for t, grp in tier1_groups:
            non_h_chars = sum(it[1] for it in grp if not it[3])
            has_img_or_tbl = any(("![" in it[0] or TABLE_ROW_RE.match(it[0].splitlines()[0])) for it in grp)
            if non_h_chars < 60 and not has_img_or_tbl:
                if not pending_title and t != "卷首":
                    pending_title = t
                pending_prefix.extend(grp)
                continue
            if pending_prefix:
                grp = [*pending_prefix, *grp]
                if t == "卷首" and pending_title:
                    t = pending_title
                pending_prefix = []
                pending_title = ""
            compact_t1.append((t, grp))
        if pending_prefix:
            if compact_t1:
                compact_t1[-1][1].extend(pending_prefix)
            else:
                compact_t1.append((pending_title or book_title, pending_prefix))

        for t, grp in compact_t1:
            g_chars = sum(it[1] for it in grp)
            g_blocks = sum(it[2] for it in grp)
            if g_chars > max_chars or g_blocks > max_blocks - 100:
                # 巨型「第N篇」按内部任意小标题（##/###）归并切章，再兜底空行切卷
                sub = _split_chunks_by_heading_predicate(
                    grp,
                    lambda ln: bool((m := HEADING_RE.match(ln)) and 2 <= len(m.group(1)) <= 3),
                )
                sub_headed = [s for s in sub if s[0] != "卷首"]
                if len(sub_headed) >= 2:
                    sub_merged = _merge_small_heading_groups(sub)
                    for st, schunks in sub_merged:
                        label = t if st == "卷首" else st
                        raw_chapters.append((label, schunks, False))
                else:
                    for vt, vchunks in _split_by_blank_lines(grp, t):
                        raw_chapters.append((vt, vchunks, False))
            else:
                raw_chapters.append((t, [it[0] for it in grp], False))
    else:
        # 第二级：任意 ## 标题按 8k–18k 字归并切章
        tier2_groups = _split_chunks_by_heading_predicate(
            chunks,
            lambda ln: bool(re.match(r"^##\s+", ln)),
        )
        tier2_headed = [g for g in tier2_groups if g[0] != "卷首"]
        if len(tier2_headed) >= 2:
            for mt, mchunks in _merge_small_heading_groups(tier2_groups):
                raw_chapters.append((mt, mchunks, False))
        else:
            # 第三级：无标题长书按语义空行分段切卷
            for vt, vchunks in _split_by_blank_lines(chunks, book_title):
                raw_chapters.append((vt, vchunks, True))

    # 去重标题与正文指纹，确保 verify_output.py 100% 通过
    seen_titles: set[str] = set()
    seen_digests: set[str] = set()
    final_chapters: list[tuple[str, str]] = []
    for raw_t, ch_list, keep_h in raw_chapters:
        base_t = (raw_t or book_title).strip()
        cand_t = base_t
        suffix = 2
        while cand_t in seen_titles:
            cand_t = f"{base_t}（{suffix}）"
            suffix += 1
        seen_titles.add(cand_t)
        rendered = _normalize_chapter_body_h1(cand_t, ch_list, keep_leading_heading=keep_h)
        digest = hashlib.sha1(rendered.strip().encode("utf-8")).hexdigest()  # noqa: S324 与 verify_output 去重口径一致
        if digest in seen_digests:
            rendered = f"{rendered.rstrip()}\n\n（{cand_t}）\n"
        seen_digests.add(hashlib.sha1(rendered.strip().encode("utf-8")).hexdigest())  # noqa: S324
        final_chapters.append((cand_t, rendered))
    return final_chapters


# --------------------------------------------------------------------------- README 与目录汇总


def _extract_readme_meta(
    md_dir: Path,
    manifest: Mapping[str, Any],
    book_title: str,
    first_meta: Mapping[str, str] | None = None,
) -> tuple[list[str], str]:
    """从旧 README.md / book.json / 首章 front matter 提取元信息行与未收录块，避免丢元信息。"""
    readme_path = md_dir / "README.md"
    meta_lines: list[str] = []
    unlisted = "- 无"
    if readme_path.is_file():
        text = readme_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        in_unlisted = False
        unlisted_lines: list[str] = []
        for ln in lines:
            s = ln.strip()
            if s.startswith("## 目录"):
                in_unlisted = False
                continue
            if s.startswith("## 未收录"):
                in_unlisted = True
                continue
            if in_unlisted:
                unlisted_lines.append(ln)
                continue
            if s.startswith(("- 合集：", "- 作者：", "- 出版：", "- 日期：", "- 来源：")):
                meta_lines.append(s)
        cleaned_unlisted = "\n".join(unlisted_lines).strip()
        if cleaned_unlisted:
            unlisted = cleaned_unlisted
    book_meta: dict[str, Any] = {}
    book_json = md_dir.parent / "book.json"
    if book_json.is_file():
        try:
            loaded = json.loads(book_json.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                book_meta = loaded
        except (OSError, json.JSONDecodeError):
            book_meta = {}
    fm = first_meta or {}
    if not any(ln.startswith("- 合集：") for ln in meta_lines):
        col = str(book_meta.get("collection") or book_meta.get("series") or fm.get("collection") or "").strip()
        if col:
            meta_lines.append(f"- 合集：{col}")
    if not any(ln.startswith("- 作者：") for ln in meta_lines):
        author = str(book_meta.get("author") or fm.get("author") or "").strip()
        if author:
            meta_lines.append(f"- 作者：{author}")
    if not any(ln.startswith("- 出版：") for ln in meta_lines):
        pub = str(book_meta.get("publisher") or fm.get("publisher") or "").strip()
        if pub:
            meta_lines.append(f"- 出版：{pub}")
    if not any(ln.startswith("- 日期：") for ln in meta_lines):
        dt = str(book_meta.get("date") or fm.get("date") or "").strip()
        if dt:
            meta_lines.append(f"- 日期：{dt}")
    if not any(ln.startswith("- 来源：") for ln in meta_lines):
        src = str(manifest.get("source") or fm.get("source") or "").strip()
        if src:
            meta_lines.append(f"- 来源：`{Path(src).name}`")
    return meta_lines, unlisted


def _render_readme(
    book_title: str,
    meta_lines: Sequence[str],
    toc: Sequence[tuple[str, str]],
    images_count: int,
    unlisted: str,
) -> str:
    lines = [f"# {book_title}", ""]
    for ln in meta_lines:
        if ln.startswith("- 来源："):
            continue
        lines.append(ln)
    lines.append(f"- 章节：{len(toc)}")
    lines.append(f"- 图片：{images_count}")
    for ln in meta_lines:
        if ln.startswith("- 来源："):
            lines.append(ln)
    lines.extend(["", "## 目录", ""])
    for label, fname in toc:
        lines.append(f"- [{label}]({_link_dest(fname)})")
    lines.extend(["", "## 未收录（卷首/卷尾页）", "", unlisted, ""])
    return "\n".join(lines)


def _referenced_images_in_texts(texts: Iterable[str]) -> set[str]:
    refs: set[str] = set()
    for text in texts:
        for pat in (_IMG_REF_RE, _LINK_REF_RE):
            for g1, g2 in pat.findall(text):
                raw = (g1 or g2 or "").strip()
                if not g1 and raw:
                    raw = re.split(r"""\s+(?=["'])""", raw, maxsplit=1)[0].strip()
                    if raw.startswith("<") and raw.endswith(">"):
                        raw = raw[1:-1].strip()
                target = unquote(raw)
                while target.startswith("./"):
                    target = target[2:]
                if not target or target.startswith(("http://", "https://", "mailto:", "#", "data:")):
                    continue
                if pat is _LINK_REF_RE and Path(target.split("#", 1)[0]).suffix.lower() not in _IMAGE_EXTS:
                    continue
                refs.add(target)
    return refs


def _discover_chapter_files(md_dir: Path) -> list[Path]:
    all_md = sorted(p for p in md_dir.iterdir() if p.is_file() and p.suffix.lower() == ".md" and p.name != "README.md")
    numbered: list[tuple[int, str, Path]] = []
    others: list[Path] = []
    for p in all_md:
        m = _CHAPTER_FILE_RE.match(p.name)
        if m:
            numbered.append((int(m.group(1)), p.name, p))
        else:
            others.append(p)
    if numbered and not others:
        numbered.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in numbered]
    if numbered and others:
        numbered.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in numbered] + others
    return others


def _resolve_book_title(md_dir: Path, manifest: Mapping[str, Any], chapter_files: Sequence[Path]) -> str:
    book_json = md_dir.parent / "book.json"
    if book_json.is_file():
        try:
            data = json.loads(book_json.read_text(encoding="utf-8"))
            if isinstance(data, Mapping) and str(data.get("title") or "").strip():
                return str(data["title"]).strip()
        except (OSError, json.JSONDecodeError):
            pass
    readme = md_dir / "README.md"
    if readme.is_file():
        for ln in readme.read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.strip().startswith("# "):
                return ln.strip()[2:].strip()
    if chapter_files:
        raw = chapter_files[0].read_text(encoding="utf-8", errors="replace")
        _, meta, _ = _split_front_matter_raw(raw)
        if meta.get("title"):
            return meta["title"]
    return str(manifest.get("uid") or md_dir.parent.name or "未命名")


def _backup_md_dir(md_dir: Path, backup_dir: Path, label: str) -> Path:
    """在修改前强制完整备份原 ``md/``；失败则抛异常且绝不碰原目录。"""
    dest = backup_dir / label
    if dest.exists():
        raise FileExistsError(f"备份目标已存在，拒绝覆盖：{dest}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(md_dir, dest, symlinks=True)
    # 校验关键文件已完整落盘
    src_files = sorted(p.relative_to(md_dir).as_posix() for p in md_dir.rglob("*") if p.is_file())
    dst_files = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
    if src_files != dst_files:
        raise OSError(f"备份校验不一致（源 {len(src_files)} 个文件 vs 备份 {len(dst_files)} 个文件）")
    return dest


# --------------------------------------------------------------------------- 主入口


def normalize_book_md(
    md_dir: Path | str,
    *,
    backup_dir: Path | str | None = None,
    dry_run: bool = False,
    split_chars: int = DEFAULT_SPLIT_CHARS,
    split_blocks: int = DEFAULT_SPLIT_BLOCKS,
) -> dict[str, Any]:
    """对单本书的 ``md/`` 执行幂等 L0 规范化；二次运行必须 0 变更。"""
    root = Path(md_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"md 目录不存在：{root}")

    manifest_path = root / ".manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
        except (OSError, json.JSONDecodeError):
            manifest = {}

    chapter_files = _discover_chapter_files(root)
    if not chapter_files:
        return {
            "md_dir": str(root),
            "uid": str(manifest.get("uid") or root.parent.name),
            "changed": False,
            "skipped": True,
            "reason": "no_chapters",
            "stats": NormalizeStats().to_dict(),
        }

    stats = NormalizeStats(chapters_before=len(chapter_files))
    book_title = _resolve_book_title(root, manifest, chapter_files)
    existing_manifest_roles = load_manifest_chapter_roles(root)

    # 读取原始章文件并统计原有图片引用
    orig_records: list[dict[str, Any]] = []
    orig_img_refs: set[str] = set()
    for p in chapter_files:
        raw = p.read_text(encoding="utf-8", errors="replace")
        orig_img_refs |= _referenced_images_in_texts([raw])
        fm_items, fm_meta, body = _split_front_matter_raw(raw)
        orig_records.append(
            {
                "path": p,
                "name": p.name,
                "raw": raw,
                "fm_items": fm_items,
                "fm_meta": fm_meta,
                "body": body,
            }
        )

    # 1. 对每章执行清洗：HTML/表格 -> 页码去噪与断句缝合 -> 脚注语法归一
    removed_icon_rels: set[str] = set()
    cleaned_bodies: list[str] = []
    for rec in orig_records:
        b = clean_html_and_noise(rec["body"], stats)
        b = denoise_pages_and_stitch(b, stats, book_title=book_title)
        b = normalize_chapter_footnotes(b, root, stats, removed_icon_rels=removed_icon_rels)
        cleaned_bodies.append(b)

    # 2. 单章整书超阈值拆章（>24k 字符或 >800 L1 块）
    did_split = False
    new_chapters: list[dict[str, Any]] = []  # {name, fm_items, fm_meta, body, title, orig_raw}
    if len(orig_records) == 1:
        single_rec = orig_records[0]
        single_body = cleaned_bodies[0]
        body_chars = non_space_count(single_body)
        parsed_single = parse_markdown(single_body, stem=single_rec["path"].stem, base_dir=root)
        l1_blocks = len(parsed_single.get("blocks") or [])
        if body_chars > split_chars or l1_blocks > split_blocks:
            split_items = split_single_chapter_body(
                single_body,
                book_title,
                max_chars=split_chars,
                max_blocks=split_blocks,
            )
            if len(split_items) > 1:
                did_split = True
                width = 3 if len(split_items) >= 100 else 2
                used_fnames: set[str] = set()
                base_meta = [
                    (k, v) for k, v in single_rec["fm_items"] if k not in ("title", "chapter", "chapter_role", "pages")
                ]
                for idx, (ch_title, ch_body) in enumerate(split_items, 1):
                    safe_stem = _sanitize_filename(ch_title, f"ch{idx:0{width}d}")
                    fname = f"{idx:0{width}d}_{safe_stem}.md"
                    if fname in used_fnames:
                        fname = f"{idx:0{width}d}_{safe_stem}_{idx}.md"
                    used_fnames.add(fname)
                    fm_items = [("title", ch_title), *base_meta, ("chapter", str(idx))]
                    fm_meta = dict(fm_items)
                    new_chapters.append(
                        {
                            "name": fname,
                            "fm_items": fm_items,
                            "fm_meta": fm_meta,
                            "body": ch_body,
                            "title": ch_title,
                            "orig_raw": None,
                        }
                    )

    if not did_split:
        for rec, cleaned_b in zip(orig_records, cleaned_bodies, strict=True):
            parsed = parse_markdown(
                cleaned_b,
                stem=rec["path"].stem,
                meta=rec["fm_meta"],
                base_dir=root,
                file_name=rec["name"],
            )
            new_chapters.append(
                {
                    "name": rec["name"],
                    "fm_items": list(rec["fm_items"]),
                    "fm_meta": dict(rec["fm_meta"]),
                    "body": cleaned_b,
                    "title": parsed.get("title") or rec["fm_meta"].get("title") or rec["path"].stem,
                    "orig_raw": rec["raw"],
                }
            )

    stats.chapters_after = len(new_chapters)

    # 3. 无损章节角色标注（front_back / notes / body），并把非 body 写入 YAML front matter
    chapter_roles: dict[str, ChapterRole] = {}
    role_counts = {"body": 0, "notes": 0, "front_back": 0}
    only_ch = len(new_chapters) == 1
    for ch_idx, ch in enumerate(new_chapters):
        existing_fm_role = ch["fm_meta"].get("chapter_role")
        if existing_fm_role and existing_fm_role.strip().lower() in CHAPTER_ROLES:
            role: ChapterRole = normalize_chapter_role(existing_fm_role)
        elif not did_split and ch["name"] in existing_manifest_roles:
            role = existing_manifest_roles[ch["name"]]
        else:
            role = classify_chapter_role(
                ch["title"],
                ch["body"],
                ch["name"],
                book_title=book_title,
                is_only_chapter=only_ch,
            )
        if only_ch:
            role = "body"
        chapter_roles[ch["name"]] = role
        role_counts[role] += 1

        # 同步 YAML front matter：仅非 body 强制写入 chapter_role（已有显式 chapter_role: body 则保留）
        fm_items = [(k, v) for k, v in ch["fm_items"] if k != "chapter_role"]
        if role != "body":
            fm_items.append(("chapter_role", role))
        elif existing_fm_role:
            fm_items.append(("chapter_role", "body"))
        ch["fm_items"] = fm_items

        # 组装最终文件内容；若正文与 front matter 皆未变动，逐字节保留原样
        rendered = _format_front_matter(fm_items) + ch["body"].lstrip("\n") if fm_items else ch["body"]
        # 如果未做任何噪声修改且未改 front matter，保持完全不改写
        if (
            not did_split
            and ch["orig_raw"] is not None
            and ch["body"] == orig_records[ch_idx]["body"]
            and role == "body"
            and not existing_fm_role
        ):
            rendered = ch["orig_raw"]
        ch["rendered"] = rendered

    stats.roles = role_counts

    # 4. 计算孤儿图标文件（仅删除折叠/移除引用后不再被任何章引用的图标，绝不误删正文插图）
    new_img_refs = _referenced_images_in_texts(ch["rendered"] for ch in new_chapters)
    img_dir = root / "images"
    orphan_icons_to_delete: list[Path] = []
    if img_dir.is_dir():
        for img_file in sorted(img_dir.iterdir()):
            if not img_file.is_file():
                continue
            rel = f"images/{img_file.name}"
            if rel in new_img_refs:
                continue
            was_removed_icon = rel in removed_icon_rels or (
                rel in orig_img_refs and _image_kind(rel, root, inline=True) == "footnote"
            )
            is_footnote_icon = _image_kind(rel, root, inline=True) == "footnote"
            if was_removed_icon or is_footnote_icon:
                orphan_icons_to_delete.append(img_file)
    stats.orphan_icons_removed = len(orphan_icons_to_delete)

    # 5. 检查是否有任何实际变更（决定是否重写文件与 .manifest.json，确保严格幂等）
    md_files_changed = did_split or any(ch["rendered"] != ch["orig_raw"] for ch in new_chapters)
    old_roles = manifest.get("chapter_roles")
    manifest_needs_update = (
        md_files_changed
        or bool(orphan_icons_to_delete)
        or manifest.get("canonical_version") != CANONICAL_VERSION
        or old_roles != chapter_roles
    )

    # README 仅在拆章、文件变动、删图或首次缺 README 时重写
    meta_lines, unlisted = _extract_readme_meta(root, manifest, book_title, first_meta=orig_records[0]["fm_meta"])
    toc = [(ch["title"], ch["name"]) for ch in new_chapters]
    new_readme = _render_readme(book_title, meta_lines, toc, len(new_img_refs), unlisted)
    readme_path = root / "README.md"
    old_readme = readme_path.read_text(encoding="utf-8", errors="replace") if readme_path.is_file() else None
    readme_changed = did_split or (old_readme is None) or (bool(orphan_icons_to_delete) and old_readme != new_readme)

    anything_changed = md_files_changed or bool(orphan_icons_to_delete) or manifest_needs_update or readme_changed

    uid = str(manifest.get("uid") or root.parent.name)
    if not anything_changed or dry_run:
        return {
            "md_dir": str(root),
            "uid": uid,
            "changed": anything_changed,
            "dry_run": dry_run,
            "did_split": did_split,
            "chapter_roles": chapter_roles,
            "stats": stats.to_dict(),
        }

    # 6. 写前强制备份（失败即中止，绝不触碰原目录）
    backup_path = ""
    if backup_dir is not None:
        bk_root = Path(backup_dir)
        bk_label = uid if uid and uid != "md" else root.parent.name
        backup_path = str(_backup_md_dir(root, bk_root, bk_label))

    # 7. 落盘（先写临时文件再原子 os.replace；拆章时先写新章再删不在新集中的旧文件）
    for ch in new_chapters:
        dest = root / ch["name"]
        if not did_split and ch["orig_raw"] == ch["rendered"] and dest.is_file():
            continue
        _write_text_atomic(dest, ch["rendered"])

    if did_split:
        keep_names = {ch["name"] for ch in new_chapters}
        for old_p in chapter_files:
            if old_p.name not in keep_names and old_p.is_file():
                old_p.unlink()

    for icon_path in orphan_icons_to_delete:
        if icon_path.is_file():
            icon_path.unlink()
    if img_dir.is_dir() and not any(img_dir.iterdir()):
        img_dir.rmdir()

    if readme_changed:
        _write_text_atomic(readme_path, new_readme)

    # 更新 .manifest.json
    new_manifest = dict(manifest)
    if md_files_changed or did_split:
        total_bytes = sum((root / ch["name"]).stat().st_size for ch in new_chapters)
        total_chars = sum(len(re.sub(r"\s", "", ch["body"])) for ch in new_chapters)
        new_manifest["chapters"] = len(new_chapters)
        new_manifest["bytes"] = total_bytes
        if "chars" in new_manifest or did_split:
            new_manifest["chars"] = total_chars
        new_manifest["images"] = len(new_img_refs)
        new_manifest["structure"] = "single" if len(new_chapters) == 1 else "chapters"
        new_manifest["single_file"] = len(new_chapters) == 1
    elif orphan_icons_to_delete:
        new_manifest["images"] = len(new_img_refs)

    new_manifest["canonical_version"] = CANONICAL_VERSION
    new_manifest["normalized_at"] = _now_iso()
    new_manifest["chapter_roles"] = chapter_roles
    new_manifest["normalize_stats"] = stats.to_dict()
    _write_json_atomic(manifest_path, new_manifest)

    return {
        "md_dir": str(root),
        "uid": uid,
        "changed": True,
        "dry_run": False,
        "did_split": did_split,
        "backup_path": backup_path,
        "chapter_roles": chapter_roles,
        "stats": stats.to_dict(),
    }


def _write_text_atomic(dest: Path, content: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=dest.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        tmp_path.replace(dest)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _write_json_atomic(dest: Path, payload: Mapping[str, Any]) -> None:
    _write_text_atomic(dest, json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
