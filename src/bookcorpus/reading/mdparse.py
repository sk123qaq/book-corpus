"""伴读 Markdown 章节解析兼容导出层。

L1 共享解析内核已下沉至 ``bookcorpus.md``；L2 伴读块号版本契约 ``BLOCK_VERSION`` 留在本模块。
"""

from __future__ import annotations

from bookcorpus.md.footnotes import _attach_orphan_footnotes as _attach_orphan_footnotes
from bookcorpus.md.parse import (
    CODE_FENCE_RE as CODE_FENCE_RE,
)
from bookcorpus.md.parse import (
    FOOTNOTE_RE as FOOTNOTE_RE,
)
from bookcorpus.md.parse import (
    FRONT_MATTER_LIMIT as FRONT_MATTER_LIMIT,
)
from bookcorpus.md.parse import (
    HAN_RE as HAN_RE,
)
from bookcorpus.md.parse import (
    HEADING_RE as HEADING_RE,
)
from bookcorpus.md.parse import (
    HR_RE as HR_RE,
)
from bookcorpus.md.parse import (
    IMAGE_RE as IMAGE_RE,
)
from bookcorpus.md.parse import (
    INLINE_IMAGE_RE as INLINE_IMAGE_RE,
)
from bookcorpus.md.parse import (
    INLINE_LINK_RE as INLINE_LINK_RE,
)
from bookcorpus.md.parse import (
    LIST_RE as LIST_RE,
)
from bookcorpus.md.parse import (
    QUOTE_RE as QUOTE_RE,
)
from bookcorpus.md.parse import (
    TABLE_ALIGN_RE as TABLE_ALIGN_RE,
)
from bookcorpus.md.parse import (
    TABLE_ROW_RE as TABLE_ROW_RE,
)
from bookcorpus.md.parse import (
    _clean_inline as _clean_inline,
)
from bookcorpus.md.parse import (
    _take_h1 as _take_h1,
)
from bookcorpus.md.parse import (
    chapter_title as chapter_title,
)
from bookcorpus.md.parse import (
    han_count as han_count,
)
from bookcorpus.md.parse import (
    image_size as image_size,
)
from bookcorpus.md.parse import (
    lean_meta as lean_meta,
)
from bookcorpus.md.parse import (
    non_space_count as non_space_count,
)
from bookcorpus.md.parse import (
    parse_markdown as parse_markdown,
)
from bookcorpus.md.parse import (
    strip_front_matter as strip_front_matter,
)

# 块号生成口径的版本号：解析规则一变，旧 chapter_map / chapter_tool 缓存必须失效，
# 否则会拿旧块号去查新块，静默错位。改块号生成规则时递增。
BLOCK_VERSION = 2

__all__ = [
    "BLOCK_VERSION",
    "CODE_FENCE_RE",
    "FOOTNOTE_RE",
    "FRONT_MATTER_LIMIT",
    "HAN_RE",
    "HEADING_RE",
    "HR_RE",
    "IMAGE_RE",
    "INLINE_IMAGE_RE",
    "INLINE_LINK_RE",
    "LIST_RE",
    "QUOTE_RE",
    "TABLE_ALIGN_RE",
    "TABLE_ROW_RE",
    "_attach_orphan_footnotes",
    "_clean_inline",
    "_take_h1",
    "chapter_title",
    "han_count",
    "image_size",
    "lean_meta",
    "non_space_count",
    "parse_markdown",
    "strip_front_matter",
]
