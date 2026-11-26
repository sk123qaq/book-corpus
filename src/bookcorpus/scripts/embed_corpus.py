#!/usr/bin/env python3
"""全库 md 正文 embedding：llama.cpp Vulkan 服务 + float16 向量库。

  embed_corpus.py --status                # 进度、体积、吞吐
  embed_corpus.py --dry-run               # 只切块，报块数与预估耗时
  embed_corpus.py                         # 未完成的书全部跑，断点续跑
  embed_corpus.py --books B0001,B0002     # 指定书
  embed_corpus.py --serve                 # 前台只起 llama-server，供手工调用
  embed_corpus.py --verify                # 校验元数据与向量文件一致

产物在 build/检索/embeddings/：

  emb.db         chunk 元数据（book/path/seq/line/chars/text）与每本书进度
  vectors.f16    float16 行向量，行号 = chunk.id - 1

切块按章进行，每块约 --chunk-chars 字符（默认 700，约 460 token）。
块文本存库，向量按追加写；崩溃后按 meta.last_id 截掉半截向量再续跑。
"""

import argparse
import atexit
import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from lib_common import ROOT

from bookcorpus.md import load_manifest_chapter_roles, parse_markdown

DEFAULT_URL = "http://127.0.0.1:18080"
DEFAULT_DIM = 1024
DEFAULT_MODEL = "qwen3-embed"
TOKENS_PER_CHAR = 0.667

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS book(
  uid TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', sig TEXT NOT NULL DEFAULT '',
  chunks INTEGER NOT NULL DEFAULT 0, chars INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
  done_at TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS chunk(
  id INTEGER PRIMARY KEY, book TEXT NOT NULL, path TEXT NOT NULL, seq INTEGER NOT NULL,
  line INTEGER, chars INTEGER NOT NULL, text TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS chunk_book ON chunk(book);
CREATE INDEX IF NOT EXISTS chunk_active ON chunk(active);
"""

IMG_ONLY = re.compile(r"^!\[[^\]]*\]\([^)]*\)$")
SENT_END = re.compile(r"(?<=[。！？；!?;])")


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def hms(seconds):
    if seconds != seconds or seconds < 0 or seconds == float("inf"):
        return "?"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


# ---- 切块 ----------------------------------------------------------------


def clean_para(text):
    keep = []
    for line in text.split("\n"):
        s = line.strip()
        if not s or IMG_ONLY.match(s) or s in ("---", "***", "___"):
            continue
        keep.append(s)
    return "\n".join(keep)


def split_pieces(text, target):
    """把一段切成不超过 target 的片；优先按句号断，超长句硬切。"""
    text = text.strip()
    if len(text) <= target:
        return [text] if text else []
    pieces, buf = [], ""
    for sent in SENT_END.split(text):
        while len(sent) > target:
            if buf:
                pieces.append(buf)
                buf = ""
            pieces.append(sent[:target])
            sent = sent[target:]
        if len(buf) + len(sent) <= target:
            buf += sent
        else:
            if buf:
                pieces.append(buf)
            buf = sent
    if buf:
        pieces.append(buf)
    return [p for p in pieces if p.strip()]


def chapter_chunk_records(md_path, target=700, overlap=0, *, manifest_roles=None, skip_roles=("front_back",)):
    """一章按 L1 块流与 heading 边界切成 ~target 字符块，返回记录列表。"""
    p = Path(md_path)
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    parsed = parse_markdown(
        raw,
        stem=p.stem,
        base_dir=p.parent,
        manifest_roles=manifest_roles,
        file_name=p.name,
    )
    role = parsed.get("chapter_role") or "body"
    if role in skip_roles:
        return []

    limit = int(target * 1.15)
    out = []
    cur = []  # (line, piece, heading_path, kind)
    cur_chars = 0
    cur_has_body = False

    def _flush():
        nonlocal cur, cur_chars, cur_has_body
        if cur:
            out.append(
                {
                    "line": cur[0][0],
                    "text": "\n".join(item[1] for item in cur),
                    "heading_path": cur[0][2],
                    "chapter_title": parsed.get("title") or "",
                }
            )
        cur = []
        cur_chars = 0
        cur_has_body = False

    for blk in parsed["blocks"]:
        kind = blk.get("kind") or "paragraph"
        text = str(blk.get("clean_text") if "clean_text" in blk else (blk.get("text") or "")).strip()
        if kind == "image" and not text:
            continue
        if not text:
            continue
        line = int(blk.get("start_line") or 1)
        hp = str(blk.get("heading_path") or "")

        if kind == "heading" and cur_has_body:
            # heading 边界强制截断：不跨小节硬拼，也不把上节末尾 overlap 带入新小节
            _flush()

        for piece in split_pieces(text, target):
            if cur and cur_chars + len(piece) > limit and len(cur) > overlap:
                out.append(
                    {
                        "line": cur[0][0],
                        "text": "\n".join(item[1] for item in cur),
                        "heading_path": cur[0][2],
                        "chapter_title": parsed.get("title") or "",
                    }
                )
                cur = cur[-overlap:] if overlap else []
                cur_chars = sum(len(item[1]) for item in cur)
                cur_has_body = any(item[3] != "heading" for item in cur)
            cur.append((line, piece, hp, kind))
            cur_chars += len(piece)
            if kind != "heading":
                cur_has_body = True
    _flush()
    return out


def chapter_chunks(md_path, target=700, overlap=0, *, manifest_roles=None, skip_roles=("front_back",)):
    """一章切成若干块，返回 [(起始行, 文本)]。"""
    return [
        (rec["line"], rec["text"])
        for rec in chapter_chunk_records(
            md_path,
            target=target,
            overlap=overlap,
            manifest_roles=manifest_roles,
            skip_roles=skip_roles,
        )
    ]


def format_embed_input(text, *, book_title="", chapter_title="", heading_path=""):
    """构造可选的入模上下文头（仅送入 embedding 接口，不污染库内 chunk.text）。"""
    parts = []
    if book_title:
        parts.append(f"《{book_title}》")
    if chapter_title:
        parts.append(chapter_title)
    if heading_path:
        hp = heading_path
        if chapter_title and (hp == chapter_title or hp.startswith(chapter_title + " > ")):
            hp = hp[len(chapter_title) :].lstrip(" >")
        if hp:
            parts.append(hp)
    if not parts:
        return text
    return f"{' / '.join(parts)}\n{text}"


def chapter_paths(root, book_path):
    md = Path(root) / "books" / book_path / "md"
    if not md.is_dir():
        return []
    return [p for p in sorted(md.iterdir()) if p.name.endswith(".md") and p.name.lower() != "readme.md" and p.is_file()]


def book_chunks(
    root,
    book_path,
    target=700,
    overlap=0,
    *,
    context_header=False,
    book_title="",
    skip_roles=("front_back",),
):
    root = Path(root)
    md_dir = root / "books" / book_path / "md"
    manifest_roles = load_manifest_chapter_roles(md_dir)
    out = []
    seq = 0
    for path in chapter_paths(root, book_path):
        rel = path.relative_to(root).as_posix()
        for rec in chapter_chunk_records(
            path,
            target=target,
            overlap=overlap,
            manifest_roles=manifest_roles,
            skip_roles=skip_roles,
        ):
            text = rec["text"]
            entry = {"path": rel, "seq": seq, "line": rec["line"], "text": text, "chars": len(text)}
            if context_header:
                entry["embed_text"] = format_embed_input(
                    text,
                    book_title=book_title,
                    chapter_title=rec.get("chapter_title") or "",
                    heading_path=rec.get("heading_path") or "",
                )
            out.append(entry)
            seq += 1
    return out


def sig_of(paths, context_header=False):
    h = hashlib.sha256()
    if context_header:
        h.update(b"context_header=1\n")
    for p in paths:
        st = Path(p).stat()
        h.update(f"{Path(p).name}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:16]


def md_books(root):
    """有 md 正文的书，按 uid 排序。优先 library.db，缺库时扫目录。"""
    root = Path(root)
    db = root / "build" / "library.db"
    if db.exists():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT b.uid, b.path, b.title FROM book b"
                " JOIN derived d ON d.uid=b.uid AND d.kind='md' AND d.status='done'"
                " ORDER BY b.uid"
            ).fetchall()
        finally:
            con.close()
        return [(u, p, t or u) for u, p, t in rows]
    out = []
    for child in sorted((root / "books").iterdir()):
        if child.name.startswith("_"):
            continue
        md = child / "md"
        if md.is_dir() and any(p.name.endswith(".md") and p.name.lower() != "readme.md" for p in md.iterdir()):
            out.append((child.name, child.name, child.name))
    return out


# ---- 向量存取 ------------------------------------------------------------


def pack_f16(vecs, dim):
    out = []
    for v in vecs:
        if len(v) != dim:
            raise RuntimeError(f"维度 {len(v)} != {dim}")
        out.append(struct.pack(f"<{dim}e", *v))
    return b"".join(out)


def unpack_f16(blob, dim):
    return list(struct.unpack(f"<{len(blob) // 2}e", blob))


class Store:
    def __init__(
        self,
        db_path,
        vec_path,
        dim=DEFAULT_DIM,
        model=DEFAULT_MODEL,
        chunk_chars=700,
        overlap=0,
        context_header=False,
    ):
        self.db_path = Path(db_path)
        self.vec_path = Path(vec_path)
        self.dim = dim
        self.model = model
        self.chunk_chars = chunk_chars
        self.overlap = overlap
        self.context_header = bool(context_header)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._init_meta()
        self.dim = int(self.meta("dim", str(dim)))
        self.last_id = int(self.meta("last_id", "0"))
        self._open_vectors()

    def acquire_write_lock(self):
        """同一份向量只允许一个写者；--status/--verify 不加锁。"""
        if getattr(self, "_lock", None) is not None:
            return
        path = self.db_path.parent / (self.db_path.name + ".lock")
        self._lock = path.open("a")
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock.close()
            self._lock = None
            raise SystemExit(f"另一个 embed 进程正在写 {self.db_path}（锁 {path}），等它跑完再试") from None

    def close(self):
        try:
            self.db.close()
        finally:
            lk = getattr(self, "_lock", None)
            if lk is not None:
                self._lock = None
                lk.close()

    def meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key, value):
        self.db.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    def _init_meta(self):
        with self.db:
            for key, value in (
                ("schema", 1),
                ("dim", self.dim),
                ("dtype", "float16"),
                ("model", self.model),
                ("chunk_chars", self.chunk_chars),
                ("overlap", self.overlap),
                ("context_header", "1" if self.context_header else "0"),
                ("vectors", self.vec_path.name),
                ("last_id", 0),
                ("tokens_in", 0),
                ("seconds", 0.0),
                ("created_at", now()),
            ):
                self.db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)", (key, str(value)))
            self.db.execute(
                "INSERT INTO meta(key,value) VALUES('updated_at',?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (now(),),
            )

    def _open_vectors(self):
        expected = self.last_id * self.dim * 2
        size = self.vec_path.stat().st_size if self.vec_path.exists() else 0
        if size < expected:
            raise SystemExit(
                f"向量文件比 meta.last_id 短：{size} < {expected}，可能被截断；"
                f"用 --verify 看，或删掉 {self.vec_path.parent} 重跑"
            )
        with self.vec_path.open("r+b" if size else "wb") as fh:
            if size > expected:
                fh.truncate(expected)
            if not size:
                fh.write(b"")
        with self.db:
            n = self.db.execute("DELETE FROM chunk WHERE id > ?", (self.last_id,)).rowcount
            if n:
                print(f"清掉未提交的 chunk {n} 行", file=sys.stderr)
            old = self.meta("chunk_chars")
            if old and int(old) != self.chunk_chars:
                print(f"注意：库里已有块按 {old} 字符切，本次用 {self.chunk_chars}，新旧块混存", file=sys.stderr)
            old_ctx = self.meta("context_header", "0")
            if self.last_id > 0 and bool(int(old_ctx)) != self.context_header:
                print(
                    f"注意：库里已有向量 context_header={old_ctx}，"
                    f"本次为 {int(self.context_header)}，新旧向量空间可能混存",
                    file=sys.stderr,
                )

    def book_row(self, uid):
        return self.db.execute("SELECT * FROM book WHERE uid=?", (uid,)).fetchone()

    def append_book(self, uid, title, sig, chunks, vec_bytes, tokens):
        if len(vec_bytes) != len(chunks) * self.dim * 2:
            raise RuntimeError(f"向量字节数不对：{len(vec_bytes)}")
        base = self.last_id
        with self.vec_path.open("ab") as fh:
            fh.write(vec_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        with self.db:
            self.db.execute("UPDATE chunk SET active=0 WHERE book=?", (uid,))
            self.db.executemany(
                "INSERT INTO chunk(id,book,path,seq,line,chars,text,active) VALUES(?,?,?,?,?,?,?,1)",
                [
                    (base + i + 1, uid, c["path"], c["seq"], c["line"], c["chars"], c["text"])
                    for i, c in enumerate(chunks)
                ],
            )
            self.db.execute(
                "INSERT INTO book(uid,title,sig,chunks,chars,status,error,done_at)"
                " VALUES(?,?,?,?,?,'done','',?)"
                " ON CONFLICT(uid) DO UPDATE SET title=excluded.title, sig=excluded.sig,"
                " chunks=excluded.chunks, chars=excluded.chars, status='done',"
                " error='', done_at=excluded.done_at",
                (uid, title, sig, len(chunks), sum(c["chars"] for c in chunks), now()),
            )
            self.set_meta("last_id", base + len(chunks))
            self.set_meta("tokens_in", int(self.meta("tokens_in", "0")) + tokens)
            self.set_meta("updated_at", now())
        self.last_id = base + len(chunks)

    def mark(self, uid, title, sig, status, error="", chunks=0, chars=0):
        with self.db:
            self.db.execute(
                "INSERT INTO book(uid,title,sig,chunks,chars,status,error,done_at)"
                " VALUES(?,?,?,?,?,?,?,?)"
                " ON CONFLICT(uid) DO UPDATE SET title=excluded.title, sig=excluded.sig,"
                " chunks=excluded.chunks, chars=excluded.chars, status=excluded.status,"
                " error=excluded.error, done_at=excluded.done_at",
                (uid, title, sig, chunks, chars, status, error, now()),
            )

    def add_seconds(self, seconds):
        with self.db:
            self.set_meta("seconds", float(self.meta("seconds", "0")) + seconds)
            self.set_meta("updated_at", now())

    def deactivate_books(self, uids):
        """把退库或删掉正文的书在向量库中失活（chunk.active=0，book.status='removed'）。"""
        targets = sorted({u for u in uids if u})
        if not targets:
            return {"books": 0, "chunks": 0}
        deactivated_chunks = 0
        deactivated_books = 0
        with self.db:
            for uid in targets:
                n = self.db.execute("UPDATE chunk SET active=0 WHERE book=? AND active=1", (uid,)).rowcount
                row = self.book_row(uid)
                if n > 0 or (row and row["status"] != "removed"):
                    deactivated_chunks += max(n, 0)
                    deactivated_books += 1
                    self.db.execute(
                        "INSERT INTO book(uid,title,sig,chunks,chars,status,error,done_at)"
                        " VALUES(?,?,'',0,0,'removed','',?)"
                        " ON CONFLICT(uid) DO UPDATE SET sig='', chunks=0, chars=0,"
                        " status='removed', error='', done_at=excluded.done_at",
                        (uid, (row["title"] if row else ""), now()),
                    )
            if deactivated_books:
                self.set_meta("updated_at", now())
        return {"books": deactivated_books, "chunks": deactivated_chunks}

    def prune_orphans(self, live_uids):
        """清理已不在活库（或已无 md）的孤儿书向量。"""
        live = set(live_uids)
        db_books = {r[0] for r in self.db.execute("SELECT uid FROM book WHERE status != 'removed'").fetchall()} | {
            r[0] for r in self.db.execute("SELECT DISTINCT book FROM chunk WHERE active=1").fetchall()
        }
        orphans = sorted(db_books - live)
        return self.deactivate_books(orphans)

    def compact(self):
        """零推理磁盘压缩：仅拷贝 active=1 的块与向量并重编连续 id，回收垃圾块空间。"""
        self.acquire_write_lock()
        before_stats = self.stats()
        row_bytes = self.dim * 2
        if before_stats["garbage"] == 0:
            return {
                "kept_chunks": before_stats["chunks"],
                "removed_garbage": 0,
                "before_bytes": before_stats["vec_bytes"],
                "after_bytes": before_stats["vec_bytes"],
            }
        tmp_db = self.db_path.with_name(self.db_path.name + ".compact.building")
        tmp_vec = self.vec_path.with_name(self.vec_path.name + ".compact.building")
        for p in (tmp_db, tmp_vec):
            if p.exists():
                p.unlink()
        for suf in ("-wal", "-shm", "-journal"):
            side = Path(str(tmp_db) + suf)
            if side.exists():
                side.unlink()

        dst = sqlite3.connect(tmp_db, timeout=30)
        try:
            dst.executescript(SCHEMA)
            meta_rows = self.db.execute("SELECT key, value FROM meta").fetchall()
            book_rows = self.db.execute(
                "SELECT uid, title, sig, chunks, chars, status, error, done_at FROM book WHERE status != 'removed'"
            ).fetchall()
            new_id = 0
            batch = []
            with self.vec_path.open("rb") as src_vec, tmp_vec.open("wb") as out_vec:
                cur = self.db.execute(
                    "SELECT id, book, path, seq, line, chars, text FROM chunk WHERE active=1 ORDER BY id"
                )
                for row in cur:
                    src_vec.seek((row["id"] - 1) * row_bytes)
                    blob = src_vec.read(row_bytes)
                    if len(blob) != row_bytes:
                        raise RuntimeError(f"压缩时读取向量截断：chunk id={row['id']}")
                    out_vec.write(blob)
                    new_id += 1
                    batch.append(
                        (new_id, row["book"], row["path"], row["seq"], row["line"], row["chars"], row["text"], 1)
                    )
                    if len(batch) >= 4000:
                        dst.executemany(
                            "INSERT INTO chunk(id,book,path,seq,line,chars,text,active) VALUES(?,?,?,?,?,?,?,?)",
                            batch,
                        )
                        batch.clear()
                if batch:
                    dst.executemany(
                        "INSERT INTO chunk(id,book,path,seq,line,chars,text,active) VALUES(?,?,?,?,?,?,?,?)",
                        batch,
                    )
                out_vec.flush()
                os.fsync(out_vec.fileno())
            with dst:
                dst.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", meta_rows)
                dst.executemany(
                    "INSERT OR REPLACE INTO book(uid,title,sig,chunks,chars,status,error,done_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    book_rows,
                )
                dst.execute(
                    "INSERT INTO meta(key,value) VALUES('last_id',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(new_id),),
                )
                dst.execute(
                    "INSERT INTO meta(key,value) VALUES('updated_at',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (now(),),
                )
            try:
                dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                dst.execute("PRAGMA journal_mode=DELETE")
            except sqlite3.Error:
                pass
            dst.close()
        except BaseException:
            dst.close()
            for p in (tmp_db, tmp_vec):
                if p.exists():
                    p.unlink()
            raise

        self.db.close()
        for suf in ("-wal", "-shm", "-journal"):
            side = Path(str(self.db_path) + suf)
            if side.exists():
                side.unlink()
            tmp_side = Path(str(tmp_db) + suf)
            if tmp_side.exists():
                tmp_side.unlink()
        tmp_vec.replace(self.vec_path)
        tmp_db.replace(self.db_path)
        self.db = sqlite3.connect(self.db_path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.last_id = new_id
        after_bytes = self.vec_path.stat().st_size if self.vec_path.exists() else 0
        return {
            "kept_chunks": new_id,
            "removed_garbage": before_stats["garbage"],
            "before_bytes": before_stats["vec_bytes"],
            "after_bytes": after_bytes,
        }

    def stats(self):
        row = self.db.execute(
            "SELECT count(*) AS n, coalesce(sum(chars),0) AS chars FROM chunk WHERE active=1"
        ).fetchone()
        books = dict(self.db.execute("SELECT status, count(*) FROM book GROUP BY status").fetchall())
        size = self.vec_path.stat().st_size if self.vec_path.exists() else 0
        return {
            "chunks": row["n"],
            "chars": row["chars"],
            "books": books,
            "last_id": self.last_id,
            "garbage": self.last_id - row["n"],
            "vec_bytes": size,
            "tokens_in": int(self.meta("tokens_in", "0")),
            "seconds": float(self.meta("seconds", "0")),
        }


# ---- 服务 ----------------------------------------------------------------


def find_server(root):
    env = os.environ.get("BOOKS_LLAMA_SERVER")
    if env:
        return Path(env)
    base = Path(root) / "工作区" / "中间态" / "embed"
    if not base.is_dir():
        return None
    for child in sorted(base.iterdir(), reverse=True):
        p = child / "llama-server"
        if child.name.startswith("llama") and p.is_file() and os.access(p, os.X_OK):
            return p
    return None


def find_model(root):
    env = os.environ.get("BOOKS_EMBED_MODEL")
    if env:
        return Path(env)
    base = Path(root) / "工作区" / "中间态" / "embed" / "models"
    if not base.is_dir():
        return None
    models = sorted(p for p in base.iterdir() if p.suffix == ".gguf")
    prefer = [p for p in models if "Qwen3-Embedding-0.6B" in p.name]
    if prefer:
        return prefer[0]
    return models[0] if models else None


def healthy(url):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=2) as r:  # noqa: S310 地址来自 --server 或本机默认值
            return r.status == 200
    except Exception:
        return False


def server_cmd(binary, model, url):
    host, port = "127.0.0.1", "18080"
    m = re.match(r"http://([^:/]+)(?::(\d+))?", url)
    if m:
        host, port = m.group(1), m.group(2) or port
    return [
        str(binary),
        "-m",
        str(model),
        "--embeddings",
        "--pooling",
        "last",
        "-c",
        "8192",
        "-b",
        "2048",
        "-ub",
        "2048",
        "-ngl",
        "99",
        "-np",
        "4",
        "--host",
        host,
        "--port",
        port,
        "--no-webui",
        "--alias",
        DEFAULT_MODEL,
    ]


def ensure_server(root, url, keep=False):
    if healthy(url):
        return None
    binary, model = find_server(root), find_model(root)
    if not binary or not model:
        raise SystemExit(
            f"找不到 llama-server 或模型。放到 {Path(root) / '工作区' / '中间态' / 'embed'} 下，"
            "或设 BOOKS_LLAMA_SERVER / BOOKS_EMBED_MODEL。"
        )
    log = binary.parent.parent / "llama-server.log"
    with log.open("ab") as fh:
        proc = subprocess.Popen(
            server_cmd(binary, model, url),
            cwd=str(binary.parent),
            stdout=fh,
            stderr=subprocess.STDOUT,
        )
    print(f"起 llama-server pid {proc.pid}，日志 {log}", file=sys.stderr)
    deadline = time.time() + 180
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"llama-server 退出（{proc.returncode}），看日志 {log}")
        if healthy(url):
            if not keep:
                atexit.register(lambda: proc.terminate() if proc.poll() is None else None)
            return proc
        time.sleep(1)
    proc.terminate()
    raise SystemExit(f"llama-server 180 秒没起来，看日志 {log}")


def post_embeddings(url, texts, dim, timeout=900, retries=3):
    body = json.dumps({"model": DEFAULT_MODEL, "input": texts}).encode()
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(  # noqa: S310 地址来自 --server 或本机默认值
                url.rstrip("/") + "/v1/embeddings",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 同上
                data = json.loads(resp.read())
            items = sorted(data["data"], key=lambda d: d.get("index", 0))
            vecs = [d["embedding"] for d in items]
            if len(vecs) != len(texts):
                raise RuntimeError(f"返回 {len(vecs)} 条，请求 {len(texts)} 条")
            for v in vecs:
                if len(v) != dim:
                    raise RuntimeError(f"维度 {len(v)} != {dim}")
                if not all(math.isfinite(x) for x in v):
                    raise RuntimeError("向量里有 NaN/Inf")
            return vecs, int(data.get("usage", {}).get("prompt_tokens", 0))
        except Exception as exc:  # 网络、超时、服务 5xx 都在这里重试
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"embedding 请求失败：{last}")


# ---- 命令 ----------------------------------------------------------------


def cmd_status(store):
    s = store.stats()
    print(f"库 {store.db_path}")
    print(f"模型 {store.meta('model')}  维度 {store.dim}  dtype {store.meta('dtype')}")
    books = ", ".join(f"{k}={v}" for k, v in sorted(s["books"].items())) or "无"
    print(f"书 {books}")
    print(f"有效块 {s['chunks']}  垃圾块 {s['garbage']}  last_id {s['last_id']}")
    print(f"正文字符 {s['chars']:,}  向量 {human(s['vec_bytes'])}")
    if s["seconds"] > 0 and s["tokens_in"]:
        print(f"累计 {s['tokens_in']:,} token / {hms(s['seconds'])}，{s['tokens_in'] / s['seconds']:.0f} tok/s")
    if s["chars"]:
        print(f"估算 token {int(s['chars'] * TOKENS_PER_CHAR):,}")


def cmd_verify(store):
    s = store.stats()
    problems = []
    expect = store.last_id * store.dim * 2
    if s["vec_bytes"] != expect:
        problems.append(f"向量文件 {s['vec_bytes']} 字节，应为 {expect}")
    row = store.db.execute("SELECT count(*) AS n, COALESCE(max(id), 0) AS m FROM chunk").fetchone()
    if row["m"] != store.last_id:
        problems.append(f"chunk 最大 id {row['m']} != last_id {store.last_id}")
    if row["n"] != store.last_id:
        problems.append(f"chunk 行数 {row['n']} != last_id {store.last_id}")
    bad = 0
    sample = store.db.execute("SELECT id FROM chunk WHERE active=1 ORDER BY id LIMIT 32").fetchall()
    with store.vec_path.open("rb") as fh:
        for r in sample:
            fh.seek((r["id"] - 1) * store.dim * 2)
            vec = unpack_f16(fh.read(store.dim * 2), store.dim)
            norm = math.sqrt(sum(x * x for x in vec))
            if not all(math.isfinite(x) for x in vec) or norm < 1e-6:
                bad += 1
    if bad:
        problems.append(f"抽样 {bad} 条向量全零或含 NaN")
    idx = store.db.execute("PRAGMA integrity_check").fetchone()[0]
    if idx != "ok":
        problems.append(f"sqlite integrity_check={idx}")
    if problems:
        print("失败：")
        for p in problems:
            print("  " + p)
        return 1
    print(
        f"全过：{s['chunks']} 块，{s['books'].get('done', 0)} 本 done，"
        f"{human(s['vec_bytes'])} 向量，抽样 {len(sample)} 条正常"
    )
    return 0


def cmd_dry(root, args, books):
    total_chunks = total_chars = 0
    rows = []
    use_ctx = bool(getattr(args, "context_header", False))
    for uid, path, title in books:
        chunks = book_chunks(root, path, args.chunk_chars, args.overlap, context_header=use_ctx, book_title=title)
        chars = sum(c["chars"] for c in chunks)
        total_chunks += len(chunks)
        total_chars += chars
        rows.append((chars, uid, title, len(chunks)))
    rows.sort(reverse=True)
    tokens = total_chars * TOKENS_PER_CHAR
    print(f"书 {len(books)} 本，块 {total_chunks}，字 {total_chars:,}，估算 token {int(tokens):,}")
    print(f"按 24,800 tok/s 纯计算约 {hms(tokens / 24800)}，含 25% 开销约 {hms(tokens / 24800 * 1.25)}")
    print("块数前 5：")
    for chars, uid, title, n in rows[:5]:
        print(f"  {uid:<6} {title[:30]:<30} {n:6d} 块 {chars:>12,} 字")
    return 0


def cmd_serve(root, args):
    binary, model = find_server(root), find_model(root)
    if not binary or not model:
        raise SystemExit(f"找不到 llama-server 或模型，放到 {Path(root) / '工作区' / '中间态' / 'embed'}")
    cmd = server_cmd(binary, model, args.server)
    print(" ".join(cmd), file=sys.stderr)
    return subprocess.call(cmd, cwd=str(binary.parent))


def _md_sig_dir(md_dir):
    try:
        with os.scandir(md_dir) as it:
            entries = [e for e in it if e.name.endswith(".md") and e.name.lower() != "readme.md" and e.is_file()]
    except OSError:
        return None
    if not entries:
        return None
    entries.sort(key=lambda e: e.name)
    h = hashlib.sha256()
    for e in entries:
        st = e.stat()
        h.update(f"{e.name}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:16]


def diff_embed(root, db_path=None, server_url=None, check_network=True, disk_sigs=None):
    """只读计算向量库与活库 md 的增量差集（不建库、不启 llama-server）。"""
    root = Path(root)
    db = Path(db_path) if db_path else (root / "build" / "检索" / "embeddings" / "emb.db")
    url = server_url or os.environ.get("BOOKS_EMBED_URL", DEFAULT_URL)
    engine_ready = bool((check_network and healthy(url)) or (find_server(root) and find_model(root)))
    live_books = md_books(root)
    live_map = {}
    for uid, path, _title in live_books:
        if disk_sigs is not None:
            rec = disk_sigs.get(path) or disk_sigs.get(uid)
            if rec is not None:
                live_map[uid] = rec[0] if isinstance(rec, tuple) else rec
        else:
            sig = _md_sig_dir(root / "books" / path / "md")
            if sig is not None:
                live_map[uid] = sig

    if not db.is_file():
        return {
            "has_db": False,
            "engine_ready": engine_ready,
            "added": sorted(live_map),
            "modified": [],
            "orphan": [],
            "unchanged": 0,
            "chunks": 0,
            "garbage": 0,
            "garbage_ratio": 0.0,
        }

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        book_rows = {r["uid"]: (r["status"], r["sig"]) for r in con.execute("SELECT uid, status, sig FROM book")}
        db_live = {u for u, (st, _) in book_rows.items() if st != "removed"}
        # 用 chunk_book 索引做松散索引扫描拿全部 book，只对不在 db_live 里的检查 active=1，避免全表扫 110 万行
        chunk_books = {
            r[0]
            for r in con.execute(
                "WITH RECURSIVE db(b) AS ("
                "  SELECT min(book) FROM chunk"
                "  UNION ALL"
                "  SELECT (SELECT min(book) FROM chunk WHERE book > b) FROM db WHERE b IS NOT NULL"
                ") SELECT b FROM db WHERE b IS NOT NULL"
            )
        }
        for u in chunk_books - db_live:
            if con.execute("SELECT 1 FROM chunk WHERE book=? AND active=1 LIMIT 1", (u,)).fetchone():
                db_live.add(u)
        total_chunks = con.execute("SELECT count(*) FROM chunk").fetchone()[0]
        inactive_chunks = con.execute("SELECT count(*) FROM chunk WHERE active=0").fetchone()[0]
        active_chunks = total_chunks - inactive_chunks
        m_row = con.execute("SELECT value FROM meta WHERE key='last_id'").fetchone()
        last_id = int(m_row[0]) if m_row else active_chunks
    finally:
        con.close()

    added, modified = [], []
    unchanged = 0
    for uid, cur_sig in sorted(live_map.items()):
        rec = book_rows.get(uid)
        if rec is None or rec[0] == "removed":
            added.append(uid)
        elif (rec[0] == "done" and rec[1] == cur_sig) or (rec[0] == "empty" and rec[1] == cur_sig):
            unchanged += 1
        else:
            modified.append(uid)
    orphan = sorted(db_live - set(live_map))
    garbage = max(0, last_id - active_chunks)
    garbage_ratio = round(garbage / last_id, 4) if last_id > 0 else 0.0
    return {
        "has_db": True,
        "engine_ready": engine_ready,
        "added": added,
        "modified": modified,
        "orphan": orphan,
        "unchanged": unchanged,
        "chunks": active_chunks,
        "garbage": garbage,
        "garbage_ratio": garbage_ratio,
    }


def deactivate_books_at(root, uids):
    """供 retie_book.py 直接调用：若向量库存在则失活指定书的块。"""
    db = Path(root) / "build" / "检索" / "embeddings" / "emb.db"
    vec = Path(root) / "build" / "检索" / "embeddings" / "vectors.f16"
    if not db.is_file() or not uids:
        return {"books": 0, "chunks": 0}
    store = Store(db, vec, DEFAULT_DIM, DEFAULT_MODEL)
    try:
        store.acquire_write_lock()
        return store.deactivate_books(uids)
    finally:
        store.close()


def run_embedding(root, store, books, args):
    store.acquire_write_lock()
    pruned = {"books": 0, "chunks": 0}
    if getattr(args, "sync", False) and not getattr(args, "books", None):
        pruned = store.prune_orphans([u for u, _, _ in books])
        if pruned["books"]:
            print(f"已失活孤儿书 {pruned['books']} 本（{pruned['chunks']} 块标为垃圾块）", flush=True)

    use_ctx = bool(getattr(args, "context_header", False))
    total_bytes = 0
    todo = []
    skipped = 0
    for uid, path, title in books:
        paths = chapter_paths(root, path)
        if not paths:
            continue
        sig = sig_of(paths, context_header=use_ctx)
        row = store.book_row(uid)
        if row and row["status"] in ("done", "empty") and row["sig"] == sig and not args.force:
            skipped += 1
            continue
        size = sum(p.stat().st_size for p in paths)
        total_bytes += size
        todo.append((uid, path, title, paths, size, sig))

    if not todo:
        s = store.stats()
        print(
            f"本次：新嵌 0 本，跳过 {skipped} 本，失活 {pruned['books']} 本，失败 0 本；"
            f"库内 {s['chunks']} 块，{human(s['vec_bytes'])}"
        )
        return 0

    if (
        getattr(args, "allow_missing_engine", False)
        and not healthy(args.server)
        and not (find_server(root) and find_model(root))
    ):
        print(f"未配置本地向量引擎（llama-server / GGUF 模型），跳过 {len(todo)} 本待嵌书", flush=True)
        return 0

    done_bytes = 0
    processed = errors = 0
    tokens_run = 0
    run_started = time.time()
    book_started = run_started
    proc = ensure_server(root, args.server, keep=args.keep_server)
    try:
        for i, (uid, path, title, _paths, size, sig) in enumerate(todo, 1):
            t0 = time.time()
            book_started = time.time()
            try:
                chunks = book_chunks(
                    root,
                    path,
                    args.chunk_chars,
                    args.overlap,
                    context_header=use_ctx,
                    book_title=title,
                )
                if not chunks:
                    store.mark(uid, title, sig, "empty", "没有正文")
                    done_bytes += size
                    continue
                vecs = []
                sent = 0
                for start in range(0, len(chunks), args.batch):
                    batch = chunks[start : start + args.batch]
                    inputs = [c.get("embed_text") or c["text"] for c in batch]
                    v, tok = post_embeddings(args.server, inputs, store.dim, timeout=args.timeout)
                    vecs.extend(v)
                    sent += tok
                    if len(chunks) > args.batch * 4 and time.time() - t0 > 15:
                        print(f"  {uid} {start + len(batch)}/{len(chunks)} 块", file=sys.stderr, flush=True)
                        t0 = time.time()
                store.append_book(uid, title, sig, chunks, pack_f16(vecs, store.dim), sent)
                tokens_run += sent
                processed += 1
                elapsed = time.time() - run_started
                rate = tokens_run / elapsed if elapsed else 0
                remain = total_bytes - done_bytes - size
                eta = remain / (done_bytes + size) * elapsed if done_bytes + size else float("nan")
                print(
                    f"[{i}/{len(todo)}] {uid} {(title or '')[:20]} | {len(chunks)} 块 "
                    f"{sum(c['chars'] for c in chunks):,} 字 | {sent:,} tok | "
                    f"{rate / 1000:.1f}k tok/s | 剩 {hms(eta)}",
                    flush=True,
                )
            except Exception as exc:
                errors += 1
                store.mark(uid, title, sig, "error", str(exc)[:500])
                print(
                    f"[{i}/{len(todo)}] {uid} {(title or '')[:20]} 失败：{exc}",
                    file=sys.stderr,
                    flush=True,
                )
            finally:
                done_bytes += size
                store.add_seconds(time.time() - book_started)
    finally:
        if proc and not args.keep_server:
            proc.terminate()
    s = store.stats()
    print(
        f"本次：新嵌 {processed} 本，跳过 {skipped} 本，失活 {pruned['books']} 本，失败 {errors} 本；"
        f"库内 {s['chunks']} 块，{human(s['vec_bytes'])}"
    )
    return 1 if errors else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", help="工作区根，默认取 BOOKS_ROOT")
    ap.add_argument("--status", action="store_true", help="看进度")
    ap.add_argument("--verify", action="store_true", help="校验向量与元数据")
    ap.add_argument("--dry-run", action="store_true", help="只切块或看增量差集，不请求服务")
    ap.add_argument("--sync", action="store_true", help="增量同步：先失活孤儿书，再补嵌新增/变更书")
    ap.add_argument("--prune", action="store_true", help="只失活已退库/无 md 的孤儿书，不启推理服务")
    ap.add_argument("--compact", action="store_true", help="零推理磁盘压缩：回收 active=0 垃圾块")
    ap.add_argument("--allow-missing-engine", action="store_true", help="未配 llama-server/模型时平滑跳过返回 0")
    ap.add_argument("--json", action="store_true", help="JSON 输出（配合 --dry-run / --prune / --compact）")
    ap.add_argument("--serve", action="store_true", help="前台起 llama-server")
    ap.add_argument("--books", help="逗号分隔的 uid，默认全部")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 本")
    ap.add_argument("--force", action="store_true", help="已完成的也重嵌（旧向量留作垃圾）")
    ap.add_argument("--chunk-chars", type=int, default=700)
    ap.add_argument("--overlap", type=int, default=0, help="块间重叠的段数，默认 0")
    ap.add_argument(
        "--context-header",
        action="store_true",
        help="在送入 embedding 模型的文本前拼接《书名》/章名/小节名（默认关闭以保持与存量向量空间一致）",
    )
    ap.add_argument("--emb-dir", help="自定义向量库输出目录（默认 build/检索/embeddings）")
    ap.add_argument("--batch", type=int, default=32, help="每次请求的块数")
    ap.add_argument("--timeout", type=int, default=900, help="单次请求超时秒数")
    ap.add_argument("--server", default=os.environ.get("BOOKS_EMBED_URL", DEFAULT_URL))
    ap.add_argument("--keep-server", action="store_true", help="跑完不杀自己起的服务")
    args = ap.parse_args()

    root = Path(args.root).resolve() if args.root else Path(ROOT)
    emb_dir = Path(args.emb_dir).resolve() if args.emb_dir else (root / "build" / "检索" / "embeddings")
    emb_db = emb_dir / "emb.db"
    emb_vec = emb_dir / "vectors.f16"

    if args.serve:
        return cmd_serve(root, args)
    if args.dry_run and args.json:
        print(json.dumps(diff_embed(root, db_path=emb_db, server_url=args.server), ensure_ascii=False))
        return 0

    store = Store(
        emb_db,
        emb_vec,
        DEFAULT_DIM,
        DEFAULT_MODEL,
        args.chunk_chars,
        args.overlap,
        context_header=args.context_header,
    )
    if args.status:
        cmd_status(store)
        return 0
    if args.verify:
        return cmd_verify(store)
    if args.prune:
        store.acquire_write_lock()
        res = store.prune_orphans([u for u, _, _ in md_books(root)])
        if args.json:
            print(json.dumps(res, ensure_ascii=False))
        else:
            print(f"已失活孤儿书 {res['books']} 本（{res['chunks']} 块）")
        return 0
    if args.compact:
        res = store.compact()
        if args.json:
            print(json.dumps(res, ensure_ascii=False))
        else:
            print(
                f"压缩完成：保留 {res['kept_chunks']} 块，回收垃圾 {res['removed_garbage']} 块，"
                f"{human(res['before_bytes'])} -> {human(res['after_bytes'])}"
            )
        return 0

    books = md_books(root)
    if args.books:
        want = {x.strip() for x in args.books.split(",") if x.strip()}
        books = [b for b in books if b[0] in want]
    if args.limit:
        books = books[: args.limit]
    if args.dry_run:
        return cmd_dry(root, args, books)
    return run_embedding(root, store, books, args)


if __name__ == "__main__":
    sys.exit(main())
