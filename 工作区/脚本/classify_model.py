#!/usr/bin/env python3
"""走 pi 的一次性书目分类：一次调用给出一级、二级、置信与理由。

ingest.py 的导入预填与 classify_books.py --backend model 共用这里的提示词、
命令与 JSON 解析。分类体系与一级说明在这里，别处只引用，不再各写一份。

  classify_model.classify(["书名"], ["作者"], "opencode-go/deepseek-v4.1-flash", 240)
  → [{"level1":"文学","level2":"类型小说","confidence":0.93,"reason":"作者与系列都是推理"}]

解析失败抛 ClassifyError，调用方降级为人工填。不依赖 TypeSafe，不写盘。
PI_BIN 指向 .py 文件时用当前解释器跑它，测试的 stub 就靠这条。
"""
import json
import os
import re
import shutil
import subprocess
import sys

DEFAULT_MODEL = "opencode-go/deepseek-v4.1-flash"
PROMPT_VERSION = "2026-10-24.1"
TIMEOUT = 240
UNCERTAIN = "拿不准"
NO_SUB = "不细分"

TAXONOMY = {
    "文学": ("严肃文学小说", "类型小说", "网络文学", "诗歌", "散文随笔", "戏剧", "儿童与青少年文学"),
    "人文社科": ("历史", "思想与哲学", "宗教与神话", "政治与法律", "社会与经济",
             "人类学与考古", "心理与认知", "语言与文字"),
    "商业与管理": ("战略", "管理与组织", "投资与金融", "创新与创业", "营销与品牌"),
    "科学技术": ("数学与统计", "物理与天文", "生命与医学", "计算机与技术", "工程与制造", "科普"),
    "建筑与古迹": ("中国古建筑", "宗教建筑", "城市与聚落", "建筑史与理论", "地方文物"),
    "艺术与图像": ("美术史", "佛教美术", "绘画与概念设计", "摄影", "设计", "书法篆刻"),
    "游戏": ("游戏设计", "开发与工程", "设定集与美术集", "攻略与资料", "游戏研究"),
    "影视动画漫画": ("漫画与画集", "动画与动画电影", "电影与影史", "编剧与制作"),
    "写作与编剧": ("编剧理论与对白", "写作方法", "叙事与故事结构"),
    "生活与实用": ("饮食", "健康与身体", "两性与情色", "求职与职业", "手工与家居"),
    "其他": ("待归类", "图册与写真", "杂志与工具书"),
}
TOP_RUBRIC = {
    "文学": "小说、诗、散文、戏剧等文学作品，含推理科幻奇幻网络小说、诺奖等文学奖获奖作品",
    "人文社科": "历史、哲学、宗教、政治、法律、社会、经济、人类学、考古、心理、语言",
    "商业与管理": "企业战略、管理、投资理财、金融、创业创新、营销品牌，不含公告等单页文件",
    "科学技术": "数学、物理、天文、生物、医学、计算机、工程、科普读物",
    "建筑与古迹": "建筑史、古建筑、寺塔祠庙、城市与聚落规划、文物保护",
    "艺术与图像": "美术史、绘画与概念设计、佛教美术、书法、设计；不含写真集与杂志",
    "游戏": "游戏设计、开发、引擎、设定集、美术集、攻略、游戏研究",
    "影视动画漫画": "电影、动画、漫画、影史、制作；不含真人写真集",
    "写作与编剧": "编剧理论、对白、写作方法、叙事结构",
    "生活与实用": "饮食、健康、身体、两性与情色、职业、家居手工",
    "其他": "写真集与成人向图册、摄影写真、杂志、财报公告与培训资料等非书文档、来源不明或不属于以上任何一类",
}

FENCE = re.compile(r"```(?:json)?\s*(.+?)```", re.S)


class ClassifyError(RuntimeError):
    """模型调用或解析失败，消息直接给人看。"""


def build_prompt(titles, authors):
    tax = "\n".join("%s：%s" % (k, "、".join(v)) for k, v in TAXONOMY.items())
    rubric = "\n".join("%s：%s" % (k, v) for k, v in TOP_RUBRIC.items())
    books = "\n".join("%d. 书名《%s》，作者 %s" % (i + 1, t, a or "未知")
                      for i, (t, a) in enumerate(zip(titles, authors)))
    return ("只输出一行 JSON 数组，不要解释，不要代码块。\n"
            "分类体系（一级：该一级下的二级）：\n%s\n"
            "一级说明：\n%s\n"
            "书：\n%s\n"
            "为每本书给 level1（必须取体系里的一级名，拿不准写「%s」）、"
            "level2（取该一级下的二级名，没有合适的写「%s」）、"
            "confidence（0 到 1）、reason（不超过 20 字，说依据）。\n"
            "输出 [{\"level1\":\"\",\"level2\":\"\",\"confidence\":0.0,\"reason\":\"\"}]"
            % (tax, rubric, books, UNCERTAIN, NO_SUB))


def pi_argv(model, prompt):
    raw = os.environ.get("PI_BIN") or shutil.which("pi") or "pi"
    head = [sys.executable, raw] if raw.endswith(".py") else [raw]
    return head + ["-p", "--no-tools", "--no-session", "--no-context-files",
                   "--no-skills", "--no-extensions", "--model", model, prompt]


def _head(text, n=120):
    return re.sub(r"\s+", " ", (text or "").strip())[:n]


def _array_text(text):
    body = text or ""
    code = FENCE.search(body)
    if code:
        body = code.group(1)
    start, end = body.find("["), body.rfind("]")
    if start < 0 or end < start:
        raise ClassifyError("模型没回 JSON 数组：" + _head(text))
    return body[start:end + 1]


def _norm(rec):
    l1 = str(rec.get("level1") or "").strip()
    if l1 not in TAXONOMY:
        l1 = UNCERTAIN
    l2 = str(rec.get("level2") or "").strip()
    if l2 not in (TAXONOMY.get(l1) or []):
        l2 = ""
    try:
        conf = float(rec.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    reason = re.sub(r"\s+", " ", str(rec.get("reason") or "")).strip()
    return {"level1": l1, "level2": l2, "confidence": round(min(1.0, max(0.0, conf)), 3),
            "reason": reason}


def parse(text, n):
    """模型输出 → n 条 {level1, level2, confidence, reason}。不合规就抛 ClassifyError。"""
    raw = _array_text(text)
    try:
        got = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClassifyError("JSON 解析失败：%s；片段 %s" % (exc, _head(raw, 160)))
    if not isinstance(got, list):
        raise ClassifyError("模型回的不是数组：%s" % _head(raw))
    if len(got) != n:
        raise ClassifyError("条数不符：要 %d 得 %d" % (n, len(got)))
    out = []
    for i, rec in enumerate(got):
        if not isinstance(rec, dict):
            raise ClassifyError("第 %d 条不是对象：%s" % (i + 1, _head(str(rec), 60)))
        out.append(_norm(rec))
    return out


def classify(titles, authors, model=DEFAULT_MODEL, timeout=TIMEOUT):
    """一次调用给全部书名两级分类。titles 为空返回空表，失败抛 ClassifyError。"""
    titles = [str(t) for t in (titles or [])]
    if not titles:
        return []
    authors = [str(a or "") for a in (authors or [])][:len(titles)]
    authors += [""] * (len(titles) - len(authors))
    cmd = pi_argv(model, build_prompt(titles, authors))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ClassifyError("pi 超时 %ds，模型 %s 没回" % (timeout, model))
    except OSError as exc:
        raise ClassifyError("起不了 pi（%s）：%s" % (cmd[0], exc))
    if r.returncode != 0:
        raise ClassifyError("pi 退出 %d：%s"
                            % (r.returncode, _head(r.stderr or r.stdout, 160)))
    return parse(r.stdout, len(titles))
