#!/usr/bin/env python3
"""回答阶段的进程内只读工具：book_grep 与 read。

取代每次 fork Node.js `pi` + `readonly_tools.ts`，在 Python 进程内直接执行：
- book_grep：限定在本轮选中书目的 `md/` 目录内跑 ripgrep（无 rg 时退回 Python regex）
- read：按 1-based offset/limit 读授权章节 `.md`，带 `L行号: ` 前缀，避免模型手数行号
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

MAX_OUTPUT_CHARS = 16000
DEFAULT_GREP_MAX = 40
DEFAULT_READ_LIMIT = 200
MAX_READ_LIMIT = 400

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "book_grep",
            "description": (
                "Read-only regex search inside the md directories of the books in scope. "
                "Returns matching file:line:text lines. Use 2-4 char core keywords."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Ripgrep regex, e.g. 赵太爷|打了"},
                    "path": {
                        "type": "string",
                        "description": "Optional relative path under one of the allowed book md directories",
                    },
                    "max": {"type": "integer", "description": "Max matching lines (1-200), default 40"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": (
                "Read a numbered excerpt from an allowed chapter markdown file. "
                "Returns lines prefixed with L<line>: so you can cite exact quotes and line numbers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the markdown chapter file"},
                    "offset": {"type": "integer", "description": "1-based starting line number, default 1"},
                    "limit": {"type": "integer", "description": "Max lines to read (1-400), default 200"},
                },
                "required": ["path"],
            },
        },
    },
]

_RG_CACHE: str | None = None


def rg_binary() -> str:
    """进程内缓存 `rg` 可执行文件路径，避免每次搜索重复 stat PATH。"""
    global _RG_CACHE
    if _RG_CACHE is None:
        raw = os.environ.get("BOOKS_RG") or shutil.which("rg") or ""
        _RG_CACHE = raw
    return _RG_CACHE


def _to_int(value: Any, default: int) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def friendly_tool_label(name: str, args: dict[str, Any] | None, titles: dict[str, str] | None = None) -> str:
    """把工具名和参数转成给前端看的简短中文进度提示。"""
    args = args if isinstance(args, dict) else {}
    raw_path = str(args.get("path") or "").strip()
    chapter_hint = ""
    if raw_path:
        if titles and raw_path in titles:
            chapter_hint = f"《{titles[raw_path]}》"
        else:
            stem = Path(raw_path).stem
            if stem and stem != "md":
                chapter_hint = stem
    if name == "book_grep":
        pattern = str(args.get("pattern") or "").strip()
        short_pat = pattern[:18] + ("…" if len(pattern) > 18 else "")
        scope = f"{chapter_hint}中" if chapter_hint else "原文中"
        if short_pat:
            return f"正在{scope}检索「{short_pat}」…"
        return f"正在{scope}检索关键词…"
    if name == "read":
        offset = _to_int(args.get("offset") or args.get("start_line"), 1)
        target = chapter_hint or "章节原文"
        if offset > 1:
            return f"正在阅读 {target}（第 {offset} 行起）…"
        return f"正在阅读 {target}…"
    return f"正在调用 {name}…"


class ToolExecutor:
    """限定在 `md_roots` 范围内执行的只读 `book_grep` / `read`。"""

    def __init__(self, root: str | Path, md_roots: list[str] | tuple[str, ...]) -> None:
        self.root = Path(root).resolve()
        resolved: list[Path] = []
        seen: set[Path] = set()
        for item in md_roots:
            text = str(item or "").strip()
            if not text:
                continue
            cand = (self.root / text).resolve()
            if cand == self.root or not cand.is_relative_to(self.root):
                continue
            if cand not in seen:
                seen.add(cand)
                resolved.append(cand)
        self.roots = resolved

    def _rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    def _inside_roots(self, raw_path: str) -> Path:
        if not self.roots:
            raise ValueError("BOOK_MDS is not set")
        text = str(raw_path or "").strip()
        if not text:
            raise ValueError("path is empty")
        p = Path(text)
        target = p.resolve() if p.is_absolute() else (self.root / p).resolve()
        if not any(target == allowed or target.is_relative_to(allowed) for allowed in self.roots):
            raise ValueError("path outside the selected books")
        return target

    def _run_rg(self, target: Path, pattern: str, limit: int) -> str:
        if not target.exists():
            return ""
        rg = rg_binary()
        if rg:
            cmd = [
                rg,
                "-n",
                "--no-heading",
                "--color",
                "never",
                "-m",
                str(limit),
                "-e",
                pattern,
                self._rel(target),
            ]
            proc = subprocess.run(
                cmd,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            if proc.returncode == 0:
                out = proc.stdout or ""
                if target.is_file() and ":" in out:
                    # rg 对单文件输入省略前缀文件名，补全为 路径:行号:内容，保持和目录搜索同形。
                    rel = self._rel(target)
                    lines = [f"{rel}:{line}" if not line.startswith(rel + ":") else line for line in out.splitlines()]
                    return "\n".join(lines) + ("\n" if lines else "")
                return out
            if proc.returncode == 1:
                return ""
            err = (proc.stderr or proc.stdout or "").strip()
            raise ValueError(f"rg failed ({proc.returncode}): {err[:200]}")

        # 纯 Python 退路：环境未安装 rg 时保证可用。
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc
        files = [target] if target.is_file() else sorted(target.rglob("*.md"))
        hits: list[str] = []
        for fp in files:
            try:
                content = fp.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            rel = self._rel(fp)
            per_file = 0
            for idx, line in enumerate(content, 1):
                if rx.search(line):
                    hits.append(f"{rel}:{idx}:{line}")
                    per_file += 1
                    if per_file >= limit:
                        break
        return ("\n".join(hits) + "\n") if hits else ""

    def book_grep(self, pattern: str, path: str | None = None, max_hits: int | None = None) -> str:
        if not self.roots:
            raise ValueError("BOOK_MDS is not set")
        pat = str(pattern or "").strip()
        if not pat:
            raise ValueError("pattern is empty")
        limit = max(1, min(200, _to_int(max_hits, DEFAULT_GREP_MAX)))
        if path and str(path).strip():
            target = self._inside_roots(str(path))
            out = self._run_rg(target, pat, limit)
        else:
            quota = max(5, limit // len(self.roots))
            chunks = [self._run_rg(root, pat, quota) for root in self.roots]
            out = "".join(c for c in chunks if c)
        if not out.strip():
            return "No matches"
        return out[:MAX_OUTPUT_CHARS]

    def read(self, path: str, offset: int | None = None, limit: int | None = None) -> str:
        target = self._inside_roots(path)
        if not target.is_file():
            raise ValueError(f"file not found: {path}")
        if target.suffix.lower() != ".md":
            raise ValueError("only .md files are readable")
        start = max(1, _to_int(offset, 1))
        count = max(1, min(MAX_READ_LIMIT, _to_int(limit, DEFAULT_READ_LIMIT)))
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(lines)
        if total == 0:
            return "(empty file)"
        if start > total:
            return f"(offset {start} exceeds file length {total})"
        end = min(total, start + count - 1)
        out_lines: list[str] = []
        used_chars = 0
        last_line = start - 1
        for lineno in range(start, end + 1):
            raw = lines[lineno - 1]
            rendered = f"L{lineno}: {raw}" if raw.strip() else ""
            delta = len(rendered) + 1
            if out_lines and used_chars + delta > MAX_OUTPUT_CHARS - 120:
                break
            out_lines.append(rendered)
            used_chars += delta
            last_line = lineno
        if last_line < total:
            out_lines.append(f"[已截断：显示 L{start}-L{last_line} / 共 {total} 行；可用 offset={last_line + 1} 续读]")
        return "\n".join(out_lines)

    def execute(self, name: str, raw_args: str | dict[str, Any] | None) -> tuple[str, bool, dict[str, Any]]:
        """执行工具调用，返回 `(result_text, is_error, parsed_args)`。"""
        if isinstance(raw_args, dict):
            args = raw_args
        elif isinstance(raw_args, str):
            try:
                loaded = json.loads(raw_args) if raw_args.strip() else {}
                args = loaded if isinstance(loaded, dict) else {}
            except ValueError as exc:
                return f"工具参数不是合法 JSON：{exc}", True, {}
        else:
            args = {}
        try:
            if name == "book_grep":
                max_val = args.get("max") if args.get("max") is not None else args.get("limit")
                res = self.book_grep(
                    str(args.get("pattern") or ""), args.get("path"), _to_int(max_val, DEFAULT_GREP_MAX)
                )
                return res, False, args
            if name == "read":
                off = args.get("offset")
                if off is None:
                    off = args.get("start_line")
                lim = args.get("limit")
                if lim is None and args.get("end_line") is not None and off is not None:
                    lim = max(1, _to_int(args.get("end_line"), 1) - _to_int(off, 1) + 1)
                res = self.read(str(args.get("path") or ""), _to_int(off, 1), _to_int(lim, DEFAULT_READ_LIMIT))
                return res, False, args
            return f"未知工具：{name}", True, args
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            return f"工具执行失败：{exc}", True, args
