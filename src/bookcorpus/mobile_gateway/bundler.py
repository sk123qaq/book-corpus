"""`.bcbook` v1 ZIP 学习包打包器：严格复用 Library.chapters() 与两种 SHA-256 口径。"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bookcorpus.reading.book_types import resolve_read_profile
from bookcorpus.reading.library import CLOTHES, Library
from bookcorpus.reading.mdparse import BLOCK_VERSION
from bookcorpus.reading.store import UID_RE, _payload_stale

SCHEMA_VERSION = 1
FIXED_ZIP_DATE_TIME = (2026, 1, 1, 0, 0, 0)
SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")
STREAM_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class BundleArtifact:
    """已构建或命中缓存的 `.bcbook` 归档产物。"""

    uid: str
    include_cache: bool
    path: Path
    size: int
    sha256: str
    etag: str


def _read_json_dict(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _sha256_file(path: Path) -> str:
    """分块流式计算文件 SHA-256，避免大文件一次性载入内存。"""
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(STREAM_CHUNK_BYTES)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _valid_sha256(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    return text if SHA256_HEX_RE.match(text) else ""


def _resolve_book_source_sha256(
    book_json: dict[str, Any],
    md_manifest: dict[str, Any],
    *,
    book_dir: Path | None = None,
    logical_chapters: list[dict[str, Any]] | None = None,
) -> str:
    """返回确定性 64 位十六进制 book_source_sha256，保证通过 Android BookSourceHash 校验。"""
    sources = book_json.get("sources")
    if isinstance(sources, list):
        for src in sources:
            if isinstance(src, dict) and src.get("primary_src"):
                hit = _valid_sha256(src.get("sha256"))
                if hit:
                    return hit
        for src in sources:
            if isinstance(src, dict):
                hit = _valid_sha256(src.get("sha256"))
                if hit:
                    return hit

    manifest_sha = _valid_sha256(md_manifest.get("source_sha256"))
    if manifest_sha:
        return manifest_sha

    if book_dir is not None:
        source_dir = book_dir / "source"
        if source_dir.is_dir():
            for cand in sorted(source_dir.iterdir()):
                if cand.is_file() and not cand.name.startswith("."):
                    return _sha256_file(cand)

    chapter_hashes = [str(ch.get("chapter_content_sha256") or "") for ch in (logical_chapters or [])]
    return hashlib.sha256("\n".join(chapter_hashes).encode("utf-8")).hexdigest()


def _resolve_canonical_version(md_manifest: dict[str, Any]) -> int:
    raw = md_manifest.get("canonical_version")
    if raw is None or raw == "":
        return 1
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1


def _candidate_cache_dirs(work_reading_dir: Path, uid: str, chapter_n: int, ordinal: int) -> list[Path]:
    if chapter_n >= 1:
        return [work_reading_dir / uid / "ch" / f"{chapter_n:02d}"]
    return [work_reading_dir / uid / "ch" / f"{ordinal:02d}"]


def _collect_non_stale_cache_entries(
    work_reading_dir: Path,
    uid: str,
    chapter_n: int,
    ordinal: int,
    chapter_content_sha256: str,
) -> list[tuple[str, bytes]]:
    """收集某逻辑章节下未过期的 companion_cache 文件，返回 (archive_rel_path, raw_bytes)。"""
    ch_dir: Path | None = None
    for cand in _candidate_cache_dirs(work_reading_dir, uid, chapter_n, ordinal):
        if cand.is_dir():
            ch_dir = cand
            break
    if ch_dir is None:
        return []

    prefix = f"companion_cache/ch_{ordinal:02d}"
    entries: list[tuple[str, bytes]] = []

    cmap_path = ch_dir / "chapter_map.json"
    if cmap_path.is_file():
        cmap_doc = _read_json_dict(cmap_path)
        if cmap_doc and cmap_doc.get("supported", True) and not _payload_stale(cmap_doc, chapter_content_sha256):
            entries.append((f"{prefix}/chapter_map.json", cmap_path.read_bytes()))

    notes_path = ch_dir / "assist_notes.json"
    if notes_path.is_file():
        notes_doc = _read_json_dict(notes_path)
        notes = notes_doc.get("notes") if isinstance(notes_doc, dict) else None
        if isinstance(notes, list) and notes and not _payload_stale(notes_doc, chapter_content_sha256):
            entries.append((f"{prefix}/assist_notes.json", notes_path.read_bytes()))

    tools_dir = ch_dir / "chapter_tools"
    if tools_dir.is_dir():
        for tool_path in sorted(tools_dir.glob("*.json")):
            if not tool_path.is_file():
                continue
            tool_doc = _read_json_dict(tool_path)
            if tool_doc and not _payload_stale(tool_doc, chapter_content_sha256):
                entries.append((f"{prefix}/chapter_tools/{tool_path.name}", tool_path.read_bytes()))

    return entries


def has_non_stale_companion_cache(library: Library, uid: str) -> bool:
    """判断指定书目是否存在至少一条未过期的伴读缓存。"""
    work_reading_dir = Path(library.layout.work_reading)
    if not (work_reading_dir / uid / "ch").is_dir():
        return False
    md_dir = Path(library.layout.md(uid))
    for ch in library.chapters(uid):
        source_files = list(ch.get("source_files") or [ch["file"]])
        chunks: list[bytes] = []
        for fname in source_files:
            fpath = md_dir / fname
            if not fpath.is_file():
                chunks = []
                break
            chunks.append(fpath.read_bytes())
        if not chunks:
            continue
        content_sha = hashlib.sha256(b"\0".join(chunks)).hexdigest()
        if _collect_non_stale_cache_entries(
            work_reading_dir,
            uid,
            int(ch["n"]),
            int(ch["ordinal"]),
            content_sha,
        ):
            return True
    return False


def _collect_image_entries(book_dir: Path, md_dir: Path) -> list[tuple[str, bytes]]:
    """收集插图目录：优先 books/<uid>/md/images/，兼容 books/<uid>/images/。"""
    collected: dict[str, bytes] = {}
    for img_root in (md_dir / "images", book_dir / "images"):
        if not img_root.is_dir():
            continue
        for item in sorted(img_root.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(img_root).as_posix()
            arc_name = f"images/{rel}"
            collected.setdefault(arc_name, item.read_bytes())
    return sorted(collected.items(), key=lambda pair: pair[0])


def _write_deterministic_zip(dest_path: Path, entries: list[tuple[str, bytes]]) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_name(f"{dest_path.name}.tmp.{uuid.uuid4().hex[:8]}")
    with zipfile.ZipFile(tmp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arc_name, data in sorted(entries, key=lambda pair: pair[0]):
            info = zipfile.ZipInfo(filename=arc_name, date_time=FIXED_ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    tmp_path.replace(dest_path)


def build_bcbook_bundle(
    library: Library,
    uid: str,
    dest_path: Path | str,
    *,
    include_cache: bool = True,
) -> BundleArtifact:
    """为 ``uid`` 构建确定性 `.bcbook` v1 ZIP 学习包并原子落盘。"""
    clean_uid = str(uid or "").strip()
    if not UID_RE.match(clean_uid):
        raise ValueError("非法 uid")

    book_entry = library.book(clean_uid)
    if book_entry is None:
        raise FileNotFoundError(f"书目不存在或暂无正文: {clean_uid}")

    book_dir = Path(library.layout.book(clean_uid))
    md_dir = Path(library.layout.md(clean_uid))
    book_json = _read_json_dict(book_dir / "book.json")
    md_manifest_path = md_dir / ".manifest.json"
    md_manifest = _read_json_dict(md_manifest_path) if md_manifest_path.is_file() else {}

    subjects = list(book_entry.get("subjects") or book_json.get("subjects") or [])
    title = str(book_entry.get("title") or book_json.get("title") or clean_uid)
    author = str(book_entry.get("author") or "")
    profile = resolve_read_profile(subjects, title=title)
    cloth_color = CLOTHES[sum(ord(ch) for ch in clean_uid) % len(CLOTHES)]
    canonical_version = _resolve_canonical_version(md_manifest)

    chapters = library.chapters(clean_uid)
    if not chapters:
        raise FileNotFoundError(f"书目暂无可用章节: {clean_uid}")

    work_reading_dir = Path(library.layout.work_reading)
    logical_chapters: list[dict[str, Any]] = []
    cache_entries: list[tuple[str, bytes]] = []

    for ch in chapters:
        ordinal = int(ch["ordinal"])
        source_files = list(ch.get("source_files") or [ch["file"]])
        chunks: list[bytes] = []
        for fname in source_files:
            fpath = md_dir / fname
            if not fpath.is_file():
                raise FileNotFoundError(f"章节源文件缺失: {fpath}")
            chunks.append(fpath.read_bytes())
        chapter_content_sha256 = hashlib.sha256(b"\0".join(chunks)).hexdigest()

        logical_chapters.append(
            {
                "ordinal": ordinal,
                "n": f"{ordinal:02d}",
                "title": str(ch.get("title") or ""),
                "chapter_role": str(ch.get("chapter_role") or "body"),
                "merged": bool(ch.get("merged", False)),
                "source_files": source_files,
                "chapter_content_sha256": chapter_content_sha256,
                "chars": int(ch.get("chars") or 0),
            }
        )

        if include_cache:
            cache_entries.extend(
                _collect_non_stale_cache_entries(
                    work_reading_dir,
                    clean_uid,
                    int(ch["n"]),
                    ordinal,
                    chapter_content_sha256,
                )
            )

    book_source_sha256 = _resolve_book_source_sha256(
        book_json,
        md_manifest,
        book_dir=book_dir,
        logical_chapters=logical_chapters,
    )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "uid": clean_uid,
        "title": title,
        "author": author,
        "subjects": subjects,
        "read_type": str(profile.get("read_type") or "social"),
        "sub_kind": str(profile.get("sub_kind") or ""),
        "book_source_sha256": book_source_sha256,
        "cloth_color": cloth_color,
        "cover_rel": None,
        "block_version": BLOCK_VERSION,
        "canonical_version": canonical_version,
        "logical_chapters": logical_chapters,
    }

    entries: list[tuple[str, bytes]] = [
        ("manifest.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")),
    ]

    for md_item in sorted(md_dir.glob("*.md")):
        if md_item.is_file() and not md_item.name.startswith("."):
            entries.append((f"md/{md_item.name}", md_item.read_bytes()))

    if md_manifest_path.is_file():
        entries.append(("md/.manifest.json", md_manifest_path.read_bytes()))

    entries.extend(_collect_image_entries(book_dir, md_dir))
    entries.extend(cache_entries)

    out_path = Path(dest_path)
    _write_deterministic_zip(out_path, entries)
    digest = _sha256_file(out_path)
    size = out_path.stat().st_size
    return BundleArtifact(
        uid=clean_uid,
        include_cache=include_cache,
        path=out_path,
        size=size,
        sha256=digest,
        etag=f'"{digest}"',
    )


def _bundle_signature(library: Library, uid: str, include_cache: bool) -> str:
    """计算影响 bundle 内容的磁盘指纹（book.json / md / images / companion_cache）。"""
    book_dir = Path(library.layout.book(uid))
    md_dir = Path(library.layout.md(uid))
    parts: list[Any] = [uid, int(include_cache), BLOCK_VERSION]

    for probe in (book_dir / "book.json", md_dir / ".manifest.json"):
        if probe.is_file():
            st = probe.stat()
            parts.append((probe.name, st.st_mtime_ns, st.st_size))

    if md_dir.is_dir():
        for item in sorted(md_dir.iterdir()):
            if item.is_file() and item.suffix == ".md":
                st = item.stat()
                parts.append(("md", item.name, st.st_mtime_ns, st.st_size))

    for img_root in (md_dir / "images", book_dir / "images"):
        if img_root.is_dir():
            for item in sorted(img_root.rglob("*")):
                if item.is_file():
                    st = item.stat()
                    parts.append(("img", item.relative_to(img_root).as_posix(), st.st_mtime_ns, st.st_size))

    if include_cache:
        ch_base = Path(library.layout.work_reading) / uid / "ch"
        if ch_base.is_dir():
            for item in sorted(ch_base.rglob("*.json")):
                if item.is_file() and "cache" not in item.relative_to(ch_base).parts:
                    st = item.stat()
                    parts.append(("cache", item.relative_to(ch_base).as_posix(), st.st_mtime_ns, st.st_size))

    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def ensure_cached_bundle(
    library: Library,
    uid: str,
    *,
    include_cache: bool = True,
    cache_root: Path | str | None = None,
) -> BundleArtifact:
    """按 (uid, include_cache, sig) 复用或重建临时 `.bcbook` 文件，确保 Range 偏移稳定。"""
    clean_uid = str(uid or "").strip()
    if not UID_RE.match(clean_uid):
        raise ValueError("非法 uid")
    if library.book(clean_uid) is None:
        raise FileNotFoundError(f"书目不存在或暂无正文: {clean_uid}")

    sig = _bundle_signature(library, clean_uid, include_cache)
    cache_dir = Path(cache_root) if cache_root is not None else Path(library.layout.work_reading) / ".mobile_bundles"
    cache_dir.mkdir(parents=True, exist_ok=True)
    flag = "1" if include_cache else "0"
    target = cache_dir / f"{clean_uid}_{flag}_{sig}.bcbook"

    if target.is_file():
        digest = _sha256_file(target)
        size = target.stat().st_size
        return BundleArtifact(
            uid=clean_uid,
            include_cache=include_cache,
            path=target,
            size=size,
            sha256=digest,
            etag=f'"{digest}"',
        )

    artifact = build_bcbook_bundle(library, clean_uid, target, include_cache=include_cache)
    for stale_file in cache_dir.glob(f"{clean_uid}_{flag}_*.bcbook"):
        if stale_file != target:
            stale_file.unlink(missing_ok=True)
    return artifact
