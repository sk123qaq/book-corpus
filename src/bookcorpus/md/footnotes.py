"""Markdown 脚注解析、章内配对与跨文件提示元数据。"""

from __future__ import annotations

import posixpath
import re
import unicodedata
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import unquote

HAN_RE = re.compile(r"[\u4e00-\u9fff]")

_CIRCLED_MAP: dict[str, str] = {}
for _idx, _cp in enumerate(range(0x2460, 0x2474), 1):  # ①..⑳
    _CIRCLED_MAP[chr(_cp)] = str(_idx)
for _idx, _cp in enumerate(range(0x3251, 0x3260), 21):  # ㉑..㉟
    _CIRCLED_MAP[chr(_cp)] = str(_idx)
for _idx, _cp in enumerate(range(0x32B1, 0x32C0), 36):  # ㊱..㊿
    _CIRCLED_MAP[chr(_cp)] = str(_idx)

_CIRCLED_CHARS = "\u2460-\u2473\u3251-\u325f\u32b1-\u32bf"

NOTE_HEADING_RE = re.compile(
    r"^\s*[【\[\(（〔［〈《]?\s*"
    r"(?:"
    r"(?:作者|译者|譯者|编者|編者|校者|校|编|編|译|譯|原)?[注註](?:释|釋)?\s*\d{0,4}"
    r"|(?:原书|全书|本书|本章|本节|各章|分章|附录)\s*(?:脚注|腳注|尾注|注释|註釋|注解|引注|译注|譯注|备注|備註)"
    r"[^\n]{0,20}"
    r"|(?:版本|校勘)[和与及]?(?:注释|註釋)(?:说明|說明)?"
    r"|(?:脚注|腳注|尾注|注释|註釋|注解|引注|译注|譯注|备注|備註)"
    r"|ご?注意"
    r"|notes?|footnotes?|endnotes?"
    r")"
    r"\s*(?:[（(][^）)]*[）)])?"
    r"\s*[】\]\)）〕］〉》]?\s*$",
    re.I,
)

NOTE_CHAPTER_TITLE_RE = re.compile(
    r"^\s*(?:第\s*[0-9一二三四五六七八九十百千]+\s*[章节回部分]\s*[:：·.\s]*|"
    r"chapter\s*\d+\s*[:：·.\s]*|"
    r"附录\s*[0-9A-Za-z一二三四五六七八九十]*\s*[:：·.\s]*|"
    r"appendix\s*[0-9A-Za-z]*\s*[:：·.\s]*)?"
    r"(?:(?:原书|全书|本书|本章|本节|各章|分章|末尾|篇末|numbered)\s*)?"
    r"(?:"
    r"(?:作者|译者|譯者|编者|編者|校者|校|编|編|译|譯|原)?[注註](?:释|釋)?\s*\d{0,4}"
    r"|脚注|腳注|尾注|注释|註釋|注解|引注|译注|譯注|备注|備註|注意"
    r"|(?:版本|校勘)[和与及]?(?:注释|註釋)(?:说明|說明)?"
    r"|notes|footnotes|endnotes"
    r")"
    r"(?:\s*[（(][^）)]*[）)])?\s*[^\n]{0,16}$",
    re.I,
)

_CORE_NUM = r"\d{1,4}(?:[.-]\d{1,3}|[a-zA-Z])?"
_ZHU_CORE = r"(?:卢梭|编者|译|原)?[注註]释?\s*[0-9一二三四五六七八九十*]*"
_CORE_LABEL = rf"(?:{_CORE_NUM}|[a-zA-Z]|[{_CIRCLED_CHARS}]|[*†‡]+|{_ZHU_CORE})"
_BRACKETED_OR_CORE = rf"(?:\[{_CORE_LABEL}\]|[（(]{_CORE_LABEL}[）)]|[〔［【]{_CORE_LABEL}[〕］】]|{_CORE_LABEL})"
_DBL_LABEL = rf"(?:{_CORE_LABEL}|[^\[\]\n]{{1,12}})"
_TARGET = r"(<[^>\n]*>|[^)\n]*?)"
_EMPH_OPEN = r"(?:\*\*|\*)?"
_EMPH_CLOSE = r"(?:\*\*|\*)?"

# 按“最长/最具体优先”排布的行内脚注引用模式；匹配在行内链接清理前执行。
INLINE_FOOTNOTE_RE = re.compile(
    rf"(?P<word_cond>\[\^\[if !supportFootnotes\]\^\[(\d{{1,4}})\]\^\^\]\({_TARGET}\))"
    rf"|(?P<hat_dbl>\^\[\[{_EMPH_OPEN}({_DBL_LABEL}){_EMPH_CLOSE}\.?\]\]\({_TARGET}\)\^)"
    rf"|(?P<dbl_hat>\^\[\^{_EMPH_OPEN}({_BRACKETED_OR_CORE}){_EMPH_CLOSE}\^\]\({_TARGET}\)\^)"
    rf"|(?P<hat_sgl>\^\[{_EMPH_OPEN}({_BRACKETED_OR_CORE}){_EMPH_CLOSE}\.?\]\({_TARGET}\)\^)"
    rf"|(?P<inner_hat>\[\^{_EMPH_OPEN}({_BRACKETED_OR_CORE}){_EMPH_CLOSE}\^\]\({_TARGET}\))"
    rf"|(?P<dbl_br>(?<!\!)\[\[{_EMPH_OPEN}({_DBL_LABEL}){_EMPH_CLOSE}\.?\]\]\({_TARGET}\))"
    rf"|(?P<sgl_link>(?<![!\[\^])\[{_EMPH_OPEN}({_BRACKETED_OR_CORE}){_EMPH_CLOSE}\.?\]\({_TARGET}\))"
    rf"|(?P<std_md>(?<!\[)\[\^([0-9A-Za-z_-]{{1,20}})\](?![:(]))"
    rf"|(?P<bare_sup>(?<!\[)\^(?:\[(?:{_CORE_NUM})\]|[（(](?:{_CORE_NUM})[）)]|[〔［【](?:{_CORE_NUM})[〕］】]|[{_CIRCLED_CHARS}]|(?:{_ZHU_CORE}))\^(?!\]))"
)

_DEF_DBL_BRACKET_RE = re.compile(
    rf"^\[\[{_EMPH_OPEN}({_DBL_LABEL}){_EMPH_CLOSE}[.。:：]?\]\]\({_TARGET}\)(?:\.(?!\.)\s*|、\s*|\s*)(.*)$"
)
_DEF_MD_BACKLINK_RE = re.compile(
    rf"^\[{_EMPH_OPEN}({_BRACKETED_OR_CORE}){_EMPH_CLOSE}[.。:：]?\]\({_TARGET}\)(?:\.(?!\.)\s*|、\s*|\s*)(.+)$"
)
_DEF_CARET_BACKLINK_RE = re.compile(
    rf"^\[\^{_EMPH_OPEN}({_BRACKETED_OR_CORE}){_EMPH_CLOSE}\^[.。:：]?\]\({_TARGET}\)(?:\.(?!\.)\s*|、\s*|\s*)(.+)$"
)
_DEF_STD_MD_RE = re.compile(r"^\[\^([^\]]+)\]:\s*(.*)$")
_PLAIN_ENDNOTE_LINE_RE = re.compile(r"^\s*(\d{1,4})\s+\S")
_TITLE_NORM_RE = re.compile(r"[\W_]+")

# Tier 2 上下文相关的裸前缀：Category A（方括号 / 注N）与 Category B（圆括号 / 圈数字）
_TIER2_A_BRACKET_RE = re.compile(r"^(\[(\d{1,4}(?:[.-]\d{1,3})?)\]\s*)(\S.*)$")
_TIER2_A_FULLBR_RE = re.compile(r"^([〔［【](\d{1,4})[〕］】]\s*)(\S.*)$")
_TIER2_A_DBL_RE = re.compile(r"^(\[\[(\d{1,4})\]\]\(\)\s*)(\S.*)$")
# 中文书常见 `注6脊髓…` 连写；只在段首且后接正文时认。
_TIER2_A_ZHU_RE = re.compile(rf"^(({_ZHU_CORE})(?:[：:.、]\s*|\s+)?)(\S.*)$")
_TIER2_B_PAREN_RE = re.compile(r"^([（(](\d{1,4})[）)]\s*)(\S.*)$")
_TIER2_B_CIRCLED_RE = re.compile(rf"^(([{_CIRCLED_CHARS}])\s*)(\S.*)$")
# Category C：`1.` / `1、` 这类只有落在注释区标题之后才认，避免吃掉有序列表。
_TIER2_C_DOT_RE = re.compile(r"^(\s*(\d{1,4})\s*[.、．]\s*)(\S.*)$")

_BARE_REF_RE = re.compile(
    rf"(?<![0-9A-Za-z])"
    rf"(?:注\s*(?P<zhu>[0-9]{{1,4}})"
    rf"|註\s*(?P<zhu_t>[0-9]{{1,4}})"
    rf"|\[(?P<bracket>[0-9]{{1,4}})\]"
    rf"|〔(?P<fullbr>[0-9]{{1,4}})〕"
    rf"|［(?P<fullwidth>[0-9]{{1,4}})］"
    rf"|【(?P<lenticular>[0-9]{{1,4}})】"
    rf"|(?P<circled>[{_CIRCLED_CHARS}]))"
)

_ALT_ZHU_NUM_RE = re.compile(r"^(?:卢梭|编者|译|原)?[注註]释?\s*(\d+|[*†‡]+)$")

_OUTER_PAIRS = (
    ("[", "]"),
    ("(", ")"),
    ("（", "）"),
    ("〔", "〕"),
    ("［", "］"),
    ("【", "】"),
)


def _han_count(text: str) -> int:
    return len(HAN_RE.findall(text or ""))


def _pick_sentinels(text: str) -> tuple[str, str]:
    for open_tok, close_tok in (("\ue000", "\ue001"), ("\ue010", "\ue011"), ("\ue020", "\ue021")):
        if open_tok not in text and close_tok not in text:
            return open_tok, close_tok
    return "\ue030", "\ue031"


def _clean_target(raw_target: str) -> str:
    target = (raw_target or "").strip()
    if len(target) >= 2 and target[0] == "<" and target[-1] == ">":
        target = target[1:-1].strip()
    target = target.split("#", 1)[0].strip()
    return unquote(target).strip()


def _is_external_url(target: str) -> bool:
    return target.lower().startswith(("http://", "https://", "mailto:"))


def normalize_marker(raw: str) -> tuple[str, str]:
    """将各种括号/星号/全角包裹的注号规整为 ``(display, key)``。"""
    text = (raw or "").strip().strip("*").strip()
    changed = True
    while changed and text:
        changed = False
        for left, right in _OUTER_PAIRS:
            if text.startswith(left) and text.endswith(right) and len(text) > len(left) + len(right):
                text = text[len(left) : -len(right)].strip().strip("*").strip()
                changed = True
                break
    text = re.sub(r"[.。、:：]+$", "", text).strip()
    display = text or (raw or "").strip()

    nfkc = unicodedata.normalize("NFKC", display).strip().lower()
    nfkc = re.sub(r"\s+", "", nfkc)
    key = display if (len(display) == 1 and display in _CIRCLED_MAP) else nfkc
    return display, key


def _marker_alt_keys(key: str) -> list[str]:
    if not key:
        return []
    keys = [key]
    if len(key) == 1 and key in _CIRCLED_MAP:
        num_key = _CIRCLED_MAP[key]
        if num_key not in keys:
            keys.append(num_key)
    m = _ALT_ZHU_NUM_RE.match(key)
    if m:
        core = m.group(1)
        if core and core not in keys:
            keys.append(core)
    return keys


def _infer_style(raw_label: str, display: str) -> str:
    if len(display) == 1 and display in _CIRCLED_MAP:
        return "circled"
    if display.startswith(("注", "註", "卢梭注", "编者注", "译注", "原注")):
        return "zhu"
    if set(display) <= {"*", "†", "‡"}:
        return "star"
    if "(" in raw_label or "（" in raw_label:
        return "paren"
    if "[" in raw_label or "【" in raw_label or "〔" in raw_label or "［" in raw_label:
        return "bracket"
    return "plain"


def _match_inline_fields(match: re.Match[str]) -> tuple[str, str, bool] | None:
    """从正则命中里提取 ``(raw_label, raw_target, is_sgl_link)``。"""
    kind = match.lastgroup
    if not kind:
        return None
    base = match.re.groupindex[kind]
    if kind == "bare_sup":
        return match.group(base)[1:-1], "", False
    if kind == "std_md":
        return match.group(base + 1), "", False
    return match.group(base + 1), match.group(base + 2), kind == "sgl_link"


def extract_inline_footnotes(
    raw_text: str,
    cleaner: Callable[[str], str],
) -> tuple[str, list[dict[str, Any]]]:
    """在清理行内 markdown 之前抽取脚注引用，并在清理后计算精确偏移量。"""
    source = raw_text or ""
    if not source:
        return cleaner(source), []

    open_tok, close_tok = _pick_sentinels(source)
    captured: list[dict[str, Any]] = []

    def _replace(match: re.Match[str]) -> str:
        fields = _match_inline_fields(match)
        if fields is None:
            return match.group(0)
        raw_label, raw_target, is_sgl_link = fields
        target = _clean_target(raw_target)
        if target and _is_external_url(target):
            return match.group(0)
        if is_sgl_link and target and not target.lower().endswith(".md"):
            return match.group(0)
        display, key = normalize_marker(raw_label)
        if not key or len(display) > 20:
            return match.group(0)
        idx = len(captured)
        captured.append(
            {
                "marker": display,
                "_key": key,
                "_style": _infer_style(raw_label, display),
                "target_file": posixpath.basename(target) if target else "",
            }
        )
        return f"{open_tok}{idx}{close_tok}"

    replaced = INLINE_FOOTNOTE_RE.sub(_replace, source)
    cleaned = cleaner(replaced)
    if not captured:
        return cleaned, []

    sentinel_re = re.compile(re.escape(open_tok) + r"(\d+)" + re.escape(close_tok))
    out_parts: list[str] = []
    refs: list[dict[str, Any]] = []
    cursor = 0
    out_len = 0
    for m in sentinel_re.finditer(cleaned):
        piece = cleaned[cursor : m.start()]
        out_parts.append(piece)
        out_len += len(piece)
        idx = int(m.group(1))
        if 0 <= idx < len(captured):
            item = dict(captured[idx])
            item["pos"] = out_len
            refs.append(item)
        cursor = m.end()
    out_parts.append(cleaned[cursor:])
    return "".join(out_parts), refs


def parse_explicit_footnote_def(raw_text: str) -> dict[str, Any] | None:
    """识别无歧义的脚注定义段（``[^1]: ...`` / ``[[1]]() ...`` / ``[1](ch.md) ...`` 等）。"""
    text = (raw_text or "").strip()
    if not text:
        return None

    m = _DEF_STD_MD_RE.match(text)
    if m:
        display, key = normalize_marker(m.group(1))
        if not key:
            return None
        return {
            "display": display,
            "key": key,
            "body": m.group(2).strip(),
            "def_kind": "explicit",
            "back_file": "",
        }

    m = _DEF_DBL_BRACKET_RE.match(text)
    if m:
        target = _clean_target(m.group(2))
        if target and _is_external_url(target):
            return None
        display, key = normalize_marker(m.group(1))
        body = m.group(3).strip()
        if not key:
            return None
        return {
            "display": display,
            "key": key,
            "body": body,
            "def_kind": "dbl_bracket",
            "back_file": posixpath.basename(target) if target else "",
        }

    m = _DEF_MD_BACKLINK_RE.match(text)
    if m:
        target = _clean_target(m.group(2))
        if not target or _is_external_url(target) or not target.lower().endswith(".md"):
            return None
        display, key = normalize_marker(m.group(1))
        body = m.group(3).strip()
        if not key or not body:
            return None
        return {
            "display": display,
            "key": key,
            "body": body,
            "def_kind": "md_backlink",
            "back_file": posixpath.basename(target),
        }

    m = _DEF_CARET_BACKLINK_RE.match(text)
    if m:
        target = _clean_target(m.group(2))
        if not target or _is_external_url(target) or not target.lower().endswith(".md"):
            return None
        display, key = normalize_marker(m.group(1))
        body = m.group(3).strip()
        if not key or not body:
            return None
        return {
            "display": display,
            "key": key,
            "body": body,
            "def_kind": "md_backlink",
            "back_file": posixpath.basename(target),
        }

    return None


def _match_tier2_prefix(text: str, *, allow_dot: bool = False) -> tuple[str, str, str, int, str] | None:
    """匹配段落开头的裸注号前缀，返回 ``(category, family, display, prefix_len, key)``。"""
    for family, pat in (
        ("a_bracket", _TIER2_A_BRACKET_RE),
        ("a_fullbr", _TIER2_A_FULLBR_RE),
        ("a_dbl", _TIER2_A_DBL_RE),
        ("a_zhu", _TIER2_A_ZHU_RE),
    ):
        m = pat.match(text)
        if m:
            display, key = normalize_marker(m.group(2))
            if key:
                return "A", family, display, len(m.group(1)), key
    for family, pat in (
        ("b_paren", _TIER2_B_PAREN_RE),
        ("b_circled", _TIER2_B_CIRCLED_RE),
    ):
        m = pat.match(text)
        if m:
            display, key = normalize_marker(m.group(2))
            if key:
                return "B", family, display, len(m.group(1)), key
    if allow_dot:
        m = _TIER2_C_DOT_RE.match(text)
        if m:
            display, key = normalize_marker(m.group(2))
            if key:
                return "C", "c_dot", display, len(m.group(1)), key
    return None


def _iter_block_ref_lists(block: dict[str, Any]) -> list[tuple[int | None, list[dict[str, Any]]]]:
    out: list[tuple[int | None, list[dict[str, Any]]]] = []
    fn_refs = block.get("fn_refs")
    if isinstance(fn_refs, list) and fn_refs:
        out.append((None, fn_refs))
    item_refs = block.get("item_fn_refs")
    if isinstance(item_refs, list):
        for item_idx, refs in enumerate(item_refs):
            if isinstance(refs, list) and refs:
                out.append((item_idx, refs))
    return out


def _reset_resolution_state(blocks: list[dict[str, Any]]) -> None:
    for block in blocks:
        block.pop("ref_blocks", None)
        block.pop("ref_ids", None)
        for _item_idx, refs in _iter_block_ref_lists(block):
            for ref in refs:
                ref["target_block"] = ""
                ref.pop("status", None)
                ref.pop("target_chapter", None)


def _promote_note_paragraph(block: dict[str, Any], info: tuple[str, str, str, int, str]) -> None:
    """把带注号前缀的普通段落提升为 footnote 定义块，并剥掉前缀。"""
    _cat, _family, display, prefix_len, _key = info
    block["kind"] = "footnote"
    block["marker"] = display
    raw_txt = str(block.get("text") or "")
    stripped = raw_txt[prefix_len:]
    block["text"] = stripped
    block["clean_text"] = stripped
    block["chars"] = _han_count(stripped)
    for inner_ref in block.get("fn_refs") or []:
        inner_ref["pos"] = int(inner_ref.get("pos", 0)) - prefix_len
    refs = [r for r in block.get("fn_refs") or [] if int(r.get("pos", 0)) >= 0]
    if refs:
        block["fn_refs"] = refs
    else:
        block.pop("fn_refs", None)


def _promote_notes_section(blocks: list[dict[str, Any]], notes_start_idx: int) -> int:
    """注释区标题之后，把 ``注N``/``[N]``/``①``/``N.`` 开头的段落全部提升为 footnote。"""
    promoted = 0
    for idx in range(notes_start_idx + 1, len(blocks)):
        block = blocks[idx]
        if block.get("kind") != "paragraph":
            continue
        info = _match_tier2_prefix(str(block.get("text") or ""), allow_dot=True)
        if not info:
            continue
        _promote_note_paragraph(block, info)
        promoted += 1
    return promoted


def _promote_trailing_notes(blocks: list[dict[str, Any]]) -> int:
    """没有注释区标题时，把文末连续同族的 ``注N``/``[N]``/``(N)``/圈码段落提升为 footnote。

    至少连续 3 段才认，避免把正文末尾的短括号说明误当注文。
    """
    run: list[tuple[int, dict[str, Any], tuple[str, str, str, int, str]]] = []
    family = ""
    for idx in range(len(blocks) - 1, -1, -1):
        block = blocks[idx]
        if block.get("kind") != "paragraph":
            break
        info = _match_tier2_prefix(str(block.get("text") or ""))
        if not info or info[0] not in ("A", "B"):
            break
        if not family:
            family = info[1]
        elif info[1] != family:
            break
        run.append((idx, block, info))
    if len(run) < 3:
        return 0
    for _idx, block, info in run:
        _promote_note_paragraph(block, info)
    return len(run)


def _promote_trailing_note_list(blocks: list[dict[str, Any]]) -> int:
    """文末整块 endnote list（``- [[1]]()…`` / ``- 注1 …``）拆成 footnote 块。"""
    if not blocks or blocks[-1].get("kind") != "list":
        return 0
    items = [str(item) for item in (blocks[-1].get("items") or [])]
    if len(items) < 3:
        return 0
    infos = [_match_tier2_prefix(item) for item in items]
    item_refs = blocks[-1].get("item_fn_refs") or []
    markers: list[str] = []
    matched = 0
    for idx, (_item, info) in enumerate(zip(items, infos, strict=False)):
        marker = info[2] if info and info[0] in ("A", "B") else ""
        if not marker and idx < len(item_refs) and item_refs[idx]:
            marker = str((item_refs[idx][0] or {}).get("marker") or "")
        markers.append(marker)
        if marker:
            matched += 1
    if matched < max(3, int(len(items) * 0.6)):
        return 0
    list_blk = blocks.pop()
    base_start = int(list_blk.get("start_line") or 0)
    base_end = int(list_blk.get("end_line") or base_start)
    item_lines = list_blk.get("_item_lines") if isinstance(list_blk.get("_item_lines"), list) else None
    heading_path = str(list_blk.get("heading_path") or "")
    number = len(blocks) + 1
    for idx, (item, info, marker) in enumerate(zip(items, infos, markers, strict=False)):
        text = item[info[3] :] if info and info[0] in ("A", "B") else item
        clean = text.strip()
        block: dict[str, Any] = {
            "id": f"B{number:02d}",
            "kind": "footnote",
            "heading_path": heading_path,
            "text": clean,
            "clean_text": clean,
            "chars": _han_count(text),
        }
        if item_lines and idx < len(item_lines):
            ln = int(item_lines[idx])
            block["start_line"] = ln
            block["end_line"] = ln
        elif base_start > 0:
            ln = min(base_end, base_start + idx) if base_end >= base_start else base_start
            block["start_line"] = ln
            block["end_line"] = ln
        if marker:
            block["marker"] = marker
        blocks.append(block)
        number += 1
    return len(items)


def _bare_ref_key(match: re.Match[str]) -> tuple[str, str, str]:
    for group, style in (
        ("zhu", "zhu"),
        ("zhu_t", "zhu"),
        ("bracket", "bracket"),
        ("fullbr", "bracket"),
        ("fullwidth", "bracket"),
        ("lenticular", "bracket"),
        ("circled", "circled"),
    ):
        raw = match.group(group)
        if raw:
            if group in ("zhu", "zhu_t"):
                raw = match.group(0).strip()
            display, key = normalize_marker(raw)
            return display, key, style
    return "", "", ""


def _bare_ref_enabled(
    key: str,
    style: str,
    candidates_by_key: dict[str, list[Any]],
    has_note_chapters: bool,
) -> bool:
    """裸注号只在有注定义（本章）或有独立注释章（跨文件）时启用。"""
    if not key:
        return False
    if any(candidates_by_key.get(alt) for alt in _marker_alt_keys(key)):
        return True
    # 跨文件注释章没有本地定义，只信 `注N` 与圈码这类强形态，`[N]` 留给本地定义判断。
    return has_note_chapters and style in ("zhu", "circled")


def _shift_ref_positions(refs: list[dict[str, Any]], spans: list[tuple[int, int]]) -> None:
    for ref in refs:
        pos = int(ref.get("pos", 0))
        removed = 0
        for start, end in spans:
            if end <= pos:
                removed += end - start
            elif start < pos:
                pos = start
        ref["pos"] = pos - removed


def _extract_bare_refs(
    blocks: list[dict[str, Any]],
    candidates_by_key: dict[str, list[Any]],
    notes_start_idx: int | None,
    has_note_chapters: bool,
) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
    """从正文段落抽裸注号，剥掉标记并入 ``fn_refs``，返回新增引用供配对。"""
    added: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for idx, block in enumerate(blocks):
        if notes_start_idx is not None and idx >= notes_start_idx:
            break
        if block.get("kind") not in ("paragraph", "quote"):
            continue
        text = str(block.get("text") or "")
        if not text:
            continue
        accepted: list[tuple[re.Match[str], str, str, str]] = []
        for match in _BARE_REF_RE.finditer(text):
            before = text[: match.start()]
            if not before.strip():
                # 段首注号是定义形态，不是引用。
                continue
            if before.rstrip().endswith("^"):
                # 残破的 `^1^` 已由行内表达式处理；这里不重复认。
                continue
            display, key, style = _bare_ref_key(match)
            if not _bare_ref_enabled(key, style, candidates_by_key, has_note_chapters):
                continue
            accepted.append((match, display, key, style))
        if not accepted:
            continue
        spans = [match.span() for match, _d, _k, _s in accepted]
        _shift_ref_positions(block.get("fn_refs") or [], spans)
        pieces: list[str] = []
        cursor = 0
        out_len = 0
        new_refs: list[dict[str, Any]] = []
        for match, display, key, style in accepted:
            piece = text[cursor : match.start()]
            pieces.append(piece)
            out_len += len(piece)
            new_refs.append(
                {
                    "marker": display,
                    "_key": key,
                    "_style": style,
                    "target_file": "",
                    "pos": out_len,
                }
            )
            cursor = match.end()
        pieces.append(text[cursor:])
        updated = "".join(pieces)
        block["text"] = updated
        block["clean_text"] = updated
        block["chars"] = _han_count(updated)
        existing = block.get("fn_refs")
        if not isinstance(existing, list):
            existing = []
            block["fn_refs"] = existing
        base = len(existing)
        bid = str(block.get("id") or f"B{idx + 1:02d}")
        for offset, ref in enumerate(new_refs):
            ref["id"] = f"fnref-{bid}-{base + offset}"
            existing.append(ref)
            added.append((idx, block, ref))
    return added


def _attach_orphan_footnotes(blocks: list[dict[str, Any]]) -> None:
    """把正文末尾的注释列表回填到空的 footnote 块。

    有些书的脚注不是 ``[^1]: ...`` 定义，而是在章末用列表集中列出；
    解析器会先得到若干空 footnote 块。这里按 marker 优先、顺序兜底做
    宽松回填：只要列表条目数不少于空脚注数就回填，多余的条目留在列表里。
    """
    orphans = [block for block in blocks if block["kind"] == "footnote" and not str(block["text"]).strip()]
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
        text = str(items[chosen])
        info = _match_tier2_prefix(text)
        if info:
            text = text[info[3] :]
        else:
            plain = _PLAIN_ENDNOTE_LINE_RE.match(text)
            if plain:
                text = text[plain.end(1) :].lstrip()
        orphan["text"] = text
        orphan["clean_text"] = text
        orphan["chars"] = _han_count(text)
        used.add(chosen)
    keep = [idx for idx in range(len(items)) if idx not in used]
    if not keep:
        blocks.pop()
        return
    kept_items = [str(items[idx]) for idx in keep]
    blocks[-1]["items"] = kept_items
    blocks[-1]["text"] = " ".join(kept_items)
    blocks[-1]["clean_text"] = "\n".join(kept_items)
    blocks[-1]["chars"] = _han_count(blocks[-1]["text"])
    refs = blocks[-1].get("item_fn_refs")
    if isinstance(refs, list):
        blocks[-1]["item_fn_refs"] = [refs[idx] for idx in keep if idx < len(refs)]


def resolve_chapter_footnotes(
    blocks: list[dict[str, Any]],
    source_files: Sequence[str] = (),
    note_chapters: Sequence[str] = (),
) -> None:
    """按最终块 ID 原地配对章内脚注引用与定义，可安全重复执行（幂等）。"""
    if not blocks:
        return

    _reset_resolution_state(blocks)
    own_files = {posixpath.basename(f) for f in (source_files or ()) if f}

    # 确定注释区标题起点，以及文末连续 Category B 裸前缀区间
    notes_start_idx: int | None = None
    for idx, block in enumerate(blocks):
        if block.get("kind") == "heading" and NOTE_HEADING_RE.match((block.get("text") or "").strip()):
            notes_start_idx = idx
            break
    if notes_start_idx is not None:
        _promote_notes_section(blocks, notes_start_idx)
    else:
        _promote_trailing_notes(blocks)

    # 为全部引用分配确定性 ID，同时统计本文件可见的本地引用 key 及其最早出现位置
    local_ref_indices_by_key: dict[str, list[int]] = {}
    all_refs: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for b_idx, block in enumerate(blocks):
        bid = str(block.get("id") or f"B{b_idx + 1:02d}")
        for item_idx, refs in _iter_block_ref_lists(block):
            for r_idx, ref in enumerate(refs):
                if "_key" not in ref:
                    _, key = normalize_marker(str(ref.get("marker") or ""))
                    ref["_key"] = key
                if item_idx is None:
                    ref["id"] = f"fnref-{bid}-{r_idx}"
                else:
                    ref["id"] = f"fnref-{bid}-i{item_idx}-{r_idx}"
                all_refs.append((b_idx, block, ref))
                tfile = str(ref.get("target_file") or "")
                is_external = bool(tfile and own_files and tfile not in own_files)
                if not is_external:
                    for alt in _marker_alt_keys(str(ref.get("_key") or "")):
                        local_ref_indices_by_key.setdefault(alt, []).append(b_idx)

    # 识别文末连续同类 Category B 裸前缀（如 (1)/(2)/(3) 或 ①/②/③）
    trailing_cat_b: set[int] = set()
    tail_family = ""
    for idx in range(len(blocks) - 1, -1, -1):
        blk = blocks[idx]
        if blk.get("kind") != "paragraph":
            break
        info = _match_tier2_prefix(blk.get("text") or "")
        if not info or info[0] != "B":
            break
        family = info[1]
        if not tail_family:
            tail_family = family
        elif family != tail_family:
            break
        trailing_cat_b.add(idx)

    # 收集候选定义块：Tier 1（明确 footnote 定义）与 Tier 2（上下文合格的裸前缀段）
    # entry: (def_idx, block, tier, tier2_info)
    candidates_by_key: dict[str, list[tuple[int, dict[str, Any], int, tuple[str, str, str, int, str] | None]]] = {}
    for idx, block in enumerate(blocks):
        kind = block.get("kind")
        if kind == "footnote" and block.get("marker"):
            _, key = normalize_marker(str(block.get("marker") or ""))
            if key:
                for alt in _marker_alt_keys(key):
                    candidates_by_key.setdefault(alt, []).append((idx, block, 1, None))
            continue

        if kind != "paragraph":
            continue
        info = _match_tier2_prefix(block.get("text") or "")
        if not info:
            continue
        cat, _family, _display, _prefix_len, key = info
        in_notes_sec = notes_start_idx is not None and idx > notes_start_idx
        earlier_refs = [r_i for r_i in local_ref_indices_by_key.get(key, ()) if r_i < idx]
        if cat == "A":
            if not (in_notes_sec or earlier_refs):
                continue
        else:  # cat == "B"
            if not (in_notes_sec or (idx in trailing_cat_b and earlier_refs)):
                continue
        for alt in _marker_alt_keys(key):
            candidates_by_key.setdefault(alt, []).append((idx, block, 2, info))

    all_refs.extend(_extract_bare_refs(blocks, candidates_by_key, notes_start_idx, bool(note_chapters)))

    # 执行引用到定义的就近配对
    promoted_indices: set[int] = set()
    for b_idx, block, ref in all_refs:
        tfile = str(ref.get("target_file") or "")
        if tfile and (not own_files or tfile not in own_files):
            ref["target_block"] = ""
            continue

        key = str(ref.get("_key") or "")
        pool: list[tuple[int, dict[str, Any], int, tuple[str, str, str, int, str] | None]] = []
        for alt in _marker_alt_keys(key):
            pool = candidates_by_key.get(alt, [])
            if pool:
                break
        if not pool:
            ref["target_block"] = ""
            continue

        # 优先取当前块之后最近的定义块（支持同一章分节重新编号的场景）
        after = [cand for cand in pool if cand[0] > b_idx]
        if after:
            chosen = min(after, key=lambda cand: (cand[2], cand[0] - b_idx))
        else:
            tier1 = [cand for cand in pool if cand[2] == 1 and cand[0] != b_idx]
            if not tier1:
                ref["target_block"] = ""
                continue
            chosen = min(tier1, key=lambda cand: abs(cand[0] - b_idx))

        def_idx, def_block, tier, tier2_info = chosen
        if tier == 2 and tier2_info is not None and def_idx not in promoted_indices:
            promoted_indices.add(def_idx)
            _, _, display, prefix_len, _ = tier2_info
            def_block["kind"] = "footnote"
            def_block["marker"] = display
            raw_txt = str(def_block.get("text") or "")
            stripped = raw_txt[prefix_len:]
            def_block["text"] = stripped
            def_block["clean_text"] = stripped
            def_block["chars"] = _han_count(stripped)
            for inner_ref in def_block.get("fn_refs") or []:
                inner_ref["pos"] = int(inner_ref.get("pos", 0)) - prefix_len
            if isinstance(def_block.get("fn_refs"), list):
                def_block["fn_refs"] = [r for r in def_block["fn_refs"] if int(r.get("pos", 0)) >= 0]
                if not def_block["fn_refs"]:
                    def_block.pop("fn_refs", None)

        target_bid = str(def_block.get("id") or "")
        src_bid = str(block.get("id") or "")
        ref["target_block"] = target_bid
        ref_blocks = def_block.setdefault("ref_blocks", [])
        if src_bid and src_bid not in ref_blocks:
            ref_blocks.append(src_bid)
        ref_ids = def_block.setdefault("ref_ids", [])
        if ref["id"] not in ref_ids:
            ref_ids.append(ref["id"])

    # 无编号的小图标（note.png）按文档顺序配给本章未引用的数字尾注，
    # 仅在数量严格相等时启用，避免把装饰图标错配到无关脚注。
    paired_ids = {id(ref) for _b_idx, _block, ref in all_refs if ref.get("target_block")}
    generic_icons = [
        (b_idx, block, ref)
        for b_idx, block, ref in all_refs
        if id(ref) not in paired_ids
        and ref.get("_style") == "icon"
        and str(ref.get("marker") or "").lower() in {"", "note", "notes", "footnote", "tip", "icon", "fn", "注", "脚注"}
    ]
    lone_defs = [
        block
        for block in blocks
        if block.get("kind") == "footnote" and block.get("marker") and not block.get("ref_blocks")
    ]
    if generic_icons and len(generic_icons) == len(lone_defs):
        for (_b_idx, block, ref), def_block in zip(generic_icons, lone_defs, strict=False):
            marker = str(def_block.get("marker") or "")
            display, key = normalize_marker(marker)
            ref["marker"] = display or marker
            ref["_key"] = key
            ref["target_block"] = str(def_block.get("id") or "")
            src_bid = str(block.get("id") or "")
            if src_bid:
                def_block.setdefault("ref_blocks", []).append(src_bid)
            if ref.get("id"):
                def_block.setdefault("ref_ids", []).append(ref["id"])

    # 清理内部临时字段，保持返回 JSON 干净
    for block in blocks:
        block.pop("_key", None)
        block.pop("_def_kind", None)
        for _item_idx, refs in _iter_block_ref_lists(block):
            for ref in refs:
                ref.pop("_key", None)


def _norm_title(text: str) -> str:
    return _TITLE_NORM_RE.sub("", unicodedata.normalize("NFKC", text or "").lower())


def _find_footnote_match_in_chapter(
    target_blocks: list[dict[str, Any]],
    marker: str,
    source_files: list[str] | tuple[str, ...] = (),
    source_title: str = "",
) -> tuple[str, bool]:
    _, key = normalize_marker(marker)
    if not key or not target_blocks:
        return "", False
    alt_keys = _marker_alt_keys(key)
    own_sources = {posixpath.basename(f) for f in (source_files or ()) if f}
    norm_src_title = _norm_title(source_title)

    # Pass A: 已识别为 footnote 定义的块
    # entry: (block_id, back_match, heading_match, exact_key)
    matches_a: list[tuple[str, bool, bool, bool]] = []
    cur_heading = ""
    for block in target_blocks:
        kind = block.get("kind")
        if kind == "heading":
            cur_heading = _norm_title(str(block.get("text") or ""))
            continue
        if kind != "footnote" or not block.get("marker"):
            continue
        _, def_key = normalize_marker(str(block.get("marker") or ""))
        if not def_key:
            continue
        def_alts = _marker_alt_keys(def_key)
        if def_key not in alt_keys and key not in def_alts:
            continue
        back_file = posixpath.basename(str(block.get("back_file") or ""))
        back_match = bool(back_file and own_sources and back_file in own_sources)
        heading_match = bool(
            len(norm_src_title) >= 2
            and cur_heading
            and (norm_src_title in cur_heading or cur_heading in norm_src_title)
        )
        matches_a.append((str(block.get("id") or ""), back_match, heading_match, def_key == key))

    matches = matches_a
    if not matches:
        # Pass B: 无回链且未标成 footnote 的纯文本尾注行（如 `[1] ...`、`1 Ann Gibbons...`）
        matches_b: list[tuple[str, bool, bool, bool]] = []
        cur_heading = ""
        for block in target_blocks:
            kind = block.get("kind")
            if kind == "heading":
                cur_heading = _norm_title(str(block.get("text") or ""))
                continue
            if kind != "paragraph":
                continue
            raw_txt = str(block.get("text") or "")
            info = _match_tier2_prefix(raw_txt)
            if info:
                def_key = info[4]
            else:
                m_plain = _PLAIN_ENDNOTE_LINE_RE.match(raw_txt)
                if not m_plain:
                    continue
                _, def_key = normalize_marker(m_plain.group(1))
            if not def_key:
                continue
            def_alts = _marker_alt_keys(def_key)
            if def_key not in alt_keys and key not in def_alts:
                continue
            heading_match = bool(
                len(norm_src_title) >= 2
                and cur_heading
                and (norm_src_title in cur_heading or cur_heading in norm_src_title)
            )
            matches_b.append((str(block.get("id") or ""), False, heading_match, def_key == key))
        matches = matches_b

    if not matches:
        return "", False
    back_hits = [m for m in matches if m[1]]
    if back_hits:
        best = max(back_hits, key=lambda m: m[3])
        return best[0], True
    head_hits = [m for m in matches if m[2]]
    if head_hits:
        best = max(head_hits, key=lambda m: m[3])
        return best[0], False
    if len(matches) == 1:
        return matches[0][0], False
    return "", False


def find_footnote_block_in_chapter(
    target_blocks: list[dict[str, Any]],
    marker: str,
    source_files: list[str] | tuple[str, ...] = (),
    source_title: str = "",
) -> str:
    """在目标注释章的块列表里精确查找对应注文的 ``block_id``；有歧义返回空串。"""
    bid, _ = _find_footnote_match_in_chapter(
        target_blocks,
        marker,
        source_files=source_files,
        source_title=source_title,
    )
    return bid


def enrich_cross_file_footnotes(
    blocks: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
    current_n: int,
    current_files: list[str] | tuple[str, ...] = (),
    *,
    chapter_reader: Callable[[int], dict[str, Any] | None] | None = None,
    source_title: str = "",
) -> dict[str, Any]:
    """补充跨文件注释状态与候选中转章列表，并按需定位目标注释块 ID。"""
    by_file: dict[str, dict[str, Any]] = {}
    note_chapters: list[dict[str, str]] = []
    for ch in chapters or []:
        ch_n = int(ch["n"])
        sources = ch.get("source_files") or [ch.get("file", "")]
        for fname in sources:
            base = posixpath.basename(fname or "")
            if base:
                by_file[base] = ch
        title = (ch.get("title") or "").strip()
        if ch_n != int(current_n) and NOTE_CHAPTER_TITLE_RE.match(title):
            note_chapters.append(
                {
                    "n": f"{ch_n:02d}",
                    "title": title,
                    "file": str(ch.get("file") or ""),
                }
            )

    own_files = {posixpath.basename(f) for f in (current_files or ()) if f}
    target_blocks_cache: dict[int, list[dict[str, Any]]] = {}

    def _read_target_blocks(ch_n: int) -> list[dict[str, Any]]:
        if chapter_reader is None:
            return []
        if ch_n not in target_blocks_cache:
            loaded = chapter_reader(ch_n)
            target_blocks_cache[ch_n] = list((loaded or {}).get("blocks") or [])
        return target_blocks_cache[ch_n]

    total_refs = 0
    unresolved_refs = 0
    local_defs = sum(1 for blk in (blocks or []) if blk.get("kind") == "footnote" and blk.get("marker"))

    for block in blocks or []:
        for _item_idx, refs in _iter_block_ref_lists(block):
            for ref in refs:
                total_refs += 1
                if ref.get("target_block"):
                    ref["status"] = "local"
                    continue
                unresolved_refs += 1
                tfile = posixpath.basename(str(ref.get("target_file") or ""))
                marker = str(ref.get("marker") or "")
                target_ch = by_file.get(tfile) if (tfile and tfile not in own_files) else None
                if target_ch is not None and int(target_ch["n"]) != int(current_n):
                    target_n = int(target_ch["n"])
                    ref["status"] = "external_chapter"
                    ch_info: dict[str, str] = {
                        "n": f"{target_n:02d}",
                        "title": str(target_ch.get("title") or ""),
                        "file": str(target_ch.get("file") or tfile),
                    }
                    if chapter_reader is not None:
                        bid = find_footnote_block_in_chapter(
                            _read_target_blocks(target_n),
                            marker,
                            source_files=current_files,
                            source_title=source_title,
                        )
                        if bid:
                            ch_info["block_id"] = bid
                    ref["target_chapter"] = ch_info
                elif tfile and tfile not in own_files:
                    ref["status"] = "external_unknown"
                elif note_chapters:
                    ref["status"] = "external_candidate"
                    if chapter_reader is not None:
                        fallback_hit: dict[str, str] | None = None
                        for cand in note_chapters[:6]:
                            cand_n = int(cand["n"])
                            bid, back_matched = _find_footnote_match_in_chapter(
                                _read_target_blocks(cand_n),
                                marker,
                                source_files=current_files,
                                source_title=source_title,
                            )
                            if not bid:
                                continue
                            hit_info = {
                                "n": cand["n"],
                                "title": cand["title"],
                                "file": cand["file"],
                                "block_id": bid,
                            }
                            if back_matched:
                                fallback_hit = hit_info
                                break
                            if fallback_hit is None:
                                fallback_hit = hit_info
                        if fallback_hit is not None:
                            ref["target_chapter"] = fallback_hit
                else:
                    ref["status"] = "unresolved"

    return {
        "total_refs": total_refs,
        "local_defs": local_defs,
        "unresolved_refs": unresolved_refs,
        "note_chapters": note_chapters,
    }
