"""深读结构层：章节模型、三档处置与守恒账。"""

from .deep import DEEP_PROMPT_VERSION, DeepError, audit_deep, generate_deep, validate_deep
from .disposition import audit_conservation, plan_dispositions
from .model import SYSTEM_PROMPT, DepthModelError, generate_chapter_model
from .render import DEPTH_RENDER_VERSION, render_depth_chapter
from .schema import PROMPT_VERSION, ChapterModelError, validate_chapter_model
from .skim import (
    SKIM_PROMPT_VERSION,
    SkimError,
    audit_skim,
    generate_skim,
    validate_skim,
)

__all__ = [
    "DEEP_PROMPT_VERSION",
    "DEPTH_RENDER_VERSION",
    "PROMPT_VERSION",
    "SKIM_PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "ChapterModelError",
    "DeepError",
    "DepthModelError",
    "SkimError",
    "audit_conservation",
    "audit_deep",
    "audit_skim",
    "generate_chapter_model",
    "generate_deep",
    "generate_skim",
    "plan_dispositions",
    "render_depth_chapter",
    "validate_chapter_model",
    "validate_deep",
    "validate_skim",
]
