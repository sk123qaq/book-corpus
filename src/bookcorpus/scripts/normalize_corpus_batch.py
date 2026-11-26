#!/usr/bin/env python3
"""存量三批离线 L0 规范化编排器：扫描分桶、pdf-text 源重抽、备份回滚守卫与增量同步串联。

三批按收益从高到低严格互斥分桶（默认跳过 canonical_version >= 1，支持 --force）：
  1. Batch 1（HTML 脏书）：含 <table>/<td>/<tr> 或显著 HTML 标签污染（按 HTML 污染字节降序）。
  2. Batch 2（带 ## 或超阈值单章书）：非 Batch 1、单文件且带 ## 或非 pdf-text 超 >24k 字 / >800 块。
  3. Batch 3（3A 无目录 pdf-text 源重抽 + 3B 其余待洗存量书）。

用法：
  book-corpus normalize-batch --root ~/书库 --batch all --dry-run --json
  book-corpus normalize-batch --root /tmp/ws --batch 1 --sync
  book-corpus normalize-batch --root /tmp/ws --batch 3 --pdf-reextract-policy fallback --resume
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import lib_common as L

from bookcorpus import workspace
from bookcorpus.md.normalize import (
    _PRESERVED_COMMENT_RE,
    CANONICAL_VERSION,
    DEFAULT_SPLIT_BLOCKS,
    DEFAULT_SPLIT_CHARS,
    TextRetentionGuardError,
    _backup_md_dir,
    _discover_chapter_files,
    _split_code_segments,
    normalize_book_md,
)
from bookcorpus.md.parse import (
    HTML_TABLE_TAG_STRICT_RE,
    HTML_TAG_STRICT_RE,
    non_space_count,
    parse_markdown,
    protect_math_spans,
)

DEFAULT_EMBED_URL = "http://127.0.0.1:18080"
HTML_SIGNIFICANT_BYTES = 100
HTML_SIGNIFICANT_TAGS = 4

_TABLE_POLLUTION_RE = HTML_TABLE_TAG_STRICT_RE
_HTML_TAG_SCAN_RE = HTML_TAG_STRICT_RE
_HTML_COMMENT_SCAN_RE = re.compile(r"<!--[^\n]*?-->")
_H2_RE = re.compile(r"^##\s+", re.M)
_NOTE_ICON_RE = re.compile(r"!\[[^\]]*\]\([^)]*(?:note|footnote|tip|icon)[^)]*\)", re.I)
_DBL_FN_RE = re.compile(r"\[\[[^\[\]\n]{1,24}?\]\]\([^)\n]*\)")
_PAGE_LINE_RE = re.compile(
    r"^(?:[-–—]{1,3}\s*\d{1,4}\s*[-–—]{0,3}|\d{1,4}\s*[-–—]{1,3}|第\s*\d{1,4}\s*页|(?:page|p\.?)\s*\d{1,4})$",
    re.I | re.M,
)
_RATIO_RE = re.compile(r"([\d,]+)\s*/\s*([\d,]+)\s+([\d.]+)%\s+\d+\s+md\b")
_ANCHOR_RE = re.compile(r"锚点\s*:\s*([\d,]+)\s*/\s*([\d,]+)\s+窗命中\s+([\d.]+)%")

_PDF_PYTHON_CACHE: dict[tuple[str, str], tuple[str | None, str | None]] = {}
_PDF_PYTHON_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def _today_ymd() -> str:
    return datetime.datetime.now().astimezone().strftime("%Y%m%d")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _verify_script_path() -> Path:
    cand = Path(L.skill_dir("ebook-to-md")) / "scripts" / "verify_output.py"
    if cand.is_file():
        return cand
    return SRC.parent / ".agents" / "skills" / "ebook-to-md" / "scripts" / "verify_output.py"


def _run_verify(md_dir: Path) -> tuple[int, str]:
    """执行 verify_output.py 质检，返回 ``(returncode, output)``。"""
    script = _verify_script_path()
    if not script.is_file():
        return 127, f"verify_output.py 不存在：{script}"
    proc = subprocess.run(
        [sys.executable, str(script), str(md_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def _can_import_pymupdf(py_exe: str, *, pythonpath: str | None = None) -> bool:
    env: dict[str, str] | None = None
    if pythonpath:
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{pythonpath}{os.pathsep}{existing}" if existing else pythonpath
    try:
        proc = subprocess.run(
            [py_exe, "-c", "import pymupdf"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _matching_system_pythons(py_ver: str) -> list[str]:
    """根据 ``python3.11`` 等版本名查找本机可用的匹配 Python 解释器。"""
    cands: list[str] = []
    local_bin = Path.home() / ".local" / "bin" / py_ver
    if local_bin.exists():
        cands.append(str(local_bin))
    which_py = shutil.which(py_ver)
    if which_py:
        cands.append(which_py)
    ver_num = py_ver.removeprefix("python")
    uv_root = Path.home() / ".local" / "share" / "uv" / "python"
    if uv_root.is_dir() and ver_num:
        for p in sorted(uv_root.glob(f"cpython-{ver_num}*/bin/{py_ver}"), reverse=True):
            if p.exists():
                cands.append(str(p))
    for sys_dir in ("/usr/local/bin", "/usr/bin"):
        p = Path(sys_dir) / py_ver
        if p.exists():
            cands.append(str(p))
    cur_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    if cur_ver == py_ver:
        cands.append(sys.executable)
    return cands


def _find_pdf_python_spec(root: Path) -> tuple[str | None, str | None]:
    """查找可导入 ``pymupdf`` 的 Python 解释器与可选 ``PYTHONPATH``（兼容断链 .venv-pdf）。"""
    override = (os.environ.get("BOOKS_PDF_PYTHON") or "").strip()
    cache_key = (str(root.resolve()), override)
    with _PDF_PYTHON_LOCK:
        if cache_key in _PDF_PYTHON_CACHE:
            return _PDF_PYTHON_CACHE[cache_key]

        candidates: list[tuple[str, str | None]] = []
        if override:
            candidates.append((override, None))

        venv_dir = root / "工作区" / ".venv-pdf"
        for name in ("python", "python3"):
            venv_py = venv_dir / "bin" / name
            if venv_py.exists():
                candidates.append((str(venv_py), None))

        venv_lib = venv_dir / "lib"
        if venv_lib.is_dir():
            for sp_dir in sorted(venv_lib.glob("python*/site-packages"), reverse=True):
                if not sp_dir.is_dir():
                    continue
                py_ver = sp_dir.parent.name
                for py_exe in _matching_system_pythons(py_ver):
                    candidates.append((py_exe, str(sp_dir)))

        candidates.append((sys.executable, None))

        found: tuple[str | None, str | None] = (None, None)
        seen: set[tuple[str, str | None]] = set()
        for cand, extra_pp in candidates:
            if not cand or (cand, extra_pp) in seen:
                continue
            seen.add((cand, extra_pp))
            if _can_import_pymupdf(cand, pythonpath=extra_pp):
                found = (cand, extra_pp)
                break
        _PDF_PYTHON_CACHE[cache_key] = found
        return found


def _find_pdf_python(root: Path) -> str | None:
    """查找可导入 ``pymupdf`` 的 Python 解释器（优先环境覆盖与工作区 .venv-pdf）。"""
    return _find_pdf_python_spec(root)[0]


def _resolve_primary_pdf(root: Path, uid: str, book_meta: dict[str, Any], manifest: dict[str, Any]) -> Path | None:
    """定位主源 PDF 文件，找不到则返回 ``None``。"""
    book_dir = root / "books" / uid
    for src in book_meta.get("sources") or []:
        if not isinstance(src, dict):
            continue
        fmt = str(src.get("format") or "").lower()
        rel_file = str(src.get("file") or "").strip()
        if not rel_file:
            continue
        if fmt and fmt != "pdf" and not rel_file.lower().endswith(".pdf"):
            continue
        col = str(src.get("collection") or "").strip()
        cand = (
            root / "collections" / col / "source" / Path(rel_file).name
            if col
            else (book_dir / rel_file if "/" in rel_file else book_dir / "source" / rel_file)
        )
        if cand.is_file():
            return cand
    source_dir = book_dir / "source"
    if source_dir.is_dir():
        pdfs = sorted(p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
        if pdfs:
            return pdfs[0]
    man_src = str(manifest.get("source") or "").strip()
    if man_src:
        cand = Path(man_src) if Path(man_src).is_absolute() else (root / man_src)
        if cand.is_file() and cand.suffix.lower() == ".pdf":
            return cand
    return None


def _primary_sha256(book_meta: dict[str, Any], manifest: dict[str, Any], pdf_path: Path) -> str:
    for src in book_meta.get("sources") or []:
        if isinstance(src, dict) and str(src.get("sha256") or "").strip():
            return str(src["sha256"]).strip()
    if str(manifest.get("source_sha256") or "").strip():
        return str(manifest["source_sha256"]).strip()
    return str(L.sha256_file(str(pdf_path)))


def _scan_chapter_signals(chapter_files: list[Path], md_dir: Path) -> dict[str, Any]:
    """只读扫描一本书的 md 章节，提取分桶所需信号（跳过代码围栏内部）。"""
    has_table_tags = False
    html_bytes = 0
    html_tag_count = 0
    has_h2 = False
    has_note_icons = False
    has_dbl_fn = False
    has_page_lines = False
    total_chars = 0
    blank_blocks_est = 0
    single_text = ""

    for ch in chapter_files:
        raw = ch.read_text(encoding="utf-8", errors="replace")
        total_chars += non_space_count(raw)
        if len(chapter_files) == 1:
            single_text = raw
        non_code = "\n".join(seg for is_code, seg in _split_code_segments(raw) if not is_code) if "```" in raw else raw
        if "<" in non_code:
            scan_text, _ = protect_math_spans(non_code)
            if _TABLE_POLLUTION_RE.search(scan_text):
                has_table_tags = True
            for m in _HTML_TAG_SCAN_RE.finditer(scan_text):
                html_tag_count += 1
                html_bytes += len(m.group(0))
            for m in _HTML_COMMENT_SCAN_RE.finditer(scan_text):
                if _PRESERVED_COMMENT_RE.match(m.group(0)):
                    continue
                html_bytes += len(m.group(0))
        if not has_h2 and _H2_RE.search(non_code):
            has_h2 = True
        if not has_note_icons and _NOTE_ICON_RE.search(non_code):
            has_note_icons = True
        if not has_dbl_fn and "[[" in non_code and _DBL_FN_RE.search(non_code):
            has_dbl_fn = True
        if not has_page_lines and _PAGE_LINE_RE.search(non_code):
            has_page_lines = True
        if len(chapter_files) == 1:
            blank_blocks_est += len(re.split(r"\n{2,}", non_code))

    if not has_note_icons and (md_dir / "images").is_dir():
        for img in (md_dir / "images").iterdir():
            if img.is_file() and re.match(r"^(?:note|footnote|tip|icon|fn)[_-]?\d*$", img.stem, re.I):
                has_note_icons = True
                break

    return {
        "has_table_tags": has_table_tags,
        "html_bytes": html_bytes,
        "html_tag_count": html_tag_count,
        "has_h2": has_h2,
        "has_note_icons": has_note_icons,
        "has_dbl_fn": has_dbl_fn,
        "has_page_lines": has_page_lines,
        "chars": total_chars,
        "blank_blocks_est": blank_blocks_est,
        "single_text": single_text,
    }


def scan_corpus(
    root: Path,
    *,
    force: bool = False,
    uids: list[str] | None = None,
    split_chars: int = DEFAULT_SPLIT_CHARS,
    split_blocks: int = DEFAULT_SPLIT_BLOCKS,
) -> dict[str, Any]:
    """只读扫描工作区 books/*/md，返回三批严格互斥分桶计划与盘点汇总。"""
    books_dir = root / "books"
    want = {u.strip() for u in (uids or []) if u.strip()}
    batch1: list[dict[str, Any]] = []
    batch2: list[dict[str, Any]] = []
    batch3: list[dict[str, Any]] = []
    skipped_canonical = 0
    skipped_no_md = 0
    total_books_with_md = 0

    if books_dir.is_dir():
        entries = sorted(p for p in books_dir.iterdir() if p.is_dir() and not p.name.startswith("_"))
    else:
        entries = []

    for bdir in entries:
        uid = bdir.name
        if want and uid not in want:
            continue
        md_dir = bdir / "md"
        if not md_dir.is_dir():
            skipped_no_md += 1
            continue
        chapter_files = _discover_chapter_files(md_dir)
        if not chapter_files:
            skipped_no_md += 1
            continue
        total_books_with_md += 1

        manifest = _read_json(md_dir / ".manifest.json")
        try:
            canon_ver = int(manifest.get("canonical_version") or 0)
        except (TypeError, ValueError):
            canon_ver = 0
        if not force and canon_ver >= CANONICAL_VERSION:
            skipped_canonical += 1
            continue

        book_meta = _read_json(bdir / "book.json")
        title = str(book_meta.get("title") or manifest.get("title") or uid).strip()
        method = str(manifest.get("method") or "").strip()
        sig = _scan_chapter_signals(chapter_files, md_dir)
        chapters_before = len(chapter_files)
        l1_blocks = 0

        is_batch1 = bool(
            sig["has_table_tags"]
            or sig["html_bytes"] >= HTML_SIGNIFICANT_BYTES
            or sig["html_tag_count"] >= HTML_SIGNIFICANT_TAGS
        )
        if (
            not is_batch1
            and chapters_before == 1
            and not sig["has_h2"]
            and sig["chars"] <= split_chars
            and sig["blank_blocks_est"] > split_blocks // 2
        ):
            parsed = parse_markdown(sig["single_text"], stem=chapter_files[0].stem, base_dir=md_dir)
            l1_blocks = len(parsed.get("blocks") or [])

        pdf_path = _resolve_primary_pdf(root, uid, book_meta, manifest) if method == "pdf-text" else None
        base_item: dict[str, Any] = {
            "uid": uid,
            "title": title,
            "method": method or "unknown",
            "chapters_before": chapters_before,
            "chars": sig["chars"],
            "l1_blocks": l1_blocks,
            "has_h2": sig["has_h2"],
            "has_table_tags": sig["has_table_tags"],
            "html_bytes": sig["html_bytes"],
            "html_tag_count": sig["html_tag_count"],
            "has_note_icons": sig["has_note_icons"],
            "has_dbl_fn": sig["has_dbl_fn"],
            "has_page_lines": sig["has_page_lines"],
            "canonical_version": canon_ver,
            "has_source_pdf": bool(pdf_path and pdf_path.is_file()),
        }

        if is_batch1:
            base_item["batch"] = 1
            base_item["subroute"] = "html-clean"
            batch1.append(base_item)
        elif chapters_before == 1 and (
            sig["has_h2"] or (method != "pdf-text" and (sig["chars"] > split_chars or l1_blocks > split_blocks))
        ):
            base_item["batch"] = 2
            base_item["subroute"] = "single-split"
            batch2.append(base_item)
        elif chapters_before == 1 and method == "pdf-text":
            base_item["batch"] = 3
            base_item["subroute"] = "3A"
            batch3.append(base_item)
        else:
            base_item["batch"] = 3
            base_item["subroute"] = "3B"
            batch3.append(base_item)

    batch1.sort(key=lambda x: (-int(x["html_bytes"]), not bool(x["has_table_tags"]), x["uid"]))
    batch2.sort(key=lambda x: (-int(x["chars"]), x["uid"]))
    batch3.sort(
        key=lambda x: (
            0 if x["subroute"] == "3A" else 1,
            0 if (x["has_note_icons"] or x["has_dbl_fn"] or x["has_page_lines"]) else 1,
            x["uid"],
        )
    )

    b3a = [x for x in batch3 if x["subroute"] == "3A"]
    b3b = [x for x in batch3 if x["subroute"] == "3B"]
    return {
        "root": str(root),
        "scanned_at": _now_iso(),
        "force": force,
        "total_books_with_md": total_books_with_md,
        "skipped_canonical": skipped_canonical,
        "skipped_no_md": skipped_no_md,
        "counts": {
            "total_pending": len(batch1) + len(batch2) + len(batch3),
            "batch1": len(batch1),
            "batch2": len(batch2),
            "batch3": len(batch3),
            "batch3a_pdf_text": len(b3a),
            "batch3b_other": len(b3b),
            "batch1_html_bytes": sum(int(x["html_bytes"]) for x in batch1),
            "batch1_table_books": sum(1 for x in batch1 if x["has_table_tags"]),
            "batch2_h2_books": sum(1 for x in batch2 if x["has_h2"]),
            "batch3a_with_source_pdf": sum(1 for x in b3a if x["has_source_pdf"]),
            "batch3b_note_icons": sum(1 for x in b3b if x["has_note_icons"]),
            "batch3b_dbl_fn": sum(1 for x in b3b if x["has_dbl_fn"]),
            "batch3b_page_lines": sum(1 for x in b3b if x["has_page_lines"]),
        },
        "batches": {
            "1": batch1,
            "2": batch2,
            "3": batch3,
        },
    }


def _atomic_restore_md(md_dir: Path, backup_path: Path) -> None:
    """从备份目录原子还原 ``md/``，保证回滚后与原盘逐字节一致。"""
    if not backup_path.is_dir():
        raise FileNotFoundError(f"回滚备份不存在：{backup_path}")
    pid = os.getpid()
    tid = threading.get_ident()
    restore_tmp = md_dir.parent / f".md-restore-{pid}-{tid}"
    broken_tmp = md_dir.parent / f".md-broken-{pid}-{tid}"
    for p in (restore_tmp, broken_tmp):
        if p.exists():
            shutil.rmtree(p)
    shutil.copytree(backup_path, restore_tmp, symlinks=True)
    src_files = sorted(p.relative_to(backup_path).as_posix() for p in backup_path.rglob("*") if p.is_file())
    dst_files = sorted(p.relative_to(restore_tmp).as_posix() for p in restore_tmp.rglob("*") if p.is_file())
    if src_files != dst_files:
        shutil.rmtree(restore_tmp, ignore_errors=True)
        raise OSError(f"回滚校验失败：文件列表不一致 ({len(src_files)} vs {len(dst_files)})")
    if md_dir.exists():
        md_dir.rename(broken_tmp)
    try:
        restore_tmp.rename(md_dir)
    except Exception:
        if broken_tmp.exists() and not md_dir.exists():
            broken_tmp.rename(md_dir)
        raise
    if broken_tmp.exists():
        shutil.rmtree(broken_tmp, ignore_errors=True)


def _atomic_swap_stage(stage_dir: Path, md_dir: Path) -> None:
    """把暂存目录内容原子替换进 ``md_dir``（旧目录须已在调用前完成备份）。"""
    pid = os.getpid()
    tid = threading.get_ident()
    staged_local = md_dir.parent / f".md-stage-{pid}-{tid}"
    old_local = md_dir.parent / f".md-old-{pid}-{tid}"
    for p in (staged_local, old_local):
        if p.exists():
            shutil.rmtree(p)
    shutil.copytree(stage_dir, staged_local, symlinks=True)
    if md_dir.exists():
        md_dir.rename(old_local)
    try:
        staged_local.rename(md_dir)
    except Exception:
        if old_local.exists() and not md_dir.exists():
            old_local.rename(md_dir)
        raise
    if old_local.exists():
        shutil.rmtree(old_local, ignore_errors=True)


def _parse_audit_metrics(out: str) -> dict[str, Any]:
    m = _RATIO_RE.search(out)
    k = _ANCHOR_RE.search(out)
    return {
        "md_chars": int(m.group(1).replace(",", "")) if m else None,
        "pdf_chars": int(m.group(2).replace(",", "")) if m else None,
        "ratio": float(m.group(3)) / 100.0 if m else None,
        "anchors": float(k.group(3)) / 100.0 if k else None,
    }


def _reextract_pdf_text_to_stage(
    root: Path,
    uid: str,
    stage_dir: Path,
    *,
    split_chars: int,
    split_blocks: int,
) -> tuple[bool, str, dict[str, Any]]:
    """在暂存目录执行新版 pdf_text_to_md -> pdf_audit_volume -> normalize_book_md -> verify_output。"""
    book_dir = root / "books" / uid
    md_dir = book_dir / "md"
    old_manifest = _read_json(md_dir / ".manifest.json")
    book_meta = _read_json(book_dir / "book.json")
    pdf_path = _resolve_primary_pdf(root, uid, book_meta, old_manifest)
    if pdf_path is None or not pdf_path.is_file():
        return False, "source_pdf_missing", {}

    pdf_py = _find_pdf_python(root)
    if not pdf_py:
        return False, "pymupdf_unavailable", {}
    spec_py, extra_pp = _find_pdf_python_spec(root)
    pdf_env: dict[str, str] | None = None
    if extra_pp and spec_py == pdf_py:
        pdf_env = dict(os.environ)
        existing_pp = pdf_env.get("PYTHONPATH", "")
        pdf_env["PYTHONPATH"] = f"{extra_pp}{os.pathsep}{existing_pp}" if existing_pp else extra_pp

    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)

    title = str(book_meta.get("title") or old_manifest.get("title") or uid).strip()
    author = str(book_meta.get("author") or "").strip()
    series = str(book_meta.get("series") or "").strip()
    sha256 = _primary_sha256(book_meta, old_manifest, pdf_path)

    text_script = HERE / "pdf_text_to_md.py"
    audit_script = HERE / "pdf_audit_volume.py"
    cmd_extract = [
        pdf_py,
        str(text_script),
        "--pdf",
        str(pdf_path),
        "--out",
        str(stage_dir),
        "--uid",
        uid,
        "--title",
        title,
        "--author",
        author,
        "--collection",
        series,
        "--source-sha256",
        sha256,
        "--method",
        "pdf-text",
        "--structure",
        "auto",
    ]
    proc_ext = subprocess.run(cmd_extract, cwd=str(root), env=pdf_env, capture_output=True, text=True, check=False)
    if proc_ext.returncode != 0 or not _discover_chapter_files(stage_dir):
        detail = (proc_ext.stderr or proc_ext.stdout or "").strip()[-240:]
        return False, f"pdf_extract_failed: {detail}", {}

    proc_audit = subprocess.run(
        [pdf_py, str(audit_script), str(pdf_path), "-o", str(stage_dir)],
        cwd=str(root),
        env=pdf_env,
        capture_output=True,
        text=True,
        check=False,
    )
    audit_info = _parse_audit_metrics((proc_audit.stdout or "") + (proc_audit.stderr or ""))
    if proc_audit.returncode != 0:
        return False, "pdf_audit_failed", {"audit": audit_info}

    try:
        norm_res = normalize_book_md(
            stage_dir,
            backup_dir=None,
            dry_run=False,
            split_chars=split_chars,
            split_blocks=split_blocks,
        )
    except TextRetentionGuardError as exc:
        return False, f"text_retention_guard: {exc}", {"audit": audit_info}

    # 补齐源元数据与审计字段，保证与 pdf_md_batch 落盘契约一致
    stage_man_path = stage_dir / ".manifest.json"
    stage_man = _read_json(stage_man_path)
    stage_man["source_format"] = "pdf"
    stage_man["source_sha256"] = sha256
    if "chapters" not in stage_man:
        stage_man["chapters"] = len(_discover_chapter_files(stage_dir))
    stage_man["audit_rc"] = proc_audit.returncode
    if audit_info.get("ratio") is not None:
        stage_man["audit_ratio"] = audit_info["ratio"]
    if audit_info.get("anchors") is not None:
        stage_man["audit_anchors"] = audit_info["anchors"]

    v_rc, v_out = _run_verify(stage_dir)
    if v_rc != 0:
        return False, f"reextract_verify_failed: {v_out[-240:]}", {"audit": audit_info}

    stage_man["verify_rc"] = 0
    stage_man["verified"] = True
    _write_json_atomic(stage_man_path, stage_man)
    return True, "", {"audit": audit_info, "normalize": norm_res}


def _process_in_place(
    md_dir: Path,
    uid: str,
    batch_num: int,
    backup_root: Path,
    *,
    split_chars: int,
    split_blocks: int,
    subroute: str,
    fallback_reason: str = "",
) -> dict[str, Any]:
    """执行写前强备份 -> L0 规范化 -> verify_output -> 失败原子回滚。"""
    batch_backup_dir = backup_root / f"batch{batch_num}"
    expected_backup = batch_backup_dir / uid
    if expected_backup.exists():
        return {
            "uid": uid,
            "batch": batch_num,
            "subroute": subroute,
            "ok": False,
            "status": "failed",
            "changed": False,
            "rolled_back": False,
            "backup_path": str(expected_backup),
            "fallback_reason": fallback_reason,
            "error": f"备份目标已存在，拒绝覆盖：{expected_backup}",
        }

    created_backup: Path | None = None

    try:
        created_backup = _backup_md_dir(md_dir, batch_backup_dir, uid)
        norm_res = normalize_book_md(
            md_dir,
            backup_dir=None,
            dry_run=False,
            split_chars=split_chars,
            split_blocks=split_blocks,
        )
    except TextRetentionGuardError as exc:
        if expected_backup.is_dir():
            created_backup = expected_backup
        if created_backup is not None and created_backup.is_dir():
            _atomic_restore_md(md_dir, created_backup)
            return {
                "uid": uid,
                "batch": batch_num,
                "subroute": subroute,
                "ok": False,
                "status": "rolled_back",
                "changed": False,
                "rolled_back": True,
                "guard": "text_retention_guard",
                "backup_path": str(created_backup),
                "fallback_reason": fallback_reason,
                "error": f"text_retention_guard: {exc}",
            }
        return {
            "uid": uid,
            "batch": batch_num,
            "subroute": subroute,
            "ok": False,
            "status": "failed",
            "changed": False,
            "rolled_back": False,
            "guard": "text_retention_guard",
            "backup_path": "",
            "fallback_reason": fallback_reason,
            "error": f"text_retention_guard: {exc}",
        }
    except Exception as exc:
        if expected_backup.is_dir():
            created_backup = expected_backup
        if created_backup is not None and created_backup.is_dir():
            _atomic_restore_md(md_dir, created_backup)
            return {
                "uid": uid,
                "batch": batch_num,
                "subroute": subroute,
                "ok": False,
                "status": "rolled_back",
                "changed": False,
                "rolled_back": True,
                "backup_path": str(created_backup),
                "fallback_reason": fallback_reason,
                "error": f"{type(exc).__name__}: {exc}",
            }
        return {
            "uid": uid,
            "batch": batch_num,
            "subroute": subroute,
            "ok": False,
            "status": "failed",
            "changed": False,
            "rolled_back": False,
            "backup_path": "",
            "fallback_reason": fallback_reason,
            "error": f"{type(exc).__name__}: {exc}",
        }

    if not norm_res.get("changed"):
        if created_backup is not None and created_backup.is_dir():
            shutil.rmtree(created_backup, ignore_errors=True)
        return {
            "uid": uid,
            "batch": batch_num,
            "subroute": subroute,
            "ok": True,
            "status": "unchanged",
            "changed": False,
            "rolled_back": False,
            "backup_path": "",
            "fallback_reason": fallback_reason,
            "stats": norm_res.get("stats") or {},
        }

    v_rc, v_out = _run_verify(md_dir)
    if v_rc != 0:
        if created_backup is None or not created_backup.is_dir():
            created_backup = (
                expected_backup if expected_backup.exists() else _backup_md_dir(md_dir, batch_backup_dir, uid)
            )
        _atomic_restore_md(md_dir, created_backup)
        return {
            "uid": uid,
            "batch": batch_num,
            "subroute": subroute,
            "ok": False,
            "status": "rolled_back",
            "changed": False,
            "rolled_back": True,
            "verify_rc": v_rc,
            "backup_path": str(created_backup),
            "fallback_reason": fallback_reason,
            "error": f"verify_output 失败 (rc={v_rc})，已从备份原子还原：{v_out[-240:]}",
            "stats": norm_res.get("stats") or {},
        }

    return {
        "uid": uid,
        "batch": batch_num,
        "subroute": subroute,
        "ok": True,
        "status": "succeeded",
        "changed": True,
        "did_split": bool(norm_res.get("did_split")),
        "rolled_back": False,
        "verify_rc": 0,
        "backup_path": str(created_backup) if created_backup else "",
        "fallback_reason": fallback_reason,
        "stats": norm_res.get("stats") or {},
    }


def _process_book(
    item: dict[str, Any],
    root: Path,
    batch_dir: Path,
    backup_root: Path,
    *,
    split_chars: int,
    split_blocks: int,
    pdf_reextract_policy: str,
) -> dict[str, Any]:
    uid = str(item["uid"])
    batch_num = int(item["batch"])
    subroute = str(item["subroute"])
    md_dir = root / "books" / uid / "md"

    if subroute == "3A":
        backup_target = backup_root / "batch3" / uid
        if backup_target.exists():
            return {
                "uid": uid,
                "batch": 3,
                "subroute": "3A",
                "ok": False,
                "status": "failed",
                "changed": False,
                "rolled_back": False,
                "backup_path": str(backup_target),
                "error": f"备份目标已存在，拒绝覆盖：{backup_target}",
            }
        stage_dir = batch_dir / "stage" / uid
        ok_stage, fail_reason, extra = _reextract_pdf_text_to_stage(
            root,
            uid,
            stage_dir,
            split_chars=split_chars,
            split_blocks=split_blocks,
        )
        if ok_stage:
            try:
                created_backup = _backup_md_dir(md_dir, backup_root / "batch3", uid)
            except Exception as exc:
                shutil.rmtree(stage_dir, ignore_errors=True)
                return {
                    "uid": uid,
                    "batch": 3,
                    "subroute": "3A",
                    "ok": False,
                    "status": "failed",
                    "changed": False,
                    "rolled_back": False,
                    "error": f"写前备份失败，中止替换：{type(exc).__name__}: {exc}",
                }
            try:
                _atomic_swap_stage(stage_dir, md_dir)
                v_rc, v_out = _run_verify(md_dir)
                if v_rc != 0:
                    _atomic_restore_md(md_dir, created_backup)
                    return {
                        "uid": uid,
                        "batch": 3,
                        "subroute": "3A",
                        "ok": False,
                        "status": "rolled_back",
                        "changed": False,
                        "rolled_back": True,
                        "verify_rc": v_rc,
                        "backup_path": str(created_backup),
                        "error": f"替换后 verify_output 失败 (rc={v_rc})，已从备份原子还原：{v_out[-240:]}",
                    }
            except Exception as exc:
                _atomic_restore_md(md_dir, created_backup)
                return {
                    "uid": uid,
                    "batch": 3,
                    "subroute": "3A",
                    "ok": False,
                    "status": "rolled_back",
                    "changed": False,
                    "rolled_back": True,
                    "backup_path": str(created_backup),
                    "error": f"原子替换异常，已还原：{type(exc).__name__}: {exc}",
                }
            finally:
                shutil.rmtree(stage_dir, ignore_errors=True)

            norm_info = extra.get("normalize") or {}
            return {
                "uid": uid,
                "batch": 3,
                "subroute": "3A",
                "reextracted": True,
                "ok": True,
                "status": "succeeded",
                "changed": True,
                "did_split": bool(norm_info.get("did_split")),
                "rolled_back": False,
                "verify_rc": 0,
                "backup_path": str(created_backup),
                "audit": extra.get("audit") or {},
                "stats": norm_info.get("stats") or {},
            }

        shutil.rmtree(stage_dir, ignore_errors=True)
        if pdf_reextract_policy == "skip":
            return {
                "uid": uid,
                "batch": 3,
                "subroute": "3A-skipped",
                "reextracted": False,
                "ok": True,
                "status": "skipped",
                "changed": False,
                "rolled_back": False,
                "fallback_reason": fail_reason,
                "note": f"pdf-text 源重抽跳过：{fail_reason}",
            }
        res = _process_in_place(
            md_dir,
            uid,
            3,
            backup_root,
            split_chars=split_chars,
            split_blocks=split_blocks,
            subroute="3A-fallback",
            fallback_reason=fail_reason,
        )
        res["reextracted"] = False
        return res

    return _process_in_place(
        md_dir,
        uid,
        batch_num,
        backup_root,
        split_chars=split_chars,
        split_blocks=split_blocks,
        subroute=subroute,
    )


def _embed_healthy(url: str) -> bool:
    try:
        req = urllib.request.Request(url.rstrip("/") + "/health")  # noqa: S310 URL 来自 --embed-url/BOOKS_EMBED_URL 或本机默认值
        with urllib.request.urlopen(req, timeout=2) as resp:  # noqa: S310 同上
            return int(getattr(resp, "status", 0)) == 200
    except Exception:
        return False


def _run_sync_pipeline(
    root: Path,
    *,
    skip_embed: bool = False,
    embed_url: str | None = None,
) -> dict[str, Any]:
    """串联增量同步：library -> build_para_index --sync -> embed_corpus --sync + --compact。"""
    resolved_root = str(root.resolve())
    child_env = dict(os.environ)
    child_env["BOOKS_ROOT"] = resolved_root
    child_env["BOOK_CORPUS_WRITER_LOCK_PATH"] = str(Path(L.writer_lock_path(resolved_root)).resolve())

    sync_info: dict[str, Any] = {
        "library": {"status": "pending"},
        "fts": {"status": "pending"},
        "embed": {"status": "pending"},
        "ok": True,
    }

    # 1. book-corpus library
    proc_lib = subprocess.run(
        [sys.executable, str(HERE / "build_library.py")],
        cwd=resolved_root,
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc_lib.returncode != 0:
        sync_info["library"] = {
            "status": "failed",
            "rc": proc_lib.returncode,
            "error": (proc_lib.stderr or proc_lib.stdout or "").strip()[-300:],
        }
        sync_info["ok"] = False
        return sync_info
    sync_info["library"] = {"status": "succeeded", "rc": 0}

    # 2. build_para_index.py --sync
    proc_fts = subprocess.run(
        [sys.executable, str(HERE / "build_para_index.py"), "--sync", "--root", resolved_root, "--json"],
        cwd=resolved_root,
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc_fts.returncode != 0:
        sync_info["fts"] = {
            "status": "failed",
            "rc": proc_fts.returncode,
            "error": (proc_fts.stderr or proc_fts.stdout or "").strip()[-300:],
        }
        sync_info["ok"] = False
        return sync_info
    fts_payload: dict[str, Any] = {}
    for line in reversed((proc_fts.stdout or "").splitlines()):
        s = line.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, dict):
                    fts_payload = parsed
                    break
            except json.JSONDecodeError:
                pass
    sync_info["fts"] = {"status": "succeeded", "rc": 0, "result": fts_payload}

    # 3. embed_corpus.py --sync + --compact
    url = (embed_url or os.environ.get("BOOKS_EMBED_URL") or DEFAULT_EMBED_URL).strip()
    rerun_cmd = (
        f"BOOKS_EMBED_URL={url} book-corpus embed --root {resolved_root} --sync"
        f" && book-corpus embed --root {resolved_root} --compact"
    )
    if skip_embed:
        sync_info["embed"] = {"status": "skipped_flag", "url": url, "rerun_cmd": rerun_cmd}
        return sync_info

    if not _embed_healthy(url):
        sync_info["embed"] = {"status": "skipped_unreachable", "url": url, "rerun_cmd": rerun_cmd}
        return sync_info

    proc_emb = subprocess.run(
        [sys.executable, str(HERE / "embed_corpus.py"), "--root", resolved_root, "--sync", "--server", url],
        cwd=resolved_root,
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc_emb.returncode != 0:
        sync_info["embed"] = {
            "status": "failed",
            "rc": proc_emb.returncode,
            "url": url,
            "error": (proc_emb.stderr or proc_emb.stdout or "").strip()[-300:],
            "rerun_cmd": rerun_cmd,
        }
        sync_info["ok"] = False
        return sync_info

    proc_cmp = subprocess.run(
        [sys.executable, str(HERE / "embed_corpus.py"), "--root", resolved_root, "--compact", "--json"],
        cwd=resolved_root,
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )
    compact_payload: dict[str, Any] = {}
    if proc_cmp.returncode == 0:
        try:
            compact_payload = json.loads((proc_cmp.stdout or "").strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            compact_payload = {}
    sync_info["embed"] = {
        "status": "succeeded",
        "rc": 0,
        "url": url,
        "compact": compact_payload,
    }
    return sync_info


def _load_resume_succeeded(progress_path: Path) -> set[str]:
    if not progress_path.is_file():
        return set()
    latest: dict[str, str] = {}
    for line in progress_path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            rec = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("uid"):
            latest[str(rec["uid"])] = str(rec.get("status") or "")
    return {uid for uid, st in latest.items() if st in ("succeeded", "unchanged")}


def run_batch(
    root: Path,
    *,
    batch: str = "all",
    uids: list[str] | None = None,
    limit: int | None = None,
    workers: int = 4,
    force: bool = False,
    resume: bool = False,
    dry_run: bool = False,
    backup_root: Path | None = None,
    pdf_reextract_policy: str = "fallback",
    sync: bool = False,
    skip_embed: bool = False,
    embed_url: str | None = None,
    split_chars: int = DEFAULT_SPLIT_CHARS,
    split_blocks: int = DEFAULT_SPLIT_BLOCKS,
) -> dict[str, Any]:
    """编排三批规范化主入口；``dry_run=True`` 绝不加锁、绝不写盘。"""
    resolved_root = root.expanduser().resolve()
    resolved_backup_root = (
        backup_root.expanduser().resolve()
        if backup_root is not None
        else (resolved_root / "工作区" / "批次" / f"规范化-{_today_ymd()}")
    )
    plan_path = resolved_backup_root / "plan.json"

    loaded_plan: dict[str, Any] = {}
    if not dry_run and resume and plan_path.is_file():
        loaded_plan = _read_json(plan_path)
    if loaded_plan and isinstance(loaded_plan.get("batches"), dict):
        plan = loaded_plan
        if uids:
            want_uids = {u.strip() for u in uids if u.strip()}
            plan = {
                **loaded_plan,
                "batches": {
                    k: [item for item in (loaded_plan["batches"].get(k) or []) if item.get("uid") in want_uids]
                    for k in ("1", "2", "3")
                },
            }
    else:
        plan = scan_corpus(
            resolved_root,
            force=force,
            uids=uids,
            split_chars=split_chars,
            split_blocks=split_blocks,
        )

    if batch == "all":
        selected = [
            *plan["batches"]["1"],
            *plan["batches"]["2"],
            *plan["batches"]["3"],
        ]
    else:
        selected = list(plan["batches"][batch])

    if dry_run:
        preview = selected[:limit] if (limit is not None and limit >= 0) else selected
        return {
            "dry_run": True,
            "root": str(resolved_root),
            "batch": batch,
            "backup_root": str(resolved_backup_root),
            "counts": plan["counts"],
            "skipped_canonical": plan["skipped_canonical"],
            "skipped_no_md": plan["skipped_no_md"],
            "total_books_with_md": plan["total_books_with_md"],
            "selected_count": len(preview),
            "selected": preview,
            "top_samples": {
                "batch1": plan["batches"]["1"][:10],
                "batch2": plan["batches"]["2"][:10],
                "batch3a": [x for x in plan["batches"]["3"] if x["subroute"] == "3A"][:10],
                "batch3b": [x for x in plan["batches"]["3"] if x["subroute"] == "3B"][:10],
            },
        }

    with L.writer_lock(str(resolved_root)):
        resolved_backup_root.mkdir(parents=True, exist_ok=True)
        progress_path = resolved_backup_root / "progress.jsonl"
        summary_path = resolved_backup_root / "summary.json"

        if not resume or not plan_path.is_file():
            _write_json_atomic(plan_path, plan)

        already_done = _load_resume_succeeded(progress_path) if resume else set()
        todo = [item for item in selected if item["uid"] not in already_done]
        resumed_skipped = len(selected) - len(todo)
        if limit is not None and limit >= 0:
            todo = todo[:limit]

        results: list[dict[str, Any]] = []
        io_lock = threading.Lock()

        def _append_progress(rec: dict[str, Any]) -> None:
            with io_lock, progress_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()

        def _run_item(item: dict[str, Any]) -> dict[str, Any]:
            rec = _process_book(
                item,
                resolved_root,
                resolved_backup_root,
                resolved_backup_root,
                split_chars=split_chars,
                split_blocks=split_blocks,
                pdf_reextract_policy=pdf_reextract_policy,
            )
            _append_progress(rec)
            return rec

        pool_size = max(1, min(workers, max(1, len(todo))))
        if pool_size <= 1 or len(todo) <= 1:
            for item in todo:
                results.append(_run_item(item))
        else:
            with futures.ThreadPoolExecutor(max_workers=pool_size) as pool:
                job_futs = [pool.submit(_run_item, item) for item in todo]
                for job in job_futs:
                    results.append(job.result())

        sync_report: dict[str, Any] | None = None
        if sync:
            sync_report = _run_sync_pipeline(
                resolved_root,
                skip_embed=skip_embed,
                embed_url=embed_url,
            )

        succeeded = [r for r in results if r.get("status") == "succeeded"]
        unchanged = [r for r in results if r.get("status") == "unchanged"]
        skipped = [r for r in results if r.get("status") == "skipped"]
        rolled_back = [r for r in results if r.get("status") == "rolled_back"]
        failed = [r for r in results if r.get("status") == "failed"]

        summary: dict[str, Any] = {
            "dry_run": False,
            "root": str(resolved_root),
            "batch": batch,
            "backup_root": str(resolved_backup_root),
            "at": _now_iso(),
            "resume": resume,
            "resumed_skipped": resumed_skipped,
            "processed": len(results),
            "succeeded": len(succeeded),
            "unchanged": len(unchanged),
            "skipped": len(skipped),
            "rolled_back": len(rolled_back),
            "failed": len(failed),
            "reextracted": sum(1 for r in results if r.get("reextracted")),
            "did_split": sum(1 for r in results if r.get("did_split")),
            "sync": sync_report,
            "results": results,
        }
        _write_json_atomic(summary_path, summary)
        return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="存量三批离线 L0 规范化编排器（含 pdf-text 源重抽与回滚守卫）。")
    ap.add_argument("--root", help="工作区根，默认按 BOOKS_ROOT 或 books.toml 查找")
    ap.add_argument("--batch", choices=("1", "2", "3", "all"), default="all", help="执行批次：1|2|3|all（默认 all）")
    ap.add_argument("--uids", help="逗号分隔的 UID 列表（只处理指定书目）")
    ap.add_argument("--limit", type=int, default=None, help="最多处理前 N 本")
    ap.add_argument("--workers", type=int, default=4, help="并发数（默认 4）")
    ap.add_argument("--force", action="store_true", help="强制重跑已达 canonical_version >= 1 的书")
    ap.add_argument("--resume", action="store_true", help="从批次目录 progress.jsonl 断点续跑，跳过已成功书目")
    ap.add_argument("--backup-root", help="备份与批次账目录，默认 <root>/工作区/批次/规范化-<YYYYMMDD>")
    ap.add_argument(
        "--pdf-reextract-policy",
        choices=("fallback", "skip"),
        default="fallback",
        help="3A 源重抽无法执行或源审计未通过时的策略：fallback（回退现盘 L0）或 skip（默认 fallback）",
    )
    ap.add_argument(
        "--sync",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="批次结束后串联 library/fts/embed 同步",
    )
    ap.add_argument("--skip-embed", action="store_true", help="配合 --sync 使用，跳过向量同步步骤")
    ap.add_argument("--embed-url", default=None, help=f"覆盖 BOOKS_EMBED_URL（默认 {DEFAULT_EMBED_URL}）")
    ap.add_argument("--split-chars", type=int, default=DEFAULT_SPLIT_CHARS, help="单章拆章字符阈值")
    ap.add_argument("--split-blocks", type=int, default=DEFAULT_SPLIT_BLOCKS, help="单章拆章 L1 块阈值")
    ap.add_argument("--dry-run", action="store_true", help="只读扫描分桶并输出计划，不加锁、不写盘")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = ap.parse_args(argv)

    root = Path(workspace.resolve(args.root))
    uid_list = [u.strip() for u in (args.uids or "").split(",") if u.strip()] or None
    backup_root = Path(args.backup_root) if args.backup_root else None

    out = run_batch(
        root,
        batch=args.batch,
        uids=uid_list,
        limit=args.limit,
        workers=args.workers,
        force=args.force,
        resume=args.resume,
        dry_run=args.dry_run,
        backup_root=backup_root,
        pdf_reextract_policy=args.pdf_reextract_policy,
        sync=bool(args.sync),
        skip_embed=bool(args.skip_embed),
        embed_url=args.embed_url,
        split_chars=args.split_chars,
        split_blocks=args.split_blocks,
    )

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif out.get("dry_run"):
        c = out["counts"]
        print(
            f"[dry-run] 活库有 md {out['total_books_with_md']} 本，"
            f"已规范化跳过 {out['skipped_canonical']} 本，待清洗 {c['total_pending']} 本"
        )
        print(
            f"  Batch 1 (HTML 脏书): {c['batch1']} 本"
            f"（含 table {c['batch1_table_books']} 本，HTML {c['batch1_html_bytes']:,} B）"
        )
        print(f"  Batch 2 (单章拆章): {c['batch2']} 本（含 ## {c['batch2_h2_books']} 本）")
        print(
            f"  Batch 3: {c['batch3']} 本（3A pdf-text 源重抽 {c['batch3a_pdf_text']} 本 / "
            f"有源 PDF {c['batch3a_with_source_pdf']} 本；3B 其余 {c['batch3b_other']} 本）"
        )
    else:
        print(
            f"批次完成 ({out['batch']})：处理 {out['processed']} 本，"
            f"成功 {out['succeeded']}、无变更 {out['unchanged']}、跳过 {out['skipped']}、"
            f"回滚 {out['rolled_back']}、失败 {out['failed']}（续跑跳过 {out['resumed_skipped']}）"
        )
        sync_rep = out.get("sync")
        if sync_rep:
            emb = sync_rep.get("embed") or {}
            print(
                f"同步：library={sync_rep['library']['status']} "
                f"fts={sync_rep['fts']['status']} embed={emb.get('status')}"
            )
            if emb.get("status") == "skipped_unreachable":
                print("embed_sync: skipped_unreachable")
                print(f"补跑命令：{emb.get('rerun_cmd')}")

    if out.get("dry_run"):
        return 0
    sync_failed = bool(out.get("sync") and not out["sync"].get("ok"))
    has_errors = bool(out.get("failed") or out.get("rolled_back") or sync_failed)
    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
