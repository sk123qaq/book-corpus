"""章表的共享规则：编号、标题页合并、整本聚合。

``Library``（读盘路径）与 ``ShelfIndex``（索引路径）必须用同一份规则，
否则书架的章数/字数会和书页对不上。
"""

import re

CHAPTER_RE = re.compile(r"^(\d+)_(.*)\.md$")
TITLE_PAGE_MAX_CHARS = 120
BODY_MIN_CHARS = 500
BODY_STEM_PREFIX = "前置页"


def merge_title_pages(chapters):
    """把过短的标题页/分隔页与紧随其后的 ``前置页`` 正文并成一章。

    部分 EPUB 转换把章标题与题记写成一个 md，正文另起 ``NN_前置页 X.md``。
    若当成两章，标题页会变成 30 字的「章」，正文反而另立门户。
    保留标题页的 ``n``（URL 稳定），把正文文件并进来。
    """
    merged = []
    index = 0
    while index < len(chapters):
        current = dict(chapters[index])
        following = chapters[index + 1] if index + 1 < len(chapters) else None
        if (
            current["chars"] <= TITLE_PAGE_MAX_CHARS
            and following is not None
            and following["stem"].startswith(BODY_STEM_PREFIX)
            and following["chars"] >= BODY_MIN_CHARS
        ):
            current["body_file"] = following["file"]
            current["body_stem"] = following["stem"]
            current["body_n"] = following["n"]
            current["chars"] += following["chars"]
            current["full_chars"] = current.get("full_chars", 0) + following.get("full_chars", 0)
            current["title"] = current["title"] or following["title"]
            current["merged"] = True
            current["source_files"] = [current["file"], following["file"]]
            merged.append(current)
            index += 2
            continue
        current["merged"] = False
        current["source_files"] = [current["file"]]
        merged.append(current)
        index += 1
    return merged


def book_totals(chapters):
    """合并后的章数与字数（字数按 ``full_chars``，缺省退回 ``chars``）。"""
    merged = merge_title_pages(chapters)
    total = sum(int(item.get("full_chars", item["chars"])) for item in merged)
    return len(merged), total
