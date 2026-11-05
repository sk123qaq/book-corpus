"""书库日课数据加载的验收测试。只用临时目录造假数据。"""

from __future__ import annotations

import copy
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from apps.daily.feed import load_feed

BASE_ISSUE = {
    "version": 1,
    "date": "2026-11-05",
    "kind": "expository",
    "kindLabel": "论述",
    "subject": "行为经济学",
    "book": {"uid": "B0003", "title": "“错误”的行为", "author": "理查德·塞勒"},
    "chapter": {
        "index": 2,
        "title": "第2章 机会成本和禀赋效应",
        "path": "books/B0003/md/04_第2章机会成本和禀赋效应.md",
    },
    "headline": "一百美元的酒，他不卖，也不买",
    "body": ["第一段。", "第二段。"],
    "quote": {"text": "支付附加费是要从兜里掏钱的。", "line": 54},
    "why": "同一笔钱，换个说法，手就松紧不同。",
    "research": {"chapters": "第1–7章", "web": 0, "queries": [], "note": "测试数据"},
}


def make_issue(date: str) -> dict:
    payload = copy.deepcopy(BASE_ISSUE)
    payload["date"] = date
    return payload


def write_issue(directory: Path, date: str, name: str | None = None) -> Path:
    path = directory / f"{name or date}.json"
    path.write_text(json.dumps(make_issue(date), ensure_ascii=False), encoding="utf-8")
    return path


def make_day_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "日课"
    directory.mkdir(parents=True)
    return directory


def test_newest_first_with_issue_numbers(tmp_path):
    directory = make_day_dir(tmp_path)
    for date in ("2026-11-04", "2026-11-03", "2026-11-05"):
        write_issue(directory, date)

    feed = load_feed(tmp_path)

    assert [item["date"] for item in feed["issues"]] == ["2026-11-05", "2026-11-04", "2026-11-03"]
    assert [item["issue"] for item in feed["issues"]] == [3, 2, 1]
    assert feed["today"] == dt.date.today().isoformat()
    assert Path(feed["dir"]).is_absolute()
    assert feed["errors"] == []


def test_bad_json_is_skipped_without_breaking_numbering(tmp_path):
    directory = make_day_dir(tmp_path)
    write_issue(directory, "2026-11-04")
    write_issue(directory, "2026-11-05")
    (directory / "2026-11-03.json").write_text("{ 坏掉的 JSON", encoding="utf-8")

    feed = load_feed(tmp_path)

    assert [item["date"] for item in feed["issues"]] == ["2026-11-05", "2026-11-04"]
    assert [item["issue"] for item in feed["issues"]] == [2, 1]
    assert len(feed["errors"]) == 1
    assert feed["errors"][0]["file"] == "2026-11-03.json"
    assert "JSON" in feed["errors"][0]["reason"]


def test_filename_must_match_date(tmp_path):
    directory = make_day_dir(tmp_path)
    write_issue(directory, "2026-11-05", name="2026-11-04")

    feed = load_feed(tmp_path)

    assert feed["issues"] == []
    assert len(feed["errors"]) == 1
    assert feed["errors"][0]["file"] == "2026-11-04.json"
    assert "不一致" in feed["errors"][0]["reason"]


def test_invalid_filename_is_skipped(tmp_path):
    directory = make_day_dir(tmp_path)
    (directory / "notes.json").write_text(json.dumps(make_issue("2026-11-05")), encoding="utf-8")
    (directory / "2026-11-4.json").write_text(json.dumps(make_issue("2026-11-04")), encoding="utf-8")

    feed = load_feed(tmp_path)

    assert feed["issues"] == []
    assert {item["file"] for item in feed["errors"]} == {"notes.json", "2026-11-4.json"}
    assert all("文件名" in item["reason"] for item in feed["errors"])


def test_invalid_fields_are_skipped(tmp_path):
    directory = make_day_dir(tmp_path)
    payload = make_issue("2026-11-05")
    payload["body"] = []
    (directory / "2026-11-05.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    feed = load_feed(tmp_path)

    assert feed["issues"] == []
    assert len(feed["errors"]) == 1
    assert "body" in feed["errors"][0]["reason"]


def test_missing_directory_returns_error_not_exception(tmp_path):
    feed = load_feed(tmp_path)

    assert feed["issues"] == []
    assert len(feed["errors"]) == 1
    assert feed["errors"][0]["file"] is None
    assert "目录不存在" in feed["errors"][0]["reason"]
    assert feed["dir"] == str((tmp_path / "日课").resolve())


def test_empty_directory_returns_empty_feed(tmp_path):
    make_day_dir(tmp_path)

    feed = load_feed(tmp_path)

    assert feed["issues"] == []
    assert feed["errors"] == []
