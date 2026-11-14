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
sys.path.insert(0, str(HERE))
from defpass import paragraphs
from lib_common import PATHS, ROOT

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


def chapter_chunks(md_path, target=700, overlap=0):
    """一章切成若干块，返回 [(起始行, 文本)]。"""
    limit = int(target * 1.15)
    out = []
    cur = []  # (line, piece)
    cur_chars = 0
    for line, para in paragraphs(md_path):
        para = clean_para(para)
        if not para:
            continue
        for piece in split_pieces(para, target):
            if cur and cur_chars + len(piece) > limit and len(cur) > overlap:
                out.append((cur[0][0], "\n".join(p for _, p in cur)))
                cur = cur[-overlap:] if overlap else []
                cur_chars = sum(len(p) for _, p in cur)
            cur.append((line, piece))
            cur_chars += len(piece)
    if cur:
        out.append((cur[0][0], "\n".join(p for _, p in cur)))
    return out


def chapter_paths(root, book_path):
    md = Path(root) / "books" / book_path / "md"
    if not md.is_dir():
        return []
    return [p for p in sorted(md.iterdir()) if p.name.endswith(".md") and p.name.lower() != "readme.md" and p.is_file()]


def book_chunks(root, book_path, target=700, overlap=0):
    root = Path(root)
    out = []
    seq = 0
    for path in chapter_paths(root, book_path):
        rel = path.relative_to(root).as_posix()
        for line, text in chapter_chunks(path, target, overlap):
            out.append({"path": rel, "seq": seq, "line": line, "text": text, "chars": len(text)})
            seq += 1
    return out


def sig_of(paths):
    h = hashlib.sha256()
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
    def __init__(self, db_path, vec_path, dim=DEFAULT_DIM, model=DEFAULT_MODEL, chunk_chars=700, overlap=0):
        self.db_path = Path(db_path)
        self.vec_path = Path(vec_path)
        self.dim = dim
        self.model = model
        self.chunk_chars = chunk_chars
        self.overlap = overlap
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._init_meta()
        self.last_id = int(self.meta("last_id", "0"))
        self._open_vectors()

    def acquire_write_lock(self):
        """同一份向量只允许一个写者；--status/--verify 不加锁。"""
        path = self.db_path.parent / (self.db_path.name + ".lock")
        self._lock = path.open("a")
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(f"另一个 embed 进程正在写 {self.db_path}（锁 {path}），等它跑完再试") from None

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
    row = store.db.execute("SELECT count(*) AS n, max(id) AS m FROM chunk").fetchone()
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
    for uid, path, title in books:
        chunks = book_chunks(root, path, args.chunk_chars, args.overlap)
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


def run_embedding(root, store, books, args):
    store.acquire_write_lock()
    total_bytes = 0
    todo = []
    for uid, path, title in books:
        paths = chapter_paths(root, path)
        size = sum(p.stat().st_size for p in paths)
        total_bytes += size
        todo.append((uid, path, title, paths, size))
    if not todo:
        print("没有要跑的书")
        return 0

    done_bytes = 0
    processed = skipped = errors = 0
    tokens_run = 0
    run_started = time.time()
    book_started = run_started
    proc = ensure_server(root, args.server, keep=args.keep_server)
    try:
        for i, (uid, path, title, paths, size) in enumerate(todo, 1):
            sig = sig_of(paths)
            row = store.book_row(uid)
            if row and row["status"] == "done" and row["sig"] == sig and not args.force:
                skipped += 1
                done_bytes += size
                continue
            t0 = time.time()
            book_started = time.time()
            try:
                chunks = book_chunks(root, path, args.chunk_chars, args.overlap)
                if not chunks:
                    store.mark(uid, title, sig, "empty", "没有正文")
                    done_bytes += size
                    continue
                vecs = []
                sent = 0
                for start in range(0, len(chunks), args.batch):
                    batch = chunks[start : start + args.batch]
                    v, tok = post_embeddings(args.server, [c["text"] for c in batch], store.dim, timeout=args.timeout)
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
        f"本次：新嵌 {processed} 本，跳过 {skipped} 本，失败 {errors} 本；"
        f"库内 {s['chunks']} 块，{human(s['vec_bytes'])}"
    )
    return 1 if errors else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--status", action="store_true", help="看进度")
    ap.add_argument("--verify", action="store_true", help="校验向量与元数据")
    ap.add_argument("--dry-run", action="store_true", help="只切块，不请求服务")
    ap.add_argument("--serve", action="store_true", help="前台起 llama-server")
    ap.add_argument("--books", help="逗号分隔的 uid，默认全部")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 本")
    ap.add_argument("--force", action="store_true", help="已完成的也重嵌（旧向量留作垃圾）")
    ap.add_argument("--chunk-chars", type=int, default=700)
    ap.add_argument("--overlap", type=int, default=0, help="块间重叠的段数，默认 0")
    ap.add_argument("--batch", type=int, default=32, help="每次请求的块数")
    ap.add_argument("--timeout", type=int, default=900, help="单次请求超时秒数")
    ap.add_argument("--server", default=os.environ.get("BOOKS_EMBED_URL", DEFAULT_URL))
    ap.add_argument("--keep-server", action="store_true", help="跑完不杀自己起的服务")
    args = ap.parse_args()

    root = Path(ROOT)
    if args.serve:
        return cmd_serve(root, args)
    store = Store(PATHS.embed_db, PATHS.embed_vectors, DEFAULT_DIM, DEFAULT_MODEL, args.chunk_chars, args.overlap)
    if args.status:
        cmd_status(store)
        return 0
    if args.verify:
        return cmd_verify(store)

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
