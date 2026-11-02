#!/usr/bin/env python3
"""单本 PDF 分诊：普查 + 文本层/纸面探针 + 3 张缩略图 + 模型建议。只读源文件。

只写 --out 目录：census.jsonl、text.jsonl 或 scan.jsonl、thumbs/<页码>.png、result.json。
不写 triage_final.csv、skip_list.tsv、人账；把人工确认的路线写回账是 pdf_apply_review.py 的事。

pymupdf/numpy/PIL 只装在 工作区/.venv-pdf/，所以本脚本用那个解释器把自己重启一次，
--worker 是那次内部运行（普查、探针、缩略图都在里面做）；父进程只读它落盘的产物、拼指标、调模型。
--root 指向临时库时，若临时库里没有 .venv-pdf，仍用本仓的解释器。

模型只给建议，不改路线：route 永远由人工复核决定。

用法：
  python3 工作区/脚本/ops_pdf_triage.py --uid B1234 --out 工作区/运维/jobs/<id>
  python3 工作区/脚本/ops_pdf_triage.py --uid B1234 --out /tmp/j --root <临时库> --no-model
"""
import argparse
import csv
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L  # noqa: E402
from pdf_census import classify  # noqa: E402  纯函数，只吃 census 行，不需要 pymupdf
ROOT = L.ROOT

MODEL = "opencode-go/deepseek-v4.1-flash"
ROUTES = ("OCR", "本地抽文本", "不转-记图册", "人工确认", "日文暂缓")
SCAN_SAMPLE = 5
THUMB_EDGE = 1200
MODEL_TIMEOUT = 240
PROBE_TIMEOUT = 1800
TRIAGE_COLS = ["uid", "title", "subject1", "subject2", "class", "route", "pages",
               "mb", "ppi", "text_page_share", "ink", "bands", "note", "verdict",
               "image_share", "measure", "route_metric"]


def now():
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def read_jsonl(path):
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def write_jsonl(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def write_json(path, doc):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, path)


def find_book(root, uid):
    """库/**/book.json 里按 uid 取书。_ 开头的目录不是账上的书，跳过。"""
    lib = os.path.join(root, "books")
    for dirpath, dirnames, filenames in os.walk(lib):
        dirnames[:] = [d for d in dirnames if not d.startswith("_")]
        if "book.json" not in filenames:
            continue
        try:
            with open(os.path.join(dirpath, "book.json"), encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if doc.get("uid") == uid:
            return doc
    return None


def source_path(root, doc):
    """源文件在 v2 里是 books/ 或 collections/ 下的根相对路径。"""
    srcs = [s for s in doc.get("sources") or [] if (s.get("format") or "").lower() == "pdf"]
    primary = [s for s in srcs if s.get("primary_src")] or srcs
    if not primary:
        return ""
    f = primary[0].get("file") or ""
    if f.startswith(("books/", "collections/")):
        return os.path.join(root, f)
    owner = primary[0].get("collection") or doc.get("uid") or ""
    top = "collections" if owner.startswith("C") else "books"
    return os.path.join(root, top, owner, "source", os.path.basename(f))


def venv_python(root):
    env = os.environ.get("BOOKS_PDF_PY")
    if env and os.path.isfile(env):
        return env
    local = os.path.join(root, "工作区", ".venv-pdf", "bin", "python")
    if os.path.isfile(local):
        return local
    return os.path.join(ROOT, "工作区", ".venv-pdf", "bin", "python")


def thumb_pages(pages):
    """首页、正文中页、末页。页数少时去重，短书只有一两张图也不报错。"""
    return sorted({1, (pages + 1) // 2, pages})


def thumb_name(page):
    return str(page).zfill(3)


def render_thumbs(pdf, out, pages):
    import pymupdf
    tdir = os.path.join(out, "thumbs")
    os.makedirs(tdir, exist_ok=True)
    doc = pymupdf.open(pdf)
    try:
        for pno in thumb_pages(pages):
            page = doc.load_page(pno - 1)
            edge = max(page.rect.width, page.rect.height) or 1
            zoom = min(THUMB_EDGE / edge, 3.0)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom),
                                  colorspace=pymupdf.csRGB)
            final = os.path.join(tdir, thumb_name(pno) + ".png")
            tmp = final + ".tmp.png"
            pix.save(tmp)
            os.replace(tmp, final)
    finally:
        doc.close()


def worker(root, uid, out, pdf, sample=SCAN_SAMPLE):
    """内部运行：跑普查、按类型选探针、渲 3 张缩略图。只写 out 目录。

    探针或缩略图炸了不拖住整本：census 照写，错误集进 errors 字段，剩下的交给人工看。
    """
    import pdf_census
    import pdf_scan_probe
    import pdf_text_probe

    doc = find_book(root, uid) or {}
    subs = doc.get("subjects") or []
    row = pdf_census.measure(uid, doc.get("title") or "", subs[0] if subs else "",
                             subs[1] if len(subs) > 1 else "", pdf, os.path.getsize(pdf))
    pages = int(row.get("pages") or 0)
    if pages <= 0:
        write_jsonl(os.path.join(out, "census.jsonl"), [row])
        sys.stderr.write("普查拿不到页数：%s\n" % (row.get("errors") or "无错误信息"))
        return 1
    cls = pdf_census.classify(row)
    errs = []
    if cls.startswith(("A", "B")):
        try:
            probe = pdf_text_probe.probe(pdf)
            probe.update({"uid": uid, "title": doc.get("title") or ""})
            write_jsonl(os.path.join(out, "text.jsonl"), [probe])
        except Exception as exc:
            errs.append("文本层探针失败 %s: %s" % (type(exc).__name__, exc))
    elif cls.startswith("C"):
        try:
            probe = pdf_scan_probe.probe(pdf, pages, sample)
            probe.update({"uid": uid, "title": doc.get("title") or ""})
            write_jsonl(os.path.join(out, "scan.jsonl"), [probe])
        except Exception as exc:
            errs.append("纸面探针失败 %s: %s" % (type(exc).__name__, exc))
    try:
        render_thumbs(pdf, out, pages)
    except Exception as exc:
        errs.append("缩略图失败 %s: %s" % (type(exc).__name__, exc))
    if errs:
        row["errors"] = list(row.get("errors") or []) + errs
    write_jsonl(os.path.join(out, "census.jsonl"), [row])
    return 0


def build_metrics(census, probe):
    """给页面看的指标。文本层书的 ink/bands 无意义，留 null。"""
    share = None
    if probe and probe.get("text_page_share") is not None:
        share = round(float(probe["text_page_share"]), 2)
    elif census.get("text_page_ratio") is not None:
        share = round(float(census["text_page_ratio"]), 3)
    bands = probe.get("bands_median") if probe else None
    if isinstance(bands, float) and bands.is_integer():
        bands = int(bands)
    m = {
        "text_page_share": share,
        "ink": probe.get("ink_median") if probe else None,
        "bands": bands,
        "image_share": None if share is None else round(1 - share, 2),
        "text_page_ratio": census.get("text_page_ratio"),
        "chars_per_page": census.get("chars_per_page"),
        "bad_char_ratio": probe.get("bad_char_ratio") if probe else None,
        "toc_entries": probe.get("toc_entries") if probe else None,
        "img_ppi_median": census.get("img_ppi_median"),
        "full_page_images": census.get("full_page_images"),
        "pages_with_text": census.get("pages_with_text"),
    }
    return m


PROMPT = """你是书库的 PDF 分诊助手。三张缩略图依次是首页、正文中页、末页。看图与指标，判断这本书该走哪条转换路线。

书名：{title}
学科：{subject1} / {subject2}
页数：{pages}
普查类型：{cls}
指标：{metrics}

路线词表，只能原样选一个：
OCR
本地抽文本
不转-记图册
人工确认
日文暂缓

规则：
- 文本层覆盖高、乱码率低，选「本地抽文本」。
- 没有文本层，但抽页能看到成行文字，选「OCR」。
- 整页图版、画册、写真、杂志，几乎没有文字，选「不转-记图册」。
- 拿不准选「人工确认」。
- 语言探针判为日文时优先「日文暂缓」。

只输出一行 JSON，不要解释，不要代码块：
{{"route":"OCR","confidence":0.0,"reason":"一句话理由"}}
"""


def build_prompt(doc, census, cls, metrics):
    subs = doc.get("subjects") or []
    line = " ".join("%s=%s" % (k, "-" if v is None else v) for k, v in metrics.items())
    return PROMPT.format(title=doc.get("title") or "", subject1=subs[0] if subs else "",
                         subject2=subs[1] if len(subs) > 1 else "",
                         pages=census.get("pages"), cls=cls, metrics=line)


def pi_prefix():
    """PI_BIN 指向 .py 的测试 stub 时用当前解释器跑；否则当可执行文件。"""
    bin_ = os.environ.get("PI_BIN") or "pi"
    return [sys.executable, bin_] if bin_.endswith(".py") else [bin_]


def parse_model(text):
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = dec.raw_decode(text[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "route" in obj:
            return normalize_model(obj)
    return None


def normalize_model(obj):
    route = str(obj.get("route") or "").strip()
    try:
        conf = round(float(obj.get("confidence")), 2)
    except (TypeError, ValueError):
        conf = None
    out = {"route": route, "confidence": conf,
           "reason": str(obj.get("reason") or "").strip(), "at": now()}
    if route not in ROUTES:
        out["warn"] = "route 不在词表：%s" % route
    return out


def ask_model(out, thumbs, prompt):
    argv = pi_prefix() + ["-p", "--no-tools", "--no-session", "--no-context-files",
                          "--no-skills", "--no-extensions", "--model", MODEL]
    argv += ["@" + t for t in thumbs] + [prompt]
    try:
        p = subprocess.run(argv, cwd=out, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=MODEL_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, "模型超时 %d 秒" % MODEL_TIMEOUT
    except OSError as exc:
        return None, "模型没跑起来：%s" % exc
    text = p.stdout.decode("utf-8", "replace")
    model = parse_model(text)
    if model:
        return model, ""
    detail = (p.stderr.decode("utf-8", "replace") or text).strip()[-200:]
    return None, "pi rc=%d，回话里没有 JSON：%s" % (p.returncode, detail or "空")


def write_triage(out, result, census, metrics):
    """给 pdf_apply_review.py 的一行分诊表。route 只作模型建议，人工确认在 review 里。"""
    model = result.get("model") or {}
    route = model.get("route") or "人工确认"
    row = {
        "uid": result["uid"], "title": result["title"],
        "subject1": census.get("subject1") or "",
        "subject2": census.get("subject2") or "",
        "class": result["class"], "route": route,
        "pages": result["pages"],
        "mb": round(float(census.get("bytes") or 0) / 1000000.0, 1),
        "ppi": metrics.get("img_ppi_median") or "",
        "text_page_share": metrics.get("text_page_share"),
        "ink": metrics.get("ink"), "bands": metrics.get("bands"),
        "note": model.get("reason") or "ops 分诊",
        "verdict": "",
        "image_share": metrics.get("image_share"),
        "measure": "ops 分诊", "route_metric": route,
    }
    for key in ("text_page_share", "ink", "bands", "image_share"):
        if row[key] is None:
            row[key] = ""
    path = os.path.join(out, "triage.csv")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=TRIAGE_COLS)
        writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in TRIAGE_COLS})
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--uid", required=True)
    ap.add_argument("--out", required=True, help="输出目录，相对路径按 --root 解析")
    ap.add_argument("--root", default=ROOT, help="书库根，测试指到临时库")
    ap.add_argument("--no-model", dest="model", action="store_false",
                    help="只出指标与缩略图，不调模型")
    ap.add_argument("--pdf", help=argparse.SUPPRESS)
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()

    root = os.path.abspath(a.root)
    out = a.out if os.path.isabs(a.out) else os.path.join(root, a.out)
    os.makedirs(out, exist_ok=True)
    if a.worker:
        return worker(root, a.uid, out, a.pdf)

    # 同一 out 重跑时先清上一次的探针与缩略图，免得旧图混进这次的结果
    for stale in ("text.jsonl", "scan.jsonl", "census.jsonl", "result.json"):
        if os.path.exists(os.path.join(out, stale)):
            os.remove(os.path.join(out, stale))
    shutil.rmtree(os.path.join(out, "thumbs"), ignore_errors=True)
    doc = find_book(root, a.uid)
    if not doc:
        sys.exit("找不到 uid=%s 的书（%s/books/**/book.json）" % (a.uid, root))
    pdf = source_path(root, doc)
    if not pdf or not os.path.isfile(pdf):
        sys.exit("要分诊的 pdf 源不在：%s" % (pdf or "book.json 里没有 pdf 源"))
    py = venv_python(root)
    if not os.path.isfile(py):
        sys.exit("缺带 pymupdf 的解释器：%s" % py)

    cmd = [py, os.path.abspath(__file__), "--worker", "--root", root, "--uid", a.uid,
           "--out", out, "--pdf", pdf]
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        sys.exit("探针超时 %d 秒：%s" % (PROBE_TIMEOUT, pdf))
    rows = read_jsonl(os.path.join(out, "census.jsonl"))
    if p.returncode != 0 or not rows:
        tail = (p.stderr.decode("utf-8", "replace") or p.stdout.decode("utf-8", "replace"))
        sys.exit("探针跑失败（rc=%d）：%s" % (p.returncode, tail.strip()[-800:] or "没有产物"))

    census = rows[0]
    cls = classify(census)
    pages = int(census.get("pages") or 0)
    probe_file = "text.jsonl" if cls.startswith(("A", "B")) else "scan.jsonl"
    probes = read_jsonl(os.path.join(out, probe_file))
    probe = probes[0] if probes else None
    metrics = build_metrics(census, probe)
    tdir = os.path.join(out, "thumbs")
    thumbs = sorted("thumbs/" + n for n in os.listdir(tdir) if n.endswith(".png")) \
        if os.path.isdir(tdir) else []

    prompt = build_prompt(doc, census, cls, metrics)

    result = {
        "uid": a.uid,
        "title": doc.get("title") or census.get("title") or "",
        "path": os.path.relpath(pdf, root),
        "pages": pages,
        "class": cls,
        "metrics": metrics,
        "thumbs": thumbs,
        "model": None,
        "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "at": now(),
        "census": "census.jsonl",
        "probe": probe_file if probe else "",
        "errors": census.get("errors") or [],
    }
    if not thumbs:
        result["errors"] = list(result["errors"]) + ["缩略图没生成"]
    if a.model and thumbs:
        model, err = ask_model(out, thumbs, prompt)
        result["model"] = model
        if err:
            result["model_error"] = err
    write_triage(out, result, census, metrics)
    result["triage"] = "triage.csv"
    write_json(os.path.join(out, "result.json"), result)

    m = metrics
    print("%s %s 页 %s %s 指标 text_page_share=%s ink=%s bands=%s 图 %d 张"
          % (a.uid, result["title"][:30], pages, cls, m.get("text_page_share"),
             m.get("ink"), m.get("bands"), len(thumbs)))
    if result.get("model"):
        mm = result["model"]
        print("模型建议 %s 置信 %s %s" % (mm.get("route"), mm.get("confidence"),
                                          mm.get("reason", "")[:60]))
    elif result.get("model_error"):
        print("模型没回 JSON：%s" % result["model_error"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
