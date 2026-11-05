"""Markdown 章节解析：front matter、标题与稳定的源文块。

解析只做两件事：按空行切块、把段落内换行并成一个空格。块文本不做改写，
块 ``chars`` 是汉字数（``[\\u4e00-\\u9fff]``），章节 ``chars`` 是非空白字符数。
图片 ``images/note.png`` 是脚注图标，不渲染大图；其余图片保留相对路径。
"""

import re

HAN_RE = re.compile(r"[\u4e00-\u9fff]")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
QUOTE_RE = re.compile(r"^>\s?(.*)$")
LIST_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+(.*)$")
FOOTNOTE_RE = re.compile(r"^\[\^([^\]]+)\]:\s*(.*)$")
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
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    return re.sub(r"`([^`]+)`", r"\1", text)


def _append(blocks, kind, text, **extra):
    text = _clean_inline(text.strip())
    block = {"id": "B%02d" % (len(blocks) + 1), "kind": kind, "text": text, "chars": han_count(text)}
    block.update(extra)
    blocks.append(block)


def _image_kind(rel):
    return "footnote" if rel.rsplit("/", 1)[-1].lower() == "note.png" else "image"


def _emit_inline(blocks, text):
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
            _append(blocks, "image", alt, rel=rel)
        pos = match.end()
    tail = text[pos:]
    if tail.strip():
        _append(blocks, "paragraph", tail)


def _parse_chunk(blocks, chunk):
    lines = [line for line in chunk.split("\n") if line.strip()]
    if not lines:
        return

    heading = HEADING_RE.match(lines[0].strip())
    if heading and len(lines) == 1:
        _append(blocks, "heading", heading.group(2), level=len(heading.group(1)))
        return

    footnote = FOOTNOTE_RE.match(lines[0].strip())
    if footnote:
        _append(blocks, "footnote", footnote.group(2), marker=footnote.group(1))
        return

    if all(QUOTE_RE.match(line.strip()) for line in lines):
        text = " ".join((QUOTE_RE.match(line.strip()).group(1) or "").strip() for line in lines)
        _append(blocks, "quote", text)
        return

    if all(LIST_RE.match(line.strip()) for line in lines):
        items = [LIST_RE.match(line.strip()).group(1).strip() for line in lines]
        _append(blocks, "list", " ".join(items))
        return

    only = IMAGE_RE.fullmatch(chunk.strip())
    if only:
        alt, rel = only.group(1), only.group(2)
        _append(blocks, _image_kind(rel), alt, rel=rel)
        return

    _emit_inline(blocks, " ".join(line.strip() for line in lines))


def parse_markdown(raw, stem="", meta=None):
    """解析一章。返回 ``dict``：title/blocks/body_han/body_chars/meta``。"""
    parsed_meta, body = strip_front_matter(raw)
    merged = dict(parsed_meta)
    if meta:
        merged.update(meta)
    h1, rest = _take_h1(body)
    blocks = []
    for chunk in re.split(r"\n[ \t]*\n", rest):
        if chunk.strip():
            _parse_chunk(blocks, chunk)
    title = (merged.get("title") or h1 or stem or "").strip()
    body_han = sum(block["chars"] for block in blocks)
    body_chars = sum(non_space_count(block["text"]) for block in blocks)
    return {
        "title": title,
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
