"""抽样审计章节实际走的渲染路径：depth、legacy 或 missing。

只读磁盘，不触发模型。depth 章按 G10 守恒账、G1 原文逐字、G5 版式、
G8 结构检查；legacy 章沿用旧包的 G1/G5/G8。只有全部样本都走深读且
门禁通过才返回 0。
"""

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from ..library import Library
from ..quality import check_html
from ..service import _Structure, audit_pack, source_texts
from ..store import Store
from ..tiers import TIER_ORDER, project
from .disposition import audit_conservation

QUALITY_KEYS = {
    "contrast",
    "font-size",
    "measure",
    "line-height",
    "paragraph-marker",
    "external-resource",
    "heading-gap",
}
SECTION_RE = re.compile(r"<section\b[^>]*>|</section\s*>", re.I)


def _parser():
    parser = argparse.ArgumentParser(
        prog="python -m bookcorpus.reading.depth.audit",
        description="抽样审计章节渲染路径与深读门禁",
    )
    parser.add_argument("--root", help="实例根目录；默认从当前目录向上找 books.toml")
    parser.add_argument("--limit", type=int, default=20, help="最多抽多少章；0 表示全部")
    parser.add_argument("--out", help="把 JSON 报告写到这个路径；旁边再写同名 .md")
    return parser


def _now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _source_section(html_text):
    """截出 data-body="source" 的 section；找不到返回 None。"""
    marker = html_text.find('data-body="source"')
    if marker < 0:
        return None
    start = html_text.rfind("<section", 0, marker)
    if start < 0:
        start = marker
    depth = 0
    for match in SECTION_RE.finditer(html_text, start):
        if match.group(0).startswith("</"):
            depth -= 1
            if depth <= 0:
                return html_text[start : match.end()]
        else:
            depth += 1
    return html_text[start:]


def _source_gate(blocks, html_text):
    source = _source_section(html_text)
    if source is None:
        return {
            "id": "G1",
            "name": "原文逐字",
            "status": "fail",
            "detail": '缺 data-body="source" 原文区',
        }
    rendered = source_texts(source)
    missing = []
    for block in blocks:
        block_id = block.get("id") or ""
        text = (block.get("text") or "").strip()
        if not block_id or block_id not in rendered:
            missing.append(block_id or "?")
            continue
        if text and rendered[block_id] != text:
            missing.append(block_id)
    return {
        "id": "G1",
        "name": "原文逐字",
        "status": "pass" if not missing else "fail",
        "detail": "全部原文块按源序逐字命中" if not missing else "缺失或改写：" + ", ".join(missing[:8]),
    }


def _quality_gate(html_text):
    related = [finding for finding in check_html(html_text) if finding["key"] in QUALITY_KEYS]
    fails = [finding for finding in related if finding["level"] == "fail"]
    warns = [finding for finding in related if finding["level"] == "warn"]
    picked = fails or warns
    status = "fail" if fails else "warn" if warns else "pass"
    return {
        "id": "G5",
        "name": "版式",
        "status": status,
        "detail": "对比度、字号、行长、行高、分段均在范围内"
        if not picked
        else "；".join(finding["detail"] for finding in picked[:8]),
    }


def _structure_gate(html_text):
    structure = _Structure()
    structure.feed(html_text)
    problems = ([] if structure.doctype else ["缺 doctype"]) + structure.errors
    if structure.stack:
        problems.append("未闭合：" + ",".join(structure.stack[-4:]))
    return {
        "id": "G8",
        "name": "结构完整",
        "status": "pass" if not problems else "fail",
        "detail": "doctype、标签闭合与嵌套正确" if not problems else "；".join(problems[:8]),
    }


def _conservation_gate(store, uid, n, blocks):
    model = store.depth_json(uid, n, "chapter_model.json")
    ledger = store.depth_json(uid, n, "ledger.json")
    missing = [
        name for name, value in (("chapter_model.json", model), ("ledger.json", ledger)) if not isinstance(value, dict)
    ]
    if missing:
        return {
            "id": "G10",
            "name": "守恒账",
            "status": "fail",
            "detail": "缺 " + "、".join(missing),
        }
    try:
        gate = audit_conservation(model, ledger, blocks)
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "id": "G10",
            "name": "守恒账",
            "status": "fail",
            "detail": f"审计异常：{exc}",
        }
    return {
        "id": "G10",
        "name": "守恒账",
        "status": gate["status"],
        "detail": gate.get("detail", ""),
    }


def _depth_gates(store, uid, n, blocks, html_text):
    return [
        _conservation_gate(store, uid, n, blocks),
        _source_gate(blocks, html_text),
        _quality_gate(html_text),
        _structure_gate(html_text),
    ]


def _legacy_gates(store, uid, n, blocks, html_text):
    annotation = store.annotation(uid, n)
    manifest = store.manifest(uid, n) or {}
    try:
        projections = {tier: project(blocks, annotation, tier) for tier in TIER_ORDER}
        report = audit_pack(
            blocks,
            projections,
            html_text,
            annotation,
            bool(manifest.get("draft")),
        )
        gates = [gate for gate in report["gates"] if gate["id"] in ("G1", "G5", "G8")]
        if len(gates) == 3:
            return gates
    except (KeyError, TypeError, ValueError) as exc:
        return [
            {
                "id": "G1",
                "name": "原文逐字",
                "status": "fail",
                "detail": f"旧包审计异常：{exc}",
            }
        ]
    return gates


def _audit_chapter(library, store, uid, n):
    raw = library.read_chapter(uid, n)
    if raw is None:
        return None
    blocks = raw.get("blocks") or []
    source_sha = raw.get("source_sha256")
    depth_state = store.depth_state(uid, n, source_sha)
    depth_html_path = store.depth_path(uid, n, "chapter.html")
    depth_ready = depth_state.get("status") == "ready" and Path(depth_html_path).is_file()
    html_text = _read(depth_html_path)
    manifest = store.manifest(uid, n)

    if depth_state.get("status") in ("ready", "model") and Path(depth_html_path).is_file():
        path = "depth"
        fallback_reason = None
        gates = _depth_gates(store, uid, n, blocks, html_text)
    elif manifest:
        path = "legacy"
        fallback_reason = "旧包按旧路径打开"
        html_text = _read(store.chapter_html_path(uid, n))
        gates = _legacy_gates(store, uid, n, blocks, html_text)
    else:
        path = "missing"
        fallback_reason = "尚未生成；默认路径不回退到旧渲染"
        gates = []

    ready = path == "depth" and depth_ready and all(gate["status"] == "pass" for gate in gates)
    return {
        "uid": uid,
        "n": n,
        "path": path,
        "ready": ready,
        "depth_status": depth_state.get("status"),
        "fallback_reason": fallback_reason,
        "gates": gates,
    }


def _collect(library, store, limit):
    items = []
    for book in library.books():
        for chapter in library.chapters(book["uid"]):
            item = _audit_chapter(library, store, book["uid"], chapter["n"])
            if item is None:
                continue
            items.append(item)
            if limit and len(items) >= limit:
                return items
    return items


def _markdown(report):
    counts = report["counts"]
    lines = [
        "# 深度路径审计",
        "",
        f"- 根：`{report['root']}`",
        f"- 时间：{report['generated_at']}",
        f"- 样本：{report['sampled']} 章（上限 {report['limit'] or '全部'}）",
        f"- 路径：depth {counts['depth']} / legacy {counts['legacy']} / missing {counts['missing']}",
        f"- 结论：{'PASS' if report['pass'] else 'FAIL'}",
    ]
    if report["chapters"]:
        lines += ["", "| 章 | 路径 | 就绪 | 门禁 |", "|---|---|---|---|"]
        for item in report["chapters"]:
            gates = " ".join(f"{gate['id']}:{gate['status']}" for gate in item["gates"]) or "-"
            lines.append(
                f"| {item['uid']}/{item['n']:02d} | {item['path']} | {'是' if item['ready'] else '否'} | {gates} |"
            )
    failures = [(item, gate) for item in report["chapters"] for gate in item["gates"] if gate["status"] == "fail"]
    failures += [(item, None) for item in report["chapters"] if item["path"] != "depth"]
    if failures:
        lines += ["", "## 未通过", ""]
        for item, gate in failures:
            if gate is None:
                lines.append(f"- `{item['uid']}/{item['n']:02d}`：{item['path']}；{item['fallback_reason']}")
            else:
                lines.append(f"- `{item['uid']}/{item['n']:02d}` [{gate['id']}]：{gate['detail']}")
    return "\n".join(lines) + "\n"


def _write(path, text):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".audit-", dir=str(target.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        tmp.replace(target)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.limit < 0:
        print("--limit 不能为负数", file=sys.stderr)
        return 2
    try:
        library = Library(args.root)
        store = Store(library.layout)
        chapters = _collect(library, store, args.limit)
    except (FileNotFoundError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    counts = {path: sum(1 for item in chapters if item["path"] == path) for path in ("depth", "legacy", "missing")}
    report = {
        "root": library.root,
        "generated_at": _now(),
        "limit": args.limit,
        "sampled": len(chapters),
        "counts": counts,
        "pass": bool(chapters) and all(item["ready"] for item in chapters),
        "chapters": chapters,
    }
    text = json.dumps(report, ensure_ascii=False, indent=1)
    markdown = _markdown(report)
    print(text)
    print(markdown, file=sys.stderr)
    if args.out:
        _write(args.out, text + "\n")
        _write(str(Path(args.out).with_suffix(".md")), markdown)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
