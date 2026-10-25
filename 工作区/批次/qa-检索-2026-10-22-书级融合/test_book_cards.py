#!/usr/bin/env python3
"""B5 单元用例：触发、不触发、JSON 形状、反馈追加、空选择、动作、门槛。

  python3 工作区/批次/qa-检索-2026-10-22-书级融合/test_book_cards.py

不碰数据库、不调模型：ranking/hits/toc/select 全部用假对象。
"""
import json
import os
import sys
import tempfile
from dataclasses import asdict
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
sys.path.insert(0, HERE)
import book_cards as BC  # noqa: E402

CASES = [0]


def case(name, ok, detail=""):
    CASES[0] += 1
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, ("  " + detail) if detail else ""))
    if not ok:
        raise SystemExit("失败：%s" % name)


def book(path, score, type_="社科", chapters=20, groups=("礼治",)):
    return SimpleNamespace(book=path, title=path.split("/")[-1], type=type_,
                           chapters=chapters, best=score, support=3, coverage=0.8,
                           groups=list(groups), score=score)


def hit(path, book_path, line=1, text="礼治 秩序 法律", score=10.0):
    return {"book": book_path, "path": path, "line": line, "text": text, "score": score}


BOOKS = {
    "库/甲": {"title": "乡土中国", "author": "费孝通"},
    "库/乙": {"title": "剑来", "author": "烽火戏诸侯"},
    "库/丙": {"title": "局外人", "author": "加缪"},
    "库/丁": {"title": "遮天", "author": "辰东"},
}
CONCEPTS = {"礼治": {"aliases": ["礼俗", "教化"], "exclude": []},
            "秩序": {"aliases": ["规矩"], "exclude": []}}


def main():
    # 1 提示
    h = BC.query_hints("乡土中国", BOOKS, CONCEPTS)
    case("书名查询认成 title", h["title"] == "乡土中国" and not h["verbatim"])
    h = BC.query_hints("乡土中国 礼治", BOOKS, CONCEPTS)
    case("书名加词仍是 title", h["title"] == "乡土中国")
    h = BC.query_hints("费孝通", BOOKS, CONCEPTS)
    case("作者查询认成 author", h["author"] == "费孝通")
    h = BC.query_hints("费孝通为什么提出差序格局", BOOKS, CONCEPTS)
    case("问句里的作者不算 author", h["author"] == "")
    h = BC.query_hints("礼和法不相同的地方是维持规范的力量", BOOKS, CONCEPTS)
    case("长原句认成 verbatim", h["verbatim"] and not h["question"])
    h = BC.query_hints("礼治社会靠什么维持秩序", BOOKS, CONCEPTS, ["礼治", "秩序"])
    case("问句里的概念命中", h["concept"] and h["question"])
    h = BC.query_hints("遮天里老疯子叫板太古王族", BOOKS, CONCEPTS)
    case("两字书名加「里」认成 title", h["title"] == "遮天")
    h = BC.query_hints("《搜神记》残卷里最鲜明的是谁", BOOKS, CONCEPTS)
    case("书名号里的书名认成 title", h["title"] == "搜神记")

    # 2 触发
    r = [book("库/甲", 10.0), book("库/乙", 9.5), book("库/丙", 9.0)]
    fire, why = BC.trigger("礼治为什么重要", r, {"title": "", "author": "", "verbatim": False,
                                               "concept": True, "question": True})
    case("分差 <10% 触发", fire and "margin" in why, str(why))
    r = [book("库/甲", 10.0), book("库/乙", 8.0),
         book("库/丙", 7.0, "文学"), book("库/丁", 6.0, "生活与实用")]
    fire, why = BC.trigger("礼治", r, {"title": "", "author": "", "verbatim": False,
                                       "concept": False, "question": False})
    case("三个类型触发", fire and "types" in why, str(why))
    r = [book("库/甲", 10.0), book("库/乙", 8.0, chapters=60)]
    fire, why = BC.trigger("礼治", r, {"title": "", "author": "", "verbatim": False,
                                       "concept": False, "question": False})
    case("50 章以上的书触发", fire and "long" in why, str(why))
    r = [book("库/甲", 10.0)]
    fire, why = BC.trigger("礼治为什么重要", r, {"title": "", "author": "", "verbatim": False,
                                               "concept": True, "question": True})
    case("概念问句触发", fire and "concept" in why, str(why))
    fire, why = BC.trigger("乡土中国", r, {"title": "乡土中国", "author": "", "verbatim": False,
                                           "concept": False, "question": False})
    case("书名查询不触发", not fire and why == [])
    fire, why = BC.trigger("费孝通", r, {"title": "", "author": "费孝通", "verbatim": False,
                                         "concept": False, "question": False})
    case("作者查询不触发", not fire)
    fire, why = BC.trigger("礼和法不相同的地方是维持规范的力量", r,
                           {"title": "", "author": "", "verbatim": True,
                            "concept": False, "question": False})
    case("原句查询不触发", not fire)
    case("空 ranking 不触发", BC.trigger("礼治", [], {"title": "", "author": "", "verbatim": False,
                                                     "concept": True, "question": True}) == (False, []))

    # 3 卡片与 JSON 形状
    ranking = [book("库/甲", 10.0), book("库/乙", 9.0), book("库/丙", 8.0)]
    hits = [hit("库/甲/md/01.md", "库/甲"), hit("库/乙/md/01.md", "库/乙"),
            hit("库/丙/md/01.md", "库/丙")]

    def toc_fn(b):
        return [{"path": "库/%s/md/01.md" % b.split("/")[-1], "title": "第一章",
                 "order": 0, "volume": "", "book": b}], "ok"

    def select_fn(hs, groups, terms, toc, max_chapters=3, title_weight=0.0):
        own = [h for h in hs if toc and h["path"] == toc[0]["path"]]
        return [{"chapter_path": h["path"], "chapter_title": "第一章", "line": h["line"],
                 "snippet": h["text"][:20], "score": h["score"]} for h in own[:max_chapters]]

    cards = BC.build_cards(ranking, hits, ["礼治"], 3, toc_fn, select_fn)
    case("卡片字段齐全", len(cards) == 3 and cards[0].best_chapter.endswith("01.md")
         and cards[0].readme_status == "ok" and cards[0].reason.startswith("第1名"))
    res = BC.CardSet(query="礼治", terms=["礼治"], trigger=True, reasons=["margin"],
                     hints={"title": "", "author": "", "verbatim": False},
                     cards=cards, default_selection=[c.book for c in cards[:3]])
    res.uncertainty = BC.uncertainty(ranking, res.hints, res.reasons, cards)
    blob = json.loads(json.dumps(asdict(res), ensure_ascii=False, default=str))
    case("JSON ask_user=false", blob["ask_user"] is False)
    case("JSON 默认前三本", len(blob["default_selection"]) == 3)
    case("JSON 不确定性写明", blob["uncertainty"]["user_choice_unknown"] is True)

    # 4 动作与空选择
    shown, chosen, picked, rejected = BC.apply_action(cards)
    case("空选择展开全部卡（至多六本）且不编造选择",
         len(chosen) == min(len(cards), 6) and picked == [])
    shown, chosen, picked, rejected = BC.apply_action(cards, select="1,2")
    case("选两本", [c.book for c in chosen] == [cards[0].book, cards[1].book]
         and len(picked) == 2 and "第3" not in rejected)
    shown, chosen, picked, rejected = BC.apply_action(cards, select="1-3")
    case("1-3 最多三本", len(chosen) == 3)
    shown, chosen, picked, rejected = BC.apply_action(cards, select="9")
    case("越界选择为空", chosen == [])
    mixed = cards + [book("库/丁", 7.0, "网文/类型")]
    shown, chosen, picked, rejected = BC.apply_action(mixed, exclude="网文/类型")
    case("排除类型", all(c.type != "网文/类型" for c in shown)
         and picked == [] and rejected == "type:网文/类型")
    shown, chosen, picked, rejected = BC.apply_action(cards, refresh_cards=mixed[:2])
    case("换一批", shown == mixed[:2] and picked == [] and rejected == "refresh")

    # 5 下一步只含选中的书
    nb = BC.next_books(cards[:2], hits, ["礼治"], 3, toc_fn, select_fn)
    case("下一步只含选中的书", [b["book"] for b in nb] == [cards[0].book, cards[1].book])
    case("下一步每本有章", all(len(b["selected"]) == 1 for b in nb))
    case("下一步带 book_score（book_query 文本输出不再 KeyError）",
         [b.get("book_score") for b in nb] == [cards[0].score, cards[1].score])

    # 5b 复用 Planner 缓存建卡 + 选中后扩展
    class FakePlanner:
        actions = set()
        meta = {}
        expand = {}

        def search(self, terms):
            return hits, 1.0

    res2, hits2, nouns2, ranking2 = BC.cards_for(
        FakePlanner(), ["礼治"], "礼治为什么重要", 3, books=BOOKS, concepts=CONCEPTS,
        toc_fn=toc_fn, select_fn=select_fn)
    case("cards_for 复用首轮建卡", res2.trigger and len(res2.cards) == 3
         and len(res2.default_selection) == 3, str(res2.reasons))
    # 5c 选章用全词命中，不是名词命中
    noun_hits = [hit("库/甲/md/01.md", "库/甲", line=1, score=10.0,
                     text="石昊 天人族 在这里出现，但这一章不是答案所在。" * 2)]
    full_hits = [hit("库/甲/md/02.md", "库/甲", line=2, score=30.0,
                     text="石昊 天人族 收拾 了对方，这一章才是目标章。" * 2)]

    class FakePlanner2:
        actions = {"收拾"}
        meta = {}
        expand = {}
        exclude = []

        def search(self, terms):
            return (full_hits if "收拾" in terms else noun_hits), 1.0

    def toc2(b):
        return [{"path": "库/甲/md/01.md", "title": "第一章", "order": 0,
                 "volume": "", "book": "库/甲"},
                {"path": "库/甲/md/02.md", "title": "第二章", "order": 1,
                 "volume": "", "book": "库/甲"}], "ok"

    def select2(hs, groups, terms, toc, max_chapters=3, title_weight=0.0):
        path = "库/甲/md/02.md" if any("收拾" in h["text"] for h in hs) else "库/甲/md/01.md"
        h = next(h for h in hs if h["path"] == path)
        return [{"chapter_path": path, "chapter_title": "第二章", "line": h["line"],
                 "snippet": h["text"][:20], "score": h["score"]}]

    res3, hits3, nouns3, rank3 = BC.cards_for(
        FakePlanner2(), ["石昊", "收拾"], "石昊怎么收拾天人族", 1, books=BOOKS,
        concepts=CONCEPTS, toc_fn=toc2, select_fn=select2)
    case("cards_for 选章用全词命中", res3.cards[0].best_chapter == "库/甲/md/02.md",
         res3.cards[0].best_chapter)
    nb3 = BC.next_books(res3.cards, hits3, ["石昊", "收拾"], 3, toc2, select2)
    case("next_books 选章用全词命中", nb3[0]["selected"][0]["chapter_path"] == "库/甲/md/02.md")

    calls = []

    def exp(q, b, p, t, l, mode, status="", nbrs=None):
        calls.append(b)
        return {"status": "ok", "book": b}

    nb2 = [{"book": cards[0].book, "selected": [{"chapter_path": "x", "chapter_title": "ch",
                                                 "line": 1}]},
           {"book": cards[1].book, "selected": []}]
    n = BC.expand_selected("礼治为什么重要", nb2, exp)
    case("expand_selected 只扩有章的", n == 1 and calls == [cards[0].book]
         and nb2[0]["expand"]["status"] == "ok")

    # 6 反馈追加
    fd, path = tempfile.mkstemp(suffix=".tsv")
    os.close(fd)
    os.unlink(path)
    row = BC.feedback_row("礼治", cards, cards[:2], "第3名", "yes", ts="2026-10-23T00:00:00+0800")
    BC.append_feedback(path, row)
    BC.append_feedback(path, BC.feedback_row("礼治", cards, [], "", "", ts="2026-10-23T00:00:01+0800"))
    lines = open(path, encoding="utf-8").read().splitlines()
    case("反馈表头与两行", len(lines) == 3 and lines[0].split("\t") == BC.FEEDBACK_COLS)
    case("反馈字段", lines[1].split("\t")[1] == "礼治" and lines[1].split("\t")[3] != ""
         and lines[2].split("\t")[3] == "")
    os.unlink(path)

    # 7 门槛
    v = BC.gate_verdict(0.70, 0.82, 0.70, 0.30, 0.20)
    case("门槛：命中 +10 点过", v["pass_gain"] and v["pass"])
    v = BC.gate_verdict(0.70, 0.70, 0.70, 0.30, 0.10)
    case("门槛：命中不变且长书占比降过", v["pass_long"] and v["pass"])
    v = BC.gate_verdict(0.70, 0.68, 0.66, 0.30, 0.20)
    case("门槛：两条都不满足不过", not v["pass"])
    v = BC.gate_verdict(0.70, 0.75, 0.60, 0.30, 0.25, 0.30, 0.18)
    case("门槛：长书占比按展示列表算", v["pass_long"] and v["pass"])

    print("PASS %d cases" % CASES[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
