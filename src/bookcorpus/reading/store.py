"""阅读工作台持久化：唯一写区 ``工作区/阅读``。

只存三类可随时删除再生的轻量产物：
- ``<uid>/ch/<nn>/assist_notes.json``：读者手动钉住的局部旁批
- ``<uid>/ch/<nn>/chapter_map.json``：按需生成的全章知识地图与术语表
- ``ops_log.jsonl``：各章各功能的 append-only 操作日志
"""

from __future__ import annotations

import contextlib
import fcntl
import glob
import json
import os
import re
import threading
import uuid
from datetime import UTC, datetime

UID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
OPS_LOG_LIMIT = 500


def _now_iso():
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class Store:
    """工作区阅读产物与操作日志读写器（全部原子写或加锁追加）。"""

    def __init__(self, layout):
        self.layout = layout
        self.base = os.path.join(layout.work, "阅读")
        self._op_lock = threading.Lock()

    def _check(self, uid, n):
        if not UID_RE.match(uid or ""):
            raise ValueError("非法 uid")
        n = int(n)
        if n <= 0:
            raise ValueError("非法章号")
        return uid, n

    def pack_dir(self, uid, n):
        uid, n = self._check(uid, n)
        return os.path.join(self.base, uid, "ch", "%02d" % n)

    def _write(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + f".tmp.{uuid.uuid4().hex[:8]}"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp, path)

    @contextlib.contextmanager
    def _file_lock(self, path):
        """串行化同一目标文件的读-改-写（进程内锁 + 跨进程 flock）。"""
        lock_path = path + ".lock"
        with self._op_lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(lock_path, "a+", encoding="utf-8") as lock_fh:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    def _read_json(self, path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    # ---- 钉住旁批（assist_notes.json） --------------------------------
    def assist_notes_path(self, uid, n):
        return os.path.join(self.pack_dir(uid, n), "assist_notes.json")

    def assist_notes(self, uid, n, source_sha=None):
        doc = self._read_json(self.assist_notes_path(uid, n))
        if not isinstance(doc, dict):
            return []
        notes = doc.get("notes")
        if not isinstance(notes, list):
            return []
        stale = bool(source_sha and doc.get("source_sha256") and doc.get("source_sha256") != source_sha)
        out = []
        for item in notes:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            copy = dict(item)
            if stale:
                copy["stale"] = True
            out.append(copy)
        return out

    def save_assist_notes(self, uid, n, notes, *, source_sha=""):
        payload = {
            "schema": 1,
            "uid": uid,
            "n": "%02d" % int(n),
            "source_sha256": source_sha or "",
            "updated_at": _now_iso(),
            "notes": list(notes or []),
        }
        self._write(self.assist_notes_path(uid, n), json.dumps(payload, ensure_ascii=False, indent=1))
        return payload

    def pin_assist_note(self, uid, n, note, *, source_sha="", valid_block_ids=None):
        """追加或更新一条按块锚定的旁批，落盘失败不影响已有笔记。"""
        block_ids = [str(bid) for bid in (note.get("block_ids") or []) if str(bid).strip()]
        if not block_ids:
            raise ValueError("旁批必须锚定至少一个正文块")
        if valid_block_ids is not None:
            missing = [bid for bid in block_ids if bid not in valid_block_ids]
            if missing:
                raise ValueError("旁批引用了不存在的正文块：" + ", ".join(missing))
        content = str(note.get("content") or "").strip()
        diagram_html = str(note.get("diagram_html") or "").strip()
        figure = note.get("figure") if isinstance(note.get("figure"), dict) else None
        if not content and not diagram_html and not figure:
            raise ValueError("旁批内容不能为空")

        with self._file_lock(self.assist_notes_path(uid, n)):
            existing = self.assist_notes(uid, n)
            note_id = str(note.get("id") or f"note-{uuid.uuid4().hex[:8]}")
            clean = {
                "id": note_id,
                "action": str(note.get("action") or "explain"),
                "title": str(note.get("title") or "").strip()[:120],
                "block_ids": block_ids,
                "selected_text": str(note.get("selected_text") or "").strip()[:1000],
                "question": str(note.get("question") or "").strip()[:400],
                "content": content,
                "diagram_html": diagram_html,
                "created_at": str(note.get("created_at") or _now_iso()),
                "updated_at": _now_iso(),
            }
            if figure is not None:
                clean["figure"] = figure
            updated = False
            new_notes = []
            for item in existing:
                if item.get("id") == note_id:
                    new_notes.append(clean)
                    updated = True
                else:
                    item.pop("stale", None)
                    new_notes.append(item)
            if not updated:
                new_notes.append(clean)
            self.save_assist_notes(uid, n, new_notes, source_sha=source_sha)
        return clean

    def unpin_assist_note(self, uid, n, note_id, *, source_sha=""):
        with self._file_lock(self.assist_notes_path(uid, n)):
            existing = self.assist_notes(uid, n)
            removed = None
            kept = []
            for item in existing:
                if item.get("id") == note_id and removed is None:
                    removed = item
                else:
                    item.pop("stale", None)
                    kept.append(item)
            if removed is None:
                return None
            self.save_assist_notes(uid, n, kept, source_sha=source_sha)
        return removed

    # ---- 全章知识地图（chapter_map.json） -----------------------------
    def chapter_map_path(self, uid, n):
        return os.path.join(self.pack_dir(uid, n), "chapter_map.json")

    def chapter_map_cache_dir(self, uid, n):
        return os.path.join(self.pack_dir(uid, n), "cache")

    def chapter_map(self, uid, n, source_sha=None):
        doc = self._read_json(self.chapter_map_path(uid, n))
        if not isinstance(doc, dict):
            return None
        if source_sha and doc.get("source_sha256") and doc.get("source_sha256") != source_sha:
            copy = dict(doc)
            copy["stale"] = True
            return copy
        return doc

    def save_chapter_map(self, uid, n, payload):
        self._write(self.chapter_map_path(uid, n), json.dumps(payload, ensure_ascii=False, indent=1))
        return payload

    # ---- 全章书型工具缓存（chapter_tools/<key>.json） ------------------
    def chapter_tool_path(self, uid, n, key):
        safe_key = re.sub(r"[^A-Za-z0-9_-]", "_", str(key or "tool")).strip("_") or "tool"
        return os.path.join(self.pack_dir(uid, n), "chapter_tools", f"{safe_key}.json")

    def chapter_tool(self, uid, n, key, source_sha=None):
        doc = self._read_json(self.chapter_tool_path(uid, n, key))
        if not isinstance(doc, dict):
            return None
        if source_sha and doc.get("source_sha256") and doc.get("source_sha256") != source_sha:
            copy = dict(doc)
            copy["stale"] = True
            return copy
        return doc

    def save_chapter_tool(self, uid, n, key, payload):
        self._write(self.chapter_tool_path(uid, n, key), json.dumps(payload, ensure_ascii=False, indent=1))
        return payload

    def list_chapter_tools(self, uid, n, source_sha=None):
        """列出当前章节未过期的全章工具缓存键（不含 .json 后缀）。"""
        folder = os.path.join(self.pack_dir(uid, n), "chapter_tools")
        if not os.path.isdir(folder):
            return []
        keys = []
        for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
            doc = self._read_json(path)
            if not isinstance(doc, dict):
                continue
            if source_sha and doc.get("source_sha256") and doc.get("source_sha256") != source_sha:
                continue
            keys.append(os.path.splitext(os.path.basename(path))[0])
        return keys

    # ---- 全章活跃度统计（供书架/书页只读计数） -------------------------
    def activity_counts(self):
        """统计每本书已有旁批或知识地图的章数，以及总笔记/地图数。"""
        book_chapters = {}
        total_maps = 0
        total_notes = 0
        for path in glob.glob(os.path.join(self.base, "*", "ch", "*", "chapter_map.json")):
            parts = path.split(os.sep)
            if len(parts) < 4:
                continue
            uid, ch_str = parts[-4], parts[-2]
            doc = self._read_json(path)
            if isinstance(doc, dict) and doc.get("supported", True):
                book_chapters.setdefault(uid, set()).add(ch_str)
                total_maps += 1
        for path in glob.glob(os.path.join(self.base, "*", "ch", "*", "assist_notes.json")):
            parts = path.split(os.sep)
            if len(parts) < 4:
                continue
            uid, ch_str = parts[-4], parts[-2]
            doc = self._read_json(path)
            notes = doc.get("notes") if isinstance(doc, dict) else None
            if isinstance(notes, list) and notes:
                book_chapters.setdefault(uid, set()).add(ch_str)
                total_notes += len(notes)
        return {
            "by_book": {uid: len(chs) for uid, chs in book_chapters.items()},
            "chapters": sum(len(chs) for chs in book_chapters.values()),
            "maps": total_maps,
            "notes": total_notes,
        }

    # ---- 操作日志（ops_log.jsonl） ------------------------------------
    def ops_log_path(self):
        return os.path.join(self.base, "ops_log.jsonl")

    def append_op(self, entry):
        record = {
            "id": str(entry.get("id") or f"op-{uuid.uuid4().hex[:10]}"),
            "ts": str(entry.get("ts") or _now_iso()),
            "uid": str(entry.get("uid") or ""),
            "n": "%02d" % int(entry.get("n") or 0) if entry.get("n") else "",
            "book_title": str(entry.get("book_title") or ""),
            "chapter_title": str(entry.get("chapter_title") or ""),
            "action": str(entry.get("action") or ""),
            "block_ids": list(entry.get("block_ids") or []),
            "status": str(entry.get("status") or "ok"),
            "model": str(entry.get("model") or ""),
            "tokens": int(entry.get("tokens") or 0),
            "elapsed_ms": int(entry.get("elapsed_ms") or 0),
            "summary": str(entry.get("summary") or "")[:300],
            "error": str(entry.get("error") or "")[:500],
        }
        path = self.ops_log_path()
        line = json.dumps(record, ensure_ascii=False)
        with self._file_lock(path), open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return record

    def ops_logs(self, *, limit=OPS_LOG_LIMIT, uid=None, n=None, action=None, status=None):
        path = self.ops_log_path()
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            return []
        target_n = ("%02d" % int(n)) if n not in (None, "") else None
        out = []
        for raw in reversed(lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            if uid and item.get("uid") != uid:
                continue
            if target_n and item.get("n") != target_n:
                continue
            if action and item.get("action") != action:
                continue
            if status and item.get("status") != status:
                continue
            out.append(item)
            if len(out) >= limit:
                break
        return out
