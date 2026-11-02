#!/usr/bin/env python3
"""PDF 这一组的离线验收：分诊、模型四列、批次队列与统计。不碰真库、不调真模型。

跑法：python3 工作区/运维/验收/test_pdf.py
"""
import csv
import importlib.util
import json
import os
import py_compile
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SCRIPTS = os.path.join(REPO, "工作区/脚本")
TRIAGE_PY = os.path.join(SCRIPTS, "ops_pdf_triage.py")
APPLY_PY = os.path.join(SCRIPTS, "pdf_apply_review.py")
BATCH_PY = os.path.join(SCRIPTS, "pdf_md_batch.py")
VENV_PY = os.path.join(REPO, "工作区/.venv-pdf/bin/python")
STUB = os.path.join(REPO, "工作区/运维/验收/fixtures/pi_stub.py")
SKIP_HEADER = "uid\ttitle\tsubject1\tsubject2\tpages\tmb\timage_share\twhy\tverdict\troute_metric"
MODEL_COLS = ["model_route", "model_confidence", "model_reason", "model_at"]
TRIAGE_COLS = ["uid", "title", "subject1", "subject2", "class", "route", "pages", "mb",
               "ppi", "text_page_share", "ink", "bands", "note"]

FAILED = []

GEN = '''
import os, sys
import pymupdf
from PIL import Image

kind, path, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
doc = pymupdf.open()
if kind == "text":
    for _ in range(n):
        page = doc.new_page()
        for i in range(30):
            page.insert_text((56, 56 + i * 24),
                             "the quick brown fox jumps over the lazy dog %02d %s"
                             % (i, "x" * 24), fontsize=9)
else:
    img = os.path.join(os.path.dirname(path), "_noise.png")
    im = Image.new("L", (600, 800))
    im.putdata([(i * 37) % 256 for i in range(600 * 800)])
    im.convert("RGB").save(img)
    for _ in range(n):
        page = doc.new_page()
        page.insert_image(page.rect, filename=img)
doc.save(path)
'''


def check(ok, msg):
    print(("  ok    " if ok else "  FAIL  ") + msg)
    if not ok:
        FAILED.append(msg)


def run(argv, env=None, cwd=None):
    e = dict(os.environ)
    e.pop("PI_BIN", None)
    e.pop("PI_STUB_JSON", None)
    e.update(env or {})
    return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=e,
                          cwd=cwd, timeout=900)


def out_text(p):
    return p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")


def make_pdf(path, kind, pages):
    gen = os.path.join(os.path.dirname(path), "_gen.py")
    if not os.path.exists(gen):
        with open(gen, "w", encoding="utf-8") as fh:
            fh.write(GEN)
    p = run([VENV_PY, gen, kind, path, str(pages)])
    if not os.path.isfile(path):
        raise SystemExit("造 fixture PDF 失败：%s" % out_text(p))


def make_library(root):
    """两本书的临时库：一本文本层（4 页），一本纯图版（4 页）。"""
    books = [("B9001", "测试文本书", "文学/测试文本书", "text", "text.pdf", ["文学"]),
             ("B9002", "测试图册", "艺术与图像/测试图册", "img", "img.pdf",
              ["艺术与图像", "图册与写真"])]
    for uid, title, relpath, kind, fname, subjects in books:
        bdir = os.path.join(root, "库", relpath)
        os.makedirs(os.path.join(bdir, "source"), exist_ok=True)
        pdf = os.path.join(bdir, "source", fname)
        make_pdf(pdf, kind, 4)
        doc = {"uid": uid, "title": title, "path": relpath, "subjects": subjects,
               "sources": [{"file": "source/" + fname, "format": "pdf",
                            "bytes": os.path.getsize(pdf), "sha256": "", "primary_src": 1}]}
        with open(os.path.join(bdir, "book.json"), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
    return root


def triage(root, uid, out="工作区/运维/jobs/j", env=None, extra=()):
    argv = [sys.executable, TRIAGE_PY, "--uid", uid, "--root", root, "--out", out]
    p = run(argv + list(extra), env=env)
    rp = os.path.join(root, out, "result.json")
    res = None
    if os.path.isfile(rp):
        with open(rp, encoding="utf-8") as fh:
            res = json.load(fh)
    return p, res


def test_compile():
    print("\n== 三个脚本能编译")
    for path in (TRIAGE_PY, APPLY_PY, BATCH_PY):
        try:
            py_compile.compile(path, doraise=True)
            check(True, "py_compile %s" % os.path.basename(path))
        except py_compile.PyCompileError as exc:
            check(False, "py_compile %s: %s" % (os.path.basename(path), exc))


def test_triage_text(root):
    print("\n== 分诊：文本层 PDF（--no-model）")
    p, res = triage(root, "B9001", extra=["--no-model"])
    check(p.returncode == 0, "退出码 0（%s）" % out_text(p).strip().splitlines()[-1:])
    if not res:
        return None
    check(res["pages"] == 4, "pages=4，实际 %s" % res["pages"])
    check(res["class"].startswith("A"), "census 判 A 类，实际 %r" % res["class"])
    check(res["path"] == "库/文学/测试文本书/source/text.pdf",
          "path 是库内相对路径，实际 %r" % res["path"])
    m = res["metrics"]
    check(m["text_page_share"] == 1.0, "text_page_share=1.0，实际 %r" % m["text_page_share"])
    check(m["image_share"] == 0.0, "image_share=0.0，实际 %r" % m["image_share"])
    check(m["ink"] is None and m["bands"] is None, "文本层书不报 ink/bands")
    check(m["chars_per_page"] and m["chars_per_page"] > 200,
          "chars_per_page>200，实际 %r" % m["chars_per_page"])
    check(res["thumbs"] == ["thumbs/001.png", "thumbs/002.png", "thumbs/004.png"],
          "缩略图是首页/中页/末页，实际 %r" % res["thumbs"])
    png_ok = []
    for t in res["thumbs"]:
        fp = os.path.join(root, "工作区/运维/jobs/j", t)
        with open(fp, "rb") as fh:
            png_ok.append(fh.read(8) == b"\x89PNG\r\n\x1a\n" and os.path.getsize(fp) > 1000)
    check(all(png_ok) and len(png_ok) == 3, "3 张缩略图都是非空 PNG")
    check(res["model"] is None, "--no-model 时 model 是 null")
    check(len(res.get("prompt_hash") or "") == 64, "prompt_hash 是 sha256")
    check(os.path.isfile(os.path.join(root, "工作区/运维/jobs/j/census.jsonl")),
          "落盘 census.jsonl")
    return res


def test_triage_scan(root, text_res):
    print("\n== 分诊：纯图版 PDF（--no-model）")
    p, res = triage(root, "B9002", out="工作区/运维/jobs/j2", extra=["--no-model"])
    check(p.returncode == 0, "退出码 0")
    if not res:
        return None
    check(res["class"].startswith("C"), "census 判 C 类，实际 %r" % res["class"])
    m = res["metrics"]
    check(m["text_page_share"] == 0.0, "text_page_share=0.0，实际 %r" % m["text_page_share"])
    check(m["image_share"] == 1.0, "image_share=1.0，实际 %r" % m["image_share"])
    check(isinstance(m["ink"], float), "ink 是实测值，实际 %r" % m["ink"])
    check(isinstance(m["bands"], int), "bands 是整数，实际 %r" % m["bands"])
    check(len(res["thumbs"]) == 3, "3 张缩略图")
    if text_res:
        check(res["prompt_hash"] != text_res["prompt_hash"],
              "两本书的 prompt 不同，哈希也不同")
    return res


def test_triage_model(root, scan_res):
    print("\n== 分诊：模型回一行 JSON")
    payload = '{"route":"不转-记图册","confidence":0.9,"reason":"整页图版，几乎没有文字"}'
    p, res = triage(root, "B9002", out="工作区/运维/jobs/j3",
                    env={"PI_BIN": STUB, "PI_STUB_JSON": payload})
    check(p.returncode == 0, "退出码 0")
    if not res or not res.get("model"):
        check(False, "模型建议没解析出来：%s" % (res or {}).get("model_error"))
        return
    mm = res["model"]
    check(mm["route"] == "不转-记图册", "route 解析正确，实际 %r" % mm["route"])
    check(mm["confidence"] == 0.9, "confidence 解析正确，实际 %r" % mm["confidence"])
    check(mm["reason"].startswith("整页图版"), "reason 解析正确")
    check(bool(mm.get("at")), "model.at 带时间戳，供账上 model_at 用")
    if scan_res:
        check(res["prompt_hash"] == scan_res["prompt_hash"],
              "同一本同一指标，prompt_hash 与 --no-model 那次一致")

    print("\n== 分诊：模型回代码块包着的 JSON")
    payload = '```json\n{"route":"OCR","confidence":0.8,"reason":"扫描页有成行文字"}\n```'
    p, res = triage(root, "B9002", out="工作区/运维/jobs/j4",
                    env={"PI_BIN": STUB, "PI_STUB_JSON": payload})
    mm = (res or {}).get("model") or {}
    check(mm.get("route") == "OCR", "从代码块里挖出 JSON，route=%r" % mm.get("route"))
    check(mm.get("confidence") == 0.8, "confidence=0.8，实际 %r" % mm.get("confidence"))

    print("\n== 分诊：模型没回 JSON 时不编造")
    p, res = triage(root, "B9002", out="工作区/运维/jobs/j5",
                    env={"PI_BIN": STUB, "PI_STUB_JSON": "抱歉，我看不出这本。"})
    check(p.returncode == 0, "退出码仍为 0，等人工判断")
    check((res or {}).get("model") is None, "model 是 null")
    check("model_error" in (res or {}), "model_error 写明原因：%r"
          % (res or {}).get("model_error", "")[:60])

    print("\n== 分诊：route 不在词表时带 warn")
    payload = '{"route":"拍平重排","confidence":0.5,"reason":"拿不准"}'
    p, res = triage(root, "B9002", out="工作区/运维/jobs/j6",
                    env={"PI_BIN": STUB, "PI_STUB_JSON": payload})
    mm = (res or {}).get("model") or {}
    check(mm.get("route") == "拍平重排" and "warn" in mm,
          "原样记录且标 warn，实际 %r" % mm)


def test_triage_missing(root):
    print("\n== 分诊：uid 不在库里")
    p, _ = triage(root, "B9999", out="工作区/运维/jobs/j7", extra=["--no-model"])
    check(p.returncode != 0, "退出码非 0")
    check("找不到 uid=B9999" in out_text(p), "报错写明 uid")


def write_csv(path, cols, rows, delim=","):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=delim, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") for c in cols])


def test_apply_review(tmp):
    print("\n== pdf_apply_review：模型四列与人工 route")
    batch = os.path.join(tmp, "batch")
    os.makedirs(os.path.join(batch, "review"), exist_ok=True)
    rows = [{"uid": "B9101", "title": "图册甲", "subject1": "艺术与图像", "subject2": "",
             "class": "C 扫描件(无文本层)", "route": "OCR", "pages": "120", "mb": "40",
             "ppi": "150", "text_page_share": "0", "ink": "0.5", "bands": "3",
             "note": "初判"},
            {"uid": "B9102", "title": "文字书乙", "subject1": "文学", "subject2": "",
             "class": "C 扫描件(无文本层)", "route": "OCR", "pages": "300", "mb": "90",
             "ppi": "300", "text_page_share": "0", "ink": "0.1", "bands": "20",
             "note": "初判"}]
    write_csv(os.path.join(batch, "triage.csv"), TRIAGE_COLS, rows)
    with open(os.path.join(batch, "review", "B9101.json"), "w", encoding="utf-8") as fh:
        json.dump({"uid": "B9101", "verdict": "图片书", "evidence": "整页图版",
                   "route": "不转-记图册", "reason": "人工：整页图版",
                   "model": {"route": "OCR", "confidence": 0.86,
                             "reason": "有文字行，建议 OCR", "at": "2026-10-24T22:00:00+08:00"}},
                  fh, ensure_ascii=False)
    with open(os.path.join(batch, "review", "B9102.json"), "w", encoding="utf-8") as fh:
        json.dump({"uid": "B9102", "verdict": "文字书", "evidence": "正文连续",
                   "route": "OCR", "reason": "人工：正文",
                   "model": {"route": "本地抽文本", "confidence": 0.7,
                             "reason": "文本层看着像有", "at": "2026-10-24T22:05:00+08:00"}},
                  fh, ensure_ascii=False)

    p = run([sys.executable, APPLY_PY, "--batch", batch])
    check(p.returncode == 0, "退出码 0（%s）" % out_text(p).strip().splitlines()[:1])

    fin = os.path.join(batch, "triage_final.csv")
    check(os.path.isfile(fin), "写出 triage_final.csv")
    with open(fin, encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split(",")
    derived = ["verdict", "image_share", "measure", "route_metric"]
    check(header[:len(TRIAGE_COLS)] == TRIAGE_COLS, "原有列顺序不变")
    check(header[len(TRIAGE_COLS):] == derived + MODEL_COLS,
          "模型四列追加在末尾，实际 %r" % (header[len(TRIAGE_COLS):],))
    with open(fin, encoding="utf-8") as fh:
        got = {r["uid"]: r for r in csv.DictReader(fh)}
    a, b = got["B9101"], got["B9102"]
    check(a["route"] == "不转-记图册",
          "verdict=图片书 推出 route=不转-记图册，模型说的 OCR 不改 route，实际 %r" % a["route"])
    check(a["model_route"] == "OCR", "model_route 记模型原话，实际 %r" % a["model_route"])
    check(a["model_confidence"] == "0.86", "model_confidence，实际 %r" % a["model_confidence"])
    check(a["model_reason"] == "有文字行，建议 OCR", "model_reason，实际 %r" % a["model_reason"])
    check(a["model_at"] == "2026-10-24T22:00:00+08:00", "model_at 照抄复核 JSON")
    check(b["route"] == "OCR", "verdict=文字书 推出 route=OCR，实际 %r" % b["route"])
    check(b["model_route"] == "本地抽文本", "model_route 与 route 各记各的")

    sl = os.path.join(batch, "skip_list.tsv")
    with open(sl, encoding="utf-8") as fh:
        lines = [ln.rstrip("\n") for ln in fh if ln.strip()]
    check(lines[0] == SKIP_HEADER, "skip_list 表头不变，实际 %r" % lines[0])
    check(len(lines) == 2 and lines[1].split("\t")[0] == "B9101",
          "不转清单只收图册那本，实际 %r"
          % [ln.split("\t")[0] for ln in lines[1:]])
    check("复核判为图片书" in lines[1], "why 仍由人工 verdict 推出")

    print("\n== pdf_apply_review：没有复核 JSON 时四列为空")
    p = run([sys.executable, APPLY_PY, "--batch", os.path.join(tmp, "batch2"),
             "--triage", os.path.join(batch, "triage.csv"),
             "--out-dir", os.path.join(tmp, "out2"),
             "--review", os.path.join(tmp, "nope")])
    check(p.returncode == 0, "退出码 0")
    out2 = os.path.join(tmp, "out2", "triage_final.csv")
    with open(out2, encoding="utf-8") as fh:
        r = next(csv.DictReader(fh))
    check(r["model_route"] == "" and r["model_confidence"] == "" and r["model_at"] == "",
          "没有复核 JSON 时模型四列留空，不编造")


def make_db(path, books):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE book (uid TEXT, title TEXT, path TEXT, author TEXT, series TEXT)")
    con.execute("CREATE TABLE source (uid TEXT, file TEXT, format TEXT, sha256 TEXT,"
                " primary_src INTEGER)")
    for uid, title, path, pages_src, sha in books:
        con.execute("INSERT INTO book VALUES (?,?,?,?,?)", (uid, title, path, "作者甲", None))
        con.execute("INSERT INTO source VALUES (?,?,?,?,1)",
                    (uid, "库/%s/source/%s.pdf" % (path, uid), "pdf", sha))
    con.commit()
    con.close()


def load_batch_module():
    spec = importlib.util.spec_from_file_location("pmb_pdf", BATCH_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pmb_pdf"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_worklist(tmp):
    print("\n== pdf_md_batch：--uids 建 worklist、--triage 指定分诊表")
    mod = load_batch_module()
    db = os.path.join(tmp, "lib.db")
    books = [("B9101", "图册丙", "文学/丙", None, "sha-1"),
             ("B9102", "图册丁", "文学/丁", None, "sha-2"),
             ("B9103", "图册戊", "文学/戊", None, "sha-3"),
             ("B9104", "纯文本丁", "文学/丁", None, "sha-4")]
    make_db(db, books)
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    con.row_factory = sqlite3.Row

    tri = os.path.join(tmp, "triage_final.csv")
    cols = ["uid", "title", "subject1", "subject2", "class", "route", "pages", "mb"]
    write_csv(tri, cols, [
        {"uid": "B9101", "title": "图册丙", "subject1": "艺术与图像", "route": "OCR",
         "pages": "40", "class": "C 扫描件"},
        {"uid": "B9102", "title": "图册丁", "subject1": "艺术与图像", "route": "OCR",
         "pages": "10", "class": "C 扫描件"},
        {"uid": "B9103", "title": "图册戊", "subject1": "艺术与图像", "route": "OCR",
         "pages": "20", "class": "C 扫描件"},
        {"uid": "B9104", "title": "不归这批", "subject1": "文学", "route": "本地抽文本",
         "pages": "5", "class": "A 纯文本层"}])
    skip = os.path.join(tmp, "skip_list.tsv")
    with open(skip, "w", encoding="utf-8") as fh:
        fh.write(SKIP_HEADER + "\n")
        fh.write("B9103\t图册戊\t艺术与图像\t\t20\t1\t0.9\t复核判为图片书\t\tOCR\n")

    batch = os.path.join(tmp, "b1")
    os.makedirs(batch, exist_ok=True)
    all_rows = mod.build_worklist(con, batch, "ocr", triage=tri, skip=skip)
    check([r["uid"] for r in all_rows] == ["B9102", "B9101"],
          "不含 --uids 时排 route=OCR、跳过 skip_list、按页数升序，实际 %r"
          % [r["uid"] for r in all_rows])
    batch_a = os.path.join(tmp, "b1a")
    os.makedirs(batch_a, exist_ok=True)
    rows = mod.build_worklist(con, batch_a, "ocr", uids=["B9101", "B9102"],
                              triage=tri, skip=skip)
    check([r["uid"] for r in rows] == ["B9102", "B9101"],
          "--uids 收窄 + 按页数升序，实际 %r" % [r["uid"] for r in rows])
    wl = os.path.join(batch_a, "worklist.tsv")
    check(os.path.isfile(wl), "新批次写出 worklist.tsv")
    with open(wl, encoding="utf-8") as fh:
        head = fh.readline().rstrip("\n").split("\t")
        n = sum(1 for ln in fh if ln.strip())
    check(head == mod.WORKLIST_COLS, "worklist 表头是 WORKLIST_COLS")
    check(n == 2, "worklist 两行，实际 %s" % n)
    with open(wl, "rb") as fh:
        before = fh.read()
    again = mod.build_worklist(con, batch_a, "ocr", uids=["B9102"], triage=tri, skip=skip)
    with open(wl, "rb") as fh:
        after = fh.read()
    check(before == after, "点名的书都在账上时不重写 worklist")
    check([r["uid"] for r in again] == ["B9102"],
          "已有 worklist 时 --uids 收窄到点名的书，实际 %r" % [r["uid"] for r in again])

    print("\n== pdf_md_batch：账已有时补书、按 route 各跑各的")
    more = mod.build_worklist(con, batch_a, "text", uids=["B9104"], triage=tri, skip=skip)
    check([r["uid"] for r in more] == ["B9104"],
          "点名文本层的书补进同一份账，本次只跑它，实际 %r" % [r["uid"] for r in more])
    on_disk = mod.read_worklist(batch_a)
    check([r["uid"] for r in on_disk] == ["B9104", "B9102", "B9101"],
          "账按页数升序含三本，实际 %r" % [r["uid"] for r in on_disk])
    ocr_only = mod.build_worklist(con, batch_a, "ocr", triage=tri, skip=skip)
    check([r["uid"] for r in ocr_only] == ["B9102", "B9101"],
          "没点名时只跑账上这条 route 的书，实际 %r" % [r["uid"] for r in ocr_only])
    check([r["uid"] for r in mod.read_worklist(batch_a)] == ["B9104", "B9102", "B9101"],
          "没点名那次不往账里添书")
    try:
        mod.build_worklist(con, batch_a, "ocr", uids=["B9104"], triage=tri, skip=skip)
        check(False, "B9104 分的是文本层，跑 ocr 应当退出并报错")
    except SystemExit as exc:
        check("route=ocr" in str(exc), "报错说清是哪条线：%s" % exc)

    print("\n== pdf_md_batch：--uids 点名了库里没有的书 / 不转的书")
    try:
        mod.build_worklist(con, os.path.join(tmp, "b2"), "ocr", uids=["B9199"],
                           triage=tri, skip=skip)
        check(False, "B9199 应当退出并报错")
    except SystemExit as exc:
        check("B9199" in str(exc), "报错点名缺的 uid：%s" % exc)
    try:
        mod.build_worklist(con, os.path.join(tmp, "b2b"), "ocr", uids=["B9103"],
                           triage=tri, skip=skip)
        check(False, "B9103 在 skip_list 里，应当退出并报错")
    except SystemExit as exc:
        check("skip_list" in str(exc), "报错点名 skip_list：%s" % exc)
    con.close()


def test_report_json(tmp):
    print("\n== pdf_md_batch：--report-json")
    batch = os.path.join(tmp, "b3")
    os.makedirs(os.path.join(batch, "logs"), exist_ok=True)
    cols = ["uid", "title", "path", "src", "sha256", "format", "pages", "class", "verdict",
            "subject1", "subject2", "author", "series"]
    write_csv(os.path.join(batch, "worklist.tsv"), cols, [
        {"uid": "B9201", "title": "落地本", "path": "文学/一", "src": "库/x.pdf", "format": "pdf",
         "pages": "10"},
        {"uid": "B9202", "title": "待跑本", "path": "文学/二", "src": "库/y.pdf", "format": "pdf",
         "pages": "25"}], delim="\t")
    for uid, status in (("B9201", "landed"), ("B9202", "failed")):
        with open(os.path.join(batch, "logs", uid + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"uid": uid, "status": status, "pages": 1}, fh)
    p = run([sys.executable, BATCH_PY, "--batch", "unit", "--batch-dir", batch,
             "--report", "--report-json"])
    check(p.returncode == 0, "退出码 0")
    lines = [ln for ln in p.stdout.decode("utf-8", "replace").splitlines() if ln.strip()]
    doc = None
    try:
        doc = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        pass
    if not doc:
        check(False, "stdout 最后一行不是 JSON：%r" % lines[-1:])
        return
    check(doc.get("route") == "ocr", "route=ocr，实际 %r" % doc.get("route"))
    check(doc.get("queued") == 2, "queued=2，实际 %r" % doc.get("queued"))
    check(doc.get("landed") == 1, "landed=1，实际 %r" % doc.get("landed"))
    check(doc.get("remaining_books") == 1, "remaining_books=1，实际 %r"
          % doc.get("remaining_books"))
    check(doc.get("remaining_pages") == 25, "remaining_pages=25，实际 %r"
          % doc.get("remaining_pages"))
    check(doc.get("status_counts") == {"landed": 1, "failed": 1},
          "status_counts 对得上，实际 %r" % doc.get("status_counts"))

    batch4 = os.path.join(tmp, "b4")
    os.makedirs(os.path.join(batch4, "logs"), exist_ok=True)
    write_csv(os.path.join(batch4, "worklist.tsv"), cols, [
        {"uid": "B9201", "title": "落地本", "path": "文学/一", "src": "库/x.pdf",
         "format": "pdf", "pages": "10"},
        {"uid": "B9204", "title": "文本层本", "path": "文学/四", "src": "库/z.pdf",
         "format": "pdf", "pages": "12"}], delim="\t")
    tri4 = os.path.join(tmp, "triage4.csv")
    write_csv(tri4, ["uid", "title", "subject1", "subject2", "class", "route", "pages"], [
        {"uid": "B9201", "title": "落地本", "subject1": "文学", "route": "OCR",
         "pages": "10", "class": "C 扫描件"},
        {"uid": "B9204", "title": "文本层本", "subject1": "文学", "route": "本地抽文本",
         "pages": "12", "class": "A 纯文本层"}])
    for uid, status in (("B9201", "landed"), ("B9204", "failed")):
        with open(os.path.join(batch4, "logs", uid + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"uid": uid, "status": status, "pages": 1}, fh)
    p = run([sys.executable, BATCH_PY, "--batch", "unit4", "--batch-dir", batch4,
             "--report", "--report-json", "--route", "ocr", "--triage", tri4])
    check(p.returncode == 0, "按 route 出统计那次退出码 0（%s）" % out_text(p).strip()[-200:])
    lines = [ln for ln in p.stdout.decode("utf-8", "replace").splitlines() if ln.strip()]
    doc = None
    try:
        doc = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        pass
    if not doc:
        check(False, "只跑 ocr 那次 stdout 最后一行不是 JSON：%r" % lines[-1:])
        return
    check(doc.get("queued") == 1, "queued 只算本次 route，实际 %r" % doc.get("queued"))
    check(doc.get("landed") == 1, "landed 只算本次 route，实际 %r" % doc.get("landed"))
    check(doc.get("remaining_books") == 0, "remaining_books 只算本次 route，实际 %r"
          % doc.get("remaining_books"))
    check(doc.get("status_counts") == {"landed": 1},
          "另一条 route 的 failed 日志不进本次统计，实际 %r" % doc.get("status_counts"))


def main():
    if not os.path.isfile(VENV_PY):
        raise SystemExit("缺 %s（pymupdf）" % VENV_PY)
    if not os.path.isfile(STUB):
        raise SystemExit("缺 stub：%s" % STUB)
    roots = []
    try:
        test_compile()
        root = make_library(tempfile.mkdtemp(prefix="pdftriage-"))
        roots.append(root)
        text_res = test_triage_text(root)
        scan_res = test_triage_scan(root, text_res)
        test_triage_model(root, scan_res)
        test_triage_missing(root)

        tmp = tempfile.mkdtemp(prefix="pdfacct-")
        roots.append(tmp)
        test_apply_review(tmp)
        test_worklist(tmp)
        test_report_json(tmp)
    finally:
        for r in roots:
            shutil.rmtree(r, ignore_errors=True)
    if FAILED:
        print("\nFAIL %d 项：" % len(FAILED))
        for m in FAILED:
            print("  - " + m)
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
