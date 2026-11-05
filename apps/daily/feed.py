#!/usr/bin/env python3
"""书库日课的数据加载与校验。

读 <root>/日课/*.json，按 date 倒序返回。文件名必须等于 date，
坏 JSON、坏日期、字段不合约的期直接跳过并记入 errors，单份坏文件不影响整页。
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

DAY_DIR = "日课"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _issue_dir(root: Path) -> Path:
    return Path(root).expanduser().resolve() / DAY_DIR


def _clean_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是字符串。")
    return value


def _clean_int(value: Any, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} 必须是整数。")
    if value < minimum:
        raise ValueError(f"{field} 不能小于 {minimum}。")
    return value


def _clean_date(value: Any, field: str) -> str:
    text = _clean_text(value, field)
    if not DATE_RE.fullmatch(text):
        raise ValueError(f"{field} 必须是 YYYY-MM-DD。")
    try:
        dt.date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} 不是合法日期。") from exc
    return text


def _clean_dict(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} 必须是对象。")
    return value


def _clean_md_path(value: Any, field: str) -> str:
    text = _clean_text(value, field)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} 必须是书库内的相对路径。")
    return path.as_posix()


def _validate_issue(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("顶层必须是对象。")

    version = _clean_int(payload.get("version"), "version", 1)
    date = _clean_date(payload.get("date"), "date")
    kind = _clean_text(payload.get("kind"), "kind")
    kind_label = _clean_text(payload.get("kindLabel"), "kindLabel")
    subject = _clean_text(payload.get("subject"), "subject")

    book = _clean_dict(payload.get("book"), "book")
    for field in ("uid", "title", "author"):
        _clean_text(book.get(field), f"book.{field}")

    chapter = _clean_dict(payload.get("chapter"), "chapter")
    chapter["index"] = _clean_int(chapter.get("index"), "chapter.index", 1)
    if "label" in chapter:
        chapter["label"] = _clean_text(chapter["label"], "chapter.label")
    _clean_text(chapter.get("title"), "chapter.title")
    chapter["path"] = _clean_md_path(chapter.get("path"), "chapter.path")

    headline = _clean_text(payload.get("headline"), "headline")

    body = payload.get("body")
    if not isinstance(body, list) or not body:
        raise ValueError("body 必须是非空段落数组。")
    for index, paragraph in enumerate(body):
        _clean_text(paragraph, f"body[{index}]")

    quote = _clean_dict(payload.get("quote"), "quote")
    _clean_text(quote.get("text"), "quote.text")
    quote["line"] = _clean_int(quote.get("line"), "quote.line", 1)

    why = _clean_text(payload.get("why"), "why")

    research = _clean_dict(payload.get("research"), "research")
    _clean_text(research.get("chapters"), "research.chapters")
    research["web"] = _clean_int(research.get("web"), "research.web")
    queries = research.get("queries", [])
    if not isinstance(queries, list) or any(not isinstance(item, str) for item in queries):
        raise ValueError("research.queries 必须是字符串数组。")
    if "note" in research:
        _clean_text(research["note"], "research.note")

    return dict(
        payload,
        version=version,
        date=date,
        kind=kind,
        kindLabel=kind_label,
        subject=subject,
        headline=headline,
        body=body,
        quote=quote,
        why=why,
        research=research,
    )


def _read_issue(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"读取失败：{exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败：第 {exc.lineno} 行第 {exc.colno} 列。") from exc
    return _validate_issue(payload)


def load_feed(root: Path) -> dict[str, Any]:
    """读日课目录，返回 {today, dir, issues, errors}。只读，不缓存。"""
    directory = _issue_dir(root)
    feed: dict[str, Any] = {
        "today": dt.date.today().isoformat(),
        "dir": str(directory),
        "issues": [],
        "errors": [],
    }

    if not directory.is_dir():
        feed["errors"].append({"file": None, "reason": f"日课目录不存在：{directory}"})
        return feed

    raw_issues: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        name = path.name
        stem = path.stem
        if not DATE_RE.fullmatch(stem) or not _is_date(stem):
            feed["errors"].append({"file": name, "reason": "文件名不是合法日期，应为 YYYY-MM-DD。"})
            continue
        try:
            issue = _read_issue(path)
        except ValueError as exc:
            feed["errors"].append({"file": name, "reason": str(exc)})
            continue
        if issue["date"] != stem:
            feed["errors"].append({"file": name, "reason": f"文件名与 date 不一致，文件里是 {issue['date']}。"})
            continue
        raw_issues.append(issue)

    raw_issues.sort(key=lambda item: item["date"])
    for number, issue in enumerate(raw_issues, start=1):
        issue["issue"] = number
    feed["issues"] = list(reversed(raw_issues))
    return feed


def _is_date(text: str) -> bool:
    try:
        return bool(dt.date.fromisoformat(text))
    except ValueError:
        return False
