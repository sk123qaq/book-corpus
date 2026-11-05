#!/usr/bin/env python3
"""把 docx/xlsx/xls/pptx/txt/md 变成书库的 md 契约：README.md + images/ + .manifest.json。

三种模式（操作台的 docs.run 就是 plan → convert 两步）：

  docs_to_md.py plan    --root R --job-dir J [--applied P] [--only B0001,B0002]
  docs_to_md.py convert --root R --job-dir J
  docs_to_md.py worker  --source S --format F --out DIR     # 只在 工作区/.venv-docs 里跑

plan 只读 build/library.db，选书两条来源：`v_pending_md` 里主源是 doc 格式的（补转），
与导入 `applied.json` 里 action=add 的 doc 格式新书。convert 逐本落地，单本失败只记
report.json，进程照样返回 0，好让后面的 build_library / gen_book_catalog 继续跑；只有
基础设施错误（job 目录写不了、report 生不出来）才非 0。

落盘与 PDF 单文件同形：正文 md/<书名>.md 必须有 H1，README 走 pdf_ocr_to_md.write_readme，
manifest 的 source_sha256 是**源文件** sha（v_pending_md 拿它比对）。绝不直接写 md/：
先写 books/<uid>/.md-build-<pid>/，旧 md/ 备份到 工作区/批次/覆盖-<时间>/<uid>/，再 rename。
"""

import argparse
import base64
import binascii
import datetime
import json
import os
import pathlib
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import suppress

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))  # bookcorpus 包目录，好看 docenv
import docenv
import lib_common as L
import pdf_ocr_to_md as A

ROOT = L.ROOT
TEXT_FORMATS = ("txt", "md")
WORKER_ENV = "BOOKS_DOC_WORKER_CMD"
MAX_BYTES = 200 * 1024 * 1024
WORKER_TIMEOUT = 300
BIG_IMAGE = 50 * 1024 * 1024
UNLISTED = "- 无（整本单文件）"
H1_RE = re.compile(r"(?m)^#\s+\S")
FM_RE = re.compile(r"\A---\n.*?\n---\n\n?", re.S)
DATA_URI_MD = re.compile(r"!\[([^\]\n]*)\]\(\s*data:image/([A-Za-z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+?)\s*\)")
DATA_URI_HTML = re.compile(
    r"<img[^>]*\bsrc=\"\s*data:image/([A-Za-z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+?)\s*\"[^>]*>", re.I
)
IMG_EXT = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "gif": "gif", "webp": "webp", "bmp": "bmp", "svg+xml": "svg"}


def now():
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def job_path(job_dir, name):
    return pathlib.Path(job_dir) / name


def read_json(path, default=None):
    try:
        with pathlib.Path(path).open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, doc):
    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    L.write_json_atomic(str(target), doc)


def db_connect(root):
    """只读打开书目库。计划阶段绝不开写。"""
    path = pathlib.Path(root) / "build" / "library.db"
    if not path.is_file():
        raise SystemExit(f"没有 {path}，先跑 build_library.py --emit-json")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


PICK_SQL = (
    "SELECT p.uid, p.title, p.format, p.sha256, s.file, s.bytes AS src_bytes "
    "FROM v_pending_md p JOIN source s ON s.uid = p.uid AND s.primary_src = 1"
)


def pending_rows(root):
    con = db_connect(root)
    try:
        return [dict(r) for r in con.execute(PICK_SQL + " ORDER BY p.uid")]
    finally:
        con.close()


def book_rows(root, uids):
    """--only 用：直接按 uid 取主源，不再管 v_pending_md 怎么想。"""
    con = db_connect(root)
    try:
        out = []
        for uid in uids:
            row = con.execute(
                "SELECT b.uid AS uid, b.title AS title, s.format AS format, s.sha256 AS sha256,"
                " s.file AS file, s.bytes AS src_bytes"
                " FROM book b JOIN source s ON s.uid = b.uid AND s.primary_src = 1 WHERE b.uid = ?",
                (uid,),
            ).fetchone()
            if row:
                out.append(dict(row))
        return out
    finally:
        con.close()


def parse_uids(raw):
    out = []
    for part in re.split(r"[,，\s]+", raw or ""):
        part = part.strip()
        if not part:
            continue
        if not re.match(r"^B\d{4}$", part):
            raise SystemExit(f"uid 不像 B0001：{part}")
        if part not in out:
            out.append(part)
    return out


def manifest_sha(md_dir):
    return str((L.read_md_manifest(md_dir) or {}).get("source_sha256") or "")


def worklist_item(row, reason):
    fmt = (row.get("format") or "").lower()
    return {
        "uid": row["uid"],
        "title": row.get("title") or "",
        "format": fmt,
        "path": row.get("file") or "",
        "sha256": row.get("sha256") or "",
        "reason": reason,
    }


def plan(root, job_dir, applied_path="", only=""):
    """选书、去重、标 skipped，写 worklist.json。只读书目库。"""
    items = {}
    for row in pending_rows(root):
        if (row.get("format") or "").lower() in L.DOC_FORMATS:
            items[row["uid"]] = worklist_item(row, "backfill")
    if applied_path:
        doc = read_json(applied_path, {}) or {}
        for row in doc.get("applied") or []:
            fmt = (row.get("format") or "").lower()
            uid = (row.get("uid") or "").strip()
            if row.get("action") != "add" or fmt not in L.DOC_FORMATS or not uid:
                continue
            items[uid] = worklist_item({"uid": uid, "title": "", "format": fmt, "sha256": "", "file": ""}, "new")
        missing = [uid for uid, it in items.items() if it["reason"] == "new" and not it["path"]]
        for row in book_rows(root, missing):
            it = items[row["uid"]]
            it.update(worklist_item(row, "new"))
    uids = parse_uids(only)
    if uids:
        have = {row["uid"]: row for row in book_rows(root, uids)}
        picked = {}
        for uid in uids:
            if uid in items:
                picked[uid] = items[uid]
            elif uid in have:
                # --only 只服务单本重试；非 doc 格式不接，免得把 epub 塞进 markitdown
                if (have[uid].get("format") or "").lower() in L.DOC_FORMATS:
                    picked[uid] = worklist_item(have[uid], "backfill")
            else:
                picked[uid] = {"uid": uid, "title": "", "format": "", "path": "", "sha256": "", "reason": "new"}
        items = picked
    out = []
    for uid in sorted(items):
        it = items[uid]
        md_dir = pathlib.Path(root) / "books" / uid / "md"
        if md_dir.is_dir() and it["sha256"] and manifest_sha(md_dir) == it["sha256"]:
            it["skipped"] = True
        out.append(it)
    write_json(
        job_path(job_dir, "worklist.json"),
        out,
    )
    write_json(
        job_path(job_dir, "progress.json"),
        {"phase": "planned", "done": 0, "total": len(out), "current": "", "updated_at": now()},
    )
    todo = [x for x in out if not x.get("skipped")]
    new_n = sum(1 for x in todo if x["reason"] == "new")
    back_n = sum(1 for x in todo if x["reason"] == "backfill")
    skip_n = len(out) - len(todo)
    print(f"计划 {len(todo)} 本（新书 {new_n}、补转 {back_n}；跳过 {skip_n}）→ {job_path(job_dir, 'worklist.json')}")
    for it in out:
        flag = "跳过" if it.get("skipped") else it["reason"]
        print(f"  {it['uid']} {it['format'] or '?':<5} {it['title'] or it['path']}（{flag}）")
    return 0


def decode_text(raw):
    """UTF-8 优先，坏了试 GB18030，再坏就 replace 兜底，绝不因为编码丢整本。"""
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc), enc, False
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), "utf-8", True


def image_ext(kind):
    kind = (kind or "").lower()
    return IMG_EXT.get(kind, re.sub(r"[^a-z0-9]", "", kind) or "bin")


def extract_images(text, img_dir, warnings):
    """把 data URI 图片抽到 images/，改写成相对链接。解不出就整段删掉并计数。"""
    state = {"n": 0, "dropped": 0}
    img_dir = pathlib.Path(img_dir)

    def save(kind, b64):
        ext = image_ext(kind)
        try:
            blob = base64.b64decode(re.sub(r"\s+", "", b64), validate=False)
        except (binascii.Error, ValueError):
            return ""
        if not blob:
            return ""
        if len(b64) > BIG_IMAGE:
            warnings.append(f"图片超过 50MB（{len(blob)} 字节），照旧落盘")
        img_dir.mkdir(parents=True, exist_ok=True)
        state["n"] += 1
        name = f"img-{state['n']}.{ext}"
        with (img_dir / name).open("wb") as fh:
            fh.write(blob)
        return name

    def re_md(m):
        name = save(m.group(2), m.group(3))
        if not name:
            state["dropped"] += 1
            return ""
        return f"![{m.group(1)}](images/{name})"

    def re_html(m):
        name = save(m.group(1), m.group(2))
        if not name:
            state["dropped"] += 1
            return ""
        return f"![](images/{name})"

    text = DATA_URI_MD.sub(re_md, text)
    text = DATA_URI_HTML.sub(re_html, text)
    return text, state["n"], state["dropped"]


def ensure_h1(text, title):
    """正文必须有一级标题；原文件已有 H1 就保留，别再叠一个。"""
    body = FM_RE.sub("", text or "").strip()
    if not body:
        return ""
    head = f"# {title or '未命名'}\n\n"
    return body if H1_RE.search(body) else head + body


def worker_argv(root):
    """测试用 BOOKS_DOC_WORKER_CMD 换掉 venv python + worker 子命令。"""
    raw = os.environ.get(WORKER_ENV, "").strip()
    if raw:
        return shlex.split(raw)
    return [docenv.python(root), str(HERE / "docs_to_md.py"), "worker"]


def run_worker(root, src, fmt, out_dir):
    """起 worker 子进程，独立进程组，超时杀整组。回 (payload, error_reason)。"""
    argv = [*worker_argv(root), "--source", src, "--format", fmt, "--out", out_dir]
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        return None, f"convert-error: {exc}"
    try:
        out, err = proc.communicate(timeout=WORKER_TIMEOUT)
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.communicate()
        return None, "timeout"
    payload = None
    for line in reversed((out or "").strip().splitlines()):
        try:
            cand = json.loads(line)
        except ValueError:
            continue
        if isinstance(cand, dict) and "ok" in cand:
            payload = cand
            break
    if payload is None:
        tail = ((err or "") + (out or "")).strip().splitlines()
        return None, f"convert-error: {tail[-1] if tail else f'退出码 {proc.returncode}'}"
    if not payload.get("ok"):
        return payload, f"convert-error: {payload.get('error') or 'worker 失败'}"
    if proc.returncode != 0:
        return payload, f"convert-error: 退出码 {proc.returncode}"
    return payload, ""


def install_md(build_dir, md_dir, backup_dir):
    """临时目录 → md/。旧 md/ 先挪进备份区，绝不直接写 md/。"""
    build = pathlib.Path(build_dir)
    md = pathlib.Path(md_dir)
    md.parent.mkdir(parents=True, exist_ok=True)
    img_dir = build / "images"
    if img_dir.is_dir() and not any(img_dir.iterdir()):
        img_dir.rmdir()  # 没图的书写出去也别留个空目录
    backup = ""
    wanted = pathlib.Path(backup_dir)
    if md.is_dir():
        wanted.parent.mkdir(parents=True, exist_ok=True)
        target, n = wanted, 2
        while target.exists():
            target = pathlib.Path(f"{wanted}-{n}")
            n += 1
        md.rename(target)
        backup = str(target)
    elif md.exists():
        md.unlink()
    build.rename(md)
    return backup


def convert_book(root, item, ctx):
    """一本书：读源（或起 worker）→ 写临时 md 目录 → 备份旧 md → rename。"""
    t0 = time.time()
    uid = item["uid"]
    title = item.get("title") or uid
    fmt = (item.get("format") or "").lower()
    row = {
        "uid": uid,
        "title": title,
        "format": fmt,
        "status": "failed",
        "reason": "",
        "md": "",
        "chars": 0,
        "images": 0,
        "md_bytes": 0,
        "charset": "",
        "charset_fallback": False,
        "elapsed_ms": 0,
        "backup": "",
        "error": "",
    }
    src = pathlib.Path(root) / item["path"] if item.get("path") else None
    if not src or not src.is_file():
        row.update(reason="source-missing", error=f"源文件不在：{item.get('path') or '无'}")
        return row
    sha = L.sha256_file(str(src))
    if item.get("sha256") and sha != item["sha256"]:
        row.update(reason="source-changed", error="文件已变，重新计划")
        return row
    if src.stat().st_size > MAX_BYTES:
        row.update(reason="too-large", error="源文件超过 200MB")
        return row
    if fmt not in L.DOC_FORMATS:
        row.update(reason="unsupported", error=f"格式不认识：{fmt}")
        return row
    if ctx.get("venv_error") and fmt not in TEXT_FORMATS:
        row.update(reason="venv-missing", error=ctx["venv_error"])
        return row

    bdir = pathlib.Path(root) / "books" / uid
    build = bdir / f".md-build-{os.getpid()}"
    if build.is_dir():
        shutil.rmtree(build, ignore_errors=True)
    (build / "images").mkdir(parents=True, exist_ok=True)
    src_name = src.name
    images_dropped, warnings = 0, []
    if fmt in TEXT_FORMATS:
        raw = src.read_bytes()
        text, charset, fallback = decode_text(raw)
        method = "copy-md" if fmt == "md" else ("text-gb18030" if charset == "gb18030" else "text-utf8")
        tool_version = "copy 1" if fmt == "md" else "text 1"
        images = 0
        if fmt != "md":
            text, images, images_dropped = extract_images(text, build / "images", warnings)
    else:
        payload, err = run_worker(root, str(src), fmt, str(build))
        charset, fallback, images = "utf-8", False, 0
        method = f"markitdown-{fmt}"
        tool_version = "markitdown 0.1.9"
        if err:
            shutil.rmtree(build, ignore_errors=True)
            row.update(reason=(err.split(":", 1)[0].strip() or "convert-error"), error=err)
            row["elapsed_ms"] = int((time.time() - t0) * 1000)
            return row
        images = int(payload.get("images") or 0)
        images_dropped = int(payload.get("images_dropped") or 0)
        warnings = [str(x) for x in (payload.get("warnings") or [])]
        body_path = build / "body.md"
        text = ""
        if body_path.is_file():
            text = body_path.read_text(encoding="utf-8")
            body_path.unlink()
    # md 源是复制不是转换：逐字节保留，连 H1 都不补
    body = text if fmt == "md" else ensure_h1(text, title)
    if not body.strip():
        shutil.rmtree(build, ignore_errors=True)
        row.update(reason="empty-output", error="转换结果没有正文")
        row["elapsed_ms"] = int((time.time() - t0) * 1000)
        return row

    md_name = A.sanitize_filename(title, uid) + ".md"
    md_file = build / md_name
    with md_file.open("w", encoding="utf-8") as fh:
        fh.write(body if fmt == "md" else body + "\n")
    md_bytes = md_file.stat().st_size
    chars = len(body)
    A.write_readme(
        build,
        title,
        "",
        "",
        "",
        1,
        images,
        src_name,
        [(title, md_name)],
        UNLISTED,
    )
    A.write_manifest(
        build,
        {
            "uid": uid,
            "method": method,
            "tool_version": tool_version,
            "source": item["path"],
            "source_sha256": sha,
            "chapters": 1,
            "bytes": md_bytes,
            "chars": chars,
            "images": images,
            "images_dropped": images_dropped,
            "structure": "single",
            "single_file": True,
            "charset": charset,
            "charset_fallback": fallback,
            "generated_at": now(),
            "assembler": "docs_to_md.py",
            "verified": False,
        },
    )
    md_dir = bdir / "md"
    backup_dir = pathlib.Path(ctx["backup_root"]) / uid
    backup = install_md(build, md_dir, backup_dir)
    if backup:
        backup = os.path.relpath(backup, root)
    row.update(
        status="done",
        md=L.md_rel(uid) + "/" + md_name,
        chars=chars,
        images=images,
        md_bytes=md_bytes,
        charset=charset,
        charset_fallback=fallback,
        backup=backup,
        error="；".join(warnings),
    )
    row["elapsed_ms"] = int((time.time() - t0) * 1000)
    return row


def journal(root, rows):
    path = pathlib.Path(root) / "工作区" / "批次" / "docs" / "journal.tsv"
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", encoding="utf-8") as fh:
        if new:
            fh.write("ts\tuid\tstatus\treason\n")
        for r in rows:
            fh.write("\t".join([now(), r["uid"], r["status"], r.get("reason") or ""]) + "\n")


def progress(job_dir, phase, done, total, current=""):
    write_json(
        job_path(job_dir, "progress.json"),
        {"phase": phase, "done": done, "total": total, "current": current, "updated_at": now()},
    )


def convert(root, job_dir):
    items = read_json(job_path(job_dir, "worklist.json"), [])
    if not isinstance(items, list):
        raise SystemExit("worklist.json 不是数组：{}".format(job_path(job_dir, "worklist.json")))
    todo = [it for it in items if not it.get("skipped")]
    skipped = [
        {
            "uid": it["uid"],
            "title": it.get("title") or "",
            "format": it.get("format") or "",
            "status": "skipped",
            "reason": "up-to-date",
            "md": "",
            "chars": 0,
            "images": 0,
            "md_bytes": 0,
            "charset": "",
            "charset_fallback": False,
            "elapsed_ms": 0,
            "backup": "",
            "error": "",
        }
        for it in items
        if it.get("skipped")
    ]
    need_venv = any((it.get("format") or "").lower() not in TEXT_FORMATS for it in todo)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    ctx = {"venv_error": "", "backup_root": str(pathlib.Path(root) / "工作区" / "批次" / f"覆盖-{stamp}")}
    if need_venv and not os.environ.get(WORKER_ENV, "").strip():
        ok, detail = docenv.ensure(root)
        if not ok:
            # 整本批次都别装：装不上说明环境坏了，直接让 gate 判失败
            ctx["venv_error"] = detail
            print(f"docs 环境不可用：{detail}", file=sys.stderr)
    rows = []
    total = len(todo)
    progress(job_dir, "convert", 0, total)
    for i, it in enumerate(todo, 1):
        progress(job_dir, "convert", i - 1, total, it["uid"])
        try:
            row = convert_book(root, it, ctx)
        except (OSError, SystemExit, sqlite3.Error) as exc:
            row = {
                "uid": it["uid"],
                "title": it.get("title") or "",
                "format": it.get("format") or "",
                "status": "failed",
                "reason": "convert-error",
                "md": "",
                "chars": 0,
                "images": 0,
                "md_bytes": 0,
                "charset": "",
                "charset_fallback": False,
                "elapsed_ms": 0,
                "backup": "",
                "error": str(exc) or type(exc).__name__,
            }
        rows.append(row)
        write_json(pathlib.Path(job_dir) / "books" / f"{it['uid']}.json", row)
        journal(root, [row])
        progress(job_dir, "convert", i, total, it["uid"])
        print(f"  {it['uid']} {row['status']} {row['reason'] or row['error'] or row['md']}")

    summary = {
        "total": len(rows) + len(skipped),
        "done": sum(1 for r in rows if r["status"] == "done"),
        "skipped": len(skipped),
        "failed": sum(1 for r in rows if r["status"] == "failed"),
    }
    doc = {
        "version": 1,
        "job_id": pathlib.Path(job_dir).resolve().name,
        "generated_at": now(),
        "scope": {
            "new": sum(1 for it in todo if it.get("reason") == "new"),
            "backfill": sum(1 for it in todo if it.get("reason") == "backfill"),
        },
        "summary": summary,
        "items": [*sorted(rows, key=lambda r: r["uid"]), *sorted(skipped, key=lambda r: r["uid"])],
    }
    write_json(job_path(job_dir, "report.json"), doc)
    progress(job_dir, "done", summary["done"] + summary["skipped"], summary["total"])
    print(
        f"完成 {summary['done']}/{summary['total']}，失败 {summary['failed']}，跳过 {summary['skipped']}"
        f" → {job_path(job_dir, 'report.json')}"
    )
    return 0


def worker(source, fmt, out_dir):
    """venv 内跑：markitdown 转一份，抽图，写 body.md，stdout 只留一行 JSON。"""
    payload = {"ok": False, "chars": 0, "images": 0, "images_dropped": 0, "warnings": [], "error": ""}
    try:
        from markitdown import MarkItDown

        out = pathlib.Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        text = MarkItDown().convert(source).text_content or ""
        text, images, dropped = extract_images(text, out / "images", payload["warnings"])
        with (out / "body.md").open("w", encoding="utf-8") as fh:
            fh.write(text)
        payload.update(ok=True, chars=len(text), images=images, images_dropped=dropped)
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("plan", "convert", "worker"))
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--job-dir", default="")
    ap.add_argument("--applied", default="")
    ap.add_argument("--only", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--format", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if a.cmd == "worker":
        if not (a.source and a.format and a.out):
            print("worker 要 --source/--format/--out", file=sys.stderr)
            return 2
        return worker(a.source, a.format, a.out)
    if not a.job_dir:
        print(f"{a.cmd} 要 --job-dir", file=sys.stderr)
        return 2
    pathlib.Path(a.job_dir).mkdir(parents=True, exist_ok=True)
    if a.cmd == "plan":
        return plan(a.root, a.job_dir, a.applied, a.only)
    return convert(a.root, a.job_dir)


if __name__ == "__main__":
    sys.exit(main())
