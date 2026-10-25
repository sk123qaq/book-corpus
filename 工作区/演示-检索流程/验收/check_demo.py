#!/usr/bin/env python3
"""演示页验收：健康检查、两个示例的 trace、三体题的窗口与回答、章节截取、拒绝路径。

回答相关四条（ANSWER-BRIEF.md 的验收）：
  1. 三体题 /api/answer 的 answer 含「威慑度」「百分之十」，至少一条引文 verified 行号是 1166 或 1172
  2. /api/chapter?before=700 的前置含「你的威慑度在百分之十上下波动」，
     hit_text 含「这支舰队应该是五年前派出的」，且 text[hit_start:hit_end] == hit_text
  3. 库里没有的问题 not_found=true 或 answer 含「未找到原文依据」，不崩
  4. 全选：用「内卷和教育」那题的书卡拼 sources 再问一次，mode=multi、引文盖住 2 本书以上且都在书单内、
     第二跑命中缓存
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
THREE = "《三体》里为什么程心刚成为执剑人没多久三体就入侵了地球"
THREE_PATH = "库/文学/类型小说/刘慈欣/三体/死神永生/md/03_第二部威慑纪元12年，“青铜世纪”号.md"
fails = []


def check(name, ok, detail=""):
    print("[%s] %s%s" % ("过" if ok else "败", name, (" · " + detail) if detail else ""))
    if not ok:
        fails.append(name)


def get(path, timeout=60):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8")


def post(path, obj, timeout=240):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode("utf-8"),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8")


def code_of(path):
    try:
        return get(path)[0]
    except urllib.error.HTTPError as exc:
        return exc.code


status, body = get("/api/health")
health = json.loads(body)
check("健康检查", status == 200 and health.get("ok"), "books=%s" % health.get("meta", {}).get("books"))
_, html = get("/")
check("首页渲染入口", "书库检索" in html and "ask-form" in html)

status, body = post("/api/run", {"question": "内卷和教育之间是什么关系"})
t1 = json.loads(body)
plan = t1.get("plan") or {}
cards = t1.get("cards") or {}
check("接口返回 200", status == 200 and bool(t1.get("extract", {}).get("terms")))
check("轮次至少 2 轮", len(plan.get("rounds") or []) >= 2,
      "rounds=%d stop=%s" % (len(plan.get("rounds") or []), plan.get("stop")))
check("书级榜与选章", bool(plan.get("ranking")) and bool(plan.get("chapters")))
check("书卡触发且 5 到 8 张", cards.get("trigger") is True and 5 <= len(cards.get("cards") or []) <= 8,
      "cards=%d reasons=%s" % (len(cards.get("cards") or []), cards.get("reasons")))
check("阶段耗时齐全", set(("understand", "rounds", "rank", "cards", "result")) <= set(t1.get("stage_ms") or {}))
check("结果原文", len((t1.get("result") or {}).get("text") or "") > 200,
      "%d 字" % len((t1.get("result") or {}).get("text") or ""))

# ---- 全选：一次回答盖住书单里所有书 ----------------------------------
srcs = []
for c in cards.get("cards") or []:
    if not c.get("best_chapter"):
        continue
    srcs.append({"book": c.get("book", ""), "title": c.get("title", ""),
                 "path": c["best_chapter"], "line": int(c.get("line") or 1)})
scope = {s["book"] for s in srcs}
if len(srcs) < 2:
    check("全选多书回答", False, "书卡不足 2 张，拼不出 sources")
else:
    loc_all = {"question": t1.get("question") or "",
               "book": srcs[0]["book"], "path": srcs[0]["path"], "line": srcs[0]["line"],
               "terms": (t1.get("extract") or {}).get("terms") or [],
               "groups": (t1.get("extract") or {}).get("groups") or [],
               "sources": srcs}
    t0 = time.time()
    _, body = post("/api/answer", loc_all, timeout=300)
    multi = json.loads(body)
    multi_ms = (time.time() - t0) * 1000
    cites_m = multi.get("citations") or []
    books_m = {c.get("book") for c in cites_m if c.get("verified")}
    check("全选多书回答", multi.get("ok") is True and multi.get("mode") == "multi"
          and len(multi.get("answer") or "") > 40,
          "mode=%s sources=%d %.1f s" % (multi.get("mode"),
                                         len(multi.get("sources") or []),
                                         multi_ms / 1000.0))
    check("多书回答覆盖 2 本书以上", len(books_m) >= 2, "books=%d" % len(books_m))
    check("多书引文全部回查且在书单内",
          bool(cites_m) and all(c.get("verified") for c in cites_m) and books_m <= scope,
          "cites=%d scope=%d" % (len(cites_m), len(scope)))

    t0 = time.time()
    _, body = post("/api/answer", loc_all, timeout=300)
    again_m = json.loads(body)
    again_m_ms = (time.time() - t0) * 1000
    check("多书回答缓存命中", again_m.get("cached") is True and again_m_ms < 1500,
          "cached=%s %.0f ms" % (again_m.get("cached"), again_m_ms))

_, body = post("/api/run", {"question": "乡土中国 差序格局"})
t2 = json.loads(body)
check("不弹卡路径", (t2.get("cards") or {}).get("trigger") is False
      and not (t2.get("cards") or {}).get("cards"), "trigger=false, cards=0")
check("不弹卡仍有结果", len((t2.get("result") or {}).get("text") or "") > 200)

ch = ((t1.get("plan") or {}).get("chapters") or [{}])[0].get("selected", [{}])[0]
q = urllib.parse.quote(ch.get("chapter_path", ""))
status, body = get("/api/chapter?path=%s&line=%s&chars=600" % (q, ch.get("line", 1)))
chap = json.loads(body)
check("章节截取", status == 200 and chap.get("ok") and len(chap.get("text", "")) > 100,
      "%d 字" % len(chap.get("text", "")))
check("路径穿越拒绝", code_of("/api/chapter?path=../../etc/passwd&line=1") == 400)
check("非 md 拒绝", code_of("/api/chapter?path=/etc/passwd&line=1") == 400)
try:
    post("/api/run", {"question": ""})
    check("空问题拒绝", False)
except urllib.error.HTTPError as exc:
    check("空问题拒绝", exc.code == 400)

# ---- 三体题：窗口 + 回答 ---------------------------------------------
_, body = post("/api/run", {"question": THREE})
t3 = json.loads(body)
r3 = t3.get("result") or {}
loc = {"question": THREE, "book": r3.get("book", ""), "path": r3.get("path", ""),
       "line": r3.get("hit_line") or r3.get("line") or 1,
       "terms": (t3.get("extract") or {}).get("terms") or [],
       "groups": (t3.get("extract") or {}).get("groups") or []}
check("三体题定位", loc["path"] == THREE_PATH,
      "%s:%s" % (loc["path"].split("/")[-1], loc["line"]))

status, body = get("/api/chapter?path=%s&line=1180&chars=900&before=700"
                   % urllib.parse.quote(loc["path"]))
chap3 = json.loads(body)
text3 = chap3.get("text") or ""
hs, he = chap3.get("hit_start"), chap3.get("hit_end")
hit3 = chap3.get("hit_text") or ""
pre3 = text3[:hs] if isinstance(hs, int) else ""
check("前置上下文含威慑度", "你的威慑度在百分之十上下波动" in pre3,
      "前置 %d 字" % len(pre3))
check("命中段原文", "这支舰队应该是五年前派出的" in hit3, "命中 %d 字" % len(hit3))
check("命中偏移自洽", isinstance(hs, int) and isinstance(he, int)
      and text3[hs:he] == hit3 and he > hs)

t0 = time.time()
status, body = post("/api/answer", loc)
first = json.loads(body)
first_ms = (time.time() - t0) * 1000
answer3 = first.get("answer") or ""
cites3 = [c for c in (first.get("citations") or []) if c.get("verified")]
check("回答接口可用", status == 200 and first.get("ok") is True,
      "ok=%s cached=%s %.1f s passages=%d" % (first.get("ok"), first.get("cached"),
                                             first_ms / 1000.0, len(first.get("passages") or [])))
check("回答含威慑度与百分之十", "威慑度" in answer3 and "百分之十" in answer3, answer3[:60])
check("引文回查行号 1166/1172", any(c.get("line") in (1166, 1172) for c in cites3),
      "lines=%s" % [c.get("line") for c in cites3])

t0 = time.time()
status, body = post("/api/answer", loc)
again = json.loads(body)
again_ms = (time.time() - t0) * 1000
check("回答缓存命中", again.get("cached") is True and again_ms < 1500,
      "cached=%s %.0f ms" % (again.get("cached"), again_ms))

# ---- 凡人修仙传：qsearch 前 100 + 组覆盖 + 只读 grep -----------------
FANREN = "凡人修仙传里有次南宫婉受伤，好像还被冰封，是什么人下的手"
_, body = post("/api/run", {"question": FANREN})
t4 = json.loads(body)
r4 = t4.get("result") or {}
loc4 = {"question": FANREN, "book": r4.get("book", ""), "path": r4.get("path", ""),
        "line": r4.get("hit_line") or r4.get("line") or 1,
        "terms": (t4.get("extract") or {}).get("terms") or [],
        "groups": (t4.get("extract") or {}).get("groups") or []}
_, body = post("/api/answer", loc4)
a4 = json.loads(body)
answer4 = a4.get("answer") or ""
cites4 = [c for c in (a4.get("citations") or []) if c.get("verified")]
check("凡人题回答", a4.get("ok") is True and a4.get("not_found") is not True
      and ("长老" in answer4 or "阴罗宗" in answer4), answer4[:80])
check("凡人题引文落到 0783 与 0790/0791",
      any("0783" in (c.get("path") or "") for c in cites4)
      and any(("0790" in (c.get("path") or "") or "0791" in (c.get("path") or ""))
              for c in cites4),
      "lines=%s" % [(c.get("path", "").split("/")[-1][:12], c.get("line")) for c in cites4])

# ---- 库里没有的问题 -------------------------------------------------
loc_none = dict(loc)
loc_none.update({"question": "拜占庭帝国的军区制是怎么运转的",
                 "line": 1180, "terms": ["拜占庭", "军区制"]})
_, body = post("/api/answer", loc_none)
none = json.loads(body)
check("无答案题 not_found", none.get("not_found") is True
      or "未找到原文依据" in (none.get("answer") or ""),
      "not_found=%s cites=%d" % (none.get("not_found"), len(none.get("citations") or [])))

print("\nALL PASS" if not fails else "\n失败 %d 项：%s" % (len(fails), ", ".join(fails)))
sys.exit(0 if not fails else 1)
