"""阅读层的文本归一化工具。

两个口径必须分开：

- :func:`normalize_ws` 忽略所有空白，适合摘要、标题这类可能重排的文本比较；
- :func:`normalize_verbatim` 只统一换行并去掉首尾空白，适合引文逐字校验，
  这样 ``A B`` 与 ``AB`` 仍然不相等。

老渲染器的比较行为由 :func:`normalize_ws` 保持。
"""

import re

_WS_RE = re.compile(r"\s+")


def normalize_ws(text: str | None) -> str:
    """去掉所有空白；兼容旧渲染器的比对口径。"""
    return _WS_RE.sub("", text or "")


def normalize_verbatim(text: str | None) -> str:
    """逐字校验口径：只统一换行符并去掉首尾空白。"""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def collapse_ws(text: str | None) -> str:
    """把连续空白折成一个空格，适合标题、节名等可读文本。"""
    return " ".join((text or "").split())
