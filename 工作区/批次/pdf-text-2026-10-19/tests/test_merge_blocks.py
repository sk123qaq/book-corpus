#!/usr/bin/env python3
"""跨块并段的判据单测：只喂 _can_merge 与 _join_text，不需要 PDF。

跑：env/anydoc/bin/python 工作区/批次/pdf-text-2026-10-19/tests/test_merge_blocks.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "脚本"))
import pdf_text_to_md as P  # noqa: E402

BODY = 11.1
PAGE_W = 600.0


def blk(text, x0=100.0, y0=100.0, x1=500.0, y1=114.0, size=11.1, lh=14.0):
    return {"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "size": size, "lh": lh}


def merge(a, b):
    return P._can_merge(a, b, BODY, PAGE_W)


def case_two_lines_same_column():
    a = blk("纸面原型也有两个缺点，它对测试玩家来说较难上手")
    b = blk("所有的机制都能轻易地转换成桌上游戏。", y0=114.0, y1=128.0)
    assert merge(a, b) is True


def case_previous_ends_sentence():
    a = blk("这是完整的一句话。")
    b = blk("下一段从这里开始", y0=114.0, y1=128.0)
    assert merge(a, b) is False


def case_previous_ends_with_closer():
    a = blk("他说完了。”")
    b = blk("下一段从这里开始", y0=114.0, y1=128.0)
    assert merge(a, b) is False


def case_previous_is_numbered_heading():
    a = blk("一、正科级轨迹")
    b = blk("下一段从这里开始", y0=114.0, y1=128.0)
    assert merge(a, b) is False


def case_next_in_disjoint_column():
    a = blk("左栏这一行还没完", x0=100.0, x1=280.0)
    b = blk("右栏这一行", x0=320.0, x1=500.0, y0=114.0, y1=128.0)
    assert merge(a, b) is False


def case_gap_too_large():
    a = blk("上一段还在继续")
    b = blk("隔得太远的一行", y0=200.0, y1=214.0)
    assert merge(a, b) is False


def case_next_starts_list_marker():
    a = blk("正文这一行没结句")
    b = blk("1. 第一步要做什么", y0=114.0, y1=128.0)
    assert merge(a, b) is False


def case_larger_heading_block():
    a = blk("物理原型", size=14.8, y1=118.0, lh=18.0)
    b = blk("原型不只限于软件或纸面形式", y0=118.0, y1=132.0)
    assert merge(a, b) is False


def case_join_separator():
    assert P._join_text("纸面原型", "也有缺点") == "纸面原型也有缺点"
    assert P._join_text("paper prototype", "has flaws") == "paper prototype has flaws"


def main():
    cases = [v for k, v in sorted(globals().items()) if k.startswith("case_")]
    for c in cases:
        c()
    print("并段判据 %d 项全过" % len(cases))


if __name__ == "__main__":
    main()
