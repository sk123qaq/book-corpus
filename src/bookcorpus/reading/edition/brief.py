"""设计简报与模板适配判决。

第一版只实现 xerox。判决的目标不是"尽量生成"，而是明确说出
这本书/这一章适不适合视觉读本；不适合就退回分层读，不硬套模板。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

TEMPLATE = "xerox"
MIN_HAN = 1200
MAX_HAN = 30_000
MAX_IMAGE_RATIO = 0.55


@dataclass(frozen=True)
class Brief:
    uid: str
    n: int
    title: str
    subject: str
    han: int
    blocks: int
    images: int
    image_ratio: float
    headings: int
    has_table: bool
    has_code: bool
    has_quote: bool

    def as_dict(self) -> dict:
        return asdict(self)


def build_brief(book: dict, chapter: dict, blocks: list[dict]) -> Brief:
    content = [block for block in blocks if block.get("kind") != "footnote"]
    images = sum(1 for block in content if block.get("kind") == "image")
    han = int(chapter.get("body_han") or sum(block.get("chars", 0) for block in blocks))
    return Brief(
        uid=str(book.get("uid") or ""),
        n=int(chapter.get("n") or 0),
        title=str(chapter.get("title") or ""),
        subject=str(book.get("subject") or ""),
        han=han,
        blocks=len(content),
        images=images,
        image_ratio=round(images / max(1, len(content)), 3),
        headings=sum(1 for block in content if block.get("kind") == "heading"),
        has_table=any(block.get("kind") == "table" for block in content),
        has_code=any(block.get("kind") == "code" for block in content),
        has_quote=any(block.get("kind") == "quote" for block in content),
    )


def decide_template(brief: Brief) -> dict:
    """返回 {template, score, reason, fallback}；template=None 表示不硬套。"""
    if brief.han < MIN_HAN:
        return {
            "template": None,
            "score": 0,
            "reason": "正文只有 %d 字，撑不起一页视觉读本的版面" % brief.han,
            "fallback": "four-read",
        }
    if brief.han > MAX_HAN:
        return {
            "template": None,
            "score": 0,
            "reason": "正文 %d 字，超过单页读本上限 %d 字，应先分节或半章处理" % (brief.han, MAX_HAN),
            "fallback": "four-read",
        }
    if brief.subject.startswith("文学"):
        return {
            "template": None,
            "score": 0,
            "reason": "文学类章节是连续叙事，xerox 是论证型工作论文版式，硬套会把小说读成案例；应等杂志/初版模板",
            "fallback": "four-read",
        }
    if brief.image_ratio > MAX_IMAGE_RATIO:
        return {
            "template": None,
            "score": 0,
            "reason": "图片占内容块 %.0f%%，xerox 是文字读本模板，应走图册模板" % (brief.image_ratio * 100),
            "fallback": "four-read",
        }
    score = 100
    if brief.blocks < 20:
        score -= 20
    if brief.headings == 0:
        score -= 10
    if brief.has_table:
        score += 2
    reason = "文字为主，块数 %d，段落结构完整，适合 xerox 的逐字正文 + 页面旁注" % brief.blocks
    return {"template": TEMPLATE, "score": score, "reason": reason, "fallback": "four-read"}
