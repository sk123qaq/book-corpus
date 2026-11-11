#!/usr/bin/env python3
"""20 章深度路径验收：每章独立临时工作区生成，原子落地，汇总 token/耗时/费用。

用法：
    uv run --frozen python tools/depth_acceptance.py --root /home/colaqaq/书库 --workers 4

每章流程：临时目录复制 books.toml、book.json 与目标章节 md → 调
``python -m bookcorpus.reading.depth.cli generate`` → 把 ``工作区/阅读/<uid>/ch/<nn>/depth``
原子替换到真实根。失败换新临时目录重试一次；两次都失败保留原因，不写半成品。

长章（如 B0002/01）在 effort=max 下会超过客户端默认 32768 输出上限，runner 默认
通过 ``READING_MODEL_MAX_TOKENS=65536`` 放宽；可用 ``--max-tokens`` 覆盖。

``--no-deep`` 只用于冒烟，必须同时给 ``--allow-partial``，且报告会标记
``partial=true``，不能当作 20 章验收。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from bookcorpus.reading.model import merge_usage

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CHAPTERS = [("B0001", n) for n in range(1, 10)] + [("B0002", n) for n in range(1, 12)]
REQUIRED_ARTIFACTS = (
    "state.json",
    "chapter.html",
    "chapter_model.json",
    "ledger.json",
    "layers/skim.json",
    "layers/standard.json",
)


def _chapter_file(root, uid, n):
    matches = sorted((root / "books" / uid / "md").glob(f"{n:02d}_*.md"))
    if not matches:
        raise FileNotFoundError(f"找不到章节 {uid}/{n:02d} 的 md")
    return matches[0]


def _prepare_workspace(root, uid, n):
    tmp = Path(tempfile.mkdtemp(prefix=f"depth-{uid}-{n:02d}-"))
    shutil.copy(root / "books.toml", tmp / "books.toml")
    book_dir = tmp / "books" / uid
    (book_dir / "md").mkdir(parents=True)
    shutil.copy(root / "books" / uid / "book.json", book_dir / "book.json")
    source = _chapter_file(root, uid, n)
    shutil.copy(source, book_dir / "md" / source.name)
    return tmp


def _last_json(text):
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "status" in payload:
            return payload
    return None


def _depth_dir(tmp, uid, n):
    return tmp / "工作区" / "阅读" / uid / "ch" / f"{n:02d}" / "depth"


def _read_state(depth):
    try:
        state = json.loads((depth / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if state.get("status") != "ready" or not (depth / "chapter.html").is_file():
        return None
    return state


def _missing_artifacts(depth, args):
    names = list(REQUIRED_ARTIFACTS)
    if not args.no_deep:
        names.append("layers/deep.json")
    missing = []
    for name in names:
        path = depth / name
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(name)
    return missing


def _failed_layer(text):
    markers = (
        ("skim", ("速览",)),
        ("deep", ("深读",)),
        ("model", ("模型", "max_tokens", "守恒")),
    )
    for layer, words in markers:
        if any(word in (text or "") for word in words):
            return layer
    return None


def _install(src, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:8]
    stage = dest.parent / f".depth-stage-{token}"
    shutil.copytree(src, stage)
    backup = dest.parent / f".depth-old-{token}"
    try:
        if dest.exists():
            dest.rename(backup)
        stage.rename(dest)
    except BaseException:
        if not dest.exists() and backup.exists():
            backup.rename(dest)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)


def _run_once(root, uid, n, args):
    tmp = _prepare_workspace(root, uid, n)
    started = time.monotonic()
    try:
        cmd = [
            sys.executable,
            "-m",
            "bookcorpus.reading.depth.cli",
            "generate",
            "--root",
            str(tmp),
            "--uid",
            uid,
            "--n",
            str(n),
            "--model",
            args.model,
            "--effort",
            args.effort,
        ]
        if args.no_deep:
            cmd.append("--no-deep")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["READING_MODEL_TIMEOUT"] = str(args.model_timeout)
        if args.max_tokens:
            env["READING_MODEL_MAX_TOKENS"] = str(args.max_tokens)
        completed = subprocess.run(
            cmd,
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=args.chapter_timeout,
        )
        elapsed = round(time.monotonic() - started, 1)
        payload = _last_json(completed.stdout)
        depth = _depth_dir(tmp, uid, n)
        state = _read_state(depth)
        missing = _missing_artifacts(depth, args)
        error = (completed.stderr or "").strip()[-4000:]
        if not error and completed.returncode != 0:
            error = (completed.stdout or "").strip()[-4000:]
        if completed.returncode != 0 or payload is None or payload.get("status") != "ready" or state is None or missing:
            if missing and not error:
                error = "深度产物不完整：" + "、".join(missing)
            return {
                "ok": False,
                "elapsed_s": elapsed,
                "returncode": completed.returncode,
                "error": error,
                "failed_layer": _failed_layer(error),
                "missing_artifacts": missing,
                "payload": payload,
                "usage": (payload or {}).get("usage") or {},
                "usd": (payload or {}).get("usd"),
            }
        _install(depth, root / "工作区" / "阅读" / uid / "ch" / f"{n:02d}" / "depth")
        return {
            "ok": True,
            "elapsed_s": elapsed,
            "returncode": completed.returncode,
            "payload": payload,
            "missing_artifacts": [],
            "usage": payload.get("usage") or {},
            "usd": payload.get("usd"),
            "stderr": (completed.stderr or "").strip()[-2000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "elapsed_s": round(time.monotonic() - started, 1),
            "returncode": None,
            "error": f"生成超时（{exc.timeout}s）",
            "failed_layer": "model",
            "missing_artifacts": [],
        }
    finally:
        if not args.keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)


def _run_chapter(root, uid, n, args):
    attempts = []
    for _attempt in range(args.retry + 1):
        result = _run_once(root, uid, n, args)
        attempts.append(result)
        if result["ok"]:
            break
    last = attempts[-1]
    usage = {}
    usd = 0.0
    usage_missing = 0
    for attempt in attempts:
        attempt_usage = attempt.get("usage") or {}
        if attempt_usage:
            usage = merge_usage(usage, attempt_usage)
        elif not attempt["ok"]:
            usage_missing += 1
        if isinstance(attempt.get("usd"), (int, float)):
            usd += float(attempt["usd"])
    return {
        "uid": uid,
        "n": n,
        "chapter": f"{uid}/{n:02d}",
        "ok": last["ok"],
        "attempts": len(attempts),
        "attempts_detail": attempts,
        "elapsed_s": round(sum(item["elapsed_s"] for item in attempts), 1),
        "usage": usage,
        "usd": round(usd, 6),
        "usage_missing_attempts": usage_missing,
        "payload": last.get("payload"),
        "error": last.get("error"),
        "failed_layer": last.get("failed_layer") if not last["ok"] else None,
        "missing_artifacts": last.get("missing_artifacts") or [],
        "stderr": last.get("stderr"),
    }


def _parse_chapters(value):
    chapters = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        uid, _, number = item.partition("/")
        if not uid or not number.isdigit():
            raise ValueError(f"章节格式应为 UID/NN：{item}")
        chapters.append((uid, int(number)))
    return chapters


def _atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex[:8]}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _write_report(out_dir, report):
    out_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(out_dir / "report.json", json.dumps(report, ensure_ascii=False, indent=1))
    rows = []
    for chapter in report["chapters"]:
        usage = chapter.get("usage") or {}
        status = "ready" if chapter["ok"] else "FAILED"
        detail = (chapter.get("error") or "").splitlines()[-1][:160] if chapter.get("error") else ""
        if chapter.get("missing_artifacts"):
            detail = "缺产物：" + "、".join(chapter["missing_artifacts"])
        rows.append(
            f"| {chapter['chapter']} | {status} | {chapter['attempts']} | {chapter['elapsed_s']:.1f}s | "
            f"{usage.get('total_tokens', 0)} | {chapter.get('usd') or 0} | "
            f"{detail} |"
        )
    markdown = "\n".join(
        [
            "# 20 章深度路径验收",
            "",
            f"- 根：`{report['root']}`",
            f"- 模型：`{report['model']}` / effort `{report['effort']}` / 并行 {report['workers']}",
            f"- 生成时间：{report['generated_at']}，总耗时 {report['elapsed_s']:.1f}s",
            f"- 报告来源：{report.get('report_source', 'runner')}",
            f"- 结果：{report['ok']}/{report['total']} 章就绪"
            + (f"，{report['failed']} 章失败" if report["failed"] else ""),
            f"- 合计 tokens：{report['usage'].get('total_tokens', 0)}，"
            f"费用（provider list_cost）：${report['usd']:.4f}",
            *(["- **部分档位运行（--allow-partial），不能作为验收**"] if report.get("partial") else []),
            *[f"- 备注：{note}" for note in report.get("notes") or []],
            "",
            "| 章 | 状态 | 尝试 | 耗时 | tokens | USD | 说明 |",
            "|---|---|---|---|---|---|---|",
            *rows,
            "",
        ]
    )
    _atomic_write(out_dir / "report.md", markdown)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="书库根（含 books.toml）")
    parser.add_argument("--chapters", help="逗号分隔的 UID/NN；默认 20 章验收清单")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--model", default="deepseek-v4.1-flash")
    parser.add_argument("--effort", default="max")
    parser.add_argument("--retry", type=int, default=1)
    parser.add_argument("--chapter-timeout", type=int, default=2700)
    parser.add_argument("--model-timeout", type=int, default=600)
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=65536,
        help="单次模型输出上限；默认 65536，长章在 effort=max 下需要",
    )
    parser.add_argument("--out", default="reports/深度验收")
    parser.add_argument("--note", action="append", default=[], help="写入报告的备注，可重复")
    parser.add_argument("--only", help="只跑这些章（用于补跑失败项），逗号分隔 UID/NN")
    parser.add_argument("--resume", action="store_true", help="跳过真实根里已 ready 的章")
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument("--no-deep", action="store_true", help="只用于冒烟：跳过深读层")
    parser.add_argument("--allow-partial", action="store_true", help="允许冒烟用的 --no-*；报告会标记 partial")
    args = parser.parse_args(argv)
    partial = bool(args.no_deep)
    if partial and not args.allow_partial:
        parser.error("--no-deep 只用于冒烟；验收请加 --allow-partial")

    root = Path(args.root).expanduser().resolve()
    if not (root / "books.toml").is_file():
        parser.error(f"{root} 不是书库根：缺 books.toml")
    chapters = (
        _parse_chapters(args.only)
        if args.only
        else (_parse_chapters(args.chapters) if args.chapters else DEFAULT_CHAPTERS)
    )
    if args.resume:
        chapters = [
            (uid, n)
            for uid, n in chapters
            if _read_state(root / "工作区" / "阅读" / uid / "ch" / f"{n:02d}" / "depth") is None
        ]
    if not chapters:
        print(json.dumps({"status": "ok", "total": 0, "note": "没有待生成的章"}, ensure_ascii=False))
        return 1 if partial else 0

    started = time.monotonic()
    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(_run_chapter, root, uid, n, args): (uid, n) for uid, n in chapters}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            flag = "ok" if result["ok"] else "FAIL"
            print(f"[{flag}] {result['chapter']} {result['elapsed_s']:.1f}s", flush=True)

    order = {chapter: index for index, chapter in enumerate(chapters)}
    results.sort(key=lambda item: order[(item["uid"], item["n"])])
    usage = {}
    usd = 0.0
    for result in results:
        usage = merge_usage(usage, result.get("usage") or {})
        if isinstance(result.get("usd"), (int, float)):
            usd += float(result["usd"])
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "report_source": "runner",
        "root": str(root),
        "model": args.model,
        "effort": args.effort,
        "workers": args.workers,
        "total": len(results),
        "ok": sum(1 for result in results if result["ok"]),
        "failed": sum(1 for result in results if not result["ok"]),
        "elapsed_s": round(time.monotonic() - started, 1),
        "usage": usage,
        "usd": round(usd, 6),
        "partial": partial,
        "notes": list(args.note),
        "chapters": results,
    }
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = REPO / out_dir
    _write_report(out_dir, report)
    print(
        json.dumps(
            {"status": "ok" if report["failed"] == 0 else "failed", "report": str(out_dir)},
            ensure_ascii=False,
        )
    )
    return 0 if report["failed"] == 0 and not partial else 1


if __name__ == "__main__":
    sys.exit(main())
