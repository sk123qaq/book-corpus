"""读法：书级默认、章级覆写、建议与解析。

读法是“怎么读这一章”，不是读本档位。档位仍由读者按精力选择。
"""

import json
import os
import re
import time

from ..scripts import lib_common as L

MODES = ("narrative", "argument", "practical", "reference")
MODE_META = {
    "narrative": {"label": "叙事", "short": "叙事", "desc": "人物、场景、情节与叙事手法"},
    "argument": {"label": "论证", "short": "论证", "desc": "命题、论据、推理与反驳"},
    "practical": {"label": "实用", "short": "实用", "desc": "步骤、清单、决策与做法"},
    "reference": {"label": "资料·图册", "short": "资料", "desc": "图版、条目、术语与版本"},
}
PROMPT_VERSION = "reading-mode-v1"
CONFIDENCE_MIN = 0.55
MARGIN_MIN = 0.18
SUGGEST_DIR = os.path.join("build", "reading", "suggestions")


def mode_label(mode):
    return (MODE_META.get(mode) or {}).get("label") or mode or "未定"


def normalize_mode(mode):
    text = (mode or "").strip().lower()
    if text in MODES:
        return text
    aliases = {
        "narrative": "narrative",
        "叙事": "narrative",
        "小说": "narrative",
        "argument": "argument",
        "论证": "argument",
        "论述": "argument",
        "practical": "practical",
        "实用": "practical",
        "reference": "reference",
        "资料": "reference",
        "图册": "reference",
    }
    return aliases.get(text, "")


def category_fallback(book):
    subjects = [str(item) for item in (book or {}).get("subjects") or [] if item]
    subject = subjects[0] if subjects else ""
    if "文学" in subject:
        return "narrative", 0.62, "文学类默认按叙事读"
    if any(key in subject for key in ("艺术", "图像", "游戏", "影视", "动画", "漫画")):
        return "reference", 0.64, "图像/艺术类默认按资料图册读"
    if any(key in subject for key in ("商业", "管理", "生活", "实用")):
        return "practical", 0.60, "商业/实用类默认按实用读"
    if any(key in subject for key in ("人文社科", "科学", "技术", "建筑", "写作", "编剧")):
        return "argument", 0.58, "论述类默认按论证读"
    return "argument", 0.40, "分类不明确，先按论证读"


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dialogue_ratio(blocks):
    text = "".join((block.get("text") or "") for block in blocks if block.get("kind") == "paragraph")
    if not text:
        return 0.0
    quoted = sum(len(match.group(0)) for match in re.finditer(r"[“「][^”」]{1,400}[”」]", text))
    return min(1.0, quoted / max(1, len(text)))


def chapter_stats(blocks):
    counts = {}
    for block in blocks or []:
        kind = block.get("kind") or "paragraph"
        counts[kind] = counts.get(kind, 0) + 1
    return {
        "blocks": len(blocks or []),
        "paragraphs": counts.get("paragraph", 0),
        "headings": counts.get("heading", 0),
        "quotes": counts.get("quote", 0),
        "lists": counts.get("list", 0),
        "tables": counts.get("table", 0),
        "images": counts.get("image", 0),
        "code": counts.get("code", 0),
        "dialogue_ratio": round(_dialogue_ratio(blocks), 3),
    }


def _scores(book, stats):
    subject = " ".join((book or {}).get("subjects") or [])
    scores = dict.fromkeys(MODES, 0.08)
    reasons = {mode: [] for mode in MODES}
    if "文学" in subject:
        scores["narrative"] += 0.58
        reasons["narrative"].append("文学类")
    if any(key in subject for key in ("人文社科", "科学", "技术", "建筑", "写作", "编剧")):
        scores["argument"] += 0.46
        reasons["argument"].append("论述类")
    if any(key in subject for key in ("商业", "管理", "生活", "实用")):
        scores["practical"] += 0.50
        reasons["practical"].append("实用类")
    if any(key in subject for key in ("艺术", "图像", "游戏", "影视", "动画", "漫画")):
        scores["reference"] += 0.58
        reasons["reference"].append("图像类")
    if stats["dialogue_ratio"] >= 0.08:
        scores["narrative"] += 0.22
        reasons["narrative"].append("对话占比 %.0f%%" % (stats["dialogue_ratio"] * 100))
    if stats["images"] >= max(4, stats["blocks"] * 0.12):
        scores["reference"] += 0.22
        reasons["reference"].append("%d 张图" % stats["images"])
    if stats["lists"] + stats["tables"] + stats["code"] >= max(2, stats["blocks"] * 0.10):
        scores["practical"] += 0.16
        reasons["practical"].append("步骤/表格/代码块 %d" % (stats["lists"] + stats["tables"] + stats["code"]))
    if stats["quotes"] >= max(2, stats["blocks"] * 0.08):
        scores["argument"] += 0.12
        reasons["argument"].append("引文块 %d" % stats["quotes"])
    return scores, reasons


def rule_suggestion(book, chapter, blocks):
    stats = chapter_stats(blocks)
    scores, reasons = _scores(book, stats)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    total = sum(scores.values()) or 1.0
    confidence = ranked[0][1] / total
    margin = ranked[0][1] - ranked[1][1]
    mode = ranked[0][0]
    evidence = [
        {"label": "分类", "value": " / ".join((book or {}).get("subjects") or []) or "未分类"},
        {"label": "对话", "value": "%.0f%%" % (stats["dialogue_ratio"] * 100)},
        {"label": "图像", "value": str(stats["images"])},
        {
            "label": "结构",
            "value": "%d 段 / %d 标题 / %d 表" % (stats["paragraphs"], stats["headings"], stats["tables"]),
        },
    ]
    reason = "；".join(reasons[mode]) or "分类与结构信号不明显"
    return {
        "mode": mode,
        "confidence": round(confidence, 2),
        "margin": round(margin, 2),
        "source": "rule",
        "reason": reason,
        "evidence": evidence,
        "stats": stats,
    }


def _model_suggestion(client, book, chapter, blocks, fallback):
    if client is None:
        return fallback
    excerpt = "\n".join((block.get("text") or "") for block in blocks[:3])[:1200]
    messages = [
        {
            "role": "system",
            "content": "判断这本书这一章的主要读法。只返回 JSON："
            '{"mode":"narrative|argument|practical|reference","confidence":0到1,"reason":"不超过40字"}。'
            "不要解释，不要给读本档位。",
        },
        {
            "role": "user",
            "content": "书名：%s\n分类：%s\n章节：%s\n结构：%s\n开头：\n%s"
            % (
                (book or {}).get("title") or "",
                " / ".join((book or {}).get("subjects") or []),
                (chapter or {}).get("title") or "",
                json.dumps(fallback.get("stats") or {}, ensure_ascii=False),
                excerpt,
            ),
        },
    ]
    try:
        content, _usage = client.complete(messages, json_mode=True)
        payload = json.loads(content)
        mode = normalize_mode(payload.get("mode"))
        confidence = float(payload.get("confidence") or 0)
        if mode and 0 <= confidence <= 1:
            return {
                "mode": mode,
                "confidence": round(confidence, 2),
                "margin": 0.0,
                "source": "model",
                "reason": str(payload.get("reason") or "")[:80],
                "evidence": fallback.get("evidence") or [],
                "stats": fallback.get("stats") or {},
            }
    except Exception:
        return fallback
    return fallback


def suggestion_for(uid, n, library, root=None, client=None, force_model=False):
    """规则优先；低置信度才请模型。结果缓存，不写人账。"""
    root = os.path.abspath(os.path.expanduser(root or library.root))
    book = library.book(uid)
    chapter = library.chapter(uid, n)
    if not book or not chapter:
        return None
    raw = library.read_chapter(uid, n) or {}
    blocks = raw.get("blocks") or []
    source_sha = raw.get("source_sha256") or ""
    cached = _read_suggestion(root, uid, n)
    key = "%s|%s|%s" % (source_sha, PROMPT_VERSION, "model" if client is not None else "rule")
    if cached and cached.get("key") == key:
        return cached
    result = rule_suggestion(book, chapter, blocks)
    if force_model or (result["confidence"] < CONFIDENCE_MIN or result["margin"] < MARGIN_MIN):
        result = _model_suggestion(client, book, chapter, blocks, result)
    result.update(
        {
            "uid": uid,
            "n": int(n),
            "key": key,
            "source_sha256": source_sha,
            "prompt_version": PROMPT_VERSION,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        }
    )
    _write_suggestion(root, uid, n, result)
    return result


def _read_suggestion(root, uid, n):
    path = os.path.join(root, SUGGEST_DIR, uid, "%d.json" % int(n))
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_suggestion(root, uid, n, payload):
    path = os.path.join(root, SUGGEST_DIR, uid, "%d.json" % int(n))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    L.write_json_atomic(path, payload)


def read_profile(uid, root=None):
    """读派生文件 books/<uid>/reading.json；手改/旧文件返回 None。"""
    path = os.path.join(os.path.abspath(os.path.expanduser(root or L.ROOT)), "books", uid, "reading.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("generated") is not True:
        return None
    if data.get("version") != 1 or normalize_mode(data.get("mode")) != data.get("mode"):
        return None
    chapters = data.get("chapters")
    if chapters is not None and not isinstance(chapters, dict):
        return None
    data["chapters"] = chapters or {}
    return data


def from_rows(rows):
    """从 读法 表行拼出与 reading.json 同构的文档；派生文件缺失时的只读兜底。"""
    book_row = next((row for row in rows or [] if row.get("chapter") == "*"), None)
    if not book_row:
        return None
    chapters = {}
    for row in rows or []:
        if row.get("chapter") == "*":
            continue
        chapters[str(row["chapter"])] = {
            "mode": row.get("mode"),
            "panels": row.get("panels") or [],
            "source_sha256": row.get("source_sha256") or "",
        }
    return {
        "version": 1,
        "generated": False,
        "mode": book_row.get("mode"),
        "panels": book_row.get("panels") or [],
        "chapters": chapters,
        "updated_at": max((row.get("updated_at") or "") for row in rows),
    }


def resolve(profile, n, source_sha, suggestion):
    """章级覆写（源文哈希匹配）> 书级默认 > 建议 > 分类兜底。"""
    n_key = str(int(n))
    chapter = (profile or {}).get("chapters", {}).get(n_key) or (profile or {}).get("chapters", {}).get("%02d" % int(n))
    book_mode = normalize_mode((profile or {}).get("mode"))
    chapter_mode = normalize_mode((chapter or {}).get("mode"))
    chapter_sha = (chapter or {}).get("source_sha256") or ""
    valid_chapter = bool(chapter_mode and (not chapter_sha or chapter_sha == source_sha))
    if valid_chapter:
        mode, source = chapter_mode, "chapter"
    elif book_mode:
        mode, source = book_mode, "book"
    elif suggestion:
        mode, source = suggestion.get("mode"), "suggestion"
    else:
        mode, source = "argument", "fallback"
    return {
        "book_mode": book_mode or None,
        "chapter_override": chapter_mode if valid_chapter else None,
        "effective_mode": mode,
        "effective_source": source,
        "panels": (chapter or {}).get("panels") if valid_chapter else (profile or {}).get("panels") or [],
        "suggestion": suggestion,
    }
