"""桌面段级阅读进度分片存储：按 ``<root>/工作区/阅读/<uid>/progress/<device_id>.json`` 隔离写入。"""

from __future__ import annotations

import contextlib
import fcntl
import json
import re
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from bookcorpus.reading.store import UID_RE

DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
BLOCK_ID_RE = re.compile(r"^B\d+$")


@dataclass(frozen=True)
class DeviceProgressShard:
    """单设备针对某部作品的独占阅读游标分片（兼容可选旧版水位线字段）。"""

    uid: str
    device_id: str
    ordinal: int
    block_id: str
    char_offset: int
    updated_at_ms: int
    high_water_mark_ordinal: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的字典；缺失旧水位线字段时省略。"""
        data = asdict(self)
        if self.high_water_mark_ordinal is None:
            data.pop("high_water_mark_ordinal", None)
        return data

    @classmethod
    def from_mapping(cls, raw: dict[str, Any], *, uid: str | None = None) -> DeviceProgressShard:
        """校验并构造 ``DeviceProgressShard``。"""
        resolved_uid = str(uid if uid is not None else raw.get("uid") or "").strip()
        if not UID_RE.match(resolved_uid):
            raise ValueError("非法 uid")

        device_id = str(raw.get("device_id") or "").strip()
        if not DEVICE_ID_RE.match(device_id) or device_id in {".", ".."}:
            raise ValueError("非法 device_id")

        ordinal_raw = raw.get("ordinal")
        if isinstance(ordinal_raw, bool) or not isinstance(ordinal_raw, int):
            try:
                ordinal = int(str(ordinal_raw))
            except (TypeError, ValueError) as exc:
                raise ValueError("ordinal 必须是 >= 1 的整数") from exc
        else:
            ordinal = ordinal_raw
        if ordinal < 1:
            raise ValueError("ordinal 必须 >= 1")

        block_id = str(raw.get("block_id") or "").strip()
        if not BLOCK_ID_RE.match(block_id):
            raise ValueError("block_id 必须匹配 ^B\\d+$（如 B01）")

        char_offset_raw = raw.get("char_offset", 0)
        if isinstance(char_offset_raw, bool):
            raise ValueError("char_offset 必须是 >= 0 的整数")
        try:
            char_offset = int(char_offset_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("char_offset 必须是 >= 0 的整数") from exc
        if char_offset < 0:
            raise ValueError("char_offset 必须 >= 0")

        hwm_raw = raw.get("high_water_mark_ordinal")
        high_water_mark_ordinal: int | None
        if hwm_raw is None or hwm_raw == "":
            high_water_mark_ordinal = None
        elif isinstance(hwm_raw, bool):
            raise ValueError("high_water_mark_ordinal 必须是 >= 1 的整数")
        else:
            try:
                high_water_mark_ordinal = int(hwm_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("high_water_mark_ordinal 必须是 >= 1 的整数") from exc
            if high_water_mark_ordinal < 1:
                raise ValueError("high_water_mark_ordinal 必须 >= 1")

        ts_raw = raw.get("updated_at_ms")
        if ts_raw is None:
            updated_at_ms = int(time.time() * 1000)
        elif isinstance(ts_raw, bool):
            raise ValueError("updated_at_ms 必须是整数")
        else:
            try:
                updated_at_ms = int(ts_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("updated_at_ms 必须是整数") from exc
        if updated_at_ms < 0:
            raise ValueError("updated_at_ms 必须 >= 0")

        return cls(
            uid=resolved_uid,
            device_id=device_id,
            ordinal=ordinal,
            block_id=block_id,
            char_offset=char_offset,
            updated_at_ms=updated_at_ms,
            high_water_mark_ordinal=high_water_mark_ordinal,
        )


class ProgressStore:
    """按 ``<root>/工作区/阅读/<uid>/progress/<device_id>.json`` 原子读写并多端合并。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.reading_work_dir = self.root / "工作区" / "阅读"
        self._op_lock = threading.Lock()

    def _validate_uid(self, uid: str) -> str:
        clean = str(uid or "").strip()
        if not UID_RE.match(clean):
            raise ValueError("非法 uid")
        return clean

    def _validate_device_id(self, device_id: str) -> str:
        clean = str(device_id or "").strip()
        if not DEVICE_ID_RE.match(clean) or clean in {".", ".."}:
            raise ValueError("非法 device_id")
        return clean

    def progress_dir(self, uid: str) -> Path:
        """返回指定书籍的分片目录。"""
        clean_uid = self._validate_uid(uid)
        return self.reading_work_dir / clean_uid / "progress"

    def shard_path(self, uid: str, device_id: str) -> Path:
        """返回指定设备的独占分片文件路径。"""
        clean_uid = self._validate_uid(uid)
        clean_dev = self._validate_device_id(device_id)
        return self.reading_work_dir / clean_uid / "progress" / f"{clean_dev}.json"

    @contextlib.contextmanager
    def _file_lock(self, path: Path) -> Iterator[None]:
        lock_path = path.with_name(f"{path.name}.lock")
        with self._op_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+", encoding="utf-8") as lock_fh:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    def _write_atomic(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp.{uuid.uuid4().hex[:8]}")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)

    def put_shard(self, shard: DeviceProgressShard | dict[str, Any], *, uid: str | None = None) -> DeviceProgressShard:
        """独占写入指定设备的进度分片，绝不覆盖其他设备的分片。"""
        validated = (
            shard
            if isinstance(shard, DeviceProgressShard) and (uid is None or shard.uid == uid)
            else DeviceProgressShard.from_mapping(
                shard.to_dict() if isinstance(shard, DeviceProgressShard) else shard,
                uid=uid,
            )
        )
        target = self.shard_path(validated.uid, validated.device_id)
        payload_text = json.dumps(validated.to_dict(), ensure_ascii=False, indent=2) + "\n"
        with self._file_lock(target):
            self._write_atomic(target, payload_text)
        return validated

    def list_shards(self, uid: str) -> list[DeviceProgressShard]:
        """读取指定书籍的全部有效设备分片。"""
        folder = self.progress_dir(uid)
        if not folder.is_dir():
            return []
        shards: list[DeviceProgressShard] = []
        for path in sorted(folder.glob("*.json")):
            if not path.is_file():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            try:
                shards.append(DeviceProgressShard.from_mapping(raw, uid=self._validate_uid(uid)))
            except ValueError:
                continue
        shards.sort(key=lambda item: (-item.updated_at_ms, item.device_id))
        return shards

    def get_merged(self, uid: str) -> dict[str, Any]:
        """读取并合并指定书籍的所有设备分片，返回兼容水位线与全部分片。"""
        clean_uid = self._validate_uid(uid)
        shards = self.list_shards(clean_uid)
        merged_hwm = max(
            (s.high_water_mark_ordinal if s.high_water_mark_ordinal is not None else s.ordinal for s in shards),
            default=0,
        )
        return {
            "uid": clean_uid,
            "merged_high_water_mark_ordinal": merged_hwm,
            "shards": [s.to_dict() for s in shards],
        }

    def list_all_books(self) -> list[dict[str, Any]]:
        """列出工作区内所有存在有效进度分片的书目合并快照，按最新更新时间倒序。"""
        if not self.reading_work_dir.is_dir():
            return []
        rows: list[dict[str, Any]] = []
        for book_dir in sorted(self.reading_work_dir.iterdir()):
            if not book_dir.is_dir() or not UID_RE.match(book_dir.name):
                continue
            if not (book_dir / "progress").is_dir():
                continue
            merged = self.get_merged(book_dir.name)
            if not merged["shards"]:
                continue
            merged["latest_updated_at_ms"] = max(int(s.get("updated_at_ms") or 0) for s in merged["shards"])
            rows.append(merged)
        rows.sort(key=lambda item: (-int(item.get("latest_updated_at_ms") or 0), str(item.get("uid") or "")))
        return rows
