"""Phase 1 retirement of the research/citations layers.

The migration is deliberately one-way: archive first, then remove only the
research artefacts, keep legacy skim/standard/deep exports, and copy at most
three old questions into the deep layer.
"""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import envfile
from .depth.render import render_depth_chapter
from .render import asset_url

DEFAULT_ARCHIVE = "工作区/归档/research-retire-2026-11-11.tar.gz"
DEFAULT_BATCH = "工作区/批次/2026-11-11-分层读删档"
MAX_QUESTIONS = 3


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_json(path: Path, payload: Any) -> None:
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def discover(root: str | Path) -> list[Path]:
    """Return only files that phase 1 is allowed to remove."""
    root = Path(root)
    files: list[Path] = []
    for pack in sorted(root.glob("工作区/阅读/*/ch/*")):
        depth = pack / "depth"
        for name in ("layers/research.json", "citations.json"):
            path = depth / name
            if path.is_file():
                files.append(path)
        legacy = pack / "export" / "research.html"
        if legacy.is_file():
            files.append(legacy)
    return files


def plan(root: str | Path, archive: str | Path | None = None, batch: str | Path | None = None) -> dict[str, Any]:
    root = Path(root)
    archive_path = Path(archive) if archive else root / DEFAULT_ARCHIVE
    batch_path = Path(batch) if batch else root / DEFAULT_BATCH
    paths = discover(root)
    files = [{"path": _rel(root, path), "size": path.stat().st_size, "sha256": _sha256(path)} for path in paths]
    packs: list[dict[str, Any]] = []
    for pack in sorted(root.glob("工作区/阅读/*/ch/*")):
        depth = pack / "depth"
        has_research = (depth / "layers/research.json").is_file()
        has_citations = (depth / "citations.json").is_file()
        has_legacy = (pack / "export" / "research.html").is_file()
        if has_research or has_citations or has_legacy:
            packs.append(
                {
                    "uid": pack.parent.parent.name,
                    "n": pack.name,
                    "pack": _rel(root, pack),
                    "depth_research": has_research,
                    "depth_citations": has_citations,
                    "legacy_research": has_legacy,
                }
            )
    return {
        "schema": 1,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "root": str(root),
        "archive": str(archive_path),
        "batch": str(batch_path),
        "files": files,
        "packs": packs,
        "file_count": len(files),
        "total_bytes": sum(item["size"] for item in files),
    }


def write_archive(root: str | Path, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(root)
    manifest = manifest or plan(root)
    archive = Path(manifest["archive"])
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz") as tar:
        for item in manifest["files"]:
            path = root / item["path"]
            tar.add(path, arcname=item["path"], recursive=False)
    archive_sha = _sha256(archive)
    manifest = dict(manifest)
    manifest["archive_sha256"] = archive_sha
    manifest["archive_size"] = archive.stat().st_size
    batch = Path(manifest["batch"])
    batch.mkdir(parents=True, exist_ok=True)
    _atomic_json(batch / "manifest.json", manifest)
    _atomic_write(batch / "说明.md", _migration_note(manifest))
    return manifest


def _migration_note(manifest: dict[str, Any]) -> str:
    lines = [
        "# 分层读阶段 1：研究/引用退役",
        "",
        f"- 生成时间：{manifest['generated_at']}",
        f"- 归档：`{manifest['archive']}`",
        f"- 归档 sha256：`{manifest.get('archive_sha256', '')}`",
        f"- 归档大小：{manifest.get('archive_size', 0)} 字节",
        f"- 文件数：{manifest['file_count']}",
        "",
        "## 删除清单",
        "",
        "| 文件 | 大小 | sha256 |",
        "|---|---:|---|",
    ]
    for item in manifest["files"]:
        lines.append(f"| `{item['path']}` | {item['size']} | `{item['sha256']}` |")
    lines += [
        "",
        "## 保留",
        "",
        "legacy skim / standard / deep 导出、new depth skim / standard / deep 层、原书正文与锚点均未改动。",
        "旧 research 问题最多 3 条迁入 `layers/deep.json` 的 `open_questions`。",
        "",
    ]
    return "\n".join(lines)


def _question_entry(question: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, str]:
    block_ids = [str(item) for item in question.get("blocks") or [] if item]
    unit_ids = [str(item) for item in question.get("units") or [] if item]
    anchor = block_ids[0] if block_ids else ""
    if not anchor and unit_ids:
        anchor = unit_ids[0]
    entry = {"text": str(question.get("text") or "").strip()}
    why = str(question.get("why") or "").strip()
    if why:
        entry["why"] = why
    if anchor:
        entry["anchor"] = anchor
    return entry


def migrate_deep_questions(root: str | Path) -> dict[str, Any]:
    root = Path(root)
    result: dict[str, Any] = {"packs": 0, "questions": 0, "skipped": []}
    for pack in sorted(root.glob("工作区/阅读/*/ch/*")):
        research_path = pack / "depth" / "layers" / "research.json"
        deep_path = pack / "depth" / "layers" / "deep.json"
        if not research_path.is_file() or not deep_path.is_file():
            continue
        research = json.loads(research_path.read_text(encoding="utf-8"))
        deep = json.loads(deep_path.read_text(encoding="utf-8"))
        if not isinstance(deep, dict):
            result["skipped"].append(str(pack))
            continue
        questions = [row for row in research.get("questions") or [] if isinstance(row, dict)]
        entries = []
        for question in questions[:MAX_QUESTIONS]:
            entry = _question_entry(question, [])
            if entry["text"]:
                entries.append(entry)
        if not entries:
            continue
        deep["open_questions"] = entries
        _atomic_json(deep_path, deep)
        result["packs"] += 1
        result["questions"] += len(entries)
    return result


def _clean_state(state: dict[str, Any]) -> bool:
    changed = False
    for key in ("research_sha", "research_prompt_version", "citations_sha", "citations_prompt_version"):
        if key in state:
            state.pop(key, None)
            changed = True
    for key in ("layer_input_sha", "layer_model_sha", "layer_sha", "layer_usage", "layer_updated_at"):
        value = state.get(key)
        if isinstance(value, dict):
            for name in ("research", "citations"):
                if name in value:
                    value.pop(name, None)
                    changed = True
    return changed


def _strip_legacy_research(html_text: str) -> str:
    text = re.sub(
        r'<button class="tier-tab" data-tier-tab="research".*?</button>',
        "",
        html_text,
        flags=re.S,
    )
    text = re.sub(r'<tr data-tier-row="research">.*?</tr>', "", text, flags=re.S)
    # Drop the research key from the inline STATS object, keeping the rest.
    match = re.search(r"const STATS = (\{.*?\});", text, flags=re.S)
    if match:
        try:
            stats = json.loads(match.group(1))
            if isinstance(stats, dict) and "research" in stats:
                stats.pop("research")
                text = text[: match.start(1)] + json.dumps(stats, ensure_ascii=False) + text[match.end(1) :]
        except json.JSONDecodeError:
            pass
    # TIERS is a JSON array injected by the legacy renderer.
    match = re.search(r"const TIERS = (\[.*?\]);", text, flags=re.S)
    if match:
        try:
            tiers = json.loads(match.group(1))
            if isinstance(tiers, list):
                tiers = [item for item in tiers if not (isinstance(item, dict) and item.get("key") == "research")]
                text = text[: match.start(1)] + json.dumps(tiers, ensure_ascii=False) + text[match.end(1) :]
        except json.JSONDecodeError:
            pass
    text = re.sub(
        r'data-tiers="([^"]*)"',
        lambda m: 'data-tiers="' + " ".join(part for part in m.group(1).split() if part != "research") + '"',
        text,
    )
    text = text.replace('data-tier="research"', 'data-tier="deep"')
    text = text.replace('tier: "research"', 'tier: "deep"')
    text = text.replace('|| "research"', '|| "deep"')
    text = text.replace("e.key >= '1' && e.key <= '4'", "e.key >= '1' && e.key <= '3'")
    text = text.replace(" · 研读 300", "").replace("研读 300", "")
    return text.replace("按 4 切到研读可见", "按 3 切到深读可见")


def strip_legacy_pages(root: str | Path) -> int:
    root = Path(root)
    changed = 0
    pages = list(root.glob("工作区/阅读/*/ch/*/chapter.html"))
    pages += list(root.glob("工作区/阅读/*/ch/*/export/*.html"))
    for page in pages:
        text = page.read_text(encoding="utf-8")
        new = _strip_legacy_research(text)
        if new != text:
            _atomic_write(page, new)
            changed += 1
    return changed


def _rerender_depth_pack(root: Path, pack: Path) -> bool:
    from .service import ReadingService

    uid = pack.parent.parent.name
    n = int(pack.name)
    depth = pack / "depth"
    html_path = depth / "chapter.html"
    if not html_path.is_file():
        return False
    model_path = depth / "chapter_model.json"
    skim_path = depth / "layers" / "skim.json"
    standard_path = depth / "layers" / "standard.json"
    deep_path = depth / "layers" / "deep.json"
    if not model_path.is_file() or not skim_path.is_file():
        return False
    service = ReadingService(root=root, annotate=False, depth=True)
    raw = service.library.read_chapter(uid, n)
    book = service._book_view(service.library.book(uid))
    chapter = {"n": n, "title": raw["title"], "source_sha256": raw["source_sha256"]}
    model = json.loads(model_path.read_text(encoding="utf-8"))
    skim = json.loads(skim_path.read_text(encoding="utf-8"))
    standard = json.loads(standard_path.read_text(encoding="utf-8")) if standard_path.is_file() else None
    deep = json.loads(deep_path.read_text(encoding="utf-8")) if deep_path.is_file() else None
    ledger_path = depth / "ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else {}
    html_text = render_depth_chapter(
        book,
        chapter,
        raw["blocks"],
        model,
        ledger,
        skim,
        resolver=lambda rel: asset_url(uid, rel),
        standard=standard,
        deep=deep,
    )
    _atomic_write(html_path, html_text)
    return True


def rerender_depth_pages(root: str | Path) -> int:
    root = Path(root)
    changed = 0
    for pack in sorted(root.glob("工作区/阅读/*/ch/*")):
        if _rerender_depth_pack(root, pack):
            changed += 1
    return changed


def apply(
    root: str | Path,
    archive: str | Path | None = None,
    batch: str | Path | None = None,
    *,
    dry_run: bool = False,
    rerender: bool = True,
) -> dict[str, Any]:
    root = Path(root)
    manifest = plan(root, archive, batch)
    if dry_run:
        return {"manifest": manifest, "dry_run": True}
    manifest = write_archive(root, manifest)
    questions = migrate_deep_questions(root)
    state_changed = 0
    for pack in sorted(root.glob("工作区/阅读/*/ch/*")):
        state_path = pack / "depth" / "state.json"
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(state, dict) and _clean_state(state):
                _atomic_json(state_path, state)
                state_changed += 1
    removed = 0
    for item in manifest["files"]:
        path = root / item["path"]
        if path.is_file():
            path.unlink()
            removed += 1
    # Remove now-empty legacy export directory only when the research page was the only file.
    for pack in root.glob("工作区/阅读/*/ch/*"):
        export = pack / "export"
        if export.is_dir() and not any(export.iterdir()):
            export.rmdir()
    pages = strip_legacy_pages(root) if rerender else 0
    depth_pages = rerender_depth_pages(root) if rerender else 0
    return {
        "manifest": manifest,
        "removed": removed,
        "questions": questions,
        "state_changed": state_changed,
        "legacy_pages": pages,
        "depth_pages": depth_pages,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    envfile.load()
    parser = argparse.ArgumentParser(prog="python -m bookcorpus.reading.retire")
    parser.add_argument("--root", required=True)
    parser.add_argument("--archive")
    parser.add_argument("--batch")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-rerender", action="store_true")
    args = parser.parse_args(argv)
    result = apply(
        args.root,
        args.archive,
        args.batch,
        dry_run=args.dry_run,
        rerender=not args.no_rerender,
    )
    if args.dry_run:
        print(json.dumps(result["manifest"], ensure_ascii=False, indent=2))
    else:
        payload = {key: value for key, value in result.items() if key != "manifest"}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
