#!/usr/bin/env python3
"""L2 收尾：把同名冲突按确定性规则改好，写出最终人账，并校验路径唯一。

用法: python3 l2_finalize.py
读:  目录/分类表.csv（人账）、目录/书目录清单.csv（机器字段）
写:  目录/分类表.csv（正式书名加限定词）、目录/路径冲突.csv（带结论）
"""
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_common as L  # noqa: E402


def path_safe(s):
    return re.sub(r"[\\/:*?\"<>|\n\t]", "／", (s or "").strip()).strip(" .")


def main():
    ledger = L.read_csv(os.path.join(L.CAT, "分类表.csv"))
    pre = {r["uid"]: r for r in L.read_csv(os.path.join(L.CAT, "分类预填.csv"))}
    inv = {r["uid"]: r for r in L.read_csv(os.path.join(L.CAT, "书目录清单.csv"))}
    assert ledger, "先跑 scan_library.py 与 classify_books.py --merge"
    for r in ledger:                      # 只修本脚本自己加过的后缀，人改名不回滚
        note = r.get("备注") or ""
        tag = re.search(r"同名冲突加限定词（[^）]*）", note)
        if tag:
            r["正式书名"] = r["正式书名"].replace(
                "（%s）" % tag.group(0)[len("同名冲突加限定词（"):-1], "")
        elif not (r.get("正式书名") or "").strip() and r["uid"] in pre:
            r["正式书名"] = pre[r["uid"]].get("正式书名") or ""
        if r["uid"] in pre and not (r.get("作者层") or "").strip():
            r["作者层"] = pre[r["uid"]].get("作者层") or ""
        r["备注"] = re.sub(r"；?同名冲突加限定词（[^）]*）", "", note)

    groups = defaultdict(list)
    for r in ledger:
        m = inv.get(r["uid"], {})
        key = "/".join(x for x in (r["一级"], r["二级"],
                                   path_safe(m.get("作者")) if r["作者层"] == "1" else "",
                                   path_safe(m.get("容器")), path_safe(r["正式书名"])) if x)
        r["_path"] = key
        groups[key].append(r)

    resolved, rows = [], []
    for key, rs in sorted(groups.items()):
        if len(rs) == 1:
            continue
        keep = rs[0]
        for r in rs[1:]:
            m = inv.get(r["uid"], {})
            mo = inv.get(keep["uid"], {})
            suffix = ""
            if (m.get("format") or "") != (mo.get("format") or ""):
                suffix = "（%s）" % (m.get("format") or "副本")
            elif (m.get("作者") or "") and (m.get("作者") or "") != (mo.get("作者") or ""):
                suffix = "（%s）" % path_safe(m["作者"])
            else:
                suffix = "（%d）" % (rs.index(r) + 1)
            r["正式书名"] = r["正式书名"] + suffix
            r["备注"] = "；".join(x for x in (r["备注"], "同名冲突加限定词" + suffix) if x)
            resolved.append({"提议路径": key, "uid": r["uid"], "正式书名": r["正式书名"],
                             "来源": m.get("src_path", ""), "结论": "加限定词" + suffix})
    L.write_csv(os.path.join(L.CAT, "路径冲突.csv"), resolved,
                ["提议路径", "uid", "正式书名", "来源", "结论"])

    for r in ledger:
        m = inv.get(r["uid"], {})
        r["_path"] = "/".join(x for x in (r["一级"], r["二级"],
                                          path_safe(m.get("作者")) if r["作者层"] == "1" else "",
                                          path_safe(m.get("容器")), path_safe(r["正式书名"])) if x)
        rows.append({k: r.get(k, "") for k in L.LEDGER_HEADER})

    dupes = defaultdict(list)
    for r in ledger:
        dupes[r["_path"]].append(r["uid"])
    bad = {k: v for k, v in dupes.items() if len(v) > 1}
    print("人账 %d 行，路径唯一 %d 条，加限定词 %d 处" % (len(rows), len(dupes), len(resolved)))
    if bad:
        print("仍有冲突 %d 组:" % len(bad))
        for k, v in list(bad.items())[:10]:
            print("   ", k, v)
        raise SystemExit(1)
    L.write_csv(os.path.join(L.CAT, "分类表.csv"), rows, L.LEDGER_HEADER)
    print("已写 分类表.csv；接着跑 build_library.py --emit-json 重建 DB 与 book.json")
    print("最长路径 %d 字符，例如 %s" % (max(len(k) for k in dupes),
                                        max(dupes, key=len)[:110]))
    print("一级分布:", {k: sum(1 for r in rows if r["一级"] == k)
                    for k in sorted({r["一级"] for r in rows})})


if __name__ == "__main__":
    main()
