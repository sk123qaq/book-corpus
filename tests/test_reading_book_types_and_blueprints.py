"""分层读书型映射、五步防重叠蓝图审计与扩展伴读单元测试。"""

from __future__ import annotations

import json

import pytest

from bookcorpus.reading.book_types import READ_PROFILES, list_read_profiles, resolve_read_profile
from bookcorpus.reading.diagram_audit import (
    EDGE_LABEL_LIMIT,
    MAX_GRAPH_NODES,
    NODE_LABEL_LIMIT,
    audit_blueprint_payload,
    prompt_for_diagram,
    prompt_for_extension,
)
from bookcorpus.reading.diagrams import render_assist_figure
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

    # Over-dense graph degrades cleanly AND preserves per-node original anchors without per-item warning spam
    dense = {
        "blueprint": "argument_map",
        "caption": "过密图",
        "data": {
            "nodes": [
                {"id": f"N{i}", "label": f"节点{i}", "blocks": [f"B{(i % 2) + 1:02d}"], "source": "原文"}
                for i in range(MAX_GRAPH_NODES + 1)
            ],
            "edges": [],
        },
    }
    degraded = audit_blueprint_payload(dense, {"B01", "B02"}, fallback_block_ids=["B01"])
    assert degraded["blueprint"] == "fallback_list"
    assert degraded["fallback_notice"]
    assert degraded["items"][0]["blocks"] == ["B01"]
    assert degraded["items"][1]["blocks"] == ["B02"]
    assert "warning" not in degraded["items"][0]

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

    # 5a. 16k 字（<=24,000）完整喂入且不截断 >600 字长段
    mid_blocks = [{"id": f"B{i:02d}", "kind": "paragraph", "text": f"第{i}段" + "乙" * 750} for i in range(1, 22)]
    mid_ctx, mid_cnt, mid_sampled = service._chapter_scope_context("B0002", 1, mid_blocks)
    assert mid_cnt == 21
    assert mid_sampled is False
    assert "乙" * 740 in mid_ctx

    # 5b. 超长章（>24,000 字）触发连续场景窗口采样，保留首尾、全部小标题及中段连续上下文
    long_blocks = [
        {
            "id": f"B{i:02d}",
            "kind": "heading" if i in (1, 35) else "paragraph",
            "text": f"第{i}段内容" + "甲" * 450,
        }
        for i in range(1, 70)
    ]
    long_ctx, total_cnt, sampled = service._chapter_scope_context("B0002", 1, long_blocks)
    assert total_cnt == 69
    assert sampled is True
    assert "[B01]" in long_ctx
    assert "[B35]" in long_ctx
    assert "[B69]" in long_ctx
    assert "连续场景窗口" in long_ctx

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


def test_history_six_orthogonal_tools_and_audits(reading_root):
    prof = resolve_read_profile(["人文社科", "历史"], title="东晋门阀政治")
    assert prof["read_type"] == "history"
    assert [bp["blueprint"] for bp in prof["blueprints"]] == [
        "parallel_timeline",
        "power_hierarchy",
        "spatial_route",
        "historical_causality",
    ]
    assert [ext["action"] for ext in prof["extensions"]] == [
        "historical_dossier",
        "compare_context",
    ]

    # 1. parallel_timeline: sort_year auto-sorts BCE/CE, strips redundant 补充 prefix, keeps aftermath
    tl_raw = {
        "blueprint": "parallel_timeline",
        "caption": "越颖对抗年表",
        "reading_note": "按阵营对照",
        "summary": "两线并行推进。",
        "data": {
            "lanes": ["司马越阵营", "刘石阵营"],
            "rows": [
                {
                    "sort_year": 317,
                    "time": "317年",
                    "cells": [
                        {"title": "司马睿建康称帝", "note": "补充：江左立国", "blocks": ["B02"], "source": "补充"},
                        None,
                    ],
                },
                {
                    "sort_year": -221,
                    "time": "公元前221年",
                    "cells": [None, {"title": "秦统一度量衡", "note": "远源背景", "blocks": ["B01"], "source": "原文"}],
                },
            ],
            "aftermath": [
                {"title": "宋人史论", "body": "【补充】王夫之评东晋拒使。", "blocks": ["B02"], "source": "补充"}
            ],
        },
    }
    tl_audited = audit_blueprint_payload(tl_raw, {"B01", "B02"})
    assert [r["sort_year"] for r in tl_audited["data"]["rows"]] == [-221, 317]
    assert tl_audited["data"]["rows"][1]["cells"][0]["note"] == "江左立国"
    assert tl_audited["data"]["aftermath"][0]["body"] == "王夫之评东晋拒使。"

    # 2. power_hierarchy: up to 16 nodes allowed without degradation, preserves historical roles & relation_type
    power_16 = {
        "blueprint": "power_hierarchy",
        "caption": "战前军事格局",
        "data": {
            "nodes": [
                {
                    "id": f"H{i}",
                    "role": "sovereign" if i == 0 else ("constraint" if i == 15 else "faction"),
                    "label": f"权力节点{i}",
                    "blocks": ["B01"],
                    "source": "原文",
                }
                for i in range(16)
            ],
            "edges": [{"from": "H0", "to": "H1", "relation_type": "subordinate", "label": "统辖"}],
        },
    }
    power_audited = audit_blueprint_payload(power_16, {"B01"})
    assert power_audited["blueprint"] == "power_hierarchy"
    assert len(power_audited["data"]["nodes"]) == 16
    assert power_audited["data"]["edges"][0]["relation_type"] == "subordinate"

    # 3. spatial_route: 2D coords, not_applicable, collapsed rejection, and legacy compat
    sp_prompt = prompt_for_diagram(blueprint="spatial_route", read_type="history", selected_text="洛阳与建康")
    assert "归一化相对地理方位坐标" in sp_prompt[0]["content"]
    sp_2d = {
        "blueprint": "spatial_route",
        "caption": "南北空间格局",
        "data": {
            "mode": "geopolitics",
            "scope_note": "幽并—中原—江左",
            "nodes": [
                {
                    "id": "S1",
                    "label": "幽州辽西",
                    "x": 75,
                    "y": 15,
                    "zone": "鲜卑段氏",
                    "role": "front",
                    "blocks": ["B01"],
                },
                {
                    "id": "S2",
                    "label": "建康",
                    "x": 78,
                    "y": 82,
                    "zone": "东晋",
                    "role": "capital",
                    "blocks": ["B02"],
                },
                {
                    "id": "S3",
                    "label": "平阳",
                    "x": 38,
                    "y": 35,
                    "zone": "汉赵刘氏",
                    "role": "capital",
                    "blocks": ["B01"],
                },
            ],
            "edges": [{"from": "S1", "to": "S2", "label": "渡海通使", "kind": "diplomacy", "route_group": "远交"}],
        },
    }
    sp_audited = audit_blueprint_payload(sp_2d, {"B01", "B02"})
    assert sp_audited["data"]["mode"] == "geopolitics"
    assert sp_audited["data"]["nodes"][0]["x"] == 75.0

    sp_na = audit_blueprint_payload(
        {
            "blueprint": "spatial_route",
            "caption": "审讯",
            "data": {"mode": "not_applicable", "not_applicable_reason": "纯公堂问对。"},
        },
        {"B01"},
    )
    assert sp_na["data"]["mode"] == "not_applicable"
    assert "not_applicable" in render_assist_figure(sp_na) or "纯公堂问对" in render_assist_figure(sp_na)

    sp_collapsed = audit_blueprint_payload(
        {
            "blueprint": "spatial_route",
            "caption": "聚拢退化",
            "data": {
                "mode": "campaign",
                "nodes": [
                    {"id": "S1", "label": "A", "x": 50, "y": 50, "blocks": ["B01"]},
                    {"id": "S2", "label": "B", "x": 51, "y": 51, "blocks": ["B01"]},
                    {"id": "S3", "label": "C", "x": 52, "y": 50, "blocks": ["B02"]},
                ],
                "edges": [],
            },
        },
        {"B01", "B02"},
    )
    assert sp_collapsed["blueprint"] == "fallback_list"
    assert sp_collapsed["items"][2]["blocks"] == ["B02"]

    sp_legacy = audit_blueprint_payload(
        {
            "blueprint": "spatial_route",
            "caption": "旧缓存",
            "data": {"nodes": [{"id": "S1", "label": "洛阳", "blocks": ["B01"]}], "edges": []},
        },
        {"B01"},
    )
    assert sp_legacy["blueprint"] == "spatial_route"
    assert sp_legacy["data"]["nodes"][0]["blocks"] == ["B01"]

    # 4. historical_causality
    caus_audited = audit_blueprint_payload(
        {
            "blueprint": "historical_causality",
            "caption": "通使决策因果",
            "data": {
                "nodes": [
                    {"id": "C1", "role": "structural", "label": "八王之乱余波", "blocks": ["B01"]},
                    {"id": "C2", "role": "consequence", "label": "拒刘石而联段氏", "blocks": ["B02"]},
                ],
                "edges": [{"from": "C1", "to": "C2", "label": "倒逼"}],
            },
        },
        {"B01", "B02"},
    )
    assert caus_audited["blueprint"] == "historical_causality"
    assert caus_audited["data"]["nodes"][0]["role"] == "structural"

    # 5 & 6. historical_dossier & compare_context sections + prefix cleaning
    ext_prompt = prompt_for_extension("historical_dossier", selected_text="人物")
    assert "出场人物与阵营速查" in ext_prompt[0]["content"]
    dossier_audited = audit_blueprint_payload(
        {
            "blueprint": "extension_dossier",
            "caption": "人物与名物",
            "summary": "",
            "reading_note": "注意人名与官职",
            "sections": [
                {
                    "id": "figures",
                    "title": "出场人物与阵营速查",
                    "items": [
                        {
                            "name": "温峤",
                            "role_badge": "东晋重臣",
                            "body": "补充：理刘琨冤案",
                            "blocks": ["B01"],
                            "source": "补充",
                        }
                    ],
                },
                {
                    "id": "glossary",
                    "title": "关键制度·条约·名物注",
                    "items": [{"name": "皇太弟", "body": "储君称号", "blocks": ["B02"], "source": "原文"}],
                },
            ],
        },
        {"B01", "B02"},
    )
    assert dossier_audited["summary"] == ""
    assert dossier_audited["sections"][0]["items"][0]["body"] == "理刘琨冤案"
    assert "出场人物与阵营速查" in render_assist_figure(dossier_audited)


def test_chapter_scope_context_separates_body_and_cited_footnotes(reading_root):
    service = ReadingService(str(reading_root), client=_FakeBlueprintClient([]))
    blocks = [
        {
            "id": "B01",
            "kind": "paragraph",
            "text": "正文第一段讨论核心问题。" * 40,
            "fn_refs": [{"marker": "54", "target_block": "B05"}],
        },
        {"id": "B02", "kind": "paragraph", "text": "正文第二段延续上述分析。" * 40},
        {"id": "B03", "kind": "image", "text": ""},
        {"id": "B04", "kind": "footnote", "marker": "1", "text": "前节无关长尾注不应混入" * 30},
        {
            "id": "B05",
            "kind": "footnote",
            "marker": "54",
            "text": "本节引用的关键考证注文。" * 30,
            "ref_blocks": ["B01"],
        },
    ]
    ctx, covered, sampled = service._chapter_scope_context("B0001", 1, blocks, budget=5000)
    assert covered == 2
    assert sampled is False
    assert "[B01]" in ctx
    assert "[B02]" in ctx
    assert "[B04]" not in ctx
    assert "【章末注释（仅供考证参考，勿喧宾夺主）】" in ctx
    assert "[B05]" in ctx
    fn_part = ctx.split("【章末注释（仅供考证参考，勿喧宾夺主）】", 1)[1]
    assert len(fn_part) <= 950  # <= 18% of 5000 + header margin


def test_literature_v2_five_tools_schemas_and_anti_spoiler(reading_root):
    from bookcorpus.reading.diagram_audit import prompt_for_extension

    prof = resolve_read_profile(["文学", "戏剧"], title="雷雨")
    assert [b["blueprint"] for b in prof["blueprints"]] == ["tension_curve", "relation_graph", "spatial_route"]
    assert [e["action"] for e in prof["extensions"]] == ["narrative_clues", "imagery"]

    # 1. Anti-spoiler hard rule in all literature diagram & extension prompts
    for bp in ("tension_curve", "relation_graph", "spatial_route"):
        msgs = prompt_for_diagram(blueprint=bp, read_type="literature", selected_text="第三幕正文", block_ids=["B01"])
        assert "严禁剧透" in msgs[0]["content"]
    for act in ("narrative_clues", "imagery"):
        msgs = prompt_for_extension(act, read_type="literature", sub_kind="drama", selected_text="第三幕正文")
        assert "严禁剧透" in msgs[0]["content"]

    # 2. tension_curve beat_kind & note + legacy compatibility
    tc = audit_blueprint_payload(
        {
            "blueprint": "tension_curve",
            "caption": "第三幕张力",
            "data": {
                "points": [
                    {
                        "beat": "闷热开场",
                        "beat_kind": "setup",
                        "tension": 25,
                        "emotion": "压抑沉闷",
                        "note": "鲁家饭后静默对峙，潜流暗涌。",
                        "blocks": ["B01", "B02"],
                        "source": "原文",
                    },
                    {
                        "beat": "蘩漪关窗",
                        "beat_kind": "climax",
                        "tension": 92,
                        "emotion": "绝望决绝",
                        "note": "蘩漪从窗外反锁窗户逼退周萍，冲突爆发。",
                        "blocks": ["B517"],
                        "source": "原文",
                    },
                ]
            },
        },
        {"B01", "B02", "B517"},
    )
    assert tc["data"]["points"][0]["beat_kind"] == "setup"
    assert tc["data"]["points"][1]["beat_kind"] == "climax"
    assert "反锁" in tc["data"]["points"][1]["note"]

    # 3. relation_graph camp/arc/rel_kind + 16-node capacity + legacy compat
    rg = audit_blueprint_payload(
        {
            "blueprint": "relation_graph",
            "caption": "鲁家与周家关系",
            "data": {
                "nodes": [
                    {
                        "id": "P1",
                        "role": "protagonist",
                        "camp": "周家",
                        "label": "蘩漪",
                        "arc": "从隐忍走向绝望反扑",
                        "detail": "暗中追踪至此，锁死退路。",
                        "blocks": ["B517"],
                        "source": "原文",
                    },
                    {
                        "id": "P2",
                        "role": "antagonist",
                        "camp": "周家",
                        "label": "周萍",
                        "arc": "企图逃离却当场受困",
                        "blocks": ["B02"],
                        "source": "原文",
                    },
                ],
                "edges": [{"from": "P1", "to": "P2", "rel_kind": "conflict", "label": "逼迫对峙", "note": "窗外锁死"}],
            },
        },
        {"B02", "B517"},
    )
    assert rg["data"]["nodes"][0]["camp"] == "周家"
    assert rg["data"]["nodes"][0]["arc"] == "从隐忍走向绝望反扑"
    assert rg["data"]["edges"][0]["rel_kind"] == "conflict"

    # 4. spatial_route 3 modes: stage_blocking, route_map, not_applicable + history legacy compat
    sb = audit_blueprint_payload(
        {
            "blueprint": "spatial_route",
            "caption": "四凤内屋调度",
            "data": {
                "mode": "stage_blocking",
                "stage_desc": "四凤内屋封闭舞台",
                "nodes": [
                    {
                        "id": "S1",
                        "role": "threshold",
                        "zone": "后墙",
                        "x": 50,
                        "y": 10,
                        "label": "中间木窗",
                        "blocks": ["B01"],
                    },
                    {
                        "id": "S2",
                        "role": "outside",
                        "zone": "窗外",
                        "x": 55,
                        "y": 4,
                        "label": "窗外暗处",
                        "blocks": ["B517"],
                    },
                ],
                "edges": [{"from": "S2", "to": "S1", "kind": "barrier", "actor": "蘩漪", "label": "反关木窗"}],
            },
        },
        {"B01", "B517"},
    )
    assert sb["data"]["mode"] == "stage_blocking"
    assert sb["data"]["edges"][0]["actor"] == "蘩漪"

    na = audit_blueprint_payload(
        {
            "blueprint": "spatial_route",
            "caption": "纯心理独白",
            "data": {
                "mode": "not_applicable",
                "not_applicable_reason": "本章为封闭内心独白，未发生物理空间转换或舞台调度。",
                "nodes": [],
                "edges": [],
            },
        },
        {"B01"},
    )
    assert na["blueprint"] == "spatial_route"
    assert na["data"]["mode"] == "not_applicable"
    assert "内心独白" in na["data"]["not_applicable_reason"]

    hist = audit_blueprint_payload(
        {
            "blueprint": "spatial_route",
            "caption": "战役疆域",
            "data": {"nodes": [{"id": "H1", "role": "condition", "label": "长安", "blocks": ["B01"]}], "edges": []},
        },
        {"B01"},
    )
    assert hist["blueprint"] == "spatial_route"
    assert "mode" not in hist["data"]

    # 5. Extension dossier sections + prefix stripping
    ext = audit_blueprint_payload(
        {
            "blueprint": "extension_dossier",
            "caption": "伏笔与视角",
            "items": [
                {
                    "section": "叙事视角与时间结构",
                    "title": "[原文] 倒叙开篇",
                    "body": "补充：开篇先点出凶案日期，再回溯当日清晨动线。",
                    "blocks": ["B01"],
                    "source": "原文",
                }
            ],
        },
        {"B01"},
    )
    assert ext["items"][0]["section"] == "叙事视角与时间结构"
    assert ext["items"][0]["title"] == "倒叙开篇"
    assert ext["items"][0]["body"].startswith("开篇先点出")
