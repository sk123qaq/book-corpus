#!/usr/bin/env python3
"""运行记录：把一次用户动作从请求到答案的路径落到磁盘。

口径见 apps/demo/README.md「运行记录」。记录失败不影响回答；事件流只追加，
清单原子落盘；大文本按内容哈希进 blobs/。现有 缓存/ 与会话格式不改。

CLI:
  python3 apps/demo/runlog.py list
  python3 apps/demo/runlog.py timeline <run-id> [--full]
  python3 apps/demo/runlog.py bundle <session-id> [--out path]
  python3 apps/demo/runlog.py replay <run-id>
  python3 apps/demo/runlog.py prune [--days N] [--max-bytes N] [--apply]
  python3 apps/demo/runlog.py doctor
"""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE / "运行记录"
APP_VERSION = "demo-runlog-1"
RUN_ID_RE = re.compile(r"^[0-9A-Za-z._-]+$")
SESSION_ID_RE = re.compile(r"^[a-f0-9]{12}$")

ROW_LIMIT = 2000
PREVIEW = 400
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

_PI_VERSION = None
_WARNED = set()

ROOT = DEFAULT_ROOT
RUNS_DIR = ROOT / "runs"
BLOBS_DIR = ROOT / "blobs"
INDEX_PATH = ROOT / "index.jsonl"
SERVICE_PATH = ROOT / "service.jsonl"
LOCK_PATH = ROOT / ".index.lock"
_EXPORTS = ROOT / "exports"


def configure(root=None):
    """测试或临时实例换根。传空恢复默认。"""
    global ROOT, RUNS_DIR, BLOBS_DIR, INDEX_PATH, SERVICE_PATH, LOCK_PATH, _EXPORTS
    ROOT = Path(root or os.environ.get("BOOKS_RUNLOG_ROOT") or DEFAULT_ROOT).expanduser().resolve()
    RUNS_DIR = ROOT / "runs"
    BLOBS_DIR = ROOT / "blobs"
    INDEX_PATH = ROOT / "index.jsonl"
    SERVICE_PATH = ROOT / "service.jsonl"
    LOCK_PATH = ROOT / ".index.lock"
    _EXPORTS = ROOT / "exports"


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ms_since(t0):
    return round((time.monotonic() - t0) * 1000, 1)


def sha256(text):
    return hashlib.sha256(_text(text).encode("utf-8")).hexdigest()


def file_fingerprint(path, hash_content=True):
    """文件的大小、mtime 与内容哈希；读不到只留错误名。"""
    path = Path(path)
    try:
        st = path.stat()
        out = {"path": _redact(path), "size": st.st_size, "mtime": round(st.st_mtime, 3)}
        if hash_content:
            digest = hashlib.sha256()
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
            out["sha256"] = digest.hexdigest()
        return out
    except OSError as exc:
        return {"path": _redact(path), "error": exc.__class__.__name__}


def _text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, str):
        return value
    return str(value)


def _redact(text):
    text = _text(text)
    books_root = os.environ.get("BOOKS_ROOT", "")
    if books_root:
        text = text.replace(str(Path(books_root).resolve()), "<BOOKS_ROOT>")
    home = str(Path.home())
    if home and home != "/":
        text = text.replace(home, "~")
    return text


def _redact_obj(value):
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, list):
        return [_redact_obj(v) for v in value]
    if isinstance(value, tuple):
        return [_redact_obj(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _redact_obj(v) for k, v in value.items()}
    return value


def short(text, limit=ROW_LIMIT):
    text = _text(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"…[+{len(text) - limit}]"


def _warn(message):
    if message in _WARNED:
        return
    _WARNED.add(message)
    print(f"运行记录不可用：{message}", file=sys.stderr)


def _atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def _append_locked(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _read_jsonl(path):
    events = []
    try:
        with Path(path).open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except ValueError:
                    events.append({"type": "bad-json", "line": line[:500]})
    except OSError:
        return []
    return events


def pi_version():
    global _PI_VERSION
    if _PI_VERSION is not None:
        return _PI_VERSION
    _PI_VERSION = ""
    pi_bin = os.environ.get("PI_BIN") or shutil.which("pi")
    if not pi_bin:
        return _PI_VERSION
    try:
        proc = subprocess.run(
            [pi_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            _PI_VERSION = (proc.stdout or "").strip().splitlines()[0][:80]
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    return _PI_VERSION


def versions():
    env_keys = (
        "BOOKS_ROOT",
        "PI_BIN",
        "ANSWER_MODEL",
        "BOOKS_MODEL",
        "BOOKS_MODEL_PROVIDER",
        "BOOKS_THINKING",
        "BOOKS_RUNLOG_ROOT",
    )
    return {
        "app": APP_VERSION,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "pi": pi_version(),
        "cwd": _redact(Path.cwd()),
        "env": {k: _redact(os.environ[k]) for k in env_keys if os.environ.get(k)},
    }


def _probe():
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    BLOBS_DIR.mkdir(parents=True, exist_ok=True)
    probe = ROOT / ".probe"
    token = uuid.uuid4().hex
    with probe.open("w", encoding="utf-8") as fh:
        fh.write(token)
    with probe.open(encoding="utf-8") as fh:
        got = fh.read()
    probe.unlink(missing_ok=True)
    if got != token:
        raise OSError("探针读回不一致")


class Recorder:
    """一次动作一个写者。disabled 时所有方法退化成空操作。"""

    def __init__(self, run_id, path, meta, enabled=True):
        self.id = run_id
        self.path = Path(path)
        self.meta = dict(meta)
        self.enabled = bool(enabled)
        self.started_at = now()
        self.t0 = time.monotonic()
        self.seq = 0
        self.events = 0
        self.counts = {}
        self.index_fields = {}
        self._fh = None
        self._closed = False
        self._finishing = False
        if self.enabled:
            self.path.mkdir(parents=True, exist_ok=True)
            self._fh = (self.path / "run.jsonl").open("a", encoding="utf-8")
            start_fields = dict(self.meta)
            start_fields["run_kind"] = start_fields.pop("kind", "")
            self.event("run.start", **start_fields)

    def _disable(self, exc):
        if self.enabled:
            _warn(f"{exc.__class__.__name__}: {short(exc, 200)}")
        self.enabled = False
        if self._fh is not None:
            with contextlib.suppress(OSError):
                self._fh.close()
            self._fh = None

    def event(self, kind, **fields):
        if not self.enabled or self._closed:
            return
        self.seq += 1
        self.events += 1
        self.counts[kind] = self.counts.get(kind, 0) + 1
        row = {"seq": self.seq, "ts": now(), "type": kind}
        row.update(_redact_obj(fields))
        try:
            self._fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            self._fh.flush()
        except (OSError, TypeError, ValueError) as exc:
            self._disable(exc)

    def blob(self, text, suffix=".txt"):
        text = _text(text)
        data = text.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        rel = Path("blobs") / digest[:2] / f"{digest}{suffix}"
        if not self.enabled:
            return {"sha256": digest, "chars": len(text), "bytes": len(data), "path": ""}
        full = ROOT / rel
        if not full.exists():
            full.parent.mkdir(parents=True, exist_ok=True)
            tmp = full.with_name(f"{full.name}.tmp.{os.getpid()}")
            with tmp.open("wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(full)
        return {"sha256": digest, "chars": len(text), "bytes": len(data), "path": rel.as_posix()}

    def large(self, text, name, inline=1200):
        text = _text(text)
        if len(text) <= inline or not self.enabled:
            return {name: short(text, inline)}
        info = self.blob(text)
        return {
            name: short(text, PREVIEW),
            name + "_blob": info,
            name + "_chars": len(text),
            name + "_sha256": info["sha256"],
        }

    def set_index(self, **fields):
        self.index_fields.update(fields)

    def error(self, stage, exc):
        if isinstance(exc, BaseException):
            text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        else:
            text = _text(exc)
        self.event("error", stage=stage, error=short(text, 2000))

    @contextlib.contextmanager
    def step(self, kind, **meta):
        t0 = time.monotonic()
        self.event(kind + ".start", **meta)
        try:
            yield
        except BaseException as exc:
            self.error(kind, exc)
            raise
        finally:
            self.event(kind + ".end", ms=ms_since(t0))

    def finish(self, status="ok", error="", **extra):
        if self._closed or self._finishing:
            return {"id": self.id, "status": status}
        self._finishing = True
        if self.enabled and self._fh is not None:
            self.event("run.end", status=status, ms=ms_since(self.t0), error=short(error, 500))
        manifest = {
            "id": self.id,
            "kind": self.meta.get("kind", ""),
            "status": status,
            "started_at": self.started_at,
            "ended_at": now(),
            "ms": ms_since(self.t0),
            "events": self.events,
            "counts": dict(self.counts),
            "error": short(error, 2000),
            "versions": versions(),
        }
        for key in ("session", "turn", "question", "thinking", "parent", "entry", "anchor"):
            if key in self.meta:
                manifest[key] = self.meta[key]
        for key, value in _redact_obj(self.index_fields).items():
            if key not in manifest:
                manifest[key] = value
        manifest.update(_redact_obj(extra))
        self._closed = True
        if self.enabled and self._fh is not None:
            try:
                _atomic_write(self.path / "run.json", json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
                index_row = {
                    "id": self.id,
                    "kind": manifest["kind"],
                    "status": status,
                    "started_at": manifest["started_at"],
                    "ended_at": manifest["ended_at"],
                    "ms": manifest["ms"],
                    "session": manifest.get("session", ""),
                    "turn": manifest.get("turn", 0),
                    "question": short(manifest.get("question", ""), 300),
                    "thinking": manifest.get("thinking", ""),
                    "parent": manifest.get("parent", ""),
                    "events": self.events,
                    "error": short(error, 300),
                    "dir": self.id,
                }
                for key, value in self.index_fields.items():
                    if key not in index_row:
                        index_row[key] = _redact_obj(value)
                _append_locked(INDEX_PATH, json.dumps(index_row, ensure_ascii=False, default=str) + "\n")
            except (OSError, TypeError, ValueError) as exc:
                self._disable(exc)
            with contextlib.suppress(OSError):
                self._fh.close()
            self._fh = None
        return manifest


def start(kind, **meta):
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    meta = dict(meta)
    meta["kind"] = kind
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        BLOBS_DIR.mkdir(parents=True, exist_ok=True)
        _probe()
    except OSError as exc:
        _warn(f"{exc.__class__.__name__}: {short(exc, 200)}")
        return Recorder("", "", meta, enabled=False)
    return Recorder(run_id, RUNS_DIR / run_id, meta)


def service_event(kind, **fields):
    row = {"ts": now(), "type": kind}
    row.update(_redact_obj(fields))
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        _append_locked(SERVICE_PATH, json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        _warn(f"{exc.__class__.__name__}: {short(exc, 200)}")


def _manifest(run_dir):
    run_dir = Path(run_dir)
    path = run_dir / "run.json"
    if path.is_file():
        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                data.setdefault("dir", run_dir.name)
                return data
        except (OSError, ValueError):
            pass
    events = _read_jsonl(run_dir / "run.jsonl")
    if not events:
        return None
    first = events[0]
    last = events[-1]
    meta = {k: first.get(k) for k in ("session", "turn", "question", "thinking", "parent", "kind") if k in first}
    meta.update(
        {
            "id": run_dir.name,
            "status": last.get("status", "open"),
            "started_at": first.get("ts", ""),
            "ended_at": last.get("ts", ""),
            "events": len(events),
            "dir": run_dir.name,
            "open": True,
        }
    )
    return meta


def _run_dirs():
    try:
        return [entry for entry in RUNS_DIR.iterdir() if entry.is_dir() and RUN_ID_RE.match(entry.name)]
    except OSError:
        return []


def list_runs(limit=50, session=None, status=None):
    rows = []
    for path in _run_dirs():
        manifest = _manifest(path)
        if not manifest:
            continue
        if session and manifest.get("session") != session:
            continue
        if status and manifest.get("status") != status:
            continue
        rows.append(manifest)
    rows.sort(key=lambda row: row.get("started_at", ""), reverse=True)
    return rows[: max(1, int(limit or 50))]


def read_run(run_id):
    if not RUN_ID_RE.match(run_id or ""):
        raise ValueError("运行编号不合法")
    run_dir = RUNS_DIR / run_id
    if not run_dir.is_dir():
        raise FileNotFoundError(run_id)
    return {"manifest": _manifest(run_dir) or {}, "events": _read_jsonl(run_dir / "run.jsonl")}


def _blob_refs(value, out):
    if isinstance(value, dict):
        rel = value.get("path")
        if value.get("sha256") and isinstance(rel, str) and rel.startswith("blobs/"):
            out.add(rel)
        for item in value.values():
            _blob_refs(item, out)
    elif isinstance(value, list):
        for item in value:
            _blob_refs(item, out)


def _read_blob(path):
    full = ROOT / path
    with full.open(encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _brief(event, full=False):
    kind = event.get("type", "?")
    bits = [kind]
    for key in (
        "stage",
        "session",
        "turn",
        "question",
        "select",
        "fresh",
        "source",
        "hit",
        "terms",
        "groups",
        "note",
        "ms",
        "chars",
        "kept",
        "dropped",
        "history_chars",
        "version",
        "path",
        "line",
        "score",
        "coverage",
        "cached",
        "ok",
        "confidence",
        "not_found",
        "error",
        "reason",
        "name",
        "args",
        "result",
        "is_error",
        "duration_ms",
        "exit_code",
        "timed_out",
        "mode",
        "model",
        "thinking",
        "tool_calls",
        "event_count",
        "total_tokens",
        "cost",
        "raw_chars",
        "final_chars",
        "truncated",
        "sections",
        "passed",
        "total",
        "merged",
        "origin_run",
        "generated_ms",
        "read_ms",
    ):
        if key not in event:
            continue
        value = event[key]
        if isinstance(value, (dict, list)):
            value = short(json.dumps(value, ensure_ascii=False), 220)
        elif isinstance(value, str):
            value = short(value, 220)
        bits.append(f"{key}={value}")
    line = "  ".join(bits)
    if full:
        for key, value in event.items():
            if key.endswith("_blob") and isinstance(value, dict) and value.get("path"):
                with contextlib.suppress(OSError):
                    line += f"\n      {key}: {short(_read_blob(value['path']), 4000)}"
    return line


def timeline(run_id, full=False):
    data = read_run(run_id)
    manifest = data["manifest"]
    print(
        f"run {run_id}  kind={manifest.get('kind', '')}  status={manifest.get('status', '')}  "
        f"session={manifest.get('session', '')}  turn={manifest.get('turn', '')}  ms={manifest.get('ms', '')}"
    )
    if manifest.get("question"):
        print(f"question: {short(manifest['question'], 300)}")
    if manifest.get("parent"):
        print(f"parent: {manifest['parent']}")
    if manifest.get("error"):
        print(f"error: {short(manifest['error'], 800)}")
    for event in data["events"]:
        print(f"{event.get('seq', ''):>4} {_brief(event, full=full)}")
    return manifest


def bundle(session_id, out=None):
    if not SESSION_ID_RE.match(session_id or ""):
        raise ValueError("会话编号不合法")
    rows = list_runs(limit=100000, session=session_id)
    if not rows:
        raise FileNotFoundError("没有这个会话的运行记录")
    out = Path(out) if out else _EXPORTS / f"session-{session_id}-{time.strftime('%Y%m%dT%H%M%S')}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    session_path = HERE / "缓存" / "sessions" / f"{session_id}.json"
    blobs = set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        if session_path.is_file():
            zf.write(session_path, "session.json")
        for row in rows:
            run_dir = RUNS_DIR / row["dir"]
            data = read_run(row["dir"])
            zf.writestr(f"runs/{row['dir']}/run.json", json.dumps(row, ensure_ascii=False, indent=1) + "\n")
            if (run_dir / "run.jsonl").is_file():
                zf.write(run_dir / "run.jsonl", f"runs/{row['dir']}/run.jsonl")
            _blob_refs(data["events"], blobs)
        for rel in sorted(blobs):
            full = ROOT / rel
            if full.is_file():
                zf.write(full, rel)
        zf.writestr(
            "bundle.json",
            json.dumps(
                {
                    "session": session_id,
                    "created_at": now(),
                    "runs": [row["dir"] for row in rows],
                    "blobs": sorted(blobs),
                },
                ensure_ascii=False,
                indent=1,
            )
            + "\n",
        )
    return out


def _parse_prompt_rebuild(event):
    from importlib import import_module

    answer = import_module("answer")
    inputs = json.loads(_read_blob(event["inputs_blob"]["path"]))
    builder = inputs.get("builder", "single")
    if builder == "multi":
        prompt = answer.build_multi_prompt(
            inputs["question"],
            inputs["plans"],
            inputs["windows"],
            coverage=inputs.get("coverage", ""),
            history=inputs.get("history", ""),
            focus_title=inputs.get("focus_title", ""),
        )
        version = answer.PROMPT_VERSION_MULTI
    else:
        prompt = answer.build_prompt(
            inputs["question"],
            inputs["book"],
            inputs["windows"],
            inputs["chapters"],
            history=inputs.get("history", ""),
        )
        version = answer.PROMPT_VERSION
    return prompt, version


def replay(run_id):
    data = read_run(run_id)
    events = data["events"]
    checks = []

    blobs = set()
    _blob_refs(events, blobs)
    missing = [rel for rel in sorted(blobs) if not (ROOT / rel).is_file()]
    bad = []
    for rel in sorted(blobs):
        full = ROOT / rel
        if not full.is_file():
            continue
        digest = Path(rel).name.split(".")[0]
        with full.open("rb") as fh:
            got = hashlib.sha256(fh.read()).hexdigest()
        if got != digest:
            bad.append(rel)
    checks.append(("blobs", not missing and not bad, f"missing={len(missing)} bad={len(bad)}"))

    prompt_events = [e for e in events if e.get("type") == "prompt" and e.get("inputs_blob")]
    for event in prompt_events[:1]:
        try:
            prompt, version = _parse_prompt_rebuild(event)
            got = sha256(prompt)
            want = event.get("sha256", "")
            checks.append(("prompt-rebuild", got == want, f"{version} {'match' if got == want else 'mismatch'}"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            checks.append(("prompt-rebuild", False, f"{exc.__class__.__name__}: {exc}"))

    finals = [e for e in events if e.get("type") == "model.final" and e.get("raw_blob")]
    if finals:
        event = finals[-1]
        try:
            raw = _read_blob(event["raw_blob"]["path"])
            parser = event.get("parser", "")
            if parser == "terms":
                from importlib import import_module

                trace = import_module("trace")
                terms, groups = trace._parse_extract(raw)
                got = sha256(json.dumps({"terms": terms, "groups": groups}, ensure_ascii=False, sort_keys=True))
                want = event.get("parsed_sha256", "")
                ok = bool(terms) and got == want
            elif parser == "related":
                from importlib import import_module

                related = import_module("related")
                parsed = related._parse(raw)
                got = sha256(json.dumps(parsed, ensure_ascii=False, sort_keys=True)) if parsed else ""
                ok = bool(parsed) and got == event.get("parsed_sha256", "")
            else:
                from importlib import import_module

                answer = import_module("answer")
                parsed = answer.parse_reply(raw)
                got = sha256(parsed.get("answer", "")) if parsed else ""
                ok = bool(parsed) and got == event.get("parsed_sha256", "")
            checks.append(("raw-reparse", ok, parser or "answer"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            checks.append(("raw-reparse", False, f"{exc.__class__.__name__}: {exc}"))
    else:
        checks.append(("raw-reparse", True, "skip (no model.final)"))

    failed = 0
    for name, ok, detail in checks:
        print(f"[{'过' if ok else '败'}] {name} · {detail}")
        if not ok:
            failed += 1
    print(f"回放自检：{len(checks)} 项，失败 {failed}")
    return 0 if failed == 0 else 1


def _run_bytes(path):
    return sum(file.stat().st_size for file in Path(path).rglob("*") if file.is_file())


def prune(days=None, max_bytes=None, apply=False):
    if not days and not max_bytes:
        raise ValueError("给 --days 或 --max-bytes，至少一个")
    rows = list_runs(limit=100000)
    cutoff = time.time() - float(days or 0) * 86400 if days else None
    selected = []
    total = 0
    for row in sorted(rows, key=lambda item: item.get("started_at", "")):
        run_dir = RUNS_DIR / row["dir"]
        size = _run_bytes(run_dir)
        total += size
        old = False
        if cutoff is not None:
            with contextlib.suppress(ValueError):
                old = datetime.fromisoformat(row.get("started_at", "")).timestamp() < cutoff
        if old or (max_bytes and total > int(max_bytes)):
            selected.append((row, size))
    if not selected:
        print("没有可清理的运行")
        return 0
    freed = sum(size for _row, size in selected)
    for row, size in selected:
        print(f"{row['dir']} {size / 1024.0:8.1f} KB  {row.get('status', '')}  {short(row.get('question', ''), 80)}")
    print(f"合计 {freed / 1024.0:.1f} KB，{len(selected)} 个运行")
    if not apply:
        print("只打印，未删除。确认后加 --apply。")
        return 0
    for row, _size in selected:
        shutil.rmtree(RUNS_DIR / row["dir"], ignore_errors=True)
    remaining = list_runs(limit=100000)
    refs = set()
    for row in remaining:
        _blob_refs(read_run(row["dir"])["events"], refs)
    removed = 0
    for full in sorted(BLOBS_DIR.rglob("*"), reverse=True):
        if not full.is_file():
            continue
        rel = full.relative_to(ROOT).as_posix()
        if rel not in refs:
            with contextlib.suppress(OSError):
                full.unlink()
                removed += 1
    service_event("prune", runs=len(selected), bytes=freed, blobs=removed)
    print(f"已删 {len(selected)} 个运行，{removed} 个未引用 blob")
    return 0


def doctor():
    checks = []
    checks.append(("root", ROOT.is_dir() and os.access(ROOT, os.W_OK), str(ROOT)))
    rows = list_runs(limit=100000)
    checks.append(("runs", True, f"{len(rows)} 个"))
    bad_index = 0
    if INDEX_PATH.is_file():
        with INDEX_PATH.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    json.loads(line)
                except ValueError:
                    bad_index += 1
    checks.append(("index", bad_index == 0, f"坏行 {bad_index}"))
    blobs = 0
    blob_bytes = 0
    for full in BLOBS_DIR.rglob("*"):
        if not full.is_file():
            continue
        blobs += 1
        with contextlib.suppress(OSError):
            blob_bytes += full.stat().st_size
    checks.append(("blobs", True, f"{blobs} 个，{blob_bytes / 1024.0:.1f} KB"))
    refs = set()
    for row in rows:
        _blob_refs(read_run(row["dir"])["events"], refs)
    missing = [rel for rel in refs if not (ROOT / rel).is_file()]
    checks.append(("refs", not missing, f"缺 {len(missing)}"))
    for name, ok, detail in checks:
        print(f"[{'过' if ok else '败'}] {name} · {detail}")
    return 0 if all(ok for _name, ok, _detail in checks) else 1


def find_by_cache_key(key):
    if not key:
        return None
    for row in list_runs(limit=300):
        for event in _read_jsonl(RUNS_DIR / row["dir"] / "run.jsonl"):
            if event.get("type") in ("cache.write", "cache.hit") and event.get("key") == key:
                return {"run": row["dir"], "ms": event.get("generated_ms") or row.get("ms"), "event": event}
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="演示应用运行记录")
    parser.add_argument("--root", default=None, help="运行记录根目录")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list")
    p_list.add_argument("--session", default=None)
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--limit", type=int, default=30)

    p_time = sub.add_parser("timeline")
    p_time.add_argument("run")
    p_time.add_argument("--full", action="store_true")

    p_bundle = sub.add_parser("bundle")
    p_bundle.add_argument("session")
    p_bundle.add_argument("--out", default=None)

    p_replay = sub.add_parser("replay")
    p_replay.add_argument("run")

    p_prune = sub.add_parser("prune")
    p_prune.add_argument("--days", type=float, default=None)
    p_prune.add_argument("--max-bytes", type=int, default=None)
    p_prune.add_argument("--apply", action="store_true")

    sub.add_parser("doctor")

    args = parser.parse_args(argv)
    configure(args.root)
    if args.cmd == "list":
        for row in list_runs(limit=args.limit, session=args.session, status=args.status):
            print(
                f"{row['id']}  {row.get('kind', ''):<9} {row.get('status', ''):<6} "
                f"{float(row.get('ms') or 0):6.0f}ms  {row.get('session', '') or '-'}  "
                f"turn={row.get('turn', '')}  {short(row.get('question', ''), 80)}"
            )
        return 0
    if args.cmd == "timeline":
        timeline(args.run, full=args.full)
        return 0
    if args.cmd == "bundle":
        print(bundle(args.session, out=args.out))
        return 0
    if args.cmd == "replay":
        return replay(args.run)
    if args.cmd == "prune":
        return prune(days=args.days, max_bytes=args.max_bytes, apply=args.apply)
    if args.cmd == "doctor":
        return doctor()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
