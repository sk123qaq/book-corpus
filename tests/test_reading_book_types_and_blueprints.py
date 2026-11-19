"""分层读书型映射、五步防重叠蓝图审计与扩展伴读单元测试。"""

from __future__ import annotations

import json

import pytest

from bookcorpus.reading.book_types import READ_PROFILES, list_read_profiles, resolve_read_profile
from bookcorpus.reading.diagram_audit import (
    EDGE_LABEL_LIMIT,
    NODE_LABEL_LIMIT,
    audit_blueprint_payload,
    prompt_for_diagram,
)
from bookcorpus.reading.service import ReadingService


class _FakeBlueprintClient:
    model = "fake-flash"
    effort = "high"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def available(self):
        return True

    def complete(self, messages, *, model=None, effort=None, json_mode=False, max_tokens=None, on_delta=None):
        self.calls.append({"messages": messages, "model": model, "effort": effort, "json_mode": json_mode})
        payload = self.responses.pop(0)
        text = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else str(payload)
        if on_delta is not None:
            on_delta(text)
        return text, {"prompt_tokens": 20, "completion_tokens": 40, "total_tokens": 60}


@pytest.mark.parametrize(
    ("subjects", "expected_type", "expected_sub"),
    [
        (["商业与管理", "战略"], "practical", ""),
        (["科学技术", "计算机与技术"], "practical", ""),
        (["科学技术", "数学与统计"], "science", ""),
        (["科学技术", "不细分"], "science", ""),
        (["文学", "严肃文学小说"], "literature", "fiction"),
        (["文学", "戏剧"], "literature", "drama"),
        (["文学", "诗歌"], "literature", "poetry"),
        (["文学", "散文随笔"], "literature", "essay"),
        (["人文社科", "历史"], "history", ""),
        (["建筑与古迹", "古建园林"], "history", ""),
        (["艺术与图像", "美术史"], "history", ""),
        (["影视动画漫画", "电影与影史"], "history", ""),
        (["人文社科", "思想与哲学"], "philosophy", ""),
        (["人文社科", "社会与经济"], "social", ""),
        (["游戏", "游戏研究"], "social", ""),
        (["游戏", "游戏设计"], "practical", ""),
        (["游戏", "设定集与美术集"], "visual", ""),
        (["其他", "图册与写真"], "visual", ""),
        (["拿不准"], "social", ""),
    ],
)
def test_resolve_read_profile_covers_all_disciplines_and_boundaries(subjects, expected_type, expected_sub):
    prof = resolve_read_profile(subjects, title="测试书")
    assert prof["read_type"] == expected_type
    assert prof["sub_kind"] == expected_sub
    assert len(prof["blueprints"]) >= 1
    assert len(prof["extensions"]) >= 1
    if expected_sub == "essay" or subjects == ["拿不准"]:
        assert prof["boundary_hint"]
    assert len(list_read_profiles()) == len(READ_PROFILES)


def test_diagram_audit_normalizes_splits_overlong_and_degrades():
    raw_fenced = """```json
    {
      "blueprint": "argument_map",
      "caption": "论证测试",
      "reading_note": "先看主前提",
      "data": {
        "nodes": [
          {
            "id": "A1",
            "role": "premise",
            "label": "这是一段故意超过十八个字的超级冗长哲学前提命题用以检验分流",
            "blocks": ["B01", "B99"],
            "source": "原文"
          },
          {"id": "A2", "role": "conclusion", "label": "结论", "blocks": ["B98"], "source": "补充"}
        ],
        "edges": [
          {"from": "A1", "to": "A2", "label": "一段很长很长的关系说明文字"}
        ]
      }
    }
    ```"""
    audited = audit_blueprint_payload(raw_fenced, {"B01", "B02"}, fallback_block_ids=["B02"])
    assert audited["blueprint"] == "argument_map"
    n1, n2 = audited["data"]["nodes"]
    assert len(n1["label"]) == NODE_LABEL_LIMIT + 1
    assert n1["label"].endswith("…")
    assert "超级冗长哲学前提" in n1["detail"]
    assert n1["blocks"] == ["B01"]
    assert n2["blocks"] == ["B02"]
    assert len(audited["data"]["edges"][0]["label"]) == EDGE_LABEL_LIMIT + 1
    assert audited["audit_log"]

    # Over-dense graph degrades cleanly
    dense = {
        "blueprint": "argument_map",
        "caption": "过密图",
        "data": {"nodes": [{"id": f"N{i}", "label": f"节点{i}", "blocks": ["B01"]} for i in range(15)], "edges": []},
    }
    degraded = audit_blueprint_payload(dense, {"B01"}, fallback_block_ids=["B01"])
    assert degraded["blueprint"] == "fallback_list"
    assert degraded["items"][0]["blocks"] == ["B01"]

    # Broken text degrades cleanly
    broken = audit_blueprint_payload("{broken json...", {"B01"}, fallback_block_ids=["B01"])
    assert broken["blueprint"] == "fallback_list"
    assert broken["items"][0]["blocks"] == ["B01"]

    # Legacy family payload still compatible
    legacy = {
        "caption": "旧版流程图",
        "family": "flow",
        "data": {
            "nodes": [{"id": "N1", "label": "一步"}, {"id": "N2", "label": "二步"}],
            "edges": [{"from": "N1", "to": "N2"}],
        },
    }
    compat = audit_blueprint_payload(legacy, {"B01"}, fallback_block_ids=["B01"])
    assert compat["blueprint"] == "flow_swimlane"
    assert compat["family"] == "flow"
    assert compat["data"]["nodes"][0]["blocks"] == ["B01"]


def test_diagram_audit_supports_non_dag_blueprints():
    quad = {
        "blueprint": "quadrant_matrix",
        "caption": "优先级四象限",
        "data": {
            "xAxis": "成本",
            "yAxis": "收益",
            "quadrants": [{"pos": "q-top-left", "title": "高杠杆", "items": [{"text": "结论先行", "blocks": ["B01"]}]}],
        },
    }
    res_q = audit_blueprint_payload(quad, {"B01"})
    assert res_q["blueprint"] == "quadrant_matrix"
    assert res_q["data"]["quadrants"][0]["items"][0]["blocks"] == ["B01"]

    tl = {
        "blueprint": "parallel_timeline",
        "caption": "双线时间轴",
        "data": {
            "lanes": ["甲线", "乙线"],
            "rows": [{"time": "1911", "cells": [{"title": "事件甲", "note": "摘要", "blocks": ["B01"]}, None]}],
        },
    }
    res_tl = audit_blueprint_payload(tl, {"B01"})
    assert res_tl["blueprint"] == "parallel_timeline"
    assert len(res_tl["data"]["rows"][0]["cells"]) == 2

    prompt = prompt_for_diagram(
        blueprint="argument_map", read_type="philosophy", selected_text="选段", block_ids=["B01"]
    )
    assert "关系图" in prompt[0]["content"]
    assert "argument_map" in prompt[0]["content"]


def test_reading_service_chapter_profile_blueprint_extension_and_pin_figure(reading_root):
    bp_resp = {
        "blueprint": "tension_curve",
        "caption": "张力曲线",
        "reading_note": "先缓后急",
        "summary": "[原文] 节奏递进。",
        "data": {
            "yLabel": "张力",
            "points": [{"beat": "起势", "tension": 30, "emotion": "冷静", "blocks": ["B01"], "source": "原文"}],
        },
    }
    ext_resp = {
        "blueprint": "extension_checklist",
        "caption": "自检清单",
        "reading_note": "动作转化",
        "summary": "[补充] 先检查边界。",
        "items": [
            {"text": "检查前提", "detail": "确认是否适用", "checked": False, "blocks": ["B01"], "source": "原文"}
        ],
    }
    client = _FakeBlueprintClient([bp_resp, ext_resp])
    service = ReadingService(str(reading_root), client=client)

    ch = service.chapter("B0001", 1)
    assert ch["reading_profile"]["read_type"] == "literature"
    assert "reading" not in ch
    assert "tiers" not in ch

    bid = ch["blocks"][0]["id"]
    diag = service.assist(
        "B0001",
        1,
        "diagram",
        blueprint="tension_curve",
        read_type="literature",
        block_ids=[bid],
        selected_text=ch["blocks"][0]["text"],
    )
    assert diag["figure"]["blueprint"] == "tension_curve"
    assert diag["figure"]["data"]["points"][0]["blocks"] == [bid]
    assert diag["diagram_html"]

    ext = service.assist("B0001", 1, "checklist", block_ids=[bid], selected_text=ch["blocks"][0]["text"])
    assert ext["figure"]["blueprint"] == "extension_checklist"
    assert ext["figure"]["items"][0]["blocks"] == [bid]

    pinned = service.pin_assist_note(
        "B0001",
        1,
        {
            "action": "diagram",
            "title": "张力曲线",
            "block_ids": [bid],
            "content": diag["content"],
            "diagram_html": diag["diagram_html"],
            "figure": diag["figure"],
        },
    )
    assert pinned["figure"]["blueprint"] == "tension_curve"
    ch_after = service.chapter("B0001", 1)
    assert ch_after["assist_notes"][0]["figure"]["blueprint"] == "tension_curve"

    recs = service.records()
    assert recs["summary"]["assist"] == 2


def test_chapter_scope_full_context_caching_and_multi_block_selection(reading_root):
    # 为 B0002 (人文社科) 第1章测试全章作用域、长章均匀采样与多段选区
    chap_resp_1 = {
        "blueprint": "causal_loop",
        "caption": "礼治与差序回路",
        "reading_note": "覆盖全章各节",
        "summary": "[原文] 全章脉络。",
        "data": {
            "nodes": [
                {"id": "S1", "role": "condition", "label": "礼治靠传统教化", "blocks": ["B01"], "source": "原文"}
            ],
            "edges": [],
        },
    }
    chap_resp_2 = {
        "blueprint": "causal_loop",
        "caption": "重生成回路",
        "reading_note": "已刷新",
        "summary": "[原文] 重生成。",
        "data": {
            "nodes": [{"id": "S1", "role": "condition", "label": "差序格局外推", "blocks": ["B01"], "source": "原文"}],
            "edges": [],
        },
    }
    multi_resp = {
        "blueprint": "concept_matrix",
        "caption": "选段概念矩阵",
        "reading_note": "多段对齐",
        "summary": "[原文] 选段对比。",
        "data": {
            "columns": ["概念", "日常义", "作者义", "出处"],
            "rows": [
                {"key": "礼治", "everyday": "守规矩", "author": "传统教化约束", "blocks": ["B01"], "source": "原文"}
            ],
        },
    }
    client = _FakeBlueprintClient([chap_resp_1, chap_resp_2, multi_resp])
    service = ReadingService(str(reading_root), client=client)

    # 1. 空选区且未指定 scope="chapter" 时仍拦截
    with pytest.raises(ValueError, match="请先选中"):
        service.assist("B0002", 1, "diagram", block_ids=[], selected_text="")

    # 2. scope="chapter" 允许不传选区，覆盖全章所有段落并写入缓存
    first = service.assist("B0002", 1, "diagram", blueprint="causal_loop", scope="chapter")
    assert first["scope"] == "chapter"
    assert first["cached"] is False
    assert first["covered_blocks"] >= 1
    assert "整章全局" in client.calls[0]["messages"][0]["content"]
    assert "[B01]" in client.calls[0]["messages"][1]["content"]

    # 3. 再次调用命全章缓存，不重复调模型
    cached = service.assist("B0002", 1, "diagram", blueprint="causal_loop", scope="chapter")
    assert cached["cached"] is True
    assert cached["figure"]["caption"] == "礼治与差序回路"
    assert len(client.calls) == 1

    # 4. force=True 忽略缓存重生成
    refreshed = service.assist("B0002", 1, "diagram", blueprint="causal_loop", scope="chapter", force=True)
    assert refreshed["cached"] is False
    assert refreshed["figure"]["caption"] == "重生成回路"
    assert len(client.calls) == 2

    # 5. 超长章（>12,000 字）触发均匀骨架采样覆盖首中尾
    long_blocks = [{"id": f"B{i:02d}", "kind": "paragraph", "text": f"第{i}段内容" + "甲" * 350} for i in range(1, 50)]
    long_ctx, total_cnt, sampled = service._chapter_scope_context("B0002", 1, long_blocks)
    assert total_cnt == 49
    assert sampled is True
    assert "[B01]" in long_ctx
    assert "[B49]" in long_ctx

    # 6. 多选区传入时，prompt 带上多段块号标记；同时校验 chapter() 返回的 cached_tools 只含全章缓存
    ch_b2 = service.chapter("B0002", 1)
    assert "social_diagram_causal_loop" in ch_b2["cached_tools"]
    all_ids = [b["id"] for b in ch_b2["blocks"]]
    sel_res = service.assist(
        "B0002",
        1,
        "diagram",
        blueprint="concept_matrix",
        scope="selection",
        block_ids=all_ids,
        selected_text="合并选文",
    )
    assert sel_res["scope"] == "selection"
    assert sel_res["figure"]["blueprint"] == "concept_matrix"
    ch_b2_after = service.chapter("B0002", 1)
    assert "social_diagram_causal_loop" in ch_b2_after["cached_tools"]
    assert "social_diagram_concept_matrix" not in ch_b2_after["cached_tools"]
