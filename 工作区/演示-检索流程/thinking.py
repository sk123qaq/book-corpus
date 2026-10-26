#!/usr/bin/env python3
"""模型思考等级：页面、切词调用与回答阶段共用的一份白名单。

`pi --thinking` 接受 off/minimal/low/medium/high/xhigh/max。页面只暴露
关/低/中/高四档；接口传其它合法值也照收，非法值退回默认 high。
"""
LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
UI_LEVELS = ("off", "low", "medium", "high")
LABELS = {"off": "关", "minimal": "最低", "low": "低", "medium": "中",
          "high": "高", "xhigh": "极高", "max": "最大"}
DEFAULT = "high"


def normalize(value, default=DEFAULT):
    """合法值原样返回（小写），空值或非法值退回默认档。"""
    text = str(value or "").strip().lower()
    if text in LEVELS:
        return text
    return default if default in LEVELS else DEFAULT


def label(value):
    level = normalize(value)
    return LABELS.get(level, level)
