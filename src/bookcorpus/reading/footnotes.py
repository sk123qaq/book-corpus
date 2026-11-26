"""伴读脚注解析兼容导出层；L1 实现已下沉至 ``bookcorpus.md.footnotes``。"""

from __future__ import annotations

from bookcorpus.md.footnotes import (
    _PLAIN_ENDNOTE_LINE_RE as _PLAIN_ENDNOTE_LINE_RE,
)
from bookcorpus.md.footnotes import (
    HAN_RE as HAN_RE,
)
from bookcorpus.md.footnotes import (
    INLINE_FOOTNOTE_RE as INLINE_FOOTNOTE_RE,
)
from bookcorpus.md.footnotes import (
    NOTE_CHAPTER_TITLE_RE as NOTE_CHAPTER_TITLE_RE,
)
from bookcorpus.md.footnotes import (
    NOTE_HEADING_RE as NOTE_HEADING_RE,
)
from bookcorpus.md.footnotes import (
    _attach_orphan_footnotes as _attach_orphan_footnotes,
)
from bookcorpus.md.footnotes import (
    _marker_alt_keys as _marker_alt_keys,
)
from bookcorpus.md.footnotes import (
    _match_tier2_prefix as _match_tier2_prefix,
)
from bookcorpus.md.footnotes import (
    _promote_trailing_note_list as _promote_trailing_note_list,
)
from bookcorpus.md.footnotes import (
    enrich_cross_file_footnotes as enrich_cross_file_footnotes,
)
from bookcorpus.md.footnotes import (
    extract_inline_footnotes as extract_inline_footnotes,
)
from bookcorpus.md.footnotes import (
    find_footnote_block_in_chapter as find_footnote_block_in_chapter,
)
from bookcorpus.md.footnotes import (
    normalize_marker as normalize_marker,
)
from bookcorpus.md.footnotes import (
    parse_explicit_footnote_def as parse_explicit_footnote_def,
)
from bookcorpus.md.footnotes import (
    resolve_chapter_footnotes as resolve_chapter_footnotes,
)

__all__ = [
    "HAN_RE",
    "INLINE_FOOTNOTE_RE",
    "NOTE_CHAPTER_TITLE_RE",
    "NOTE_HEADING_RE",
    "_PLAIN_ENDNOTE_LINE_RE",
    "_attach_orphan_footnotes",
    "_marker_alt_keys",
    "_match_tier2_prefix",
    "_promote_trailing_note_list",
    "enrich_cross_file_footnotes",
    "extract_inline_footnotes",
    "find_footnote_block_in_chapter",
    "normalize_marker",
    "parse_explicit_footnote_def",
    "resolve_chapter_footnotes",
]
