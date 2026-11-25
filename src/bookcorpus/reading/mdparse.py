"""Markdown 章节解析：front matter、标题与稳定的源文块。

按空行切块、把段落内换行并成一个空格；块文本只做转档清洗（HTML 标签/实体、
Pandoc 属性、行内脚注图标），不改写正文。块 ``chars`` 是汉字数
（``[\\u4e00-\\u9fff]``），章节 ``chars`` 是非空白字符数。
``note*.png`` / ``footnote*`` / ``tip*`` / ``icon*`` 等行内小图是脚注图标，
折进所在句子的 ``fn_refs``，不渲染大图；其余图片保留相对路径。
``BLOCK_VERSION`` 标记块号生成口径，解析规则改变块号时必须递增。
"""

import html
import os
import re
import struct
from html.parser import HTMLParser
from urllib.parse import unquote

from .footnotes import (
    _PLAIN_ENDNOTE_LINE_RE,
    _marker_alt_keys,
    _match_tier2_prefix,
    _promote_trailing_note_list,
    extract_inline_footnotes,
    normalize_marker,
    parse_explicit_footnote_def,
    resolve_chapter_footnotes,
)

HAN_RE = re.compile(r"[\u4e00-\u9fff]")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
INLINE_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
HR_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
QUOTE_RE = re.compile(r"^>\s?(.*)$")
LIST_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+(.*)$")
FOOTNOTE_RE = re.compile(r"^\[\^([^\]]+)\]:\s*(.*)$")
CODE_FENCE_RE = re.compile(r"^```(\S*)\s*$")
TABLE_ROW_RE = re.compile(r"^\|.*\|\s*$")
TABLE_ALIGN_RE = re.compile(r":?-{1,}:?")
FRONT_MATTER_LIMIT = 60
# 块号生成口径的版本号：解析规则一变，旧 chapter_map / chapter_tool 缓存必须失效，
# 否则会拿旧块号去查新块，静默错位。改块号生成规则时递增。
BLOCK_VERSION = 2

_HTML_TAG_RE = re.compile(
    r"</?(?:sup|sub|span|div|a|br|i|b|u|em|strong|small|big|font|p|td|tr|th|tbody|thead|table|img)\b[^>]*>",
    re.I,
)
_PANDOC_CALIBRE_RE = re.compile(r"\s*\{[^{}\n]*\.calibre[^{}\n]*\}")
_HTML_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table\s*>", re.I | re.S)
_HTML_TABLE_LIMIT_ROWS = 300
_HTML_TABLE_LIMIT_COLS = 30
_HTML_TABLE_LIMIT_CELL = 400
_HTML_TABLE_LIMIT_PROJECTION = 20000

# 行内脚注图标先折成私有区占位符，句子合并后再物化成 fn_refs。
_ICON_OPEN = "\ue100"
_ICON_CLOSE = "\ue101"
_ICON_TOKEN_RE = re.compile(re.escape(_ICON_OPEN) + r"(\d+)" + re.escape(_ICON_CLOSE))
_ICON_STEM_RE = re.compile(r"^(?:note|footnote|tip|icon|fn)[_-]?\d*$", re.I)
_INLINE_ICON_MAX_PX = 240


def han_count(text):
    return len(HAN_RE.findall(text or ""))


def non_space_count(text):
    """非空白字符数。``str.split()`` 的空白集与 str 的 ``\\s`` 完全一致，比 re.sub 快一倍。"""
    return len("".join((text or "").split()))


def _line_at(text, start):
    """返回 (该行, 下一个行首或 -1)。不 split 全文。"""
    end = text.find("\n", start)
    if end < 0:
        return text[start:], -1
    return text[start:end], end + 1


def _strip_front_matter_with_lines(raw):
    """返回 (meta, body, consumed_lines)。首行 ``---`` 起，最多 60 行内闭合。"""
    text = raw or ""
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    first, pos = _line_at(text, 0)
    if first.strip() != "---" or pos < 0:
        return {}, text, 0
    meta = {}
    meta_start = pos
    for _ in range(1, FRONT_MATTER_LIMIT):
        line, next_pos = _line_at(text, pos)
        if line.strip() == "---":
            block = text[meta_start:pos]
            if block.endswith("\n"):
                block = block[:-1]
            for item in block.split("\n") if block else ():
                if ":" not in item:
                    continue
                key, value = item.split(":", 1)
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if key:
                    meta[key] = value
            if next_pos >= 0:
                return meta, text[next_pos:], text[:next_pos].count("\n")
            return meta, "", text.count("\n") + 1
        if next_pos < 0:
            break
        pos = next_pos
    return {}, text, 0


def strip_front_matter(raw):
    """返回 (meta, body)。首行 ``---`` 起，最多 60 行内闭合。"""
    meta, body, _ = _strip_front_matter_with_lines(raw)
    return meta, body


def _take_h1_with_lines(body):
    """摘掉第一个 H1，返回 (h1_text, 其余正文, consumed_lines)。"""
    pos = 0
    while True:
        line, next_pos = _line_at(body, pos)
        stripped = line.strip().lstrip("\ufeff")
        if not stripped:
            if next_pos < 0:
                return "", body, 0
            pos = next_pos
            continue
        if stripped.startswith("# "):
            if next_pos >= 0:
                return stripped[2:].strip(), body[next_pos:], body[:next_pos].count("\n")
            return stripped[2:].strip(), "", body.count("\n") + 1
        return "", body, 0


def _take_h1(body):
    """摘掉第一个 H1，返回 (h1_text, 其余正文)。"""
    h1, rest, _ = _take_h1_with_lines(body)
    return h1, rest


def _clean_inline(text):
    """清行内强调、HTML 内联标签/实体与 Pandoc 属性，保留正文用字。"""
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = INLINE_IMAGE_RE.sub("", text)
    text = INLINE_LINK_RE.sub(r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    if "<" in text:
        text = _HTML_TAG_RE.sub("", text)
    text = _PANDOC_CALIBRE_RE.sub("", text)
    if "&" in text:
        text = html.unescape(text)
    return re.sub(r"[ \t\u00a0]+", " ", text).strip()


def _clean_list_item(text):
    return _clean_inline(text).replace("**", "").lstrip("*").strip()


def _icon_ref(rel):
    stem = unquote(rel.rsplit("/", 1)[-1]).rsplit(".", 1)[0].strip() or "note"
    digits = re.search(r"(\d{1,3})", stem)
    return {
        "marker": digits.group(1) if digits else stem,
        "_style": "icon",
        "target_file": "",
    }


def _image_path(rel, base_dir):
    if not base_dir:
        return None
    root = os.path.normpath(base_dir)
    path = os.path.normpath(os.path.join(root, unquote(rel)))
    prefix = root if root.endswith(os.sep) else root + os.sep
    if not path.startswith(prefix):
        return None
    return path


def _fold_inline_icons(text, base_dir=None):
    """把夹在句子中的脚注图标折成占位符，返回 ``(text, icon_refs)``。

    同时吃掉紧贴图标的 ``^``、``[ ]()`` 包装，避免留下 `^` 或 `](` 碎渣。
    """
    out: list[str] = []
    refs: list[dict] = []
    pos = 0
    for match in IMAGE_RE.finditer(text):
        start, end = match.span()
        alt, rel = match.group(1), match.group(2)
        inline = bool(text[:start].strip()) and bool(text[end:].strip())
        if _image_kind(rel, base_dir, inline=inline, alt=alt) != "footnote":
            continue
        left, right = start, end
        if left > 0 and text[left - 1] == "^":
            left -= 1
        if right < len(text) and text[right] == "^":
            right += 1
        if left > 0 and text[left - 1] == "[" and text.startswith("]()", right):
            left -= 1
            right += 3
            if right < len(text) and text[right] == "^":
                right += 1
        out.append(text[pos:left])
        out.append("%s%d%s" % (_ICON_OPEN, len(refs), _ICON_CLOSE))
        refs.append(_icon_ref(rel))
        pos = right
    if not refs:
        return text, []
    out.append(text[pos:])
    return "".join(out), refs


def _materialize_icon_refs(cleaned, icon_refs):
    """把占位符从文本里去掉，按出现位置生成 fn_refs。"""
    entries: list[dict] = []
    pieces: list[str] = []
    cursor = 0
    out_len = 0
    for match in _ICON_TOKEN_RE.finditer(cleaned):
        piece = cleaned[cursor : match.start()]
        pieces.append(piece)
        out_len += len(piece)
        idx = int(match.group(1))
        if 0 <= idx < len(icon_refs):
            entry = dict(icon_refs[idx])
            entry["pos"] = out_len
            entries.append(entry)
        cursor = match.end()
    pieces.append(cleaned[cursor:])
    return "".join(pieces), entries


def _finish_inline(text, icon_refs=None, cleaner=_clean_inline):
    cleaned, fn_refs = extract_inline_footnotes(text.strip(), cleaner)
    if icon_refs:
        cleaned, icon_entries = _materialize_icon_refs(cleaned, icon_refs)
        fn_refs = sorted([*fn_refs, *icon_entries], key=lambda ref: int(ref.get("pos") or 0))
    return cleaned, fn_refs


def _append(blocks, kind, text, icon_refs=None, **extra):
    cleaned, fn_refs = _finish_inline(text, icon_refs)
    block = {"id": "B%02d" % (len(blocks) + 1), "kind": kind, "text": cleaned, "chars": han_count(cleaned)}
    if fn_refs:
        block["fn_refs"] = fn_refs
    block.update(extra)
    blocks.append(block)


def _append_raw(blocks, kind, text, **extra):
    """不经行内清理的块：代码、表格单元格等原文保留文本。"""
    block = {"id": "B%02d" % (len(blocks) + 1), "kind": kind, "text": text, "chars": han_count(text)}
    block.update(extra)
    blocks.append(block)


def _table_row(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table_align_row(cells):
    return bool(cells) and all(TABLE_ALIGN_RE.fullmatch(cell or "") for cell in cells)


def _append_table(blocks, lines, **span):
    grid = [_table_row(line) for line in lines]
    head, align, rows = [], [], grid
    if len(grid) >= 2 and _table_align_row(grid[1]):
        head = grid[0]
        align = grid[1]
        rows = grid[2:]
    flat = [cell for row in ([head] if head else []) + rows for cell in row if cell]
    extra = {"rows": rows}
    if head:
        extra["head"] = head
    if align:
        extra["align"] = align
    extra.update(span)
    _append_raw(blocks, "table", " ".join(flat), **extra)


class _HTMLTableParser(HTMLParser):
    """只抽表格结构：嵌套表格忽略内层，单元格文本按标签边界折叠。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[tuple[bool, list[str]]]] = []
        self._depth = 0
        self._rows: list[tuple[bool, list[str]]] | None = None
        self._row: list[str] | None = None
        self._row_has_head = False
        self._cell: list[str] | None = None
        self._cell_is_head = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            if self._depth == 0:
                self._rows = []
            self._depth += 1
            return
        if self._depth != 1:
            return
        if tag == "tr":
            self._row = []
            self._row_has_head = False
        elif tag in ("td", "th"):
            self._cell = []
            self._cell_is_head = tag == "th"
            if tag == "th":
                self._row_has_head = True
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "table":
            if self._depth == 1 and self._rows is not None:
                self.tables.append(self._rows)
                self._rows = None
            self._depth = max(0, self._depth - 1)
            return
        if self._depth != 1:
            return
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            cell = " ".join("".join(self._cell).split())
            self._row.append(cell[:_HTML_TABLE_LIMIT_CELL])
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._rows is not None and self._row:
                self._rows.append((self._row_has_head, self._row[:_HTML_TABLE_LIMIT_COLS]))
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _parse_html_tables(text):
    parser = _HTMLTableParser()
    try:
        parser.feed(text)
        parser.close()
    except (ValueError, AssertionError):
        return []
    return parser.tables


def _append_html_table(blocks, text, **span):
    grids = _parse_html_tables(text)
    if not grids:
        return False
    for grid in grids:
        head: list[str] = []
        rows: list[list[str]] = []
        for is_head, row in grid:
            if is_head and not head:
                head = row
                continue
            rows.append(row)
        truncated = len(rows) > _HTML_TABLE_LIMIT_ROWS
        rows = rows[:_HTML_TABLE_LIMIT_ROWS]
        flat = [cell for row in ([head] if head else []) + rows for cell in row if cell]
        projection = " ".join(flat)
        if len(projection) > _HTML_TABLE_LIMIT_PROJECTION:
            projection = projection[:_HTML_TABLE_LIMIT_PROJECTION] + "…"
        elif truncated:
            projection += "…"
        extra = {"rows": rows}
        if head:
            extra["head"] = head
        if truncated:
            extra["truncated"] = True
        _append_raw(blocks, "table", projection, **extra, **span)
    return True


def _image_size_bytes(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _image_kind(rel, base_dir=None, *, inline=False, alt=""):
    """判断图片是不是脚注图标。

    文件名规则优先（note/note_2..6/footnote/tip/icon/fn），其次看行内上下文：
    句中被引用、磁盘存在且最长边 ≤240px、体积 <200KB 的小图按图标处理。
    正文里正常的图表随图一般成块出现，不会被行内规则吃掉。
    """
    stem = unquote(rel.rsplit("/", 1)[-1]).rsplit(".", 1)[0].strip()
    if _ICON_STEM_RE.fullmatch(stem):
        return "footnote"
    if stem.lower().endswith(("_icon", "-icon")):
        return "footnote"
    if not inline or not base_dir:
        return "image"
    alt_text = (alt or "").strip().lower()
    if alt_text not in ("note", "footnote", "icon", "tip", "脚注", "注"):
        return "image"
    path = _image_path(rel, base_dir)
    if not path:
        return "image"
    size = _image_size_bytes(path)
    if size is None or size >= 200 * 1024:
        return "image"
    dims = image_size(path)
    if dims and max(dims) <= _INLINE_ICON_MAX_PX:
        return "footnote"
    return "image"


def _jpeg_size(data):
    """JPEG 的 SOF 段给画布尺寸，EXIF/ICC 段按长度跳过。"""
    pos = 2
    size = len(data)
    while pos + 4 <= size:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker == 0xDA:
            return None
        seg_len = int.from_bytes(data[pos + 2 : pos + 4], "big")
        if seg_len < 2:
            return None
        if 0xC0 <= marker <= 0xC2:
            if pos + 9 > size:
                return None
            height = int.from_bytes(data[pos + 5 : pos + 7], "big")
            width = int.from_bytes(data[pos + 7 : pos + 9], "big")
            return (width, height)
        pos += 2 + seg_len
    return None


def _svg_size(data):
    text = data.decode("utf-8", "ignore")
    match = re.search(r"<svg\b[^>]*>", text, re.I)
    if not match:
        return None
    tag = match.group(0)

    def dim(name):
        found = re.search(r'\b%s\s*=\s*"([\d.]+)(?:px)?"' % name, tag, re.I)
        return float(found.group(1)) if found else None

    width, height = dim("width"), dim("height")
    if width and height:
        return (round(width), round(height))
    box = re.search(r'\bviewBox\s*=\s*"([^"]+)"', tag, re.I)
    if box:
        parts = box.group(1).replace(",", " ").split()
        if len(parts) == 4:
            try:
                width, height = float(parts[2]), float(parts[3])
            except ValueError:
                return None
            if width > 0 and height > 0:
                return (round(width), round(height))
    return None


def image_size(path):
    """读图片头拿像素尺寸。读不出返回 ``None``，不抛异常。"""
    try:
        with open(path, "rb") as fh:
            head = fh.read()
    except OSError:
        return None
    try:
        if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
            return struct.unpack(">II", head[16:24])
        if head[:6] in (b"GIF87a", b"GIF89a") and len(head) >= 10:
            return struct.unpack("<HH", head[6:10])
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            fourcc = head[12:16]
            if fourcc == b"VP8X" and len(head) >= 30:
                return (
                    int.from_bytes(head[24:27], "little") + 1,
                    int.from_bytes(head[27:30], "little") + 1,
                )
            if fourcc == b"VP8 " and len(head) >= 30:
                return (
                    int.from_bytes(head[26:28], "little") & 0x3FFF,
                    int.from_bytes(head[28:30], "little") & 0x3FFF,
                )
            if fourcc == b"VP8L" and len(head) >= 25:
                bits = int.from_bytes(head[21:25], "little")
                return (1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF))
            return None
        if head[:2] == b"\xff\xd8":
            return _jpeg_size(head)
        if b"<svg" in head[:1024].lower():
            return _svg_size(head)
    except (struct.error, IndexError):
        return None
    return None


def _image_extra(rel, base_dir):
    path = _image_path(rel, base_dir)
    if not path:
        return {}
    size = image_size(path)
    if not size:
        return {}
    return {"width": size[0], "height": size[1]}


def _emit_inline(blocks, text, base_dir=None, **span):
    """段落里可能夹着图片；脚注图标并入句子，其余图片单独成块。"""
    folded, icon_refs = _fold_inline_icons(text, base_dir)
    consumed = 0

    def take_icons(segment):
        nonlocal consumed
        count = segment.count(_ICON_OPEN)
        refs = icon_refs[consumed : consumed + count]
        consumed += count
        return refs

    pos = 0
    for match in IMAGE_RE.finditer(folded):
        before = folded[pos : match.start()]
        if before.strip():
            _append(blocks, "paragraph", before, icon_refs=take_icons(before), **span)
        alt, rel = match.group(1), match.group(2)
        _append(blocks, "image", alt, rel=rel, **_image_extra(rel, base_dir), **span)
        pos = match.end()
    tail = folded[pos:]
    if tail.strip():
        _append(blocks, "paragraph", tail, icon_refs=take_icons(tail), **span)


def _heading_text(text):
    """标题里的粗体、HTML 标签与实体是排版噪声；去掉并折叠空格。"""
    return " ".join(_clean_inline(text.replace("**", "")).split())


def _parse_chunk(blocks, chunk, base_dir=None, *, start_line=0, end_line=0):
    span = {"start_line": start_line, "end_line": end_line} if start_line > 0 else {}
    lines = [line for line in chunk.split("\n") if line.strip()]
    if not lines:
        return
    if all(HR_RE.match(line.strip()) for line in lines):
        return

    if _HTML_TABLE_RE.search(chunk):
        cursor = 0
        matched = False
        for table_match in _HTML_TABLE_RE.finditer(chunk):
            before = chunk[cursor : table_match.start()]
            if before.strip():
                _emit_inline(blocks, " ".join(before.split("\n")).strip(), base_dir, **span)
            if _append_html_table(blocks, table_match.group(0), **span):
                matched = True
            cursor = table_match.end()
        tail = chunk[cursor:]
        if tail.strip():
            _emit_inline(blocks, " ".join(tail.split("\n")).strip(), base_dir, **span)
        if matched:
            return

    heading = HEADING_RE.match(lines[0].strip())
    if heading and len(lines) == 1:
        _append(blocks, "heading", _heading_text(heading.group(2)), level=len(heading.group(1)), **span)
        return

    footnote = FOOTNOTE_RE.match(lines[0].strip())
    if footnote:
        rest = " ".join([footnote.group(2).strip()] + [ln.strip() for ln in lines[1:]]).strip()
        _append(blocks, "footnote", rest, marker=footnote.group(1), **span)
        return

    joined = " ".join(line.strip() for line in lines)
    explicit_def = parse_explicit_footnote_def(joined)
    if explicit_def:
        extra = {
            "marker": explicit_def["display"],
            "_key": explicit_def["key"],
            "_def_kind": explicit_def["def_kind"],
        }
        if explicit_def["back_file"]:
            extra["back_file"] = explicit_def["back_file"]
        _append(blocks, "footnote", explicit_def["body"], **extra, **span)
        return

    if all(QUOTE_RE.match(line.strip()) for line in lines):
        text = " ".join((QUOTE_RE.match(line.strip()).group(1) or "").strip() for line in lines)
        if text.startswith("`") and text.endswith("`") and "`" not in text[1:-1]:
            _append_raw(blocks, "code", text[1:-1].strip(), **span)
        else:
            _append(blocks, "quote", text, **span)
        return

    if all(LIST_RE.match(line.strip()) for line in lines):
        items = []
        item_fn_refs = []
        for line in lines:
            raw_item = LIST_RE.match(line.strip()).group(1).strip()
            folded_item, item_icons = _fold_inline_icons(raw_item, base_dir)
            cleaned_item, refs = _finish_inline(folded_item, item_icons, cleaner=_clean_list_item)
            items.append(cleaned_item)
            item_fn_refs.append(refs)
        extra = {"items": items}
        if any(item_fn_refs):
            extra["item_fn_refs"] = item_fn_refs
        _append_raw(blocks, "list", " ".join(items), **extra, **span)
        return

    rows = [line for line in lines if TABLE_ROW_RE.match(line)]
    if len(rows) == len(lines):
        _append_table(blocks, lines, **span)
        return

    only = IMAGE_RE.fullmatch(chunk.strip())
    if only:
        alt, rel = only.group(1), only.group(2)
        kind = _image_kind(rel)
        extra = _image_extra(rel, base_dir) if kind == "image" else {}
        _append(blocks, kind, alt, rel=rel, **extra, **span)
        return

    _emit_inline(blocks, " ".join(line.strip() for line in lines), base_dir, **span)


def _segments(body="", base_line=1):
    """把正文切成 ("md", 文本, 起始行, 结束行) 与 ("code", (lang, 文本), 起始行, 结束行) 段，保序。

    代码块内的空行不切块；围栏行本身不进 text。
    """
    lines = body.split("\n")
    segs, buf = [], []
    buf_start = None
    i = 0
    while i < len(lines):
        fence = CODE_FENCE_RE.match(lines[i].strip())
        if fence:
            if buf:
                segs.append(("md", "\n".join(buf), buf_start, base_line + i - 1))
                buf = []
                buf_start = None
            fence_start = base_line + i
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            fence_end = base_line + min(i, len(lines) - 1)
            i += 1
            segs.append(("code", (fence.group(1), "\n".join(code).strip("\n")), fence_start, fence_end))
        else:
            if buf_start is None:
                buf_start = base_line + i
            buf.append(lines[i])
            i += 1
    if buf:
        segs.append(("md", "\n".join(buf), buf_start, base_line + len(lines) - 1))
    return segs


def _iter_md_chunks(seg, seg_start_line):
    """按空行切块并返回 ``(chunk_text, start_line, end_line)``。"""
    lines = seg.split("\n")
    buf = []
    buf_start = None
    for offset, line in enumerate(lines):
        if not line.strip():
            if buf:
                yield "\n".join(buf), buf_start, seg_start_line + offset - 1
                buf = []
                buf_start = None
        else:
            if buf_start is None:
                buf_start = seg_start_line + offset
            buf.append(line)
    if buf:
        yield "\n".join(buf), buf_start, seg_start_line + len(lines) - 1


def _sections(blocks):
    """按 H1/H2 分结：返回 [{id,title,start,end}]，end 为排他下标。

    首个 H2 之前的块是没有标题的首结；通篇无小标题时只有一个结。
    """
    marks = [i for i, b in enumerate(blocks) if b["kind"] == "heading" and b.get("level", 6) <= 2]
    if not marks:
        return [{"id": "S01", "title": "", "start": 0, "end": len(blocks)}]
    sections = []
    if marks[0] > 0:
        sections.append({"id": "S%02d" % (len(sections) + 1), "title": "", "start": 0, "end": marks[0]})
    for j, i in enumerate(marks):
        end = marks[j + 1] if j + 1 < len(marks) else len(blocks)
        sections.append({"id": "S%02d" % (len(sections) + 1), "title": blocks[i]["text"], "start": i, "end": end})
    return sections


def _attach_orphan_footnotes(blocks):
    """把正文末尾的注释列表回填到空的 footnote 块。

    有些书的脚注不是 `[^1]: ...` 定义，而是在章末用列表集中列出；
    解析器会先得到若干空 footnote 块。这里按 marker 优先、顺序兜底做
    宽松回填：只要列表条目数不少于空脚注数就回填，多余的条目留在列表里。
    """
    orphans = [block for block in blocks if block["kind"] == "footnote" and not block["text"].strip()]
    if not orphans or not blocks or blocks[-1]["kind"] != "list":
        return
    items = list(blocks[-1].get("items") or [])
    if len(items) < len(orphans):
        return
    used: set[int] = set()
    for orphan in orphans:
        marker = str(orphan.get("marker") or "")
        _, key = normalize_marker(marker)
        chosen = None
        if key:
            for idx, item in enumerate(items):
                if idx in used:
                    continue
                info = _match_tier2_prefix(str(item))
                if info and key in _marker_alt_keys(info[4]):
                    chosen = idx
                    break
        if chosen is None:
            for idx in range(len(items)):
                if idx not in used:
                    chosen = idx
                    break
        if chosen is None:
            break
        text = items[chosen]
        info = _match_tier2_prefix(text)
        if info:
            text = text[info[3] :]
        else:
            plain = _PLAIN_ENDNOTE_LINE_RE.match(text)
            if plain:
                text = text[plain.end(1) :].lstrip()
        orphan["text"] = text
        orphan["chars"] = han_count(text)
        used.add(chosen)
    keep = [idx for idx in range(len(items)) if idx not in used]
    if not keep:
        blocks.pop()
        return
    blocks[-1]["items"] = [items[idx] for idx in keep]
    refs = blocks[-1].get("item_fn_refs")
    if isinstance(refs, list):
        blocks[-1]["item_fn_refs"] = [refs[idx] for idx in keep if idx < len(refs)]


def parse_markdown(raw, stem="", meta=None, base_dir=None, source_files=(), note_chapters=()):
    """解析一章。返回 ``dict``：title/blocks/sections/body_han/body_chars/meta``。

    ``source_files`` / ``note_chapters`` 透传给脚注配对，用于识别跨文件注释章；
    单章直接调用时留空即可。
    """
    parsed_meta, body, fm_lines = _strip_front_matter_with_lines(raw)
    merged = dict(parsed_meta)
    if meta:
        merged.update(meta)
    h1, rest, h1_lines = _take_h1_with_lines(body)
    base_line = 1 + fm_lines + h1_lines
    blocks = []
    for seg_kind, seg, seg_start, seg_end in _segments(rest, base_line=base_line):
        if seg_kind == "code":
            lang, text = seg
            extra = {"start_line": seg_start, "end_line": seg_end}
            if lang:
                extra["lang"] = lang
            _append_raw(blocks, "code", text, **extra)
        else:
            for chunk, chunk_start, chunk_end in _iter_md_chunks(seg, seg_start):
                _parse_chunk(blocks, chunk, base_dir, start_line=chunk_start, end_line=chunk_end)
    _attach_orphan_footnotes(blocks)
    _promote_trailing_note_list(blocks)
    resolve_chapter_footnotes(blocks, source_files=source_files, note_chapters=note_chapters)
    title = _clean_inline(merged.get("title") or h1 or stem or "")
    body_han = sum(block["chars"] for block in blocks)
    body_chars = sum(non_space_count(block["text"]) for block in blocks)
    return {
        "title": title,
        "sections": _sections(blocks),
        "meta": merged,
        "blocks": blocks,
        "body_han": body_han,
        "body_chars": body_chars,
        "h1": h1,
    }


def chapter_title(raw, stem=""):
    """只读 front matter 与首个 H1，用于章表（不建块）。"""
    meta, body = strip_front_matter(raw)
    h1, _ = _take_h1(body)
    return _clean_inline(meta.get("title") or h1 or stem or "")


def lean_meta(raw):
    """章表只需要的三个数：标题、正文非空白字数、含标题的非空白字数。

    与 ``Library._read_meta`` 逐字段一致，只为避免同一份 raw 被 strip 两次。
    """
    text = raw or ""
    meta, body = strip_front_matter(text)
    h1, rest = _take_h1(body)
    chars = non_space_count(rest)
    # body = 标题行 + rest（_take_h1 只切后缀），只补算被摘掉的标题行，不再扫一遍全文。
    prefix = body[: len(body) - len(rest)]
    return {
        "title": _clean_inline(meta.get("title") or h1 or ""),
        "chars": chars,
        "full_chars": chars + non_space_count(prefix),
    }
