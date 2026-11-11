"""Markdown 章节解析：front matter、标题与稳定的源文块。

解析只做两件事：按空行切块、把段落内换行并成一个空格。块文本不做改写，
块 ``chars`` 是汉字数（``[\\u4e00-\\u9fff]``），章节 ``chars`` 是非空白字符数。
图片 ``images/note.png`` 是脚注图标，不渲染大图；其余图片保留相对路径。
"""

import os
import re
import struct
from urllib.parse import unquote

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


def han_count(text):
    return len(HAN_RE.findall(text or ""))


def non_space_count(text):
    return len(re.sub(r"\s", "", text or ""))


def strip_front_matter(raw):
    """返回 (meta, body)。首行 ``---`` 起，最多 60 行内闭合。"""
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    meta = {}
    if lines and lines[0].strip() == "---":
        end = None
        for i in range(1, min(len(lines), FRONT_MATTER_LIMIT)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is not None:
            for line in lines[1:end]:
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if key:
                    meta[key] = value
            lines = lines[end + 1 :]
    return meta, "\n".join(lines)


def _take_h1(body):
    """摘掉第一个 H1，返回 (h1_text, 其余正文)。"""
    lines = body.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip().lstrip("\ufeff")
        if not stripped:
            continue
        if stripped.startswith("# "):
            return stripped[2:].strip(), "\n".join(lines[i + 1 :])
        break
    return "", body


def _clean_inline(text):
    """只清行内强调标记，保留正文用字。"""
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = INLINE_IMAGE_RE.sub("", text)
    text = INLINE_LINK_RE.sub(r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    return re.sub(r"`([^`]+)`", r"\1", text)


def _append(blocks, kind, text, **extra):
    text = _clean_inline(text.strip())
    block = {"id": "B%02d" % (len(blocks) + 1), "kind": kind, "text": text, "chars": han_count(text)}
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


def _append_table(blocks, lines):
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
    _append_raw(blocks, "table", " ".join(flat), **extra)


def _image_kind(rel):
    return "footnote" if rel.rsplit("/", 1)[-1].lower() == "note.png" else "image"


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
    if not base_dir:
        return {}
    root = os.path.normpath(base_dir)
    path = os.path.normpath(os.path.join(root, unquote(rel)))
    prefix = root if root.endswith(os.sep) else root + os.sep
    if not path.startswith(prefix):
        return {}
    size = image_size(path)
    if not size:
        return {}
    return {"width": size[0], "height": size[1]}


def _emit_inline(blocks, text, base_dir=None):
    """段落里可能夹着图片；图片单独成块，其余文字保住顺序。"""
    pos = 0
    for match in IMAGE_RE.finditer(text):
        before = text[pos : match.start()]
        if before.strip():
            _append(blocks, "paragraph", before)
        alt, rel = match.group(1), match.group(2)
        kind = _image_kind(rel)
        if kind == "footnote":
            _append(blocks, "footnote", alt, rel=rel)
        else:
            _append(blocks, "image", alt, rel=rel, **_image_extra(rel, base_dir))
        pos = match.end()
    tail = text[pos:]
    if tail.strip():
        _append(blocks, "paragraph", tail)


def _heading_text(text):
    """标题里的粗体标记是排版噪声；去掉并折叠空格。"""
    return " ".join(text.replace("**", "").split())


def _parse_chunk(blocks, chunk, base_dir=None):
    lines = [line for line in chunk.split("\n") if line.strip()]
    if not lines:
        return
    if all(HR_RE.match(line.strip()) for line in lines):
        return

    heading = HEADING_RE.match(lines[0].strip())
    if heading and len(lines) == 1:
        _append(blocks, "heading", _heading_text(heading.group(2)), level=len(heading.group(1)))
        return

    footnote = FOOTNOTE_RE.match(lines[0].strip())
    if footnote:
        _append(blocks, "footnote", footnote.group(2), marker=footnote.group(1))
        return

    if all(QUOTE_RE.match(line.strip()) for line in lines):
        text = " ".join((QUOTE_RE.match(line.strip()).group(1) or "").strip() for line in lines)
        if text.startswith("`") and text.endswith("`") and "`" not in text[1:-1]:
            _append_raw(blocks, "code", text[1:-1].strip())
        else:
            _append(blocks, "quote", text)
        return

    if all(LIST_RE.match(line.strip()) for line in lines):
        items = [
            _clean_inline(LIST_RE.match(line.strip()).group(1).strip()).replace("**", "").lstrip("*").strip()
            for line in lines
        ]
        _append(blocks, "list", " ".join(items), items=items)
        return

    rows = [line for line in lines if TABLE_ROW_RE.match(line)]
    if len(rows) == len(lines):
        _append_table(blocks, lines)
        return

    only = IMAGE_RE.fullmatch(chunk.strip())
    if only:
        alt, rel = only.group(1), only.group(2)
        kind = _image_kind(rel)
        extra = _image_extra(rel, base_dir) if kind == "image" else {}
        _append(blocks, kind, alt, rel=rel, **extra)
        return

    _emit_inline(blocks, " ".join(line.strip() for line in lines), base_dir)


def _segments(body=""):
    """把正文切成 ("md", 文本) 与 ("code",  (lang, 文本)) 段，保序。

    代码块内的空行不切块；围栏行本身不进 text。
    """
    lines = body.split("\n")
    segs, buf = [], []
    i = 0
    while i < len(lines):
        fence = CODE_FENCE_RE.match(lines[i].strip())
        if fence:
            if buf:
                segs.append(("md", "\n".join(buf)))
                buf = []
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            segs.append(("code", (fence.group(1), "\n".join(code).strip("\n"))))
        else:
            buf.append(lines[i])
            i += 1
    if buf:
        segs.append(("md", "\n".join(buf)))
    return segs


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
    解析器会先得到若干空 footnote 块。若末尾列表条目数与空脚注数
    完全一致，就按顺序回填并移除该列表。
    """
    orphans = [block for block in blocks if block["kind"] == "footnote" and not block["text"].strip()]
    if not orphans or not blocks or blocks[-1]["kind"] != "list":
        return
    items = blocks[-1].get("items") or []
    if len(items) != len(orphans):
        return
    for block, text in zip(orphans, items, strict=True):
        block["text"] = text
        block["chars"] = han_count(text)
    blocks.pop()


def parse_markdown(raw, stem="", meta=None, base_dir=None):
    """解析一章。返回 ``dict``：title/blocks/sections/body_han/body_chars/meta``。"""
    parsed_meta, body = strip_front_matter(raw)
    merged = dict(parsed_meta)
    if meta:
        merged.update(meta)
    h1, rest = _take_h1(body)
    blocks = []
    for seg_kind, seg in _segments(rest):
        if seg_kind == "code":
            lang, text = seg
            extra = {} if not lang else {"lang": lang}
            _append_raw(blocks, "code", text, **extra)
        else:
            for chunk in re.split(r"\n[ \t]*\n", seg):
                if chunk.strip():
                    _parse_chunk(blocks, chunk, base_dir)
    _attach_orphan_footnotes(blocks)
    title = (merged.get("title") or h1 or stem or "").strip()
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
    return (meta.get("title") or h1 or stem or "").strip()
