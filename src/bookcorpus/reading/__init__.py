"""分层读：章节解析、三档投影与自包含读本渲染。

本包只读实例的 ``books/<uid>/md`` 与 ``book.json``；唯一写区是
``工作区/阅读``。不依赖网络，模型标注是可选项。
"""

__version__ = "0.1.0"

TIER_KEYS = ("skim", "standard", "deep")
