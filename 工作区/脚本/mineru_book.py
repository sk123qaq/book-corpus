#!/usr/bin/env python3
"""整本走 MinerU 的 OCR 路线：切片 → 提交 → 逐页回填 → 与 Paddle 路线同一套拼装与门槛。

Paddle 那条线按分片拿回逐页 md；MinerU 拿回的是一份 full.md 加一份块级 content_list（带
page_idx），所以这里多做一步：把块按页拼回 merged/pages/doc_%04d.md，之后 pdf_ocr_to_md.py、
verify_output.py、pdf_audit_volume.py、逐页体检、原子落地全部照用，manifest 的 method 写 mineru。

账都可续跑：
  work/<uid>/mineru/jobs.json       切片账：range / 切片文件 / 输出目录 / status / task_id
  work/<uid>/mineru/slices/*.pdf    切片，已经切过就复用
  work/<uid>/mineru/out/<name>/     MinerU 产物，有 *_content_list.json 就算这一片完成
  work/<uid>/merged/                与 Paddle 路线同形，拼装器的输入

MinerU 单文件上限 200 页 / 200 MB，账号每天 1000 页最高优先级、5000 页封顶。

用法:
  mineru_book.py --batch pdf-ocr-2026-10-19 --uid B1556
  mineru_book.py --batch pdf-ocr-2026-10-19 --uid B1556 --dry-run
  mineru_book.py --batch pdf-ocr-2026-10-19 --uid B1556 --no-land
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

import pymupdf as fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L  # noqa: E402
import pdf_md_batch as B  # noqa: E402

PAGE_SCAN_AREA = 0.6   # bbox 覆盖整页的图就是这张扫描图本身，不往 md 里塞
MINERU_IMG_OK = ("image", "figure")
# 只丢页码与页脚符号。脚注（page_footnote）与 ref_text 是这个版本的主体内容：
# 《芬尼根的守灵夜》戴从容译本的注释就落在 page_footnote 里，丢掉等于丢正文。
MINERU_DROP = ("page_number", "footer", "footer_image")


def batch_dir(name):
    return os.path.join(L.WORK, "批次", name)


def load_row(batch, uid):
    path = os.path.join(batch, "worklist.tsv")
    with open(path, encoding="utf-8") as fh:
        cols = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            if not line.strip():
                continue
            row = dict(zip(cols, line.rstrip("\n").split("\t")))
            if row["uid"] == uid:
                return row
    sys.exit("worklist 里没有 %s" % uid)


def plan_slices(pages, chunk_pages):
    return [(a, min(a + chunk_pages - 1, pages)) for a in range(1, pages + 1, chunk_pages)]


def slice_name(a, b):
    return "p%04d-%04d" % (a, b)


def content_list(outdir):
    """MinerU 在 -o 下再套一层输入名，所以从这里往下走一遍找 content_list。"""
    best = None
    if not os.path.isdir(outdir):
        return None
    for root, _dirs, names in os.walk(outdir):
        for n in names:
            if not n.endswith("_content_list.json") or n.endswith("_v2_content_list.json"):
                continue
            path = os.path.join(root, n)
            if best is None or os.path.getmtime(path) > os.path.getmtime(best):
                best = path
    return best


def write_slice(src, dst, a, b):
    doc = fitz.open(src)
    out = fitz.open()
    try:
        for p in range(a - 1, b):
            out.insert_pdf(doc, from_page=p, to_page=p)
        out.save(dst + ".tmp", garbage=3, deflate=True)
    finally:
        out.close()
        doc.close()
    os.replace(dst + ".tmp", dst)


def submit(ctx, uid, slice_pdf, outdir, lang, log, result):
    cmd = [B.VENV_PY, B.MINERU_CLI, slice_pdf, "-o", outdir, "--mode", "precision",
           "--model", "vlm", "--ocr", "--lang", lang, "--json", "--timeout", "1800"]
    log.write("$ %s\n" % " ".join(cmd))
    p = subprocess.run(cmd, cwd=L.ROOT, capture_output=True, text=True, timeout=3600)
    log.write((p.stdout or "")[-4000:] + (p.stderr or "")[-2000:])
    result["commands"].append("mineru.py %s --mode precision --model vlm --ocr --lang %s"
                              % (B.rel(slice_pdf), lang))
    task = None
    m = re.search(r'"task_id":\s*"([^"]+)"', p.stdout or "")
    if m:
        task = m.group(1)
    return p.returncode, task


def render_page(blocks, page_idx, imgs_dir, prefix):
    """一页的正文。文字块按 bbox 从上到下；表格取 table_body 里的文字；整页扫描图不塞回 md。"""
    picked = []
    for b in blocks:
        if b.get("page_idx") != page_idx:
            continue
        kind = b.get("type")
        if kind in MINERU_DROP:
            continue
        box = b.get("bbox") or [0, 0, 0, 0]
        picked.append((box[1], box[0], kind, b, box))
    picked.sort(key=lambda t: (t[0], t[1]))

    chunks, images = [], 0
    for _y, _x, kind, b, box in picked:
        text = (b.get("text") or b.get("table_body") or "").strip()
        if kind in MINERU_IMG_OK:
            area = abs((box[2] - box[0]) * (box[3] - box[1])) / 1e6
            rel = b.get("img_path") or ""
            if area >= PAGE_SCAN_AREA or not rel:
                continue          # 整页扫描图
            src = os.path.join(b.get("outdir") or "", rel)
            if not os.path.isfile(src):
                continue
            name = "%s_%s" % (prefix, os.path.basename(rel))
            os.makedirs(imgs_dir, exist_ok=True)
            with open(src, "rb") as fh, open(os.path.join(imgs_dir, name), "wb") as out:
                out.write(fh.read())
            chunks.append('<img src="imgs/%s">' % name)
            images += 1
        elif text:
            chunks.append(text)
    return "\n\n".join(chunks), images


def build_merged(ctx, uid, row, jobs, log):
    fresh = os.path.join(ctx["work"], uid, "merged.new")
    if os.path.isdir(fresh):
        shutil.rmtree(fresh)
    fresh_pages = os.path.join(fresh, "pages")
    fresh_imgs = os.path.join(fresh_pages, "imgs")
    os.makedirs(fresh_pages)

    per_page, seq = [], 0
    for job in jobs:
        cpath = content_list(job["outdir"])
        if not cpath:
            raise SystemExit("切片 %d-%d 没有 content_list：%s" % (job["range"][0], job["range"][1],
                                                                  job["outdir"]))
        blocks = json.load(open(cpath, encoding="utf-8"))
        for b in blocks:
            b["outdir"] = os.path.dirname(cpath)
        a, b_ = job["range"]
        for idx in range(b_ - a + 1):
            page = a + idx
            text, nimg = render_page(blocks, idx, fresh_imgs, "p%04d" % page)
            seq += 1
            with open(os.path.join(fresh_pages, "doc_%04d.md" % seq), "w", encoding="utf-8") as fh:
                fh.write(text.rstrip() + "\n")
            per_page.append({"page": page, "seq": seq, "chars": len(text), "images": nimg,
                             "head": " ".join(text.split())[:160]})

    npages = int(row["pages"])
    got = [p["page"] for p in per_page]
    doc = {"uid": uid, "source": row["src"], "pages": len(per_page), "book_pages": npages,
           "first_page": got[0], "last_page": got[-1],
           "coverage_ok": got == list(range(1, npages + 1)),
           "chunks": [{"range": j["range"], "task_id": j.get("task_id")} for j in jobs],
           "missing_images": [], "generated_at": B.now(), "per_page": per_page,
           "engine": "mineru"}
    merged = os.path.join(ctx["work"], uid, "merged")
    if os.path.isdir(merged):
        shutil.rmtree(merged)
    os.rename(fresh, merged)
    B.write_json(os.path.join(merged, "run.json"), doc)
    log.write("合并 %d 页（第 %d 到 %d 页），覆盖 1-%d：%s\n"
              % (len(per_page), got[0], got[-1], npages, doc["coverage_ok"]))
    return merged, doc


def has_text_layer(src, sample=20):
    """PDF 自带文本层才看 PDF 侧字数比与锚点；纯扫描件这两个数恒为 0，看了只会误判。"""
    doc = fitz.open(src)
    try:
        step = max(1, doc.page_count // sample)
        chars = sum(len(doc[i].get_text().strip()) for i in range(0, doc.page_count, step))
        return chars >= 100
    finally:
        doc.close()


def page_markdown(d):
    """单页产物的正文：先按 content_list 拼；它是空的（MinerU 把整页判成一个 code 块时就这样），
    退回去读 full.md，剥掉外面那层 ```txt 围栏。"""
    cp = None
    for root, _dirs, names in os.walk(d):
        for n in names:
            if n.endswith("_content_list.json") and not n.endswith("_v2_content_list.json"):
                cp = os.path.join(root, n)
    if cp:
        blocks = json.load(open(cp, encoding="utf-8"))
        for b in blocks:
            b["outdir"] = os.path.dirname(cp)
        text, _n = render_page(blocks, 0, os.path.join(d, "imgs"), os.path.basename(d))
        if text.strip():
            return text
    hits = glob.glob(os.path.join(d, "**", "full.md"), recursive=True)
    if not hits:
        return ""
    lines = open(hits[0], encoding="utf-8").read().splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r"^```\w*$", lines[0].strip()):
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip() == "```":
        lines.pop()
    return "\n".join(lines)


def rescue_pages(ctx, uid, src, pages, lang, log, result):
    """可疑页补投：每页切成 1 页的 PDF 单独交 MinerU。

    整本跑 vlm 时有一批纯注释页被版面模型并成一个空 code 块（实测 107 页），与 skill 里
    「扫描件静默丢页」同源。单页文件交给 vlm 能读出来，所以按页重投；vlm 仍空的再用 pipeline。
    返回 (读出正文 {页: 正文}, 没跑起来的页)。后者是请求被限流或失败，不算「读不出」。
    """
    rdir = os.path.join(ctx["work"], uid, "mineru", "rescue")
    os.makedirs(rdir, exist_ok=True)
    got = {}

    def outdir(model, page):
        return os.path.join(rdir, model, "p%04d" % page)

    def produced(model, page):
        base = outdir(model, page)
        if not os.path.isdir(base):
            return None
        for root, _dirs, names in os.walk(base):
            if "full.md" in names or any(
                    n.endswith("_content_list.json") and not n.endswith("_v2_content_list.json")
                    for n in names):
                return base
        return None

    for model in ("vlm", "pipeline"):
        todo = [p for p in pages if p not in got]
        if not todo:
            break
        for attempt in range(1, 4):
            if not todo:
                break
            for i in range(0, len(todo), 10):
                batch = todo[i:i + 10]
                files = []
                for p in batch:
                    f = os.path.join(rdir, "p%04d.pdf" % p)
                    if not os.path.isfile(f):
                        write_slice(src, f, p, p)
                    files.append(f)
                cmd = [B.VENV_PY, B.MINERU_CLI, *files, "-o", os.path.join(rdir, model),
                       "--mode", "precision", "--model", model, "--ocr", "--lang", lang,
                       "--json", "--timeout", "900"]
                log.write("$ mineru.py %d 个单页 --model %s --ocr --lang %s（第 %d 次）\n"
                          % (len(files), model, lang, attempt))
                p = subprocess.run(cmd, cwd=L.ROOT, capture_output=True, text=True, timeout=1800)
                tail = (p.stdout or "")[-1200:] + (p.stderr or "")[-800:]
                log.write(tail)
                result["commands"].append("mineru.py %d 个单页 --model %s --ocr --lang %s"
                                          % (len(files), model, lang))
                if "429" in tail or "rate limited" in tail:
                    log.write("补投被限流，等 45 秒再试\n")
                    time.sleep(45)
                else:
                    time.sleep(2)
            todo = [p for p in todo if not produced(model, p)]
        for page in pages:
            if page in got:
                continue
            base = produced(model, page)
            if not base:
                continue
            text = page_markdown(base)
            if text.strip():
                got[page] = text
        log.write("%s 补投：累计读出 %d / %d 页\n" % (model, len(got), len(pages)))

    not_run = [p for p in pages if p not in got
               and not produced("vlm", p) and not produced("pipeline", p)]
    log.write("补投 %d 页：读出 %d 页，跑不起来 %d 页\n" % (len(pages), len(got), len(not_run)))
    return got, not_run


def enrich(stage, ctx, row, v, doc, blank, suspect, short, rescued, skipped):
    """在拼装器写好的 manifest 上补批次、门槛与补投记录，字段与 Paddle 路线一一对应。"""
    man = L.read_md_manifest(stage)
    chapters, bytes_ = L.count_md_dir(stage)
    man.update({
        "method": "mineru", "tool": "mineru.py", "tool_version": ctx["tool_version"],
        "batch": ctx["batch_name"], "source": row["src"], "source_sha256": row["sha256"],
        "source_format": "pdf", "pages": doc["pages"], "book_pages": doc["book_pages"],
        "chapters": chapters, "bytes": bytes_, "chars": v["chars"], "images": v["images"],
        "verify_rc": v["verify_rc"], "coverage_ok": doc["coverage_ok"],
        "blank_pages": len(blank), "suspect_miss_pages": len(suspect),
        "structure": "single", "single_file": True,
        "suspect_pages": [s["page"] for s in suspect], "short_pages": len(short),
        "mineru_models": {"chunks": "vlm", "rescue": "vlm 单页"},
        "mineru_rescued_pages": list(rescued), "skipped_unreadable_pages": list(skipped),
        "generated_at": B.now(), "verified": v["verify_rc"] == 0, "accepted": "",
    })
    B.write_json(os.path.join(stage, ".manifest.json"), man)
    return man


def write_back(merged, doc, got, log):
    """把补投回来的正文写回 merged/pages 与 run.json 的逐页账。"""
    seq = {e["page"]: e["seq"] for e in doc["per_page"]}
    n = 0
    for page, text in got.items():
        with open(os.path.join(merged, "pages", "doc_%04d.md" % seq[page]), "w",
                  encoding="utf-8") as fh:
            fh.write(text.rstrip() + "\n")
        for e in doc["per_page"]:
            if e["page"] == page:
                e["chars"], e["head"], e["rescued"] = len(text), " ".join(text.split())[:160], "mineru-pipeline"
                n += 1
    B.write_json(os.path.join(merged, "run.json"), doc)
    log.write("补投正文写回 %d 页\n" % n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True)
    ap.add_argument("--uid", required=True)
    ap.add_argument("--chunk-pages", type=int, default=150, help="每片页数，上限 200")
    ap.add_argument("--lang", default="ch", help="MinerU 语种，默认 ch（中英）")
    ap.add_argument("--no-land", action="store_true", help="只拼装过检，不进库")
    ap.add_argument("--dry-run", action="store_true", help="只看切片计划与哪几片已完成")
    ap.add_argument("--force", action="store_true", help="重切、重拼、重拼装")
    a = ap.parse_args()
    if a.chunk_pages > B.MINERU_MAX_PAGES:
        sys.exit("--chunk-pages 不能超过 %d" % B.MINERU_MAX_PAGES)

    bdir = batch_dir(a.batch)
    row = load_row(bdir, a.uid)
    uid = a.uid
    ctx = {"batch": bdir, "batch_name": a.batch, "out": os.path.join(bdir, "out"),
           "tmp": os.path.join(bdir, "tmp"), "logs": os.path.join(bdir, "logs"),
           "work": os.path.join(bdir, "work"), "review": os.path.join(bdir, "review"),
           "land": not a.no_land, "force": a.force, "replace": False, "api": False,
           "route": "ocr", "method": "mineru", "assemble_only": True, "queue_rounds": 0,
           "abort": "", "stamp": B.now().replace(":", "").replace("+", "-"),
           "tool_version": B.tool_version()}
    mdir = os.path.join(ctx["work"], uid, "mineru")
    slices = os.path.join(mdir, "slices")
    outs = os.path.join(mdir, "out")
    jobs_path = os.path.join(mdir, "jobs.json")
    os.makedirs(slices, exist_ok=True)
    src = os.path.join(L.ROOT, row["src"])
    if not os.path.isfile(src):
        sys.exit("源文件不在: " + row["src"])
    if L.sha256_file(src) != row["sha256"]:
        sys.exit("源文件 sha256 与人账不符，停下人工查: " + row["src"])

    jobs = []
    for i, (x, y) in enumerate(plan_slices(int(row["pages"]), a.chunk_pages)):
        name = slice_name(x, y)
        jobs.append({"i": i, "range": [x, y], "slice": os.path.join(slices, name + ".pdf"),
                     "outdir": os.path.join(outs, name), "status": "pending", "task_id": None})
    if os.path.isfile(jobs_path) and not a.force:
        old = {tuple(j["range"]): j for j in json.load(open(jobs_path, encoding="utf-8"))}
        for j in jobs:
            prev = old.get(tuple(j["range"]))
            if prev:
                j["task_id"] = prev.get("task_id")

    if a.dry_run:
        for j in jobs:
            print("  %s  %5d-%5d  %s" % ("done" if content_list(j["outdir"]) else "todo",
                                          j["range"][0], j["range"][1],
                                          os.path.basename(j["slice"])))
        return 0

    os.makedirs(ctx["logs"], exist_ok=True)
    os.makedirs(ctx["out"], exist_ok=True)
    logpath = os.path.join(ctx["logs"], uid + ".log")
    result = {"uid": uid, "title": row["title"], "format": row["format"], "route": "mineru",
              "pages": int(row["pages"]), "dest": B.rel(os.path.join(L.ROOT, "库", row["path"], "md")),
              "at": B.now(), "commands": [], "engine": "mineru"}
    with open(logpath, "w", encoding="utf-8") as log:
        log.write("MinerU 整本路线：%d 页，每片 %d 页，语种 %s\n"
                  % (int(row["pages"]), a.chunk_pages, a.lang))
        for j in jobs:
            if content_list(j["outdir"]) and not a.force:
                j["status"] = "done"
                log.write("复用已完成切片 %d-%d\n" % tuple(j["range"]))
                continue
            if not os.path.isfile(j["slice"]) or a.force:
                write_slice(src, j["slice"], j["range"][0], j["range"][1])
                size_mb = os.path.getsize(j["slice"]) / 1e6
                log.write("切出 %s，%d 页，%.1f MB\n"
                          % (os.path.basename(j["slice"]), j["range"][1] - j["range"][0] + 1, size_mb))
                if size_mb > B.MINERU_MAX_MB:
                    sys.exit("切片 %.1f MB 超过 MinerU 单文件 %d MB 上限，把 --chunk-pages 调小"
                             % (size_mb, B.MINERU_MAX_MB))
            rc, task = submit(ctx, uid, j["slice"], j["outdir"], a.lang, log, result)
            j["task_id"] = task or j["task_id"]
            j["status"] = "done" if content_list(j["outdir"]) else "failed"
            B.write_json(jobs_path, jobs)
            result["chunks_done"] = sum(1 for k in jobs if k["status"] == "done")
            B.write_result(ctx, result)
            if j["status"] != "done":
                result.update(status="mineru-failed",
                              note="切片 %d-%d 没拿到 content_list（rc=%d）" % (*j["range"], rc))
                B.write_result(ctx, result)
                log.write("切片 %d-%d 失败，rc=%d；重跑本命令会重提这一片\n" % (*j["range"], rc))
                return 1
        B.write_json(jobs_path, jobs)

        merged, doc = build_merged(ctx, uid, row, jobs, log)
        result.update(merged_pages=doc["pages"], coverage_ok=doc["coverage_ok"],
                      merged_run_json=B.rel(os.path.join(merged, "run.json")))
        empty = [p["page"] for p in doc["per_page"] if p["chars"] < B.SUSPECT_CHARS]
        result["thin_pages"] = empty
        log.write("逐页体检前：少于 %d 字的页 %d 个\n" % (B.SUSPECT_CHARS, len(empty)))

        blank, suspect, short = B.pdf_checks(ctx, row, doc["per_page"], log, result)
        result.update(blank_pages=len(blank), suspect_pages=len(suspect), short_pages=len(short))
        if suspect:
            got, not_run = rescue_pages(ctx, uid, src, [s["page"] for s in suspect], a.lang, log,
                                        result)
            if got:
                write_back(merged, doc, got, log)
                result["mineru_rescued"] = sorted(got)
            if not_run:
                result["mineru_not_run"] = not_run
            if got:
                blank, suspect, short = B.pdf_checks(ctx, row, doc["per_page"], log, result)
                result.update(blank_pages=len(blank), suspect_pages=len(suspect),
                              short_pages=len(short))
        else:
            not_run = []
        skipped = [s["page"] for s in suspect if s["page"] not in not_run]
        if skipped:
            B.skip_rows(ctx, [{"at": B.now(), "uid": uid, "page": p, "paddle_chars": 0,
                               "mineru_chars": 0, "action": "skip",
                               "note": "整本 MinerU 路线，单页 pipeline 补投也读不出"}
                              for p in skipped])
            result["skipped_unreadable_pages"] = skipped
            log.write("%d 页补投也读不出，按跳过受理：%s\n" % (len(skipped), skipped))

        stage = os.path.join(ctx["out"], uid)
        if os.path.isdir(stage):
            moved = os.path.join(ctx["tmp"], "%s.prev-%s" % (uid, ctx["stamp"]))
            os.rename(stage, moved)
            result["stage_previous"] = B.rel(moved)
        rc = B.assemble(ctx, row, merged, stage, log, result)
        if rc != 0 or not os.path.isdir(stage):
            result.update(status="assemble-failed", note="pdf_ocr_to_md rc=%d" % rc)
            B.write_result(ctx, result)
            return 1

        vrc, v = B.verify(ctx, stage, log)
        v["verify_rc"] = vrc
        result.update(verify_rc=vrc, chapters=v["chapters"], chars=v["chars"],
                      images=v["images"], problems=v["problems"])
        if has_text_layer(src):
            arc, audit = B.audit_pdf(ctx, src, stage, log)
            result.update(audit_rc=arc, audit_ratio=audit.get("ratio"),
                          audit_anchors=audit.get("anchors"))
            fails = B.gate_text(v, arc, audit)
        else:
            log.write("源 PDF 没有文本层，PDF 侧字数比与锚点不适用；门槛只看 verify、逐页覆盖、"
                      "可疑页与缺图\n")
            result["audit_rc"] = None
            fails = B.gate(v, doc["coverage_ok"], suspect, doc["missing_images"])
        if doc["pages"] != int(row["pages"]) or not doc["coverage_ok"]:
            fails.append("页数 %d / 应 %s，连续覆盖 %s"
                         % (doc["pages"], row["pages"], doc["coverage_ok"]))
        if suspect:
            fails.append("可疑页 %d 个（MinerU 读出 < %d 字但有字迹）：%s"
                         % (len(suspect), B.SUSPECT_CHARS, [s["page"] for s in suspect][:20]))
        if skipped:
            fails = [f for f in fails if not f.startswith("可疑页")]
        if not_run:
            fails.append("补投请求没跑起来 %d 页（限流/失败，不算读不出）：%s"
                         % (len(not_run), not_run[:20]))
        if fails:
            result.update(status="staged-review", note="; ".join(fails))
            B.write_result(ctx, result)
            log.write("门槛没过：%s\n" % result["note"])
            print("门槛没过：%s" % result["note"])
            return 1

        enrich(stage, ctx, row, v, doc, blank, suspect, short,
               result.get("mineru_rescued") or [], skipped)
        if ctx["land"]:
            B.land(ctx, stage, os.path.join(L.ROOT, "库", row["path"], "md"), result)
        else:
            result["status"] = "staged-green"
        if skipped and result["status"] == "landed":
            result["status"] = "accepted"
            result["note"] = "%d 页按跳过受理（MinerU 与单页 pipeline 都读不出）" % len(skipped)
        B.write_result(ctx, result)
        log.write("状态 %s：%d 章 %d 字，短页 %d，空页 %d\n"
                  % (result["status"], v["chapters"] or 0, v["chars"] or 0,
                     len(short), len(blank)))
        print("状态 %s：%d 章 %d 字，短页 %d，空页 %d"
              % (result["status"], v["chapters"] or 0, v["chars"] or 0,
                 len(short), len(blank)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
