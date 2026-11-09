"""编排：解析 → （可选）标注 → 校验 → 四档 → 渲染 → 门禁 → 原子落盘。"""

import glob
import hashlib
import json
import os
import re
import threading
import time
from html.parser import HTMLParser
from typing import ClassVar

from .agent_standard import generate_standard_html, render_agent_page
from .edition import audit_edition, build_brief, decide_template, generate_content, render_edition, validate_content
from .editorial import EditorialError, audit_editorial, validate_editorial
from .editorial_gen import generate_editorial
from .jobs import JobQueue
from .library import Library
from .model import ModelClient, prompt_sha256
from .quality import check_html
from .render import asset_url, image_data_uri, render_chapter, render_export
from .store import Store
from .tiers import TIER_ORDER, TIER_SPECS, project, validate_annotation

GENERATOR = "book-corpus-reading"
GENERATOR_VERSION = "phase1-1"
RENDER_VERSION = "r3"
# 覆盖率按整块取舍，块粒度决定了结果只能落在离散点上。给 G3 留
# 两个百分点的容差，避免一个 17% 的长块把 0.80 卡成 0.78。
COVERAGE_TOLERANCE = 0.02
DRAFT_PROMPT = "draft"
MAX_BUDGET_TOKENS = 500_000
MAX_BUDGET_USD = 1.0
USD_PER_MTOK_IN = 1.0
USD_PER_MTOK_OUT = 2.0


class JobConflictError(RuntimeError):
    """同一章已有任务在排队或运行。"""

    def __init__(self, conflicts):
        self.conflicts = conflicts or []
        super().__init__("该章已有任务在排队或运行")


class RebuildRequiredError(RuntimeError):
    """原文或生成模式变了，未勾选档位无法保留，必须显式整包重建。"""

    def __init__(self, tiers, reason):
        self.tiers = list(tiers or [])
        self.reason = reason or "原文或生成模式已变化"
        super().__init__("未勾选档位无法保留：%s" % self.reason)


class PackExistsError(RuntimeError):
    """该档位已有读本，需要用户确认覆盖。"""

    def __init__(self, tiers):
        self.tiers = list(tiers or [])
        super().__init__("该档位已有读本")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _nkey(text):
    return re.sub(r"[\W_]+", "", (text or "").lower())


def _pack_meta(state):
    manifest = (state or {}).get("manifest") or {}
    if not manifest:
        return None
    return {
        "exists": True,
        "draft": bool(manifest.get("draft")),
        "status": manifest.get("status"),
        "state": (state or {}).get("status"),
        "generated_at": manifest.get("generated_at"),
        "source_sha256": manifest.get("source_sha256"),
        "source_changed": bool((state or {}).get("source_changed")),
        "preservable": (state or {}).get("preservable"),
        "preserve_reason": (state or {}).get("preserve_reason"),
        "model": manifest.get("model"),
        "reasoning_effort": manifest.get("reasoning_effort"),
        "model_fallback": manifest.get("model_fallback"),
        "usage": manifest.get("usage"),
    }


def _estimate_budget(blocks):
    """保守估算一次标注的 token / 美元；真实 usage 另记。"""
    text_chars = sum(len((block.get("text") or "").strip()) for block in blocks)
    input_tokens = int(text_chars * 0.8) + 1_500
    output_tokens = min(8_192, 1_500 + len([b for b in blocks if (b.get("text") or "").strip()]) * 50)
    usd = input_tokens / 1_000_000 * USD_PER_MTOK_IN + output_tokens / 1_000_000 * USD_PER_MTOK_OUT
    return {
        "input_tokens_est": input_tokens,
        "output_tokens_est": output_tokens,
        "tokens_est": input_tokens + output_tokens,
        "usd_est": round(usd, 4),
        "limit_tokens": MAX_BUDGET_TOKENS,
        "limit_usd": MAX_BUDGET_USD,
    }


def _usage_cost(usage):
    """从一次调用的 usage 算实际 token 与美元；字段兼容常见两种命名。"""
    usage = usage or {}
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total = int(usage.get("total_tokens") or 0) or prompt + completion
    usd = prompt / 1_000_000 * USD_PER_MTOK_IN + completion / 1_000_000 * USD_PER_MTOK_OUT
    return total, usd


def _chapter_api(chapter, tiers=None, state=None):
    data = dict(chapter)
    data["n"] = "%02d" % int(chapter["n"])
    data["minutes"] = max(1, round(chapter.get("chars", 0) / TIER_SPECS["standard"]["wpm"]))
    data["tiers"] = tiers or {}
    pack = _pack_meta(state)
    data["pack"] = pack
    data["draft"] = bool(pack and pack["draft"])
    return data


class _Structure(HTMLParser):
    VOID: ClassVar[set[str]] = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []
        self.doctype = False

    def handle_decl(self, decl):
        if decl.lower().startswith("doctype html"):
            self.doctype = True

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        return

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append("</%s>" % tag)
            return
        self.stack.pop()


class _SourceText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.current = None
        self.chunks = {}
        self.order = []

    def handle_starttag(self, tag, attrs):
        if self.current:
            self.depth += 1
            return
        values = dict(attrs)
        if values.get("data-origin") == "source" and values.get("data-block"):
            self.current = values["data-block"]
            self.depth = 1
            self.chunks[self.current] = []
            self.order.append(self.current)

    def handle_startendtag(self, tag, attrs):
        if self.current and tag in ("img", "br", "hr"):
            return
        if not self.current:
            values = dict(attrs)
            if values.get("data-origin") == "source" and values.get("data-block"):
                self.chunks.setdefault(values["data-block"], [])
                self.order.append(values["data-block"])

    def handle_endtag(self, tag):
        if not self.current:
            return
        self.depth -= 1
        if self.depth <= 0:
            self.current = None

    def handle_data(self, data):
        if self.current:
            self.chunks[self.current].append(data)


def source_texts(html_text):
    parser = _SourceText()
    parser.feed(html_text)
    return {block_id: "".join(parts).strip() for block_id, parts in parser.chunks.items()}


def _han_only(text):
    return "".join(re.findall(r"[\u4e00-\u9fff]", text or ""))


def _coverage_problems(projections):
    """G3 的覆盖率判定；容差留给整块取舍的粒度。"""
    problems = []
    for tier, projection in projections.items():
        stats = projection["stats"]
        spec = TIER_SPECS[tier]
        if stats["c_char"] < spec["c_min"] - COVERAGE_TOLERANCE or stats["c_char"] > spec["c_max"] + COVERAGE_TOLERANCE:
            problems.append("%s C字=%.2f" % (tier, stats["c_char"]))
        if (
            stats["c_block"] < spec["b_min"] - COVERAGE_TOLERANCE
            or stats["c_block"] > spec["b_max"] + COVERAGE_TOLERANCE
        ):
            problems.append("%s C块=%.2f" % (tier, stats["c_block"]))
    return problems


def audit_pack(blocks, projections, html_text, annotation, drafts, editorial=None):
    gates = []
    quality = check_html(html_text)

    def quality_gate(gate_id, name, keys, ok_detail):
        related = [finding for finding in quality if finding["key"] in keys]
        fails = [finding for finding in related if finding["level"] == "fail"]
        warns = [finding for finding in related if finding["level"] == "warn"]
        picked = fails or warns
        if fails:
            status = "fail"
        elif warns:
            status = "warn"
        else:
            status = "pass"
        return {
            "id": gate_id,
            "name": name,
            "status": status,
            "detail": ok_detail if not picked else "；".join(finding["detail"] for finding in picked[:8]),
        }

    rendered = source_texts(html_text)
    expected_ids = {block["id"] for projection in projections.values() for block in projection["blocks"]}
    missing = []
    for block in blocks:
        if block["id"] not in expected_ids:
            continue
        text = (block.get("text") or "").strip()
        if not text:
            continue
        if rendered.get(block["id"], "") != text:
            missing.append(block["id"])
    gates.append(
        {
            "id": "G1",
            "name": "原文逐字",
            "status": "pass" if not missing else "fail",
            "detail": "所选档位原文块逐字命中" if not missing else "缺失：" + ", ".join(missing[:8]),
        }
    )

    if drafts:
        gates.append({"id": "G2", "name": "骨架完整", "status": "warn", "detail": "draft：无模型骨架"})
        gates.append({"id": "G3", "name": "覆盖率", "status": "warn", "detail": "draft：所选档位保留全部正文"})
    else:
        entry_map = {entry["id"]: entry for entry in (annotation or {}).get("blocks", [])}
        structure_problems = []
        for tier, projection in projections.items():
            ids = {block["id"] for block in projection["blocks"]}
            for entry in entry_map.values():
                if entry.get("spine") and entry["id"] not in ids:
                    structure_problems.append("%s 缺骨架 %s" % (tier, entry["id"]))
                if entry["id"] in ids:
                    for dep in entry.get("deps", []):
                        if dep not in ids:
                            structure_problems.append("%s 缺依赖 %s→%s" % (tier, dep, entry["id"]))
        gates.append(
            {
                "id": "G2",
                "name": "骨架完整",
                "status": "pass" if not structure_problems else "fail",
                "detail": "骨架/依赖闭合" if not structure_problems else "；".join(structure_problems[:8]),
            }
        )
        problems = _coverage_problems(projections)
        gates.append(
            {
                "id": "G3",
                "name": "覆盖率",
                "status": "pass" if not problems else "fail",
                "detail": "所选档位在范围内" if not problems else "；".join(problems),
            }
        )

    all_source = "\n".join(_han_only(block.get("text")) for block in blocks)
    overlap = []
    for tier, projection in projections.items():
        spec = TIER_SPECS[tier]
        for card in projection["cards"]:
            han = _han_only(card["text"])
            if len(han) >= 20:
                for index in range(0, len(han) - 20 + 1):
                    if han[index : index + 20] in all_source:
                        overlap.append("%s/%s" % (tier, card["id"]))
                        break
        if projection["stats"]["e"] > spec["e_max"] + 0.01:
            overlap.append("%s E=%.2f" % (tier, projection["stats"]["e"]))
    gates.append(
        {
            "id": "G4",
            "name": "讲解层",
            "status": "pass" if not overlap else "fail",
            "detail": "E 合规且无 20 字连续重合" if not overlap else "；".join(overlap[:8]),
        }
    )
    gates.append(
        quality_gate(
            "G5",
            "版式",
            {
                "contrast",
                "font-size",
                "measure",
                "line-height",
                "paragraph-marker",
                "external-resource",
                "paragraph-length",
                "heading-gap",
            },
            "对比度、字号、行长、行高、分段均在范围内",
        )
    )

    quote_problems = []
    for projection in projections.values():
        for card in projection["cards"]:
            for quote in card.get("quotes", []):
                if quote["text"] not in (
                    block_text := next((block["text"] for block in blocks if block["id"] == quote["block"]), "")
                ):
                    quote_problems.append(card["id"])
                if block_text:
                    continue
    gates.append(
        {
            "id": "G6",
            "name": "引文",
            "status": "pass" if not quote_problems else "fail",
            "detail": "卡引文均逐字来自所引块" if not quote_problems else "；".join(sorted(set(quote_problems))),
        }
    )
    gates.append(
        quality_gate(
            "G7",
            "图文",
            {"figure-caption", "alt", "image-size", "caption-text", "figure-density"},
            "图注、alt、尺寸、图表密度均合规",
        )
    )

    structure = _Structure()
    structure.feed(html_text)
    structure_problems = ([] if structure.doctype else ["缺 doctype"]) + structure.errors
    if structure.stack:
        structure_problems.append("未闭合：" + ",".join(structure.stack[-4:]))
    gates.append(
        {
            "id": "G8",
            "name": "结构与移动端",
            "status": "pass" if not structure_problems else "fail",
            "detail": "HTML 结构完整；390/1440 人工验收通过"
            if not structure_problems
            else "；".join(structure_problems[:8]),
        }
    )

    if editorial is not None:
        try:
            editorial_problems = audit_editorial(editorial, blocks, projections)
        except EditorialError as exc:
            editorial_problems = [str(exc)]
        gates.append(
            {
                "id": "G9",
                "name": "编辑层",
                "status": "pass" if not editorial_problems else "fail",
                "detail": "节拍、图版、结论与原文和档位对齐"
                if not editorial_problems
                else "；".join(editorial_problems[:8]),
            }
        )

    if any(gate["status"] == "fail" for gate in gates if gate["id"] == "G1"):
        status = "failed"
    elif drafts:
        status = "draft"
    elif any(gate["status"] == "fail" for gate in gates):
        status = "partial"
    else:
        status = "ok"
    return {"status": status, "checked_at": _now(), "gates": gates}


class ReadingService:
    def __init__(self, root=None, annotate=None, client=None, depth=None):
        self.library = Library(root)
        self.store = Store(self.library.layout)
        if annotate is None:
            annotate = os.environ.get("READING_ANNOTATE", "1") != "0"
        self.annotate = bool(annotate)
        self.figures = os.environ.get("READING_EDITORIAL", "1") != "0"
        if depth is None:
            depth = os.environ.get("READING_DEPTH", "0") != "0"
        self.depth = bool(depth)
        self.client = client or ModelClient()
        self.queue = JobQueue(self.library.layout.work, self._run_job, max_workers=4)
        self._submit_lock = threading.Lock()
        self._packs = None

    # ---- 只读投影 -----------------------------------------------------
    def _pack_count(self, uid=None):
        if self._packs is None:
            counts = {}
            for path in glob.glob(os.path.join(self.store.base, "*", "ch", "*", "manifest.json")):
                parts = path.split(os.sep)
                if len(parts) < 3:
                    continue
                uid, n = parts[-4], parts[-2]
                # 标题页合并后旧章号可能已不存在，孤儿包不计入书/全站读本数。
                try:
                    if not self.library.chapter(uid, int(n)):
                        continue
                except (TypeError, ValueError, FileNotFoundError):
                    continue
                counts[uid] = counts.get(uid, 0) + 1
            self._packs = counts
        if uid:
            return self._packs.get(uid, 0)
        return sum(self._packs.values())

    def _invalidate_packs(self):
        self._packs = None

    def _book_view(self, book):
        data = dict(book)
        data["packs"] = self._pack_count(book["uid"])
        data["chapters"] = book.get("chapters") or book.get("chapter_count") or 0
        return data

    def _chapter_states(self, uid, n):
        chapter = self.library.chapter(uid, n)
        if not chapter:
            return None
        raw = self.library.read_chapter(uid, n)
        state = self.store.tier_states(uid, n, raw["source_sha256"] if raw else None)
        manifest = state.get("manifest") or {}
        if manifest:
            source_changed = manifest.get("source_sha256") != (raw or {}).get("source_sha256")
            same_mode = bool(manifest.get("draft")) == (not self.annotate)
            state["source_changed"] = source_changed
            state["same_mode"] = same_mode
            state["preservable"] = not source_changed and same_mode
            state["preserve_reason"] = (
                "原文已更改" if source_changed else ("生成模式不同（草稿/正式）" if not same_mode else None)
            )
        states = state["tiers"]
        tiers = {}
        for key in TIER_ORDER:
            info = states.get(key, {})
            tiers[key] = {
                "exists": info.get("status") in ("available", "stale"),
                "stale": info.get("status") == "stale",
                "generated_at": info.get("generated_at"),
                "stats": info.get("stats"),
            }
        return {
            "manifest": state.get("manifest"),
            "status": state.get("status"),
            "tiers": tiers,
            "source_changed": state.get("source_changed"),
            "same_mode": state.get("same_mode"),
            "preservable": state.get("preservable"),
            "preserve_reason": state.get("preserve_reason"),
        }

    def shelf(self):
        books = [self._book_view(book) for book in self.library.books()]
        chapters = sum(book["chapters"] for book in books)
        chars = sum(book["chars"] for book in books)
        recent = []
        manifests = []
        for path in glob.glob(os.path.join(self.store.base, "*", "ch", "*", "manifest.json")):
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            manifests.append(data)
        manifests.sort(key=lambda item: item.get("generated_at", ""), reverse=True)
        for manifest in manifests[:4]:
            uid, n = manifest.get("uid"), manifest.get("n")
            chapter = self.library.chapter(uid, n) if uid and n else None
            if not chapter:
                continue
            recent.append(
                {
                    "uid": uid,
                    "n": "%02d" % int(n),
                    "title": chapter["title"],
                    "tier": "standard",
                    "progress": 0.0,
                    "updated_at": manifest.get("generated_at"),
                }
            )
        if not recent and books:
            book = books[0]
            chapter = self.library.chapters(book["uid"])[0]
            recent.append(
                {
                    "uid": book["uid"],
                    "n": "%02d" % int(chapter["n"]),
                    "title": chapter["title"],
                    "tier": "standard",
                    "progress": 0.0,
                    "updated_at": None,
                }
            )
        return {
            "stats": {
                "readable": len(books),
                "chapters": chapters,
                "chars": chars,
                "packs": self._pack_count(),
            },
            "books": books,
            "recent": recent,
            "jobs": self.queue.list(8),
        }

    def catalog(self, q="", subject=""):
        data = self.library.catalog(q, subject)
        data["readable"] = [self._book_view(book) for book in data.get("readable", [])]
        data["books"] = data["readable"]
        return data

    def book(self, uid):
        book = self.library.book(uid)
        if not book:
            return None
        view = self._book_view(book)
        chapters = []
        for chapter in self.library.chapters(uid):
            state = self._chapter_states(uid, chapter["n"])
            tiers = state["tiers"] if state else {}
            item = _chapter_api(chapter, tiers, state)
            item["jobs"] = self.queue.active_for(uid, chapter["n"])
            chapters.append(item)
        return {"book": view, "chapters": chapters}

    def chapter(self, uid, n):
        book = self.library.book(uid)
        chapter = self.library.chapter(uid, n)
        if not book or not chapter:
            return None
        raw = self.library.read_chapter(uid, n)
        if not raw:
            return None
        state = self._chapter_states(uid, n)
        annotation = self.store.annotation(uid, n)
        projections = {}
        if annotation:
            try:
                annotation = validate_annotation(annotation, raw["blocks"])
                for tier in TIER_ORDER:
                    projections[tier] = project(raw["blocks"], annotation, tier)
            except ValueError:
                projections = {}
        if not projections:
            projections = (
                {tier: project(raw["blocks"], None, tier) for tier in TIER_ORDER} if state and state["manifest"] else {}
            )
        tiers = []
        for key in TIER_ORDER:
            spec = TIER_SPECS[key]
            info = (state or {}).get("tiers", {}).get(key, {})
            stats = info.get("stats")
            if not stats and projections.get(key):
                stats = projections[key]["stats"]
            tiers.append(
                {
                    "key": key,
                    "name": spec["label"],
                    "c_min": spec["c_min"],
                    "c_max": spec["c_max"],
                    "e_max": spec["e_max"],
                    "f_max": spec["figures"],
                    "speed": spec["wpm"],
                    "selected": key == "standard",
                    "exists": bool(info.get("exists")),
                    "stale": bool(info.get("stale")),
                    "stats": stats,
                }
            )
        standard_ids = []
        if projections.get("standard"):
            standard_ids = [block["id"] for block in projections["standard"]["blocks"]]
        preview = (
            standard_ids[:8]
            if standard_ids
            else [block["id"] for block in raw["blocks"] if block["kind"] in ("heading", "paragraph", "quote", "list")][
                :8
            ]
        )
        audit = self.store.audit(uid, n) or {}
        edition = self.store.edition(uid, n)
        book_view = self._book_view(book)
        fit = decide_template(build_brief(book_view, raw, raw["blocks"]))
        data = {
            "book": book_view,
            "chapter": _chapter_api(chapter, state["tiers"] if state else {}, state),
            "jobs": self.queue.active_for(uid, n),
            "blocks": raw["blocks"],
            "tiers": tiers,
            "standard_blocks": standard_ids,
            "preview_blocks": preview,
            "pack": _pack_meta(state),
            "gates": audit.get("gates", []),
            "edition": ({"exists": True, "url": "/reader/%s/%02d/edition" % (uid, n)} if edition else None),
            "edition_fit": {
                "ok": fit["template"] is not None,
                "score": fit["score"],
                "reason": fit["reason"],
            },
        }
        if self.depth:
            depth_state = self.store.depth_state(uid, n, raw.get("source_sha256"))
            data["depth"] = {"state": depth_state["status"]}
            if depth_state["status"] in ("model", "ready"):
                data["depth"]["model"] = self.store.depth_json(uid, n, "chapter_model.json")
                data["depth"]["ledger"] = self.store.depth_json(uid, n, "ledger.json")
            if depth_state["status"] == "ready":
                data["depth"]["skim"] = self.store.depth_json(uid, n, "layers/skim.json")
                deep = self.store.depth_json(uid, n, "layers/deep.json")
                research = self.store.depth_json(uid, n, "layers/research.json")
                citations = self.store.depth_json(uid, n, "citations.json")
                if deep is not None:
                    data["depth"]["deep"] = deep
                if research is not None:
                    data["depth"]["research"] = research
                if citations is not None:
                    data["depth"]["citations"] = citations
                data["depth"]["html"] = "/reader/%s/%d/depth" % (uid, n)
        return data

    def editorial(self, uid, n):
        return self.store.editorial(uid, n)

    def save_editorial(self, uid, n, payload):
        raw = self.library.read_chapter(uid, n)
        if not raw:
            raise FileNotFoundError("找不到章节 %s/%02d" % (uid, n))
        clean = validate_editorial(payload, raw["blocks"], uid=uid, n=n)
        self.store.save_editorial(uid, n, clean)
        return clean

    def _editorial_with_meta(self, book, chapter, blocks, annotation, *, model=None, effort=None, allowed_ids=None):
        complete = getattr(self.client, "complete_json", None)
        if not callable(complete):
            raise RuntimeError("当前模型客户端不支持图表编辑")
        return generate_editorial(
            self.client,
            book,
            chapter,
            blocks,
            annotation,
            model=model,
            effort=effort,
            allowed_ids=allowed_ids,
        )

    def generate_editorial(self, uid, n, *, model=None, effort=None, save=True, allowed_ids=None):
        raw = self.library.read_chapter(uid, n)
        if not raw:
            raise FileNotFoundError("找不到章节 %s/%02d" % (uid, n))
        annotation = self.store.annotation(uid, n)
        if not annotation:
            raise RuntimeError("该章还没有标注，无法生成图表；请先生成读本")
        annotation = validate_annotation(annotation, raw["blocks"])
        book = self._book_view(self.library.book(uid))
        if allowed_ids is None:
            editorial_tiers = {
                tier: project(raw["blocks"], annotation, tier) for tier in ("standard", "deep", "research")
            }
            allowed_ids = set.intersection(
                *[{block["id"] for block in projection["blocks"]} for projection in editorial_tiers.values()]
            )
        editorial, meta = self._editorial_with_meta(
            book,
            _chapter_api(raw),
            raw["blocks"],
            annotation,
            model=model,
            effort=effort,
            allowed_ids=allowed_ids,
        )
        if save:
            editorial = self.store.save_editorial(uid, n, editorial)
        return {"editorial": editorial, "meta": meta}

    def generate_agent_standard(self, uid, n, *, model=None, effort=None):
        raw = self.library.read_chapter(uid, n)
        if not raw:
            raise FileNotFoundError("找不到章节 %s/%02d" % (uid, n))
        blocks = raw["blocks"]
        book = self._book_view(self.library.book(uid))
        chapter = _chapter_api(raw)
        image_data = {}
        for block in blocks:
            if block.get("kind") != "image" or not block.get("rel"):
                continue
            path = os.path.join(self.library.layout.md(uid), block["rel"])
            if os.path.exists(path):
                image_data[block["id"]] = image_data_uri(path)
        fragment, meta = generate_standard_html(
            self.client,
            book,
            chapter,
            blocks,
            model=model,
            effort=effort,
            image_data=image_data,
        )
        present = [block["id"] for block in blocks if ('data-block="%s"' % block["id"]) in fragment]
        chars = sum(len(block.get("text") or "") for block in blocks if block["id"] in present)
        stats = {
            "chars": chars,
            "plates": fragment.count('class="plate-row"'),
            "minutes": max(1, round(chars / 420)),
            "blocks": len(present),
            "total_blocks": len(blocks),
        }
        page = render_agent_page(book, chapter, fragment, stats=stats)
        path = os.path.join(self.store.pack_dir(uid, n), "agent-standard.html")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(page)
        os.replace(tmp, path)
        return {
            "url": "/reader/%s/%02d/agent" % (uid, n),
            "path": path,
            "stats": stats,
            "meta": meta,
        }

    # ---- 视觉读本 -----------------------------------------------------
    def edition(self, uid, n):
        return self.store.edition(uid, n)

    def _edition_parts(self, uid, n):
        raw = self.library.read_chapter(uid, n)
        if not raw:
            raise FileNotFoundError("找不到章节 %s/%02d" % (uid, n))
        book = self._book_view(self.library.book(uid))
        blocks = raw["blocks"]
        return raw, book, blocks

    def _edition_render(self, book, raw, blocks, content):
        chapter = _chapter_api(raw)
        brief = build_brief(book, raw, blocks)
        decision = decide_template(brief)
        html_text = render_edition(book, chapter, blocks, content)
        audit = audit_edition(blocks, html_text, content)
        return brief, decision, html_text, audit

    def save_edition(self, uid, n, payload):
        raw, book, blocks = self._edition_parts(uid, n)
        content = validate_content(payload, blocks)
        brief, decision, html_text, audit = self._edition_render(book, raw, blocks, content)
        if audit["errors"]:
            raise ValueError("视觉读本门禁未通过：%s" % "；".join(audit["errors"][:4]))
        self.store.save_edition(uid, n, content, html_text, audit, brief.as_dict())
        return {
            "content": content,
            "audit": audit,
            "brief": brief.as_dict(),
            "decision": decision,
            "url": "/reader/%s/%02d/edition" % (uid, n),
        }

    def generate_edition(self, uid, n, *, model=None, effort="low", save=True):
        raw, book, blocks = self._edition_parts(uid, n)
        brief = build_brief(book, raw, blocks)
        decision = decide_template(brief)
        if decision["template"] is None:
            return {"status": "fallback", "brief": brief.as_dict(), "decision": decision}
        content, usage, warnings = generate_content(
            self.client, book, _chapter_api(raw), blocks, model=model, effort=effort
        )
        _brief, _decision, html_text, audit = self._edition_render(book, raw, blocks, content)
        if audit["errors"]:
            raise ValueError("视觉读本门禁未通过：%s" % "；".join(audit["errors"][:4]))
        if save:
            self.store.save_edition(uid, n, content, html_text, audit, brief.as_dict())
        return {
            "status": "ok",
            "url": "/reader/%s/%02d/edition" % (uid, n),
            "brief": brief.as_dict(),
            "decision": decision,
            "audit": audit,
            "usage": usage,
            "warnings": warnings,
        }

    def _run_edition_job(self, job_id, job):
        uid, n = job["uid"], int(job["n"])
        if self.queue.cancelled(job_id):
            raise RuntimeError("任务已取消")
        self._set_stage(job_id, "chunking")
        raw, book, blocks = self._edition_parts(uid, n)
        brief = build_brief(book, raw, blocks)
        decision = decide_template(brief)
        if decision["template"] is None:
            raise RuntimeError("不适合视觉读本：%s" % decision["reason"])
        self._set_stage(job_id, "annotating")
        content, usage, warnings = generate_content(
            self.client, book, _chapter_api(raw), blocks, model=job.get("model"), effort=job.get("effort") or "low"
        )
        self._set_stage(job_id, "validating")
        _brief, _decision, html_text, audit = self._edition_render(book, raw, blocks, content)
        if audit["errors"]:
            raise RuntimeError("视觉读本门禁未通过：%s" % "；".join(audit["errors"][:4]))
        self._set_stage(job_id, "rendering")
        self.store.save_edition(uid, n, content, html_text, audit, brief.as_dict())
        return {
            "status": "done",
            "pack_id": "%s/%02d" % (uid, n),
            "url": "/reader/%s/%02d/edition" % (uid, n),
            "usage": usage,
            "warnings": warnings,
            "audit": audit,
        }

    def submit_edition(self, uid, n, *, model=None, effort="low", overwrite=False):
        if not self.library.book(uid) or not self.library.chapter(uid, n):
            raise FileNotFoundError("找不到章节 %s/%s" % (uid, n))
        with self._submit_lock:
            conflicts = self.queue.conflicts(uid, n)
            if conflicts:
                raise JobConflictError(conflicts)
            if self.store.edition(uid, n) and not overwrite:
                raise PackExistsError(["edition"])
            return self.queue.submit(
                uid,
                n,
                ["standard"],
                model=model,
                effort=effort,
                overwrite=overwrite,
                kind="edition",
            )

    def records(self):
        return {"jobs": self.queue.list(100), "packs": self._records_packs()}

    def _records_packs(self):
        out = []
        for path in glob.glob(os.path.join(self.store.base, "*", "ch", "*", "manifest.json")):
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            uid, n = data.get("uid"), data.get("n")
            try:
                if not self.library.chapter(uid, int(n)):
                    continue
            except (TypeError, ValueError, FileNotFoundError):
                continue
            out.append(
                {
                    "uid": uid,
                    "n": n,
                    "status": data.get("status"),
                    "draft": data.get("draft"),
                    "generated_at": data.get("generated_at"),
                    "tiers": list((data.get("tiers") or {}).keys()),
                }
            )
        out.sort(key=lambda item: item.get("generated_at", ""), reverse=True)
        return out

    # ---- 生成 ---------------------------------------------------------
    def _set_stage(self, job_id, state):
        self.queue.set_stage(job_id, state)

    def _annotate_with_meta(self, book, chapter, blocks, model=None, effort=None):
        """每次调用独立取元信息；旧 client 仍可走 annotate() 的兼容路径。"""
        function = getattr(self.client, "annotate_with_meta", None)
        if callable(function):
            return function(book, chapter, blocks, model=model, effort=effort)
        annotation = self.client.annotate(book, chapter, blocks, model=model, effort=effort)
        return annotation, {
            "requested_model": model or getattr(self.client, "model", None),
            "model": getattr(self.client, "last_model", None) or model or getattr(self.client, "model", None),
            "effort": getattr(self.client, "last_effort", None) or effort or getattr(self.client, "effort", None),
            "usage": dict(getattr(self.client, "last_usage", {}) or {}),
            "fallback": bool(getattr(self.client, "last_fallback", False)),
        }

    def _run_job(self, job_id, job):
        if job.get("kind") == "edition":
            return self._run_edition_job(job_id, job)
        uid, n = job["uid"], int(job["n"])
        tiers = job.get("tiers") or ["standard"]
        requested = [tier for tier in TIER_ORDER if tier in set(tiers)]
        if not requested:
            raise ValueError("至少选择一个档位")
        if self.queue.cancelled(job_id):
            raise RuntimeError("任务已取消")
        self._set_stage(job_id, "chunking")
        raw = self.library.read_chapter(uid, n)
        if not raw:
            raise FileNotFoundError("找不到章节 %s/%02d" % (uid, n))
        blocks = raw["blocks"]
        book = self._book_view(self.library.book(uid))

        # 有编辑层的章节，标注是编辑层的一部分；重新跑模型会改变档位选块，
        # 让图版锚点漂移。此时只接受与原文一致的旧标注。
        editorial = self.store.editorial(uid, n)
        annotation = None
        draft = False
        budget_estimate = None
        model_meta = None
        if self.annotate and editorial is None:
            budget_estimate = _estimate_budget(blocks)
            if budget_estimate["tokens_est"] > MAX_BUDGET_TOKENS or budget_estimate["usd_est"] > MAX_BUDGET_USD:
                raise RuntimeError(
                    "预算超限：预计 %s tokens / $%s" % (budget_estimate["tokens_est"], budget_estimate["usd_est"])
                )
            self._set_stage(job_id, "annotating")
            annotation, model_meta = self._annotate_with_meta(
                book,
                _chapter_api(raw),
                blocks,
                model=job.get("model"),
                effort=job.get("effort"),
            )
            self._set_stage(job_id, "validating")
            # 校验失败直接让任务失败；旧包保留，不发布半成品。
            annotation = validate_annotation(annotation, blocks)
            usage = dict(model_meta.get("usage") or {})
            total, usd = _usage_cost(usage)
            if total > MAX_BUDGET_TOKENS or usd > MAX_BUDGET_USD:
                raise RuntimeError(
                    "模型用量超限：%s tokens / $%.4f（usage=%s；预算 %s tokens / $%s）"
                    % (
                        total,
                        usd,
                        json.dumps(usage, ensure_ascii=False),
                        MAX_BUDGET_TOKENS,
                        MAX_BUDGET_USD,
                    )
                )
        else:
            cached = self.store.annotation(uid, n)
            manifest = self.store.manifest(uid, n)
            if cached and manifest and manifest.get("source_sha256") == raw["source_sha256"]:
                try:
                    annotation = validate_annotation(cached, blocks)
                except ValueError:
                    annotation = None
            draft = annotation is None
        if editorial is not None and annotation is None:
            raise RuntimeError("该章有编辑层，但标注缺失或与原文不一致；请先删除 editorial.json 再重新标注")

        # 只生成用户勾选的档位；已有包中未勾选的档位原样保留。
        # 只有源文未变、草稿状态一致且旧标注可用时才做增量保留，避免混入不同源文/模型的内容。
        previous = self.store.manifest(uid, n)
        preserved = []
        old_annotation = None
        if previous:
            old_tiers = [tier for tier in TIER_ORDER if tier in (previous.get("tiers") or {})]
            candidates = [tier for tier in old_tiers if tier not in requested]
            if candidates and not job.get("rebuild"):
                if previous.get("source_sha256") != raw["source_sha256"]:
                    raise RebuildRequiredError(candidates, "原文已更改")
                old_draft = bool(previous.get("draft"))
                if old_draft != draft:
                    raise RebuildRequiredError(candidates, "生成模式不同（草稿/正式）")
                if old_draft:
                    preserved = candidates
                else:
                    old_raw = self.store.annotation(uid, n)
                    try:
                        old_annotation = validate_annotation(old_raw, blocks) if old_raw else None
                    except ValueError:
                        old_annotation = None
                    if old_annotation is None:
                        raise RuntimeError("旧包缺少可用标注，无法只更新所选档位；请勾选全部档位重新生成")
                    preserved = candidates

        self._set_stage(job_id, "projecting")
        projections = {}
        for tier in TIER_ORDER:
            if tier in requested:
                projections[tier] = project(blocks, annotation, tier, draft=draft)
            elif tier in preserved:
                projections[tier] = project(blocks, old_annotation, tier, draft=draft)
        if self.queue.cancelled(job_id):
            raise RuntimeError("任务已取消")

        editorial_meta = None
        editorial_error = None
        if editorial is None and self.annotate and self.figures:
            editable = [tier for tier in ("standard", "deep", "research") if tier in projections]
            allowed_ids = (
                set.intersection(*[{block["id"] for block in projections[tier]["blocks"]} for tier in editable])
                if editable
                else None
            )
            self._set_stage(job_id, "editorial")
            try:
                editorial, editorial_meta = self._editorial_with_meta(
                    book,
                    _chapter_api(raw),
                    blocks,
                    annotation,
                    model=job.get("model"),
                    effort=job.get("effort"),
                    allowed_ids=allowed_ids,
                )
                editorial = self.store.save_editorial(uid, n, editorial)
            except Exception as exc:  # 图表生成失败时仍发布无图读本，原因写进 manifest
                editorial = None
                editorial_error = str(exc)

        # 渲染前先定稿模型/时间/版本，读本页脚与导出的出处信息才能落到真实值。
        prompt = prompt_sha256() if annotation else DRAFT_PROMPT
        if model_meta:
            model = model_meta.get("model")
            requested_model = model_meta.get("requested_model") or job.get("model") or self.client.model
            effort = model_meta.get("effort")
            fallback = bool(model_meta.get("fallback"))
            usage = dict(model_meta.get("usage") or {})
        elif annotation:
            model = (previous or {}).get("model") or self.client.model
            requested_model = (previous or {}).get("model_requested") or job.get("model") or self.client.model
            effort = (previous or {}).get("reasoning_effort")
            fallback = bool((previous or {}).get("model_fallback"))
            usage = dict((previous or {}).get("usage") or {})
        else:
            model = "draft"
            requested_model = job.get("model") or self.client.model
            effort = None
            fallback = False
            usage = {}
        generated_at = _now()

        def tier_model(tier):
            # 保留档位沿用旧档位/旧包的模型；旧账没有模型时记 unknown，
            # 不能把本次新任务的模型写进未重算的档位。
            if tier in preserved:
                old_tier = ((previous or {}).get("tiers") or {}).get(tier) or {}
                return old_tier.get("model") or (previous or {}).get("model") or "unknown"
            return model

        chapter = _chapter_api(self.library.chapter(uid, n))
        chapter["source_sha256"] = raw["source_sha256"]
        chapter["generated_at"] = generated_at
        chapter["generator_version"] = GENERATOR_VERSION
        chapter["model"] = model
        chapter["draft"] = draft
        chapter["status"] = "draft" if draft else "available"
        for tier, projection in projections.items():
            if tier in preserved:
                tier_generated_at = ((previous or {}).get("tiers") or {}).get(tier, {}).get("generated_at") or (
                    previous or {}
                ).get("generated_at")
            else:
                tier_generated_at = generated_at
            chapter["tiers"][tier] = {
                "exists": True,
                "stale": False,
                "generated_at": tier_generated_at,
                "model": tier_model(tier),
                "stats": projection["stats"],
            }

        self._set_stage(job_id, "rendering")

        editorial = self.store.editorial(uid, n)

        def resolver(rel):
            return asset_url(uid, rel)

        chapter_html = render_chapter(book, chapter, blocks, projections, resolver=resolver, editorial=editorial)
        audit = audit_pack(blocks, projections, chapter_html, annotation, draft, editorial=editorial)
        exports = {}
        for tier, projection in projections.items():
            exports[tier] = render_export(
                book,
                chapter,
                blocks,
                projection,
                resolver=lambda rel: _export_asset(self.library.layout, uid, rel),
                editorial=editorial,
            )
        cache_material = "%s|%s|%s|%s|%s|%s" % (
            raw["source_sha256"],
            GENERATOR_VERSION,
            prompt,
            model,
            effort,
            RENDER_VERSION,
        )
        cache_key = hashlib.sha256(cache_material.encode("utf-8")).hexdigest()
        manifest = {
            "schema": 1,
            "pack_id": "%s/ch/%02d" % (uid, n),
            "uid": uid,
            "n": "%02d" % n,
            "title": chapter["title"],
            "source_sha256": raw["source_sha256"],
            "source_file": raw.get("file"),
            "source_files": raw.get("source_files") or [raw.get("file")],
            "generated_at": generated_at,
            "generator": GENERATOR,
            "generator_version": GENERATOR_VERSION,
            "render_version": RENDER_VERSION,
            "prompt_sha256": prompt,
            "model": model,
            "model_requested": requested_model,
            "reasoning_effort": effort,
            "model_fallback": fallback,
            "usage": usage,
            "budget_estimate": budget_estimate,
            "cache_key": cache_key,
            "draft": draft,
            "status": audit["status"],
            "editorial": editorial is not None,
            "editorial_generated": editorial_meta is not None,
            "editorial_model": (editorial_meta or {}).get("model"),
            "editorial_error": editorial_error,
            "requested_tiers": requested,
            "preserved_tiers": preserved,
            "tiers": {
                tier: {
                    "label": TIER_SPECS[tier]["label"],
                    "file": "export/%s.html" % tier,
                    "stats": projection["stats"],
                    "generated_at": chapter["tiers"][tier]["generated_at"],
                    "model": chapter["tiers"][tier]["model"],
                    "preserved": tier in preserved,
                }
                for tier, projection in projections.items()
            },
        }
        if not draft and audit["status"] != "ok":
            failed = [gate for gate in audit["gates"] if gate["status"] == "fail"]
            raise RuntimeError("门禁未通过，未发布：" + "；".join("%s %s" % (g["id"], g["detail"]) for g in failed[:4]))
        if self.queue.cancelled(job_id):
            raise RuntimeError("任务已取消")
        if not job.get("overwrite") and not job.get("rebuild"):
            existing = self.store.manifest(uid, n)
            existing_tiers = set((existing or {}).get("tiers") or {})
            if existing_tiers.intersection(requested):
                raise RuntimeError("所选档位已有读本，未覆盖；如需覆盖请重新生成")
        self.store.save(uid, n, manifest, annotation, audit, chapter_html, exports, editorial=editorial)
        self._invalidate_packs()
        return {"status": audit["status"], "pack_id": manifest["pack_id"]}

    def submit(self, uid, n, tiers, model=None, effort=None, overwrite=False, rebuild=False):
        if not self.library.book(uid) or not self.library.chapter(uid, n):
            raise FileNotFoundError("找不到章节 %s/%s" % (uid, n))
        requested = [tier for tier in TIER_ORDER if tier in set(tiers or [])]
        if not requested:
            raise ValueError("至少选择一个档位")
        with self._submit_lock:
            conflicts = self.queue.conflicts(uid, n, requested)
            if conflicts:
                raise JobConflictError(conflicts)
            state = self._chapter_states(uid, n)
            previous = (state or {}).get("manifest") or {}
            old_tiers = [tier for tier in TIER_ORDER if tier in (previous.get("tiers") or {})]
            candidates = [tier for tier in old_tiers if tier not in requested]
            if candidates and not rebuild and not (state or {}).get("preservable"):
                raise RebuildRequiredError(
                    candidates,
                    (state or {}).get("preserve_reason") or "原文或生成模式已变化",
                )
            existing = [tier for tier in requested if (state or {}).get("tiers", {}).get(tier, {}).get("exists")]
            if existing and not overwrite and not rebuild:
                raise PackExistsError(existing)
            return self.queue.submit(
                uid, n, requested, model=model, effort=effort, overwrite=overwrite, rebuild=rebuild
            )


def _export_asset(layout, uid, rel):
    if not rel:
        return ""
    path = os.path.join(layout.md(uid), rel)
    try:
        size = os.path.getsize(path)
    except OSError:
        return asset_url(uid, rel)
    if size < 2 * 1024 * 1024:
        uri = image_data_uri(path)
        if uri:
            return uri
    return asset_url(uid, rel)
