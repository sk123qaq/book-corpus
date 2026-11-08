"""四读信息深度的结构层：章节模型、四档处置与守恒账。"""

from .disposition import audit_conservation, plan_dispositions
from .model import SYSTEM_PROMPT, DepthModelError, generate_chapter_model
from .schema import PROMPT_VERSION, ChapterModelError, validate_chapter_model

__all__ = [
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "ChapterModelError",
    "DepthModelError",
    "audit_conservation",
    "generate_chapter_model",
    "plan_dispositions",
    "validate_chapter_model",
]
