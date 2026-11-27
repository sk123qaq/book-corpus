"""SSOT 伴读视角与学科分类导出器：从 book_types.py 与 classify_model.py 派生唯一真源 JSON。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from bookcorpus.reading.book_types import (
    _HISTORY_SOCIAL_SUB2,
    _HISTORY_TECH_ART_SUB2,
    _LIT_FICTION_SUB2,
    _PHILOSOPHY_SUB2,
    _PRACTICAL_GAME_SUB2,
    _PRACTICAL_SUBJECT1,
    _PRACTICAL_TECH_SUB2,
    _SCIENCE_SUB2,
    _SOCIAL_SUB2,
    _VISUAL_ART_SUB2,
    DEFAULT_READ_TYPE,
    LITERATURE_SUBKIND_HINTS,
    READ_PROFILES,
    _infer_pair,
    list_read_profiles,
)
from bookcorpus.scripts.classify_model import NO_SUB, TAXONOMY, UNCERTAIN

SCHEMA_VERSION = 1


def _build_routing_rules() -> dict[str, Any]:
    """构建完整且确定性的学科到伴读视角路由规则表。"""
    subject_sets = {
        "practical_subject1": sorted(_PRACTICAL_SUBJECT1),
        "practical_tech_sub2": sorted(_PRACTICAL_TECH_SUB2),
        "practical_game_sub2": sorted(_PRACTICAL_GAME_SUB2),
        "lit_fiction_sub2": sorted(_LIT_FICTION_SUB2),
        "history_tech_art_sub2": sorted(_HISTORY_TECH_ART_SUB2),
        "history_social_sub2": sorted(_HISTORY_SOCIAL_SUB2),
        "science_sub2": sorted(x for x in _SCIENCE_SUB2 if x),
        "philosophy_sub2": sorted(_PHILOSOPHY_SUB2),
        "social_sub2": sorted(x for x in _SOCIAL_SUB2 if x),
        "visual_art_sub2": sorted(_VISUAL_ART_SUB2),
    }

    rules: list[dict[str, Any]] = [
        {
            "subject1_in": sorted(_PRACTICAL_SUBJECT1),
            "default": {"read_type": "practical", "sub_kind": "", "boundary_hint": ""},
            "sub2_overrides": [],
        },
        {
            "subject1_in": ["科学技术"],
            "default": {"read_type": "science", "sub_kind": "", "boundary_hint": ""},
            "sub2_overrides": [
                {"subject2_in": sorted(_PRACTICAL_TECH_SUB2), "read_type": "practical", "sub_kind": ""},
                {
                    "subject2_in": sorted(x for x in _SCIENCE_SUB2 if x),
                    "read_type": "science",
                    "sub_kind": "",
                },
            ],
        },
        {
            "subject1_in": ["游戏"],
            "default": {"read_type": "social", "sub_kind": "", "boundary_hint": ""},
            "sub2_overrides": [
                {"subject2_in": ["设定集与美术集"], "read_type": "visual", "sub_kind": ""},
                {"subject2_in": sorted(_PRACTICAL_GAME_SUB2), "read_type": "practical", "sub_kind": ""},
            ],
        },
        {
            "subject1_in": ["文学"],
            "default": {"read_type": "literature", "sub_kind": "fiction", "boundary_hint": ""},
            "sub2_overrides": [
                {"subject2_in": ["戏剧"], "read_type": "literature", "sub_kind": "drama", "boundary_hint": ""},
                {"subject2_in": ["诗歌"], "read_type": "literature", "sub_kind": "poetry", "boundary_hint": ""},
                {
                    "subject2_in": ["散文随笔"],
                    "read_type": "literature",
                    "sub_kind": "essay",
                    "boundary_hint": _infer_pair(["文学", "散文随笔"])[2],
                },
                {
                    "subject2_in": sorted([*_LIT_FICTION_SUB2, NO_SUB]),
                    "read_type": "literature",
                    "sub_kind": "fiction",
                    "boundary_hint": "",
                },
            ],
        },
        {
            "subject1_in": ["建筑与古迹"],
            "default": {"read_type": "history", "sub_kind": "", "boundary_hint": ""},
            "sub2_overrides": [],
        },
        {
            "subject1_in": ["艺术与图像"],
            "default": {"read_type": "visual", "sub_kind": "", "boundary_hint": ""},
            "sub2_overrides": [
                {"subject2_in": sorted(_HISTORY_TECH_ART_SUB2), "read_type": "history", "sub_kind": ""},
                {
                    "subject2_in": sorted([*_VISUAL_ART_SUB2, NO_SUB]),
                    "read_type": "visual",
                    "sub_kind": "",
                },
            ],
        },
        {
            "subject1_in": ["影视动画漫画"],
            "default": {"read_type": "visual", "sub_kind": "", "boundary_hint": ""},
            "sub2_overrides": [
                {"subject2_in": ["电影与影史"], "read_type": "history", "sub_kind": ""},
            ],
        },
        {
            "subject1_in": ["人文社科"],
            "default": {"read_type": "social", "sub_kind": "", "boundary_hint": ""},
            "sub2_overrides": [
                {"subject2_in": sorted(_HISTORY_SOCIAL_SUB2), "read_type": "history", "sub_kind": ""},
                {"subject2_in": sorted(_PHILOSOPHY_SUB2), "read_type": "philosophy", "sub_kind": ""},
                {
                    "subject2_in": sorted(x for x in _SOCIAL_SUB2 if x),
                    "read_type": "social",
                    "sub_kind": "",
                },
            ],
        },
        {
            "subject1_in": ["其他"],
            "default": {
                "read_type": DEFAULT_READ_TYPE,
                "sub_kind": "",
                "boundary_hint": _infer_pair(["其他", ""])[2],
            },
            "sub2_overrides": [
                {"subject2_in": ["图册与写真"], "read_type": "visual", "sub_kind": "", "boundary_hint": ""},
            ],
        },
        {
            "subject1_in": [UNCERTAIN, ""],
            "default": {
                "read_type": DEFAULT_READ_TYPE,
                "sub_kind": "",
                "boundary_hint": _infer_pair([UNCERTAIN])[2],
            },
            "sub2_overrides": [],
        },
    ]

    candidate_pairs: list[tuple[str, str]] = [("", ""), (UNCERTAIN, "")]
    for sub1, sub2_list in TAXONOMY.items():
        candidate_pairs.append((sub1, ""))
        candidate_pairs.append((sub1, NO_SUB))
        for sub2 in sub2_list:
            candidate_pairs.append((sub1, sub2))

    resolved_pairs: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for sub1, sub2 in candidate_pairs:
        if (sub1, sub2) in seen_pairs:
            continue
        seen_pairs.add((sub1, sub2))
        read_type, sub_kind, boundary_hint = _infer_pair([sub1, sub2] if sub2 else ([sub1] if sub1 else []))
        resolved_pairs.append(
            {
                "subject1": sub1,
                "subject2": sub2,
                "read_type": read_type,
                "sub_kind": sub_kind,
                "boundary_hint": boundary_hint,
            }
        )

    return {
        "default_read_type": DEFAULT_READ_TYPE,
        "uncertain_label": UNCERTAIN,
        "no_sub_label": NO_SUB,
        "subject_sets": subject_sets,
        "rules": rules,
        "resolved_pairs": resolved_pairs,
    }


def build_profiles_payload() -> dict[str, Any]:
    """构建完整、确定性的 profiles.generated.json 载荷字典。"""
    profiles = list_read_profiles()
    assert len(profiles) == len(READ_PROFILES)
    return {
        "schema_version": SCHEMA_VERSION,
        "default_read_type": DEFAULT_READ_TYPE,
        "taxonomy": {key: list(values) for key, values in TAXONOMY.items()},
        "profiles": profiles,
        "literature_subkind_hints": dict(LITERATURE_SUBKIND_HINTS),
        "routing_rules": _build_routing_rules(),
    }


def export_profiles_json(*, indent: int = 2) -> str:
    """序列化规范化 JSON 字符串（确定性键序与 UTF-8 文本）。"""
    payload = build_profiles_payload()
    return json.dumps(payload, ensure_ascii=False, indent=indent) + "\n"


def write_profiles_json(out_path: Path | str, *, indent: int = 2) -> Path:
    """原子写出 profiles.generated.json 到目标路径。"""
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp.{uuid.uuid4().hex[:8]}")
    tmp.write_text(export_profiles_json(indent=indent), encoding="utf-8")
    tmp.replace(target)
    return target
