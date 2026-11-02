#!/usr/bin/env python3
"""批量把 source 是 ebook 的书拆成 md（方案 §7 的转换接口）。

每本书一条流水线：转 EPUB（mobi/azw3/azw 先过 Calibre）→ 拆章 → verify → audit
→ 原子 rename 进 库/<path>/md/。产物先落 工作区/批次/<批次>/out/<uid>/，只有
verify 与 audit 都过才进库，没过留在暂存区待人看。

幂等：目标 md 目录已存在就跳过；暂存区已有产物就直接复用，不重跑 verify/audit
（要重拆用 --force，要只重跑检查用 --recheck）。
范围账本 worklist.tsv 只在批次目录里还没有它时生成一次，重复运行不重写；账本一旦
存在就以它为准，--uids 也只在新批次里起作用。

按 uid 覆盖选项放 工作区/批次/<批次>/overrides/<uid>.json，内容是额外命令行参数
数组，例如 ["--keep", "^序言的结构"]。

用法:
  python3 split_md_batch.py --batch md拆分-2026-10-18 --workers 6
  python3 split_md_batch.py --batch md拆分-2026-10-18 --only B0121,B0261
  python3 split_md_batch.py --batch md拆分-2026-10-18 --no-land   # 只做暂存，不进库
  python3 split_md_batch.py --batch md拆分-2026-10-18 --no-agent-fix  # 失败时不叫 pi 修
  python3 split_md_batch.py --batch md拆分-2026-10-18 --only B0121 --recheck
  python3 split_md_batch.py --batch split-ops-20261024T2200 --uids B0001,B0002
  python3 split_md_batch.py --batch md拆分-2026-10-18 --report --report-json
"""
import argparse
import collections
import concurrent.futures as futures
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L  # noqa: E402

SKILL = os.path.join(L.skill_dir("ebook-to-md"), "scripts")
SKILL_DOC = os.path.join(os.path.dirname(SKILL), "SKILL.md")
CONVERT = os.path.join(SKILL, "ebook_to_md.py")
FIX_MODEL = os.environ.get("MD_FIX_MODEL", "opencode-go/deepseek-v4.1-flash")
PI = os.environ.get("PI_BIN") or shutil.which("pi") or "pi"
FIX_PROMPT = """修一本书的拆章产物，目标：verify_output.py 与 audit_volume.py 都过。

书：{uid}《{title}》，源文件 {src}
暂存目录：{target}（md 就在这里）
批次：{batch}

---- verify ----
{v}
---- audit ----
{a}

允许：调整章节映射与章名，把原书漏收的正文补进来，改 {batch}/overrides/{uid}.json。
禁止：改 库/ 下任何文件；改 工作区/脚本/；用重复段落或编造文本凑字数；删正文来抬比例。
工具说明在 {skill_doc} 与 scripts/。
把改动写进 {report}，只回一句结论。"""
VERIFY = os.path.join(SKILL, "verify_output.py")
AUDIT = os.path.join(SKILL, "audit_volume.py")
CALIBRE = "ebook-convert"
FORMATS = ("epub", "mobi", "azw3", "azw")
WORKLIST_COLS = ["uid", "format", "sha256", "path", "title", "src"]
# 每本书的终态词表。True 表示这件事不用人再管；页面词表由 check_ops 对齐这张表。
STATUS = {
    "landed": True, "replaced": True, "accepted": True,
    "skipped-has-md": True, "staged-green": True,
    "staged": False, "staged-review": False, "no-stage": False,
    "split-failed": False, "convert-failed": False, "error": False,
}
DONE_STATUS = tuple(name for name, done in STATUS.items() if done)
READING_KEYS = ("verify_rc", "audit_rc", "audit_ratio", "chapters", "chars",
                "images", "problems")
TIMEOUT = 900
COUNT_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]|[A-Za-z0-9]")


def tool_version(path=CONVERT):
    with open(path, "rb") as fh:
        return "sha256:" + hashlib.sha256(fh.read()).hexdigest()[:12]


def keep_prev(path, stamp):
    """旧日志改名留档，同一个文件只留最近一份，避免重跑无限堆。"""
    if not os.path.exists(path):
        return
    folder, base = os.path.split(path)
    for name in os.listdir(folder):
        if name.startswith(base + ".prev-"):
            os.remove(os.path.join(folder, name))
    os.rename(path, "%s.prev-%s" % (path, stamp))


def now():
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def read_worklist(path):
    with open(path, encoding="utf-8") as fh:
        cols = fh.readline().rstrip("\n").split("\t")
        return [dict(zip(cols, line.rstrip("\n").split("\t"))) for line in fh]


def write_worklist(path, rows):
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write("\t".join(WORKLIST_COLS) + "\n")
        for r in rows:
            fh.write("\t".join(str(r[c]) for c in WORKLIST_COLS) + "\n")
    os.replace(path + ".tmp", path)


def pending_ebooks(con):
    rows = con.execute(
        """SELECT b.uid AS uid, s.format AS format, s.sha256 AS sha256,
                  b.path AS path, b.title AS title, s.file AS src
           FROM book b JOIN source s ON s.uid=b.uid AND s.primary_src=1
           WHERE s.format IN (?,?,?,?) AND b.uid IN (SELECT uid FROM v_pending_md)
             AND b.path_state='active'
           ORDER BY CAST(SUBSTR(b.uid,2) AS INTEGER)""", FORMATS).fetchall()
    return [dict(r) for r in rows]


def build_worklist(con, batch_path, uids=None):
    """批次范围账本。文件已存在就直接读它，绝不重写；--uids 只给新批次定范围。

    uids 为空时取库里所有待拆的电子书。给了 uids 就按给的顺序写这些书，包括已经
    有 md、不在 v_pending_md 里的书（受控重拆）。给的 uid 找不到源文件时抛
    ValueError，账本不落盘，免得半条范围先写死。
    """
    path = os.path.join(batch_path, "worklist.tsv")
    if os.path.exists(path):
        return read_worklist(path)
    if uids:
        rows = lookup_rows(con, list(dict.fromkeys(uids)))
        missing = [u for u in uids if u not in {r["uid"] for r in rows}]
        if missing:
            raise ValueError("找不到源文件: " + ", ".join(missing))
    else:
        rows = pending_ebooks(con)
    write_worklist(path, rows)
    return rows


def log_tail(out, n=25):
    lines = out.strip().splitlines()
    return "\n".join(lines[-n:])


def sh(cmd, cwd):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError as exc:
        return 127, str(exc)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def fix_provenance(folder, temp_name, original_name):
    """把 Calibre 中间产物的名字换回原始文件名，只动 YAML source 与 README 来源行。"""
    changed = 0
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".md"):
            continue
        p = os.path.join(folder, name)
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        new = re.sub(r'(?m)^(source:\s*")' + re.escape(temp_name) + r'"',
                     lambda m: m.group(1) + original_name + '"', text)
        new = new.replace("- 来源：`%s`" % temp_name, "- 来源：`%s`" % original_name)
        if new != text:
            with open(p + ".tmp", "w", encoding="utf-8") as fh:
                fh.write(new)
            os.replace(p + ".tmp", p)
            changed += 1
    return changed


def parse_verify(out):
    m = re.search(r"markdown\s*:\s*(\d+) chapters", out)
    c = re.search(r"characters\s*:\s*([\d,]+)", out)
    i = re.search(r"images used\s*:\s*(\d+) \(on disk: (\d+)\)", out)
    p = re.search(r"problems\s*:\s*(\d+)", out)
    return {
        "chapters": int(m.group(1)) if m else None,
        "chars": int(c.group(1).replace(",", "")) if c else None,
        "images": int(i.group(1)) if i else None,
        "problems": int(p.group(1)) if p else None,
    }


def parse_audit(out):
    m = re.search(r"([\d,]+)\s*/\s*([\d,]+)\s+([\d.]+)%\s+\d+\s+md\b", out)
    if not m:
        return None
    return {"raw": int(m.group(2).replace(",", "")), "ratio": float(m.group(3)) / 100.0}


def lookup_rows(con, uids):
    """按 uid 直接取行，用于重跑已入库的书（不在待拆清单里）。"""
    rows = []
    for uid in uids:
        r = con.execute(
            """SELECT b.uid AS uid, s.format AS format, s.sha256 AS sha256,
                      b.path AS path, b.title AS title, s.file AS src
               FROM book b JOIN source s ON s.uid=b.uid AND s.primary_src=1
               WHERE b.uid=? AND s.format IN (?,?,?,?)""", (uid, *FORMATS)).fetchone()
        if r:
            rows.append(dict(r))
    return rows


def land(ctx, stage, dest, result):
    """把过检的暂存目录搬进库。已存在的旧版本改名留在批次区，不删。"""
    if os.path.isdir(dest):
        old = "%s.prev-%s" % (stage, ctx["stamp"])
        os.rename(dest, old)
        result["previous"] = os.path.relpath(old, L.ROOT)
    os.rename(stage, dest)
    result["status"] = "replaced" if result.get("previous") else "landed"
    return result


def agent_fix(ctx, row, target, vout, aout):
    report = os.path.join(ctx["logs"], row["uid"] + ".fix.md")
    prompt = FIX_PROMPT.format(uid=row["uid"], title=row["title"], src=row.get("src", ""),
                               target=os.path.relpath(target, L.ROOT),
                               batch=ctx["batch_name"], v=vout[-1500:], a=aout[-800:],
                               report=os.path.relpath(report, L.ROOT),
                               skill_doc=SKILL_DOC)
    t0 = time.time()
    try:
        r = subprocess.run([PI, "-p", "--approve", "--model", ctx["fix_model"],
                            "--session-dir", ctx["fix_sessions"], prompt],
                           cwd=L.ROOT, capture_output=True, text=True,
                           timeout=ctx["fix_timeout"])
        rc, out = r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired as exc:
        rc, out = 124, str(exc)
    with open(report, "a", encoding="utf-8") as fh:
        fh.write("\n## agent-fix %s rc=%d\n%s\n" % (now(), rc, out[-4000:]))
    return {"rc": rc, "ms": int((time.time() - t0) * 1000),
            "report": os.path.relpath(report, L.ROOT), "tail": out.strip()[-300:]}


def previous_readings(stage, log_path):
    """早退时沿用上一次的读数：日志还在，manifest 补日志里没有的字段。"""
    prev = {}
    if os.path.isfile(log_path):
        try:
            with open(log_path, encoding="utf-8") as fh:
                prev = json.load(fh) or {}
        except (OSError, ValueError):
            prev = {}
    man = L.read_md_manifest(stage)
    out = {}
    for key in READING_KEYS:
        for src in (prev, man):
            if isinstance(src, dict) and src.get(key) is not None:
                out[key] = src[key]
                break
    return out


def recheck_audit_source(row, ctx, uid):
    """复核要拿原文比文本量；非 epub 找转换时留下的中间 EPUB，找不到就再转一次。

    返回 (路径, 命令留痕)。转不出来时路径为 None，复核就没有文本量这一关。
    """
    if row["format"] == "epub":
        return os.path.join(L.ROOT, row["src"]), ""
    for cand in (os.path.join(ctx["batch"], "prepared", uid + ".epub"),
                 os.path.join(ctx["tmp"], uid, "book.epub")):
        if os.path.isfile(cand):
            return cand, ""
    out = os.path.join(ctx["tmp"], uid, "book.epub")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    line = "ebook-convert %s book.epub" % row["src"]
    try:
        rc, _ = sh([CALIBRE, os.path.join(L.ROOT, row["src"]), out], L.ROOT)
    except OSError:
        rc = -1
    if rc != 0 or not os.path.isfile(out):
        return None, line
    return out, line


def recheck(row, ctx, result):
    """只复核暂存区已有的产物：重跑 verify 与 audit，重写 manifest，过了就进库。"""
    uid = row["uid"]
    stage = os.path.join(ctx["out"], uid)
    dest = os.path.join(L.ROOT, "库", row["path"], "md")
    if not os.path.isdir(stage):
        result.update(status="no-stage", note="暂存区没有产物，先重拆")
        return result
    old = L.read_md_manifest(stage)
    audited, prep = recheck_audit_source(row, ctx, uid)
    rc, vout = sh([sys.executable, VERIFY, stage], L.ROOT)
    v = parse_verify(vout)
    result.update(verify_rc=rc, **v)
    commands = ([prep] if prep else []) + ["verify_output.py out"]
    rc2, aout, a = None, "", None
    if audited:
        rc2, aout = sh([sys.executable, AUDIT, audited, "-o", stage], L.ROOT)
        commands.append("audit_volume.py %s -o out" % os.path.relpath(audited, L.ROOT))
        a = parse_audit(aout)
        result["audit_rc"] = rc2
        if a:
            result["audit_ratio"] = a["ratio"]
            result["raw_chars"] = a["raw"]
    else:
        # 原文转不出来就没法量文本量：没有内容检查这一关，不许进库
        result["audit_rc"] = None
        result["audit_ratio"] = old.get("audit_ratio")
        if old.get("audit_chars") is not None:
            result["raw_chars"] = old["audit_chars"]
        result["note"] = "原文转不出中间 EPUB，文本量没法复核"

    chapters, nbytes = L.count_md_dir(stage)
    manifest = {
        "uid": old.get("uid") or uid,
        "method": old.get("method") or "ebook-to-md",
        "tool_version": old.get("tool_version") or ctx["tool_version"],
        "tool": old.get("tool") or os.path.basename(CONVERT),
        "batch": old.get("batch") or ctx["batch_name"],
        "source": old.get("source") or row["src"],
        "source_sha256": old.get("source_sha256") or row["sha256"],
        "source_format": old.get("source_format") or row["format"],
        "accepted": old.get("accepted") or "",
        "chapters": chapters, "bytes": nbytes,
        "images": v.get("images"), "chars": v.get("chars"),
        "audit_chars": a["raw"] if a else old.get("audit_chars"),
        "audit_ratio": a["ratio"] if a else old.get("audit_ratio"),
        "generated_at": now(), "verified": rc == 0 and rc2 == 0,
    }
    with open(os.path.join(stage, ".manifest.json") + ".tmp", "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(os.path.join(stage, ".manifest.json") + ".tmp",
               os.path.join(stage, ".manifest.json"))
    commands.append("write .manifest.json")

    keep_prev(os.path.join(ctx["logs"], uid + ".log"), ctx["stamp"])
    with open(os.path.join(ctx["logs"], uid + ".log"), "w", encoding="utf-8") as fh:
        fh.write("\n".join("$ " + c for c in commands) + "\n\n---- verify ----\n" + vout
                 + "\n---- audit ----\n" + aout)

    if manifest["verified"]:
        result["status"] = "staged-green"
        if ctx["land"]:
            land(ctx, stage, dest, result)
    else:
        result["status"] = "staged-review"
        note = "复核 verify rc=%s audit rc=%s" % (
            rc, "未跑" if rc2 is None else rc2)
        if not audited:
            note += "；" + result["note"]
        result["note"] = note
    return result


def run_one(row, ctx):
    uid = row["uid"]
    dest = os.path.join(L.ROOT, "库", row["path"], "md")
    stage = os.path.join(ctx["out"], uid)
    work = os.path.join(ctx["tmp"], uid)
    result = {"uid": uid, "title": row["title"], "format": row["format"],
              "dest": os.path.relpath(dest, L.ROOT), "at": now()}
    if os.path.isdir(dest) and not ctx["replace"]:
        result["status"] = "skipped-has-md"
        return result
    if ctx["recheck"]:
        return recheck(row, ctx, result)
    if os.path.isdir(stage) and not ctx["force"]:
        man = L.read_md_manifest(stage)
        if ctx["land"] and man.get("verified") and (not os.path.isdir(dest) or ctx["replace"]):
            land(ctx, stage, dest, result)
            result["note"] = "暂存区已有过检产物，直接进库"
        else:
            result.update(status="staged", note="暂存区已有产物，本次没重拆；读数是上次留下的",
                          **previous_readings(stage,
                                              os.path.join(ctx["logs"], uid + ".json")))
        return result

    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work)
    src = os.path.join(L.ROOT, row["src"])
    audited = src
    if row["format"] != "epub":
        audited = os.path.join(work, "book.epub")
        prepared = os.path.join(ctx["batch"], "prepared", uid + ".epub")
        if os.path.isfile(prepared):
            # 预先备好的中间 EPUB（如 MOBI6 缺 guide 要用修补过的 calibre）：不重跑
            audited = prepared
            result["note"] = "用提前备好的中间 EPUB"
        else:
            rc, out = sh([CALIBRE, src, audited], L.ROOT)
            keep_prev(os.path.join(ctx["logs"], uid + ".log"), ctx["stamp"])
            with open(os.path.join(ctx["logs"], uid + ".log"), "w", encoding="utf-8") as fh:
                fh.write("$ ebook-convert %s book.epub\n%s\n" % (row["src"], out))
            if rc != 0 or not os.path.isfile(audited):
                result.update(status="convert-failed", note="calibre rc=%d" % rc,
                              tail=log_tail(out))
                return result

    target = os.path.join(work, "out")
    override = os.path.join(ctx["batch"], "overrides", uid + ".json")
    tool, extra, accept = CONVERT, [], None
    if os.path.isfile(override):
        spec = json.load(open(override, encoding="utf-8"))
        if isinstance(spec, dict):
            tool = os.path.join(SKILL, spec.get("tool") or os.path.basename(CONVERT))
            extra = spec.get("args") or []
            accept = spec.get("accept") or None
        else:
            extra = spec
        result["override"] = spec
    cmd = [sys.executable, tool, audited, "-o", target, "--title", row["title"]]
    cmd += [str(x) for x in extra]
    commands = []
    rc, out = sh(cmd, L.ROOT)
    commands.append("%s %s -o out --title %r %s"
                    % (os.path.basename(tool), os.path.relpath(audited, L.ROOT),
                       row["title"], " ".join(str(x) for x in extra)))
    if rc != 0 or not os.path.isdir(target):
        result.update(status="split-failed", note="ebook_to_md rc=%d" % rc, tail=log_tail(out))
        return result

    # override 指定了别的脚本时，tool_version 跟着实际跑的脚本走
    tool_sha = tool_version(tool) if os.path.isfile(tool) else ctx["tool_version"]
    if audited != src:
        result["provenance_fixed"] = fix_provenance(
            target, os.path.basename(audited), os.path.basename(src))

    rc, vout = sh([sys.executable, VERIFY, target], L.ROOT)
    commands.append("verify_output.py out")
    v = parse_verify(vout)
    result.update(verify_rc=rc, **v)
    rc2, aout = sh([sys.executable, AUDIT, audited, "-o", target], L.ROOT)
    commands.append("audit_volume.py %s -o out" % os.path.relpath(audited, L.ROOT))
    a = parse_audit(aout)
    result["audit_rc"] = rc2
    if a:
        result["audit_ratio"] = a["ratio"]
        result["raw_chars"] = a["raw"]

    if (rc or rc2) and ctx["fix"] and not accept:
        for _ in range(ctx["fix_cap"]):
            if not (rc or rc2):
                break
            with ctx["fix_lock"]:
                if ctx["fix_total"] and ctx["fix_used"][0] >= ctx["fix_total"]:
                    break
                ctx["fix_used"][0] += 1
            result.setdefault("fix", []).append(agent_fix(ctx, row, target, vout, aout))
            rc, vout = sh([sys.executable, VERIFY, target], L.ROOT)
            rc2, aout = sh([sys.executable, AUDIT, audited, "-o", target], L.ROOT)
            v = parse_verify(vout)
            a = parse_audit(aout)
            result.update(verify_rc=rc, **v)
            result["audit_rc"] = rc2
            if a:
                result["audit_ratio"] = a["ratio"]
                result["raw_chars"] = a["raw"]

    chapters, nbytes = L.count_md_dir(target)
    manifest = {
        "uid": uid, "method": "ebook-to-md", "tool_version": tool_sha,
        "tool": os.path.basename(tool), "batch": ctx["batch_name"],
        "source": row["src"], "source_sha256": row["sha256"],
        "source_format": row["format"], "chapters": chapters, "bytes": nbytes,
        "images": v.get("images"), "chars": v.get("chars"),
        "audit_chars": (a or {}).get("raw"), "audit_ratio": (a or {}).get("ratio"),
        "generated_at": now(), "verified": rc == 0 and rc2 == 0,
        "accepted": accept or "",
    }
    with open(os.path.join(target, ".manifest.json") + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(os.path.join(target, ".manifest.json") + ".tmp",
               os.path.join(target, ".manifest.json"))
    commands.append("write .manifest.json")

    keep_prev(os.path.join(ctx["logs"], uid + ".log"), ctx["stamp"])
    with open(os.path.join(ctx["logs"], uid + ".log"), "w", encoding="utf-8") as fh:
        fh.write("\n".join("$ " + c for c in commands) + "\n\n---- verify ----\n" + vout
                 + "\n---- audit ----\n" + aout)
    os.makedirs(ctx["out"], exist_ok=True)
    if os.path.isdir(stage):
        shutil.rmtree(stage)  # --force 重拆时换掉旧暂存产物
    os.replace(target, stage)

    if rc == 0 and rc2 == 0:
        result["status"] = "staged-green"
        if ctx["land"]:
            land(ctx, stage, dest, result)
    elif accept and os.path.isdir(stage):
        # 已查清原因的例外：报警照记进 manifest 与结果，产物进库
        result["status"] = "staged-green"
        result["note"] = "已受理例外：%s" % accept
        if ctx["land"]:
            land(ctx, stage, dest, result)
            result["status"] = "accepted"
    else:
        result["status"] = "staged-review"
        result["note"] = "verify rc=%d, audit rc=%d" % (rc, rc2)
    return result


def write_result(ctx, result):
    assert result["status"] in STATUS, result["status"]
    p = os.path.join(ctx["logs"], result["uid"] + ".json")
    keep_prev(p, ctx["stamp"])
    with open(p + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(p + ".tmp", p)


def with_accept(ctx, results):
    """已受理的例外以 md/.manifest.json 为准，logs 里的旧结果可能没这个字段。"""
    for r in results:
        if r.get("accepted") is not None:
            continue
        man = L.read_md_manifest(os.path.join(L.ROOT, r.get("dest", "")))
        if man.get("accepted"):
            r["accepted"] = man["accepted"]
    return results


def report(ctx, results):
    counts = collections.Counter(r["status"] for r in results)
    print("\n状态统计")
    for k, v in sorted(counts.items()):
        print("  %-18s %d" % (k, v))
    bad = [r for r in results if r["status"] not in DONE_STATUS]
    if bad:
        print("\n待查 %d 本" % len(bad))
        for r in bad:
            print("  %-6s %-40.40s %s" % (r["uid"], r["title"], r.get("note", "")))
    low = [r for r in results if r.get("audit_ratio") is not None
           and (r.get("raw_chars") or 0) >= 100
           and (r["audit_ratio"] < 0.88 or r["audit_ratio"] > 1.15)]
    accepted = [r for r in low if r.get("accepted")]
    low = [r for r in low if not r.get("accepted")]
    if accepted:
        print("\n已受理的文本量例外 %d 本（原因记在 md/.manifest.json 的 accepted 字段）" % len(accepted))
        for r in accepted:
            print("  %-6s %-40.40s %.1f%%" % (r["uid"], r["title"], r["audit_ratio"] * 100))
    if low:
        print("\n未受理的文本量出界 %d 本" % len(low))
        for r in low:
            print("  %-6s %-40.40s %.1f%%" % (r["uid"], r["title"], r["audit_ratio"] * 100))


def book_summary(r):
    d = {k: r[k] for k in ("uid", "title", "status", "dest", "previous", "note",
                           "verify_rc", "audit_rc", "audit_ratio", "chapters")
         if k in r}
    d["fix"] = len(r.get("fix") or [])
    return d


def summary(ctx, results):
    """页面读的结构化统计。成功与否看每本的 status，不看退出码。"""
    counts = collections.Counter(r["status"] for r in results)
    return {
        "batch": ctx["batch_name"],
        "status_counts": dict(sorted(counts.items())),
        "books": sorted((book_summary(r) for r in results),
                        key=lambda b: b.get("uid", "")),
        "pending_review": [r["uid"] for r in results if r["status"] not in DONE_STATUS],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--batch", required=True, help="批次目录名，在 工作区/批次/ 下")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only", help="只跑这些 uid，逗号分隔")
    ap.add_argument("--uids", help="新批次的 uid 清单，逗号分隔；只在 worklist.tsv 不存在时写入")
    ap.add_argument("--limit", type=int, help="最多跑几本（调试用）")
    ap.add_argument("--no-land", action="store_true", dest="noland",
                    help="只做暂存，不进 库/")
    ap.add_argument("--force", action="store_true", help="忽略暂存区已有产物，重拆")
    ap.add_argument("--recheck", action="store_true",
                    help="不重拆，只复核暂存区已有产物")
    ap.add_argument("--replace", action="store_true",
                    help="目标 md 已存在时也重拆并替换，旧版本改名留在批次区")
    ap.add_argument("--report", action="store_true", help="只读 logs/ 出统计，不干活")
    ap.add_argument("--report-json", action="store_true", dest="report_json",
                    help="stdout 最后一行给 JSON，含 batch、status_counts、books、pending_review")
    ap.add_argument("--no-agent-fix", action="store_true", help="检查不过时不叫 pi 修复")
    ap.add_argument("--agent-fix-cap", type=int, default=2, help="每本最多修几次")
    ap.add_argument("--agent-fix-total", type=int, default=0, help="整批最多修几次，0 表示不限")
    ap.add_argument("--agent-fix-timeout", type=int, default=600)
    ap.add_argument("--agent-model", default=FIX_MODEL)
    a = ap.parse_args()
    if not shutil.which(CALIBRE):
        print("提示：缺 %s，mobi/azw3/azw 会转换失败；epub 不受影响。装 Calibre 后重跑。"
              % CALIBRE, file=sys.stderr)
    if a.recheck and a.force:
        ap.error("--recheck 与 --force 不能同时用")

    batch_path = os.path.join(L.WORK, "批次", a.batch)
    ctx = {"batch": batch_path, "batch_name": a.batch, "out": os.path.join(batch_path, "out"),
           "tmp": os.path.join(batch_path, "tmp"), "logs": os.path.join(batch_path, "logs"),
           "land": not a.noland, "force": a.force, "replace": a.replace,
           "recheck": a.recheck,
           "fix": not a.no_agent_fix, "fix_cap": a.agent_fix_cap,
           "fix_total": a.agent_fix_total, "fix_timeout": a.agent_fix_timeout,
           "fix_model": a.agent_model, "fix_used": [0], "fix_lock": threading.Lock(),
           "fix_sessions": os.path.join(batch_path, "fix-sessions"),
           "stamp": now().replace(":", "").replace("+", "-"), "tool_version": tool_version()}

    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(L.TABLES, "library.db"), uri=True)
    con.row_factory = sqlite3.Row

    if a.report:
        results = []
        log_files = sorted(os.listdir(ctx["logs"])) if os.path.isdir(ctx["logs"]) else []
        for uid in log_files:
            if uid.endswith(".json"):
                results.append(json.load(open(os.path.join(ctx["logs"], uid), encoding="utf-8")))
        report(ctx, with_accept(ctx, results))
        if a.report_json:
            print(json.dumps(summary(ctx, results), ensure_ascii=False))
        return 0

    wanted = None
    if a.uids is not None:
        wanted = [u.strip() for u in a.uids.split(",") if u.strip()]
        if not wanted:
            sys.exit("--uids 为空")

    os.makedirs(ctx["logs"], exist_ok=True)
    os.makedirs(ctx["out"], exist_ok=True)
    os.makedirs(ctx["tmp"], exist_ok=True)
    try:
        rows = build_worklist(con, batch_path, wanted)
    except ValueError as exc:
        sys.exit(str(exc))
    known = {r["uid"]: r for r in rows}

    if a.only:
        wanted = [u.strip() for u in a.only.split(",") if u.strip()]
        missing = [u for u in wanted if u not in known]
        rows = [known[u] for u in wanted if u in known]
        rows += lookup_rows(con, missing)
        still = [u for u in missing if u not in {r["uid"] for r in rows}]
        if still:
            sys.exit("找不到源文件: " + ", ".join(still))
    if a.limit:
        rows = rows[: a.limit]
    print("批次 %s，待拆 %d 本，%d 并发，%s" %
          (a.batch, len(rows), a.workers, "入库" if ctx["land"] else "只暂存"))

    results, done = [], 0
    with futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
        jobs = {pool.submit(run_one, r, ctx): r for r in rows}
        for job in futures.as_completed(jobs):
            r = jobs[job]
            try:
                res = job.result()
            except Exception as exc:  # 单本炸了不拖住整批
                res = {"uid": r["uid"], "title": r["title"], "format": r["format"],
                       "dest": os.path.join("库", r["path"], "md"), "at": now(),
                       "status": "error", "note": "%s: %s" % (type(exc).__name__, exc)}
            write_result(ctx, res)
            results.append(res)
            done += 1
            print("[%d/%d] %s %-6s %-42.42s %s" %
                  (done, len(rows), res["uid"], res["status"], res["title"],
                   "%.1f%%" % (res["audit_ratio"] * 100) if res.get("audit_ratio") else ""),
                  flush=True)
    report(ctx, results)
    if a.report_json:
        print(json.dumps(summary(ctx, results), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
