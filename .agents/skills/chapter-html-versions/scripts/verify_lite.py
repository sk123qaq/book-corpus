#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""章节 HTML 轻量校验（v2）：纯 Python，不依赖浏览器。

用法:
    python3 verify_lite.py <html...> --src <章节.md> [--cover 0.9] [--min-kb 8]

检查四类（FAIL 必须修）：
  1. 结构：DOCTYPE / </html> 结尾 / html lang / charset / viewport / <style> /
           无 http(s) 外链 / 无 ``` 围栏 / 无残留 {{占位符}} / 体积 ≥ --min-kb
  2. 标题覆盖：源 md 的 H2/H3（去编号后包含匹配）命中率 ≥ --cover
  3. 居中：CSS 中必须存在「max-width + margin auto」的容器规则
           —— 防的是"宽屏下正文全部贴左边"这个真实 bug
  4. 图片：HTML 里引用的本地图片文件必须存在

另给 INFO（不判 FAIL）：图表密度（figure/svg/table/details 数 ÷ 正文字数）。

退出码 0 = 全 PASS。
"""
import argparse
import os
import re
import sys
from html.parser import HTMLParser

SKIP = {"style", "script"}


class Grab(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, t, a):
        if t in SKIP:
            self.skip += 1

    def handle_endtag(self, t):
        if t in SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, d):
        if not self.skip:
            self.parts.append(d)


def norm(s):
    for ch in "\u2013\u2014\u2212\u2012\u2015":
        s = s.replace(ch, "-")
    return re.sub(r"[\s\u200b\u200e\u200f\u00a0]+", "", s)


def md_headings(src):
    out = []
    for line in open(src, encoding="utf-8", errors="replace"):
        m = re.match(r"^(#{2,3})\s+(.+?)\s*#*\s*$", line)
        if m:
            out.append(m.group(2))
    return out


def hit(h, ntext, grams3):
    nh = norm(h)
    core = norm(re.sub(r"^[0-9０-９]+\s*", "", h))
    if nh in ntext or (core and core in ntext):
        return True
    g = set(nh[i:i + 3] for i in range(max(0, len(nh) - 2)))
    if not g:
        return True
    return len(g & grams3) / len(g) >= 0.8


CENTER_AUTO = re.compile(r"margin(?:-inline)?\s*:\s*(?:0\s+)?auto|margin-(?:left|right)\s*:\s*auto")
MAXW = re.compile(r"max-width\s*:\s*[^;]+")
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def centering(style):
    """返回带 max-width + auto margin 的选择器列表。"""
    ok = []
    for sel, body in RULE.findall(style):
        if MAXW.search(body) and CENTER_AUTO.search(body):
            sel = sel.strip().split("\n")[-1].strip()
            if sel and not sel.startswith("@"):
                ok.append(sel)
    return ok


def check(path, src, cover, min_kb):
    res = []
    raw = open(path, encoding="utf-8", errors="replace").read()
    size = len(raw.encode("utf-8"))
    res.append(("PASS" if size >= min_kb * 1024 else "FAIL", "体积",
                "%.1f KB" % (size / 1024) + ("" if size >= min_kb * 1024 else "（< %dKB，疑似截断）" % min_kb)))
    res.append(("PASS" if raw.lstrip()[:15].lower().startswith("<!doctype html>") else "FAIL", "DOCTYPE", ""))
    res.append(("PASS" if raw.rstrip().lower().endswith("</html>") else "FAIL", "结尾 </html>",
                "" if raw.rstrip().lower().endswith("</html>") else "文件被截断"))
    for pat, name in ((r'<html[^>]+lang\s*=\s*["\']zh', "html lang"),
                      (r"<meta[^>]+charset", "charset"),
                      (r"<meta[^>]+viewport", "viewport"),
                      (r"<style", "<style>")):
        ok = bool(re.search(pat, raw, re.I))
        res.append(("PASS" if ok else "FAIL", name, "" if ok else "缺失"))
    ext = re.findall(r'(?:src|href)\s*=\s*["\']https?://[^"\']*', raw, re.I) \
        + re.findall(r'url\(\s*[\'"]?https?://[^)]*', raw, re.I)
    if re.search(r"@import", raw, re.I):
        ext.append("@import")
    if re.search(r"<script[^>]+src\s*=", raw, re.I):
        ext.append("<script src>")
    res.append(("FAIL" if ext else "PASS", "外部资源", "; ".join(ext[:3]) if ext else "无"))
    res.append(("FAIL" if "```" in raw else "PASS", "代码围栏", ""))
    ph = sorted(set(re.findall(r"\{\{[A-Za-z_]+\}\}", raw)))
    res.append(("FAIL" if ph else "PASS", "残留占位符", "、".join(ph[:6]) if ph else "无"))

    style = " ".join(re.findall(r"(?is)<style[^>]*>(.*?)</style>", raw))
    ctr = centering(style)
    res.append(("PASS" if ctr else "FAIL", "正文居中",
                ("；".join(ctr[:3])) if ctr else "CSS 里找不到 max-width + margin auto —— 宽屏会全部贴左"))

    g = Grab()
    g.feed(raw)
    ntext = norm("".join(g.parts))
    grams3 = set(ntext[i:i + 3] for i in range(max(0, len(ntext) - 2)))
    hs = md_headings(src)
    miss = [h for h in hs if not hit(h, ntext, grams3)]
    rate = (len(hs) - len(miss)) / len(hs) if hs else 1.0
    res.append(("PASS" if rate >= cover else "FAIL", "标题覆盖",
                "%d/%d = %.0f%%%s" % (len(hs) - len(miss), len(hs), rate * 100,
                                      ("  缺: " + "、".join(miss[:6])) if miss else "")))

    base = os.path.dirname(os.path.abspath(path))
    miss_img = []
    for r in re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', raw, re.I):
        if r.startswith(("http", "data:", "#", "mailto:", "//")):
            continue
        p = os.path.normpath(os.path.join(base, r.split("#")[0].split("?")[0]))
        if not os.path.exists(p):
            miss_img.append(r)
    res.append(("FAIL" if miss_img else "PASS", "本地图片",
                ("找不到: " + ", ".join(sorted(set(miss_img))[:4])) if miss_img else "全部存在"))

    vis = sum(len(re.findall(r"<" + t, raw, re.I)) for t in ("figure", "svg", "table", "details", "img"))
    chars = len(ntext)
    dens = (chars / vis) if vis else float("inf")
    res.append(("INFO", "图表密度", "图表 %d 个 / 正文 %d 字 = 每 %.0f 字一个%s"
                % (vis, chars, dens, "" if dens <= 900 else "（偏低，建议多加图表）")))

    # 必备模块自检（INFO：只提示，不判 FAIL）
    mods = [("知识地图", ("知识地图", "系统地图", "分类树")),
            ("所在层导航", ("所在层",)),
            ("今天还成立吗", ("今天还成立吗", "现代校验", "还成立吗")),
            ("回看", ("回看", "小结", "结论"))]
    lack = [n for n, keys in mods if not any(k in ntext for k in keys)]
    res.append(("INFO", "必备模块", "命中 %d/%d%s" % (len(mods) - len(lack), len(mods),
                ("  缺: " + "、".join(lack)) if lack else "")))

    print("== %s" % path)
    for lvl, k, d in res:
        print("   %-4s %-10s %s" % (lvl, k, d))
    fails = [r for r in res if r[0] == "FAIL"]
    print("   RESULT: %s\n" % ("FAIL (%d)" % len(fails) if fails else "PASS"))
    return not fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--src", required=True)
    ap.add_argument("--cover", type=float, default=0.9)
    ap.add_argument("--min-kb", type=int, default=8)
    a = ap.parse_args()
    ok = True
    for f in a.files:
        if not os.path.exists(f):
            print("== %s\n   FAIL 文件不存在\n" % f)
            ok = False
            continue
        ok = check(f, a.src, a.cover, a.min_kb) and ok
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
