#!/usr/bin/env python3
"""B2.5 单元用例：词集、停止判据、换词顺序、上限、df 降词、选章、可复现。

模型与数据库都不碰：search 与 df 都注入假函数。
  python3 工作区/批次/qa-检索-2026-10-22-书级融合/test_book_query.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
sys.path.insert(0, HERE)
import book_query as BQ  # noqa: E402

CASES = [0]


def case(name, ok, detail=""):
    CASES[0] += 1
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, ("  " + detail) if detail else ""))
    if not ok:
        raise SystemExit("失败：%s" % name)


def hit(book, score, text="", line=1, seq=0):
    path = "库/%s/md/%02d.md" % (book, seq + 1)
    return {"id": seq, "book": book, "path": path, "line": line, "text": text,
            "score": score, "bm25": 0.0, "coverage": 0, "kana": 0.0}


class Fake:
    """按词集返回固定命中；记录调用顺序。"""

    def __init__(self, table, df=None):
        self.table = table
        self.df = df or {}
        self.calls = []

    def search(self, terms):
        key = tuple(terms)
        self.calls.append(key)
        return list(self.table.get(key, [])), 1.0

    def dfn(self, term):
        return self.df.get(term, (1, 1))


def planner(fake, **kw):
    kw.setdefault("meta", {})
    kw.setdefault("expand", {})
    kw.setdefault("actions", {"收拾", "发烧"})
    kw.setdefault("concepts", {"礼治": {"aliases": ["礼俗", "教化"], "exclude": []},
                               "自由邦": {"aliases": ["自由"], "exclude": []}})
    kw.setdefault("broad", {"秩序": {"aliases": ["规矩", "稳定"], "exclude": []}})
    return BQ.Planner(None, search_fn=fake.search, df_fn=fake.dfn, **kw)


def main():
    # 1 名词集：去动作词；全被去掉时退回全词
    case("名词集去掉动作词", BQ.split_terms(["礼治", "秩序", "收拾"], {"收拾"}) == ["礼治", "秩序"])
    case("动作词全去时退回全词", BQ.split_terms(["收拾", "发烧"], {"收拾", "发烧"}) == ["收拾", "发烧"])

    # 2 概念解析只认精确
    con = {"自由邦": {"aliases": ["自由"], "exclude": []}}
    case("概念键精确命中", BQ.resolve("自由邦", con) == "自由邦")
    case("概念别名精确命中", BQ.resolve("自由", con) == "自由邦")
    case("不做子串包含", BQ.resolve("爱", con) is None and BQ.resolve("自由意志", con) is None)

    # 2b 概念 exclude 透传
    ex_hits = [hit("甲", 10.0, "自由邦", 1, 0), hit("甲", 9.0, "自由邦", 2, 1)]
    ex_rank = [type("R", (), {"book": "甲", "score": 10.0})()]
    A_no, _, _, _ = BQ.hit_rounds(ex_hits, ex_rank, ["自由邦"])
    A_ex, _, _, _ = BQ.hit_rounds(ex_hits, ex_rank, ["自由邦"], exclude=["自由邦"])
    case("hit_rounds 透传 exclude", A_no and not A_ex)
    case("Planner 保留 exclude", planner(Fake({}), exclude=["自由邦"]).exclude == ["自由邦"])

    # 3 停止判据
    two = [hit("甲", 10.0, "礼治 秩序", 1, 0), hit("甲", 9.0, "礼治 秩序", 2, 1),
           hit("乙", 1.0, "礼治", 1, 2)]
    A, B, C, cover = BQ.hit_rounds(two + [hit("甲", 8.0, "礼治 秩序", 3, 3)],
                                   [type("R", (), {"book": "甲", "score": 10.0})(),
                                    type("R", (), {"book": "乙", "score": 1.0})()],
                                   ["礼治", "秩序"])
    case("C：三段全覆盖", (A, C) == (True, True) and cover == 3)
    A, B, C, cover = BQ.hit_rounds(two,
                                   [type("R", (), {"book": "甲", "score": 10.0})(),
                                    type("R", (), {"book": "乙", "score": 9.0})()],
                                   ["礼治", "秩序"])
    case("A 且 B：两段全覆盖 + 分差 10%", (A, B, C) == (True, True, False) and cover == 2)
    A, B, C, cover = BQ.hit_rounds(two,
                                   [type("R", (), {"book": "甲", "score": 10.0})(),
                                    type("R", (), {"book": "乙", "score": 9.5})()],
                                   ["礼治", "秩序"])
    case("B 单独不触发停止", (A, B, C) == (True, False, False))
    A, B, C, cover = BQ.hit_rounds([hit("甲", 10.0, "礼治", 1, 0)],
                                   [type("R", (), {"book": "甲", "score": 10.0})()],
                                   ["礼治", "秩序"])
    case("并集不全时不停", (A, B, C) == (False, True, False))

    # 3b 放宽后的 A：三段式允许每段 2/3 组，但并集要全，且 C 仍按全组覆盖算
    R1 = [type("R", (), {"book": "甲", "score": 10.0})(),
          type("R", (), {"book": "乙", "score": 1.0})()]
    A, B, C, cover = BQ.hit_rounds([hit("甲", 10.0, "礼治 秩序", 1, 0),
                                    hit("甲", 9.0, "秩序 法律", 2, 1),
                                    hit("甲", 8.0, "礼治", 3, 2)],
                                   R1, ["礼治", "秩序", "法律"])
    case("三段式：两段各 2/3 组 + 并集算到线", (A, B, C) == (True, True, False) and cover == 0)
    A, B, C, cover = BQ.hit_rounds([hit("甲", 10.0, "礼治 秩序", 1, 0),
                                    hit("甲", 9.0, "礼治 秩序", 2, 1)],
                                   R1, ["礼治", "秩序", "法律"])
    case("三段式：并集缺一组不算 A", A is False)
    A, B, C, cover = BQ.hit_rounds([hit("甲", 10.0, "礼治 秩序", 1, 0),
                                    hit("甲", 9.0, "礼治", 2, 1),
                                    hit("甲", 8.0, "法律", 3, 2)],
                                   R1, ["礼治", "秩序", "法律"])
    case("三段式：只有一段覆盖两组不算 A", A is False)
    A, B, C, cover = BQ.hit_rounds([hit("甲", 10.0, "礼治", 1, 0),
                                    hit("甲", 9.0, "秩序", 2, 1)],
                                   R1, ["礼治", "秩序"])
    case("两段式口径不变：各一段不算 A", A is False)

    # 4 质量键：C > A且B，同分取早
    import dataclasses

    def rnd(n, A, B, C):
        return BQ.Round(n=n, kind="k", terms=[], groups=[], hits=0, top1="", top1_score=0,
                        top2_score=0, margin=0, cover_paras=0, A=A, B=B, C=C, stop=False, ms=0)
    case("质量键 C 高于 A+B", BQ.quality(rnd(1, False, False, True)) > BQ.quality(rnd(2, True, True, False)))
    case("质量键同 profile 取早", BQ.quality(rnd(1, True, False, False)) > BQ.quality(rnd(3, True, False, False)))
    late = rnd(3, True, True, False)
    late.top1 = "甲"
    case("没到线的一轮不压过第一轮", BQ.quality(late) < BQ.quality(dataclasses.replace(rnd(1, False, False, False), top1="甲")))

    # 5 换词顺序
    p = planner(Fake({}))
    kinds = [k for k, _, _ in p.term_sets(["石昊", "天人族"], ["石昊", "天人族"], False)]
    case("无概念无宽词：3 组里全词重复", kinds == ["noun", "full", "drop-generic"], str(kinds))
    kinds = [k for k, _, _ in p.term_sets(["礼治", "秩序", "收拾"], ["礼治", "秩序"], False)]
    case("有动作词 + 概念：4 组", kinds == ["noun", "full", "noun+concept", "drop-generic"], str(kinds))
    kinds = [k for k, _, _ in p.term_sets(["礼治", "秩序", "收拾"], ["礼治", "秩序"], True)]
    case("加宽词：5 组", kinds == ["noun", "full", "noun+concept", "full+broad", "drop-generic"], str(kinds))
    sets = dict((k, t) for k, t, _ in p.term_sets(["礼治", "秩序"], ["礼治", "秩序"], True))
    case("宽词按宽表解析", "规矩" in sets["full+broad"] and "稳定" in sets["full+broad"], str(sets.get("full+broad")))

    # 6 一轮即停
    fake = Fake({("礼治", "秩序"): [
        hit("甲", 10.0, "礼治 秩序", 1, 0), hit("甲", 9.0, "礼治 秩序", 2, 1),
        hit("甲", 8.0, "礼治 秩序", 3, 2), hit("乙", 1.0, "礼治", 1, 3)]})
    p = planner(fake)
    plan = p.plan(["礼治", "秩序"], chapters=False)
    case("一轮到线即停", len(plan.rounds) == 1 and plan.stop == "C" and plan.best_round == 1, plan.stop)
    case("一轮只查一次", p.fetches == 1)

    # 7 动作词题目：两轮后一轮不到线，全词重复被跳过
    fake = Fake({("礼治", "秩序"): [hit("甲", 10.0, "礼治", 1, 0)],
                 ("礼治", "秩序", "收拾"): [hit("乙", 10.0, "礼治 秩序 收拾", 1, 1)]})
    p = planner(fake)
    plan = p.plan(["礼治", "秩序", "收拾"], chapters=False)
    case("无停线：跑尽可用词集", plan.stop == "exhausted" and len(plan.rounds) == 3, plan.stop)
    case("跳过的重复词集不计数", [r.kind for r in plan.rounds] == ["noun", "full", "noun+concept"],
         str([r.kind for r in plan.rounds]))

    # 8 上限：五组都不到线
    fake = Fake({})
    fake.table[("礼治", "秩序")] = [hit("甲", 10.0, "礼治", 1, 0)]
    fake.table[("礼治", "秩序", "收拾")] = [hit("乙", 10.0, "礼治", 1, 1)]
    fake.table[("礼治", "秩序", "礼俗", "教化")] = [hit("丙", 10.0, "礼治", 1, 2)]
    fake.table[("礼治", "秩序", "收拾", "规矩", "稳定")] = [hit("丁", 10.0, "礼治", 1, 3)]
    fake.table[("礼治", "收拾")] = [hit("戊", 10.0, "礼治", 1, 4)]
    fake.df = {"礼治": (1, 1), "秩序": (9, 9), "收拾": (2, 2)}
    p = planner(fake)
    plan = p.plan(["礼治", "秩序", "收拾"], broad=True, chapters=False, max_rounds=5)
    case("五轮上限记 cap", plan.stop == "cap" and len(plan.rounds) == 5, plan.stop)
    case("cap 仍返回最早一轮（判据同分）", plan.best_round == 1, str(plan.best_round))

    # 9 df 降词：书级 df 最高者被去掉，并列取靠后
    p = planner(Fake({}), actions=set())
    p._df_fn = lambda t: {"礼治": (1, 1), "秩序": (9, 9), "法律": (9, 9)}[t]
    sets = {k: t for k, t, _ in p.term_sets(["礼治", "秩序", "法律"], ["礼治", "秩序", "法律"], False)}
    case("降词去掉 df 最高且并列取靠后", "法律" not in sets["drop-generic"] and "秩序" in sets["drop-generic"],
         str(sets["drop-generic"]))
    p2 = planner(Fake({}), actions=set())
    p2._df_fn = lambda t: (1, 1)
    sets = {k: t for k, t, _ in p2.term_sets(["礼治", "秩序", "法律"], ["礼治", "秩序", "法律"], False)}
    case("df 全并列时仍确定", "法律" not in sets["drop-generic"], str(sets["drop-generic"]))

    # 10 选章：同章去重、取前三、标题回填
    hits = [hit("甲", 10.0, "a", 5, 0), hit("甲", 9.0, "b", 9, 0),
            hit("甲", 8.0, "c", 1, 1), hit("甲", 7.0, "d", 1, 2),
            hit("甲", 6.0, "e", 1, 3), hit("乙", 99.0, "f", 1, 4)]
    toc = [{"path": "库/甲/md/01.md", "title": "第一章"}, {"path": "库/甲/md/02.md", "title": "第二章"}]
    rows = BQ.select_chapters(hits, "甲", ["a"], toc, 2)
    case("选章按最好段排且最多三章", len(rows) == 2 and rows[0]["line"] == 5, str(len(rows)))
    case("选章回填标题", rows[0]["chapter_title"] == "第一章", rows[0]["chapter_title"])

    # 11 可复现：同一输入两次逐项相同
    fake = Fake({("礼治", "秩序"): [
        hit("甲", 10.0, "礼治 秩序", 1, 0), hit("甲", 9.0, "礼治 秩序", 2, 1),
        hit("乙", 8.0, "礼治", 1, 2)]})
    a = planner(fake).plan(["礼治", "秩序"], chapters=False)
    b = planner(fake).plan(["礼治", "秩序"], chapters=False)
    same = [(r.n, r.kind, r.top1, r.top1_score, r.A, r.B, r.C) for r in a.rounds] == \
        [(r.n, r.kind, r.top1, r.top1_score, r.A, r.B, r.C) for r in b.rounds]
    case("同一输入可复现", same and a.stop == b.stop and a.best_round == b.best_round)

    print("PASS %d cases" % CASES[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
