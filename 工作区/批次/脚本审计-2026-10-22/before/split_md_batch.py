#!/usr/bin/env python3
"""批量把 source 是 ebook 的书拆成 md（方案 §7 的转换接口）。

每本书一条流水线：转 EPUB（mobi/azw3/azw 先过 Calibre）→ 拆章 → verify → audit
→ 原子 rename 进 库/<path>/md/。产物先落 工作区/批次/<批次>/out/<uid>/，只有
verify 与 audit 都过才进库，没过留在暂存区待人看。

幂等：目标 md 目录已存在就跳过；暂存区已有产物就从 verify 续跑，不重拆。
范围账本 worklist.tsv 只在批次目录里还没有它时生成一次，重复运行不重写。

按 uid 覆盖选项放 工作区/批次/<批次>/overrides/<uid>.json，内容是额外命令行参数
数组，例如 ["--keep", "^序言的结构"]。

用法:
  python3 split_md_batch.py --batch md拆分-2026-10-18 --workers 6
  python3 split_md_batch.py --batch md拆分-2026-10-18 --only B0121,B0261
  python3 split_md_batch.py --batch md拆分-2026-10-18 --no-land   # 只做暂存，不进库
  python3 split_md_batch.py --batch md拆分-2026-10-18 --report
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L  # noqa: E402

SKILL = os.path.expanduser("~/.agents/skills/ebook-to-md/scripts")
CONVERT = os.path.join(SKILL, "ebook_to_md.py")
VERIFY = os.path.join(SKILL, "verify_output.py")
AUDIT = os.path.join(SKILL, "audit_volume.py")
CALIBRE = "ebook-convert"
FORMATS = ("epub", "mobi", "azw3", "azw")
TIMEOUT = 900
COUNT_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]|[A-Za-z0-9]")


def tool_version():
    with open(CONVERT, "rb") as fh:
        return "sha256:" + hashlib.sha256(fh.read()).hexdigest()[:12]


def now():
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def build_worklist(con, batch_path):
    path = os.path.join(batch_path, "worklist.tsv")
    # 账本记的是批次范围：已入库的书不在 v_pending_md 里了。文件存在就以它为准
    # （重拆批次在跑之前先把范围写进去，驱动只读不重写）。
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            cols = fh.readline().rstrip("\n").split("\t")
            return [dict(zip(cols, line.rstrip("\n").split("\t"))) for line in fh]
    rows = con.execute(
        """SELECT b.uid AS uid, s.format AS format, s.sha256 AS sha256,
                  b.path AS path, b.title AS title, s.file AS src
           FROM book b JOIN source s ON s.uid=b.uid AND s.primary_src=1
           WHERE s.format IN (?,?,?,?) AND b.uid IN (SELECT uid FROM v_pending_md)
           ORDER BY CAST(SUBSTR(b.uid,2) AS INTEGER)""", FORMATS).fetchall()
    cols = list(rows[0].keys()) if rows else ["uid", "format", "sha256", "path", "title", "src"]
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write("\t".join(cols) + "\n")
        for r in rows:
            fh.write("\t".join(str(r[c]) for c in cols) + "\n")
    os.replace(path + ".tmp", path)
    return [dict(r) for r in rows]


def log_tail(out, n=25):
    lines = out.strip().splitlines()
    return "\n".join(lines[-n:])


def sh(cmd, cwd):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT)
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
            d = dict(r)
            d["redo"] = True
            rows.append(d)
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
    if os.path.isdir(stage) and not ctx["force"]:
        man = L.read_md_manifest(stage)
        if ctx["land"] and man.get("verified") and (not os.path.isdir(dest) or ctx["replace"]):
            land(ctx, stage, dest, result)
            result["note"] = "暂存区已有过检产物，直接进库"
        else:
            result.update(status="staged", note="暂存区已有产物，未重新拆分")
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

    chapters, nbytes = L.count_md_dir(target)
    manifest = {
        "uid": uid, "method": "ebook-to-md", "tool_version": ctx["tool_version"],
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
    p = os.path.join(ctx["logs"], result["uid"] + ".json")
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
    bad = [r for r in results if r["status"] not in
           ("landed", "replaced", "accepted", "staged-green", "skipped-has-md", "staged")]
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--batch", required=True, help="批次目录名，在 工作区/批次/ 下")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only", help="只跑这些 uid，逗号分隔")
    ap.add_argument("--limit", type=int, help="最多跑几本（调试用）")
    ap.add_argument("--no-land", action="store_true", dest="noland",
                    help="只做暂存，不进 库/")
    ap.add_argument("--force", action="store_true", help="忽略暂存区已有产物，重拆")
    ap.add_argument("--replace", action="store_true",
                    help="目标 md 已存在时也重拆并替换，旧版本改名留在批次区")
    ap.add_argument("--report", action="store_true", help="只读 logs/ 出统计，不干活")
    a = ap.parse_args()

    batch_path = os.path.join(L.WORK, "批次", a.batch)
    ctx = {"batch": batch_path, "batch_name": a.batch, "out": os.path.join(batch_path, "out"),
           "tmp": os.path.join(batch_path, "tmp"), "logs": os.path.join(batch_path, "logs"),
           "land": not a.noland, "force": a.force, "replace": a.replace,
           "stamp": now().replace(":", "").replace("+", "-"), "tool_version": tool_version()}
    os.makedirs(ctx["logs"], exist_ok=True)

    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(L.CAT, "library.db"), uri=True)
    con.row_factory = sqlite3.Row

    if a.report:
        results = []
        for uid in sorted(os.listdir(ctx["logs"])):
            if uid.endswith(".json"):
                results.append(json.load(open(os.path.join(ctx["logs"], uid), encoding="utf-8")))
        report(ctx, with_accept(ctx, results))
        return 0

    os.makedirs(ctx["out"], exist_ok=True)
    os.makedirs(ctx["tmp"], exist_ok=True)
    rows = build_worklist(con, batch_path)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
