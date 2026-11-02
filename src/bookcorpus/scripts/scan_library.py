#!/usr/bin/env python3
"""L1 扫描旧布局（原书/ 原书pdf/ 书md/ 等顶层池）。旧目录已不在，只作留档。

当前库请用 build_library.py --emit-json。本脚本扫到 0 个源文件时直接退出，不覆盖
目录/ 下的任何账（源文件清单/书目录清单/分类预填/重复/损坏/路径冲突）。

用法: python3 scan_library.py [--no-hash]
产出:
  源文件清单.csv   一行一个源文件（1392 行）
  书目录清单.csv   一行一本书记录（合集按卷展开）
  分类预填.csv     人账初稿，含预填依据
  重复.csv 损坏.csv 路径冲突.csv
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L  # noqa: E402

PROMO = ("！", ";", "；", "全新", "推荐", "出品", "倾力", "力作", "不朽", "必读", "震撼", "经典",
         "典藏", "巨著", "畅销", "豆瓣", "大奖", "奖得主", "诚意", "口碑", "现象级", "亲笔")
SERIES_DOC = os.path.join(L.ROOT, "书md")
BOOK_HEADER = ["uid", "一级", "二级", "作者", "作者层", "容器", "正式书名", "系列", "卷序",
               "src_path", "额外源", "md_dir", "md_chapters", "md_bytes", "html_book", "format", "bytes",
               "sha256", "备注", "预填依据"]


def clean_title_v2(filename):
    base = L.clean_title(filename)
    if base.startswith("《") and "》" in base:
        base = base[1:base.index("》")]
    changed = True
    while changed:
        changed = False
        m = re.search(r"[（(]([^（）()]*)[）)]$", base)
        if m:
            inner = m.group(1)
            if len(inner) <= 24 or any(p in inner for p in PROMO):
                base = base[:m.start()].strip(" -–—_·,，。")
                changed = True
    base = re.sub(r"(著|译|編|编)$", "", base).strip(" -–—_·,，。")
    return (base[:60].rstrip() or L.clean_title(filename))


def scan_source_files(no_hash=False):
    import concurrent.futures as cf

    items = []
    for pool, award, src_path in L.iter_source_files():
        fp = os.path.join(L.ROOT, src_path)
        fn = os.path.basename(src_path)
        fmt = os.path.splitext(fn)[1].lstrip(".").lower()
        title_clean = clean_title_v2(fn)
        s1, s2, why = L.prefill_subject(pool, fn, award, title_clean, L.author_guess(fn, title_clean))
        bad, reason = L.check_file(fp, fmt)
        items.append({"pool": pool, "award": award or "", "src_path": src_path, "file": fn,
                      "format": fmt, "bytes": os.path.getsize(fp), "title_clean": title_clean,
                      "author_guess": L.author_guess(fn, title_clean),
                      "一级": s1, "二级": s2, "预填依据": why,
                      "损坏原因": reason if bad else ""})

    def work(it):
        it["sha256"] = "" if no_hash else L.sha256_file(os.path.join(L.ROOT, it["src_path"]))
        return it

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        items = list(ex.map(work, items))
    return items


def scan_md_corpus():
    by_src = defaultdict(list)
    meta = {}
    if not os.path.isdir(SERIES_DOC):
        return by_src, meta
    for name in sorted(os.listdir(SERIES_DOC)):
        p = os.path.join(SERIES_DOC, name)
        if not os.path.isdir(p):
            continue
        rec = {"md_dir": name, "src": "", "author": "", "collection": "", "chapters": 0, "bytes": 0}
        rp = os.path.join(p, "README.md")
        if os.path.isfile(rp):
            txt = open(rp, encoding="utf-8", errors="ignore").read()
            for key, field in (("来源", "src"), ("作者", "author"), ("合集", "collection")):
                m = re.search(key + r"：`?([^`\n]+)`?", txt)
                if m:
                    rec[field] = m.group(1).strip()
        files = [f for f in os.listdir(p) if f.endswith(".md")]
        rec["chapters"] = len([f for f in files if f != "README.md"])
        rec["bytes"] = sum(os.path.getsize(os.path.join(p, f)) for f in files)
        meta[name] = rec
        if rec["src"]:
            by_src[rec["src"]].append(name)
    return by_src, meta


def scan_series_docs():
    """合集说明 -> {来源文件名: [卷名 按阅读顺序]}"""
    out = {}
    if not os.path.isdir(SERIES_DOC):
        return out
    for fn in sorted(os.listdir(SERIES_DOC)):
        if not fn.endswith("合集说明.md"):
            continue
        txt = open(os.path.join(SERIES_DOC, fn), encoding="utf-8", errors="ignore").read()
        m = re.search(r"来源：`(.+?)`", txt)
        if not m:
            continue
        vols = re.findall(r"^- \[(.+?)\]\(.+?\)", txt, re.M)
        out[m.group(1).strip()] = vols
    return out


def common_head(names):
    heads = []
    for n in names:
        head = re.split(r"[-—–]", n, maxsplit=1)[0].strip() if re.search(r"[-—–]", n) else ""
        heads.append(head)
    if heads and heads[0] and all(h == heads[0] for h in heads):
        return heads[0]
    return ""


def build_records(sources, by_src, md_meta, series_docs):
    records = []
    for s in sources:
        dirs = by_src.get(s["file"], [])
        order = series_docs.get(s["file"], [])
        if order:
            dirs = sorted(dirs, key=lambda d: order.index(d) if d in order else 10 ** 6)
        if len(dirs) >= 2:
            container = common_head(dirs)
            if not container:
                container = re.sub(r"（.*?）|\s*\[.*?\]|（共.*?册）", "", s["title_clean"]).strip()
                container = re.sub(r"（(共)?\d+.*?册?）", "", container).strip() or s["title_clean"]
        else:
            container = ""
        if not dirs:
            records.append({"src": s, "md_dir": "", "title": s["title_clean"],
                            "author": s["author_guess"], "series": "", "vol": "", "container": ""})
            continue
        if len(dirs) == 1:
            d = dirs[0]
            m = md_meta.get(d, {})
            records.append({"src": s, "md_dir": d, "title": d,
                            "author": L.clean_author(m.get("author", "") or s["author_guess"]),
                            "series": m.get("collection", ""), "vol": "", "container": ""})
            continue
        for i, d in enumerate(dirs, 1):
            m = md_meta.get(d, {})
            vol = d
            if container and d.startswith(container):
                vol = re.split(r"[-—–]", d, maxsplit=1)[-1].strip() if re.search(r"[-—–]", d) else d
            records.append({"src": s, "md_dir": d, "title": vol or d,
                            "author": L.clean_author(m.get("author", "") or s["author_guess"]),
                            "series": container, "vol": str(i), "container": container})
    # md 目录按目录名唯一归属。两个源文件同名时（md README 的「来源」只记文件名），
    # 先出现的源（池子顺序：原书 在前）认领，后出现的记 pending 并留备注。
    seen_md = {}
    for r in records:
        d = r["md_dir"]
        if not d:
            continue
        if d in seen_md:
            r["md_lost_to"] = seen_md[d]
            r["md_dir"] = ""
        else:
            seen_md[d] = r
    for r in records:
        if r["md_dir"]:
            m = md_meta.get(r["md_dir"], {})
            r["md_chapters"], r["md_bytes"] = m.get("chapters", ""), m.get("bytes", "")
        else:
            r["md_chapters"], r["md_bytes"] = "", ""
    counts = Counter(L.norm(r["author"]) for r in records if r["author"])
    for r in records:
        a = r["author"]
        layer = "1" if a and counts[L.norm(a)] >= 3 else "0"
        if layer == "1" and r["container"] and L.norm(r["container"]) == L.norm(a):
            r["container"] = ""      # 容器与作者同名时不套两层
        r["作者层"] = layer
    # uid 分配：优先沿用已有清单里的同名同源记录
    return records


def assign_uids(records, old_by_key, next_uid):
    for r in records:
        key = (r["src"]["sha256"] or r["src"]["src_path"], r["md_dir"] or r["title"])
        uid = old_by_key.get(key)
        if not uid:
            uid = "B%04d" % next_uid
            next_uid += 1
        r["uid"] = uid
        old_by_key[key] = uid
    return next_uid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-hash", action="store_true")
    args = ap.parse_args()

    old = L.read_csv(os.path.join(L.TABLES, "书目录清单.csv"))
    old_by_key = {}
    for r in old:
        old_by_key[(r.get("sha256") or r.get("src_path"), r.get("md_dir") or r.get("正式书名"))] = r["uid"]
    next_uid = 1
    for uid in {r.get("uid", "") for r in old}:
        m = re.fullmatch(r"B(\d+)", uid or "")
        if m:
            next_uid = max(next_uid, int(m.group(1)) + 1)

    sources = scan_source_files(args.no_hash)
    if not sources:
        raise SystemExit(
            "扫描到 0 个源文件：顶层的旧池目录（原书/ 原书pdf/ 书md/ …）已不在。"
            "本脚本只服务迁移前的 L1 旧布局，当前库请用 build_library.py --emit-json。"
            "未写任何文件。")
    by_src, md_meta = scan_md_corpus()
    series_docs = scan_series_docs()
    if not md_meta and not series_docs:
        print("警告：书md/ 与 系列/ 都没扫到，md_dir 与 系列列会全空")
    if os.path.isfile(os.path.join(L.TABLES, "路径冲突.csv")):
        print("警告：将覆盖已有的 路径冲突.csv（旧 schema），L2 后的人工结论请先另存")
    records = build_records(sources, by_src, md_meta, series_docs)
    assign_uids(records, old_by_key, next_uid)

    rows = []
    for r in records:
        s = r["src"]
        rows.append({
            "uid": r["uid"], "一级": s["一级"], "二级": s["二级"], "作者": r["author"],
            "作者层": r["作者层"], "容器": r["container"], "正式书名": r["title"],
            "系列": r["series"], "卷序": r["vol"], "src_path": s["src_path"], "额外源": "",
            "md_dir": r["md_dir"],
            "md_chapters": r["md_chapters"], "md_bytes": r["md_bytes"], "html_book": "",
            "format": s["format"], "bytes": s["bytes"], "sha256": s["sha256"],
            "备注": "；".join(x for x in (
                ("奖项:" + s["award"]) if s["award"] else "",
                ("同名源共用 md 目录，md 归 " + (r["md_lost_to"]["uid"] or "先出现的源"))
                if r.get("md_lost_to") else "") if x),
            "预填依据": s["预填依据"] + (("；合集拆出 " + r["vol"] + "/" + str(
                len([1 for x in records if x["src"] is s]))) if r["vol"] else ""),
        })
    # 同一本书的多份源合并成一行，源文件都进 source/，uid 只一个
    merged, order = {}, []
    for r in rows:
        key = (r["sha256"] or r["src_path"], r["md_dir"] or r["正式书名"])
        if key in merged:
            primary = merged[key]
            extra = r["src_path"]
            prev = json.loads(primary["额外源"]) if primary["额外源"] else []
            primary["额外源"] = json.dumps(prev + [extra], ensure_ascii=False)
            primary["备注"] = "；".join(x for x in (primary["备注"], "同一书的第二份源") if x)
        else:
            merged[key] = r
            order.append(r)
    if len(order) != len(rows):
        print("同一本书多份源合并 %d 行" % (len(rows) - len(order)))
    rows = order
    L.write_csv(os.path.join(L.TABLES, "源文件清单.csv"), [
        {"src_path": s["src_path"], "file": s["file"], "format": s["format"], "bytes": s["bytes"],
         "sha256": s["sha256"], "award": s["award"], "pool": s["pool"], "卷数": len(by_src.get(s["file"], [])),
         "title_clean": s["title_clean"], "author_guess": s["author_guess"], "损坏原因": s["损坏原因"],
         "一级预填": s["一级"], "二级预填": s["二级"]} for s in sources],
        ["src_path", "file", "format", "bytes", "sha256", "award", "pool", "卷数", "title_clean",
         "author_guess", "损坏原因", "一级预填", "二级预填"])
    L.write_csv(os.path.join(L.TABLES, "书目录清单.csv"), rows, BOOK_HEADER)
    L.write_csv(os.path.join(L.TABLES, "分类预填.csv"), rows, L.LEDGER_HEADER + ["预填依据"])

    dup = defaultdict(list)
    for s in sources:
        if s["sha256"]:
            dup[s["sha256"]].append(s)
    L.write_csv(os.path.join(L.TABLES, "重复.csv"), [
        {"sha256": h[:16], "文件数": len(v),
         "src_path": json.dumps([x["src_path"] for x in v], ensure_ascii=False)}
        for h, v in dup.items() if len(v) > 1], ["sha256", "文件数", "src_path"])
    L.write_csv(os.path.join(L.TABLES, "损坏.csv"), [
        {"src_path": s["src_path"], "format": s["format"], "bytes": s["bytes"],
         "原因": s["损坏原因"]} for s in sources if s["损坏原因"]],
        ["src_path", "format", "bytes", "原因"])

    groups = defaultdict(list)
    for r in rows:
        key = "/".join(x for x in (r["一级"], r["二级"], r["作者"] if r["作者层"] == "1" else "",
                                   r["容器"], r["正式书名"]) if x)
        r["提议路径"] = key
        groups[key].append(r)
    conflicts = []
    for key, rs in sorted(groups.items()):
        if len(rs) < 2:
            continue
        hashes = {r["sha256"] for r in rs if r["sha256"]}
        for r in rs:
            conflicts.append({"提议路径": key, "uid": r["uid"], "src_path": r["src_path"],
                              "md_dir": r["md_dir"], "sha256前16": (r["sha256"] or "")[:16],
                              "处置": "同源重复，保留第一个 uid，其余记入 重复.csv" if len(hashes) == 1
                              else "不同源同名，正式书名加限定词"})
    L.write_csv(os.path.join(L.TABLES, "路径冲突.csv"), conflicts,
                ["提议路径", "uid", "src_path", "md_dir", "sha256前16", "处置"])

    print("源文件 %d 个，书记录 %d 条（合集 %d 个源拆出 %d 卷）"
          % (len(sources), len(rows), len([1 for s in sources if len(by_src.get(s["file"], [])) >= 2]),
             len([1 for r in rows if r["卷序"]])))
    print("有 md 的记录 %d 条，缺 md 的记录 %d 条"
          % (len([1 for r in rows if r["md_dir"]]), len([1 for r in rows if not r["md_dir"]])))
    print("可疑文件 %d，重复源 %d 组，提议路径冲突 %d 组（%d 行）"
          % (len([1 for s in sources if s["损坏原因"]]), len([1 for v in dup.values() if len(v) > 1]),
             len({c["提议路径"] for c in conflicts}), len(conflicts)))
    print("学科分布:", dict(Counter(r["一级"] for r in rows).most_common()))
    print("建作者层的记录 %d 条，涉及作者 %d 人"
          % (len([1 for r in rows if r["作者层"] == "1"]),
             len({L.norm(r["作者"]) for r in rows if r["作者层"] == "1"})))


if __name__ == "__main__":
    main()
