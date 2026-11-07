"""视觉读本：编辑层驱动的单页读本，与四读并存。"""

from .brief import Brief, build_brief, decide_template
from .content import ContentError, validate_content
from .gate import audit_edition
from .generate import generate_content
from .render import render_edition

__all__ = [
    "Brief",
    "ContentError",
    "audit_edition",
    "build_brief",
    "decide_template",
    "generate_content",
    "render_edition",
    "validate_content",
]
