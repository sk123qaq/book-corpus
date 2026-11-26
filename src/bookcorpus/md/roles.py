"""章节角色（chapter role）无损读取契约：YAML front matter 与 ``.manifest.json``。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

ChapterRole = Literal["body", "notes", "front_back"]

CHAPTER_ROLES: tuple[ChapterRole, ...] = ("body", "notes", "front_back")
_ROLE_LOOKUP: dict[str, ChapterRole] = {"body": "body", "notes": "notes", "front_back": "front_back"}
DEFAULT_CHAPTER_ROLE: ChapterRole = "body"
_FRONT_MATTER_LIMIT = 60


def normalize_chapter_role(value: Any, default: ChapterRole = DEFAULT_CHAPTER_ROLE) -> ChapterRole:
    """将任意输入规范化为合法的章节角色，未标注或未知值回退到 ``default``。"""
    if isinstance(value, str):
        role = _ROLE_LOOKUP.get(value.strip().lower())
        if role is not None:
            return role
    return default


def _extract_role_value(entry: Any) -> ChapterRole | None:
    if isinstance(entry, str):
        return _ROLE_LOOKUP.get(entry.strip().lower())
    if isinstance(entry, Mapping):
        for key in ("chapter_role", "role"):
            val = entry.get(key)
            if isinstance(val, str):
                role = _ROLE_LOOKUP.get(val.strip().lower())
                if role is not None:
                    return role
    return None


def load_manifest_chapter_roles(md_dir: Path | str | None) -> dict[str, ChapterRole]:
    """从 ``<md_dir>/.manifest.json`` 的 ``chapter_roles`` 读取章级角色映射。"""
    if not md_dir:
        return {}
    manifest_path = Path(md_dir) / ".manifest.json"
    try:
        raw = manifest_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, Mapping):
        return {}
    roles_raw = data.get("chapter_roles")
    if not isinstance(roles_raw, Mapping):
        return {}
    out: dict[str, ChapterRole] = {}
    for key, entry in roles_raw.items():
        if not isinstance(key, str):
            continue
        k = key.strip()
        role = _extract_role_value(entry)
        if not k or role is None:
            continue
        out[k] = role
        name = Path(k).name
        if name and name not in out:
            out[name] = role
    return out


def resolve_chapter_role(
    path: Path | str | None = None,
    *,
    meta: Mapping[str, Any] | None = None,
    manifest_roles: Mapping[str, Any] | None = None,
    md_dir: Path | str | None = None,
    file_name: str | None = None,
) -> ChapterRole:
    """按优先级解析章节角色：YAML front matter > ``.manifest.json`` ``chapter_roles`` > ``body``。"""
    if meta:
        fm_role = _extract_role_value(meta.get("chapter_role"))
        if fm_role is not None:
            return fm_role

    roles_map: Mapping[str, Any]
    if manifest_roles is not None:
        roles_map = manifest_roles
    else:
        resolved_dir = Path(md_dir) if md_dir else (Path(path).parent if path else None)
        roles_map = load_manifest_chapter_roles(resolved_dir)

    candidates: list[str] = []
    if path is not None:
        p = Path(path)
        candidates.extend([str(path), p.as_posix(), p.name, p.stem])
    if file_name:
        fp = Path(file_name)
        candidates.extend([file_name, fp.name, fp.stem])

    for key in candidates:
        if key and key in roles_map:
            role = _extract_role_value(roles_map[key])
            if role is not None:
                return role

    return DEFAULT_CHAPTER_ROLE


def _read_front_matter_meta(path: Path) -> dict[str, str]:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
            if first.strip() != "---":
                return {}
            meta: dict[str, str] = {}
            for _ in range(1, _FRONT_MATTER_LIMIT):
                line = fh.readline()
                if not line:
                    return {}
                if line.strip() == "---":
                    return meta
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if key:
                    meta[key] = value
    except OSError:
        return {}
    return {}


def read_chapter_role(
    path: Path | str,
    *,
    manifest_roles: Mapping[str, Any] | None = None,
) -> ChapterRole:
    """只读章节文件头（YAML front matter）与所在目录 ``.manifest.json`` 判别章节角色。"""
    p = Path(path)
    meta = _read_front_matter_meta(p)
    return resolve_chapter_role(
        p,
        meta=meta,
        manifest_roles=manifest_roles,
        md_dir=p.parent,
    )
