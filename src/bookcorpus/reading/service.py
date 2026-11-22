"""阅读工作台编排服务：完整正文直出、划词伴读、知识地图与操作日志。"""

from __future__ import annotations

import os
import time
import uuid

from .. import corpus_snapshot
from .. import preflight as env_preflight
from . import book_types, chapmap, diagram_audit, diagrams, events, footnotes
from .library import Library
from .model import DEFAULT_EFFORT, DEFAULT_MODEL, ModelClient
from .preflight import chapter_map_gate, evaluate
from .store import Store

ASSIST_ACTIONS = ("diagram", "explain", "translate", "critique", "ask")
EXTENSION_ACTIONS = tuple(diagram_audit.EXTENSION_PROMPT_SPECS.keys())
ALL_ASSIST_ACTIONS = ASSIST_ACTIONS + EXTENSION_ACTIONS
ACTION_LABELS = {
    "chapter_map": "知识地图",
    "diagram": "图解此段",
    "explain": "讲人话举例",
    "translate": "翻译原意",
    "critique": "拆解前提",
    "ask": "自由追问",
    "checklist": "行动自检清单",
    "imagery": "意象与舞台词典",
    "compare_context": "多史家视角对照",
    "boundary_check": "演进与适用边界",
    "sparring_dossier": "学术交锋档案",
    "method_audit": "方法与样本局限",
    "craft_notes": "工艺流派考据",
    "pin": "钉住旁批",
    "unpin": "移除旁批",
}
DEFAULT_TOOLBAR = ("explain", "translate", "diagram", "critique", "ask")
WORDS_PER_MINUTE = 480


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _elapsed_ms(started):
    return max(0, int((time.perf_counter() - started) * 1000))


def _usage_tokens(usage):
    if not isinstance(usage, dict):
        return 0
    total = int(usage.get("total_tokens") or 0)
    if total:
        return total
    return int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0) + int(
        usage.get("completion_tokens") or usage.get("output_tokens") or 0
    )


def _block_preview_text(block):
    text = (block.get("text") or "").strip()
    if text:
        return text
    items = block.get("items") or []
    if items:
        return "\n".join(str(item) for item in items)
    rows = block.get("rows") or []
    if rows:
        return "\n".join(" | ".join(str(cell) for cell in row) for row in rows if isinstance(row, list))
    return ""


def _summarize_chapter_map(model_doc, blocks):
    """将结构模型收口成工作台左侧/顶部可直接渲染的知识地图。"""
    unit_by_id = {u.get("id"): u for u in (model_doc.get("units") or []) if isinstance(u, dict) and u.get("id")}
    sections = []
    for sec in model_doc.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        sec_blocks = []
        seen_blocks = set()
        nodes = []
        for uid_item in sec.get("unit_ids") or []:
            unit = unit_by_id.get(uid_item)
            if not unit:
                continue
            for bid in unit.get("source_blocks") or []:
                if bid not in seen_blocks:
                    seen_blocks.add(bid)
                    sec_blocks.append(bid)
            nodes.append(
                {
                    "id": uid_item,
                    "kind": unit.get("kind") or "claim",
                    "importance": unit.get("importance") or "support",
                    "summary": unit.get("summary") or "",
                    "block_ids": list(unit.get("source_blocks") or []),
                    "hard_info": bool(unit.get("hard_info")),
                    "hard_info_note": unit.get("hard_info_note") or "",
                }
            )
        sections.append(
            {
                "id": sec.get("id") or "",
                "title": sec.get("title") or "未命名节拍",
                "summary": sec.get("summary") or "",
                "block_ids": sec_blocks,
                "nodes": nodes,
            }
        )
    terms = []
    for term in model_doc.get("terms") or []:
        if not isinstance(term, dict) or not term.get("term"):
            continue
        terms.append(
            {
                "term": term.get("term") or "",
                "definition": term.get("definition") or "",
                "source_block": term.get("source_block") or "",
                "quote": term.get("quote") or "",
            }
        )
    figures = []
    for fig in model_doc.get("figures") or []:
        if not isinstance(fig, dict):
            continue
        figures.append(
            {
                "id": fig.get("id") or "",
                "family": fig.get("family") or "flow",
                "caption": fig.get("caption") or "",
                "reading_note": fig.get("reading_note") or "",
                "html": diagrams.render_assist_figure(fig),
            }
        )
    return {
        "sections": sections,
        "terms": terms,
        "figures": figures,
        "stats": {
            "sections": len(sections),
            "units": len(unit_by_id),
            "terms": len(terms),
            "figures": len(figures),
            "blocks": len(blocks or []),
        },
    }


def _default_toolbar():
    return [{"action": act, "label": ACTION_LABELS[act]} for act in DEFAULT_TOOLBAR]


def _assist_prompts(
    action,
    *,
    book,
    chapter,
    selected_text,
    block_ids,
    context_text,
    question="",
    blueprint="",
    read_type="",
    sub_kind="",
    scope="selection",
    covered_blocks=0,
    sampled=False,
):
    book_title = (book or {}).get("title") or ""
    chapter_title = (chapter or {}).get("title") or ""
    loc = f"《{book_title}》· {chapter_title}（段落 {', '.join(block_ids) or '整章'}）"
    context_block = f"相邻上下文：\n{context_text}\n\n" if context_text else ""
    focus_block = f"读者选中的原文：\n{selected_text}" if selected_text else "读者针对当前章节提问。"

    if action == "diagram":
        return diagram_audit.prompt_for_diagram(
            blueprint=blueprint,
            read_type=read_type,
            book=book,
            chapter=chapter,
            selected_text=selected_text,
            block_ids=block_ids,
            context_text=context_text,
            scope=scope,
            covered_blocks=covered_blocks,
            sampled=sampled,
        )
    if action in EXTENSION_ACTIONS:
        return diagram_audit.prompt_for_extension(
            action,
            sub_kind=sub_kind,
            book=book,
            chapter=chapter,
            selected_text=selected_text,
            block_ids=block_ids,
            context_text=context_text,
            scope=scope,
            covered_blocks=covered_blocks,
            sampled=sampled,
        )

    if action == "explain":
        system = (
            "你是耐心的伴读老师。读者选中了一段觉得抽象或不好懂的文字。\n"
            "请用大白话拆解它到底在说什么，并举一个具体、贴近日常或工程实际的例子。\n"
            "格式要求（直接输出清晰 Markdown，不要废话开头）：\n"
            "1. **一句话说透**（用最直白的话讲清核心意思）\n"
            "2. **举个例子**（可用书外现实或工程例子帮助理解，允许并鼓励跳出原文）\n"
            "3. **为什么这里要提它**（放回当前章节语境的意义）\n"
            "遇到源于原文的判断标 `[原文]`，补充常识或类比标 `[补充]`，让读者一眼分清。"
        )
    elif action == "translate":
        system = (
            "你是双语、古今汉语及方言旧词对读编辑。"
            "请严格聚焦读者选中的文本本身，按其语言特征提供直接对读辅助：\n"
            "- 如果是文言/古文/半文言/近代白话旧词或方言俗语："
            "给出流畅准确的【现代汉语直译】，再列出【关键实词/典故/旧词注】；\n"
            "- 如果是中译本（翻译腔或术语）：给出更自然的重述，"
            "并尽可能还原对应的【英文原词/概念】，解释中文译名可能遮蔽的语境；\n"
            "- 如果是英文原文：给出信达雅的中文翻译，并标注关键学术/行业术语。\n"
            "只输出与所选原文直接相关的译文、词注与语境还原，"
            "不要附加章节主旨分析或阅读指南。原文词义标 `[原文]`，语言或背景延展标 `[补充]`。"
        )
    elif action == "critique":
        system = (
            "你是严谨的文本细读同伴。读者主动要求拆解所选段落背后未明说的前提或视角。\n"
            "请克制、具体、不空泛地列出：\n"
            "1. **明面表述** `[原文]`\n"
            "2. **未明说的隐含前提** `[补充]`（这段话成立或如此叙述时默认了什么条件、立场或背景？）\n"
            "3. **适用边界 / 反例情景** `[补充]`（什么情况下这句话会失效、片面或需要限定？）"
        )
    else:  # ask
        system = (
            "你是随身伴读助手。紧扣读者选中的原文与前后上下文回答问题。"
            "回答直接切中要点；引用书中已有事实请标 `[原文]`，涉及书外背景知识或推理补充请标 `[补充]`。"
        )

    user = f"{loc}\n\n{context_block}{focus_block}"
    if question:
        user += f"\n\n读者追问：{question}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


class ReadingService:
    """8771 阅读工作台服务层。"""

    def __init__(self, root=None, annotate=None, client=None):
        self.library = Library(root)
        self.root = str(self.library.root)
        self.store = Store(self.library.layout)
        if annotate is None:
            annotate = os.environ.get("READING_ANNOTATE", "1") != "0"
        self.annotate = bool(annotate)
        self.client = client or ModelClient()
        self.library.prepare_index()

    def _require_model_client(self):
        if self.client.available():
            return
        env_preflight.require(["reading.model"])

    def _book_view(self, book, counts=None):
        data = dict(book)
        by_book = (counts or self.store.activity_counts()).get("by_book") or {}
        data["packs"] = by_book.get(book["uid"], 0)
        data["chapters"] = book.get("chapters") or book.get("chapter_count") or 0
        return data

    # ---- 只读书架 / 书目 / 书页 / 章节 --------------------------------
    def shelf(self):
        counts = self.store.activity_counts()
        books = [self._book_view(book, counts) for book in self.library.books()]
        # 页头四个数从统一快照来：与 8765 / 8770 同一时刻同一组数。
        # 快照缺源（如盘上还没建 library.db）才退回逐本求和——那只能数到
        # 能解析的书，缺库时页头先给「—」比给小一号的数诚实。
        snap = corpus_snapshot.snapshot(self.root)
        readable = snap["books_readable"]
        if readable is None:
            readable = len(books)
        chapters = snap["chapters"]
        if chapters is None:
            chapters = sum(book["chapters"] for book in books)
        chars = snap["chars"]
        if chars is None:
            chars = sum(book["chars"] for book in books)
        built_at = snap["built_at"]
        recent = []
        seen_chapters = set()
        for op in self.store.ops_logs(limit=40):
            key = (op.get("uid"), op.get("n"))
            if not op.get("uid") or not op.get("n") or key in seen_chapters:
                continue
            try:
                ch = self.library.chapter(op["uid"], int(op["n"]))
            except (TypeError, ValueError):
                ch = None
            if not ch:
                continue
            seen_chapters.add(key)
            recent.append(
                {
                    "uid": op["uid"],
                    "n": "%02d" % int(op["n"]),
                    "title": ch["title"],
                    "action": ACTION_LABELS.get(op.get("action"), op.get("action") or ""),
                    "updated_at": op.get("ts"),
                }
            )
            if len(recent) >= 4:
                break
        if not recent and books:
            first_ch = self.library.chapters(books[0]["uid"])
            if first_ch:
                recent.append(
                    {
                        "uid": books[0]["uid"],
                        "n": "%02d" % int(first_ch[0]["n"]),
                        "title": first_ch[0]["title"],
                        "action": "开始阅读",
                        "updated_at": None,
                    }
                )
        return {
            "stats": {
                "readable": readable,
                "chapters": chapters,
                "chars": chars,
                "paras": snap["paras"],
                "packs": counts["chapters"],
                "notes": counts["notes"],
                "maps": counts["maps"],
                "built_at": built_at,
            },
            # 「全部书目」= 藏书（library.db 全库），不是伴读能解析的书数。
            # 快照缺 library.db 时才退回 library.total()（只能数到能解析的书）。
            "total": snap["books_total"] if snap["books_total"] is not None else self.library.total(),
            "books": books,
            "recent": recent,
        }

    def catalog(self, q="", subject=""):
        counts = self.store.activity_counts()
        data = self.library.catalog(q, subject)
        snap = corpus_snapshot.snapshot(self.root)
        data["readable"] = [self._book_view(book, counts) for book in data.get("readable", [])]
        data["books"] = data["readable"]
        # 数从快照来（与书架 / 8765 / 8770 同源）；缺 library.db 时保留逐本口径。
        if snap["books_total"] is not None:
            data["total"] = snap["books_total"]
        if snap["books_readable"] is not None:
            data["readable_count"] = snap["books_readable"]
        data["stats"] = {"built_at": snap["built_at"]}
        return data

    def book(self, uid):
        book = self.library.book(uid)
        if not book:
            return None
        counts = self.store.activity_counts()
        view = self._book_view(book, counts)
        ops_by_ch = {}
        for op in self.store.ops_logs(limit=1000, uid=uid):
            ch_n = op.get("n") or ""
            ops_by_ch[ch_n] = ops_by_ch.get(ch_n, 0) + 1
        chapters = []
        for ch in self.library.chapters(uid):
            n_int = int(ch["n"])
            n_str = "%02d" % n_int
            notes = self.store.assist_notes(uid, n_int)
            cmap = self.store.chapter_map(uid, n_int)
            item = dict(ch)
            item["n"] = n_str
            item["minutes"] = max(1, round(ch.get("chars", 0) / WORDS_PER_MINUTE))
            item["note_count"] = len(notes)
            item["has_map"] = bool(cmap and cmap.get("supported", True))
            item["ops_count"] = ops_by_ch.get(n_str, 0)
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
        source_sha = raw.get("source_sha256") or ""
        blocks = raw["blocks"]
        map_gate = chapter_map_gate(blocks)
        cap_gate = evaluate(blocks, chapmap.plan_chunks(blocks, 18000, 100)).to_dict()
        notes = self.store.assist_notes(uid, n, source_sha=source_sha)
        cmap = self.store.chapter_map(uid, n, source_sha=source_sha)
        cached_tools = self.store.list_chapter_tools(uid, n, source_sha=source_sha)
        ch_view = dict(chapter)
        ch_view["n"] = "%02d" % int(chapter["n"])
        ch_view["minutes"] = max(1, round(chapter.get("chars", 0) / WORDS_PER_MINUTE))
        ch_view["source_sha256"] = source_sha
        profile = book_types.resolve_read_profile(book.get("subjects"), title=book.get("title") or "")
        fn_meta = footnotes.enrich_cross_file_footnotes(
            blocks,
            self.library.chapters(uid),
            int(chapter["n"]),
            raw.get("source_files") or (),
            chapter_reader=lambda target_n: self.library.read_chapter(uid, target_n),
            source_title=chapter.get("title") or "",
        )
        return {
            "book": self._book_view(book),
            "chapter": ch_view,
            "blocks": blocks,
            "toolbar": _default_toolbar(),
            "reading_profile": profile,
            "preflight": {"chapter_map": map_gate, "capacity": cap_gate},
            "assist_notes": notes,
            "chapter_map": cmap,
            "cached_tools": cached_tools,
            "footnotes": fn_meta,
        }

    # ---- 划词局部伴读（POST /api/assist） ------------------------------
    def _context_for_blocks(self, blocks, block_ids):
        if not blocks:
            return ""
        id_to_idx = {b.get("id"): idx for idx, b in enumerate(blocks) if b.get("id")}
        indices = [id_to_idx[bid] for bid in block_ids if bid in id_to_idx]
        if not indices:
            sample = blocks[:4]
        else:
            lo = max(0, min(indices) - 2)
            hi = min(len(blocks), max(indices) + 3)
            sample = blocks[lo:hi]
        parts = []
        for blk in sample:
            bid = blk.get("id") or ""
            txt = _block_preview_text(blk)
            if txt:
                parts.append(f"[{bid}] {txt[:240]}")
        return "\n".join(parts)[:1800]

    def _chapter_scope_context(self, uid, n, blocks, *, source_sha="", budget=12000):
        """构造全章作用域下的带块号文本（中小章全量、超长章均匀骨架覆盖）。"""
        semantic = []
        for blk in blocks or []:
            bid = blk.get("id") or ""
            txt = _block_preview_text(blk)
            if bid and txt:
                semantic.append((bid, blk.get("kind") or "paragraph", txt))
        if not semantic:
            return "", 0, False

        total_chars = sum(len(t) for _, _, t in semantic)
        if total_chars <= budget:
            lines = [f"[{bid}] {txt[:600]}" for bid, _, txt in semantic]
            return "\n".join(lines), len(semantic), False

        parts = []
        used_chars = 0
        cmap = self.store.chapter_map(uid, n, source_sha=source_sha)
        if isinstance(cmap, dict) and not cmap.get("stale") and cmap.get("sections"):
            parts.append("【已缓存全章结构节拍】")
            for sec in cmap.get("sections") or []:
                sec_title = sec.get("title") or ""
                sec_sum = sec.get("summary") or ""
                node_hints = "；".join(
                    f"{nd.get('summary') or ''}({','.join(nd.get('block_ids') or [])})"
                    for nd in (sec.get("nodes") or [])[:6]
                )
                line = f"- {sec_title}：{sec_sum} {node_hints}".strip()
                parts.append(line)
                used_chars += len(line) + 1

        # 按整章均匀抽样代表块（保留全部小标题 + 首尾 + 等步长采样），保证覆盖 B01..Bnn 全程。
        headings = {idx for idx, (_, kind, _) in enumerate(semantic) if kind == "heading"}
        picked_idx = set(headings) | {0, min(1, len(semantic) - 1), max(0, len(semantic) - 2), len(semantic) - 1}
        non_head = [idx for idx in range(len(semantic)) if idx not in picked_idx]
        target_slots = 36
        if non_head:
            step = max(1, len(non_head) / target_slots)
            pos = 0.0
            while int(pos) < len(non_head):
                picked_idx.add(non_head[int(pos)])
                pos += step

        parts.append("【全章代表段落节选（均布覆盖）】")
        per_block_cap = max(180, min(380, (14000 - used_chars) // max(1, len(picked_idx))))
        for idx in sorted(picked_idx):
            bid, _, txt = semantic[idx]
            line = f"[{bid}] {txt[:per_block_cap]}"
            if used_chars + len(line) > 14500:
                break
            parts.append(line)
            used_chars += len(line) + 1
        return "\n".join(parts), len(semantic), True

    def _chapter_tool_cache_key(self, read_type, action, blueprint):
        key_part = blueprint if action == "diagram" and blueprint else action
        return f"{read_type or 'social'}_{action}_{key_part}"

    def cached_chapter_tool(self, uid, n, *, action, blueprint="", read_type=""):
        """若命中未过期的全章缓存则返回缓存条目，否则返回 None。"""
        book = self.library.book(uid)
        raw = self.library.read_chapter(uid, n)
        if not book or not raw:
            return None
        profile = book_types.resolve_read_profile(book.get("subjects"), title=book.get("title") or "")
        effective_rt = str(read_type or profile.get("read_type") or "").strip()
        effective_bp = str(blueprint or "").strip()
        if action == "diagram" and not effective_bp and profile.get("blueprints"):
            effective_bp = profile["blueprints"][0]["blueprint"]
        key = self._chapter_tool_cache_key(effective_rt, action, effective_bp)
        cached = self.store.chapter_tool(uid, n, key, source_sha=raw.get("source_sha256") or "")
        if isinstance(cached, dict) and not cached.get("stale"):
            return dict(cached, cached=True, scope="chapter")
        return None

    def assist(
        self,
        uid,
        n,
        action,
        *,
        block_ids=None,
        selected_text="",
        question="",
        blueprint="",
        read_type="",
        sub_kind="",
        scope=None,
        force=False,
        model=None,
        effort=None,
        on_delta=None,
    ):
        action = str(action or "").strip().lower()
        if action not in ALL_ASSIST_ACTIONS:
            raise ValueError("action 必须是 %s" % "/".join(ALL_ASSIST_ACTIONS))
        effective_scope = str(scope or "").strip().lower()
        if effective_scope and effective_scope not in ("selection", "chapter"):
            raise ValueError("scope 必须是 selection 或 chapter")
        if not effective_scope:
            effective_scope = "selection"

        book = self.library.book(uid)
        chapter = self.library.chapter(uid, n)
        raw = self.library.read_chapter(uid, n)
        if not book or not chapter or not raw:
            raise FileNotFoundError("找不到章节")

        blocks = raw["blocks"]
        source_sha = raw.get("source_sha256") or ""
        valid_ids = {b.get("id") for b in blocks if b.get("id")}
        clean_blocks = [str(bid).strip() for bid in (block_ids or []) if str(bid).strip()]
        invalid = [bid for bid in clean_blocks if bid not in valid_ids]
        if invalid:
            raise ValueError("block_ids 引用了不存在的段落：" + ", ".join(invalid))
        clean_text = str(selected_text or "").strip()
        clean_q = str(question or "").strip()

        profile = book_types.resolve_read_profile(book.get("subjects"), title=book.get("title") or "")
        effective_rt = str(read_type or profile.get("read_type") or "").strip()
        effective_sub = str(sub_kind or profile.get("sub_kind") or "").strip()
        effective_bp = str(blueprint or "").strip()
        if action == "diagram" and not effective_bp:
            bps = profile.get("blueprints") or []
            if bps:
                effective_bp = bps[0]["blueprint"]

        if effective_scope == "chapter":
            clean_blocks = []
            if not force and not clean_q:
                cache_key = self._chapter_tool_cache_key(effective_rt, action, effective_bp)
                cached = self.store.chapter_tool(uid, n, cache_key, source_sha=source_sha)
                if isinstance(cached, dict) and not cached.get("stale"):
                    return dict(cached, cached=True, scope="chapter")
            if not blocks:
                raise ValueError("本章暂无可用正文块")
        else:
            if not clean_text and clean_blocks:
                blk_map = {b.get("id"): b for b in blocks if b.get("id")}
                clean_text = "\n\n".join(
                    _block_preview_text(blk_map[bid]) for bid in clean_blocks if bid in blk_map
                ).strip()
            if not clean_text and not clean_q:
                raise ValueError("请先选中一段正文，或输入具体问题")

        self._require_model_client()
        requested_model = str(model or getattr(self.client, "model", None) or DEFAULT_MODEL).strip()
        requested_effort = str(effort or getattr(self.client, "effort", None) or DEFAULT_EFFORT).strip().lower()

        sampled = False
        if effective_scope == "chapter":
            context_text, covered_blocks, sampled = self._chapter_scope_context(uid, n, blocks, source_sha=source_sha)
            ch_label = f"《{book.get('title') or ''}》「{chapter.get('title') or ''}」"
            prompt_text = clean_q or f"请围绕{ch_label}整章内容提取核心结构。"
        else:
            context_text = self._context_for_blocks(blocks, clean_blocks)
            covered_blocks = len(clean_blocks)
            if len(clean_blocks) > 1:
                blk_map = {b.get("id"): b for b in blocks if b.get("id")}
                labeled = [
                    f"[{bid}] {_block_preview_text(blk_map[bid])}"
                    for bid in clean_blocks
                    if bid in blk_map and _block_preview_text(blk_map[bid])
                ]
                prompt_text = "\n\n".join(labeled) if labeled else clean_text
            else:
                prompt_text = clean_text

        is_structured = action == "diagram" or action in EXTENSION_ACTIONS
        messages = _assist_prompts(
            action,
            book=book,
            chapter=chapter,
            selected_text=prompt_text,
            block_ids=clean_blocks,
            context_text=context_text,
            question=clean_q,
            blueprint=effective_bp,
            read_type=effective_rt,
            sub_kind=effective_sub,
            scope=effective_scope,
            covered_blocks=covered_blocks,
            sampled=sampled,
        )

        op_id = f"op-{uuid.uuid4().hex[:10]}"
        started = time.perf_counter()
        log_dir = os.path.join(self.store.base, "logs")
        try:
            with events.job_scope(log_dir, op_id):
                try:
                    # diagram 与结构化扩展需要严格 JSON，不直接向正文流推送原始 JSON 字符。
                    raw_content, usage = self.client.complete(
                        messages,
                        model=requested_model,
                        effort=requested_effort,
                        json_mode=is_structured,
                        on_delta=None if is_structured else on_delta,
                    )
                except Exception as exc:
                    events.record_exception(exc, layer=action)
                    raise
        except Exception as exc:
            self.store.append_op(
                {
                    "id": op_id,
                    "uid": uid,
                    "n": int(n),
                    "book_title": book.get("title") or "",
                    "chapter_title": chapter.get("title") or "",
                    "action": action,
                    "block_ids": clean_blocks,
                    "status": "error",
                    "model": requested_model,
                    "tokens": 0,
                    "elapsed_ms": _elapsed_ms(started),
                    "summary": (clean_q or clean_text or "全章分析")[:120],
                    "error": str(exc),
                }
            )
            raise

        elapsed_ms = _elapsed_ms(started)
        tokens = _usage_tokens(usage)
        diagram_html = ""
        figure = None
        content = str(raw_content or "").strip()
        default_anchor = [b["id"] for b in blocks[:1] if b.get("id")] if effective_scope == "chapter" else clean_blocks
        if action == "diagram":
            parsed = diagram_audit.parse_json_payload(content)
            if isinstance(parsed, dict) and (parsed.get("data") or parsed.get("items")):
                audited = diagram_audit.audit_blueprint_payload(
                    parsed,
                    valid_ids,
                    fallback_block_ids=default_anchor,
                    requested_blueprint=effective_bp,
                )
                figure = audited
                diagram_html = diagrams.render_assist_figure(audited)
                content = str(audited.get("summary") or audited.get("reading_note") or "").strip()
            else:
                figure = None
                diagram_html = ""
        elif action in EXTENSION_ACTIONS:
            ext_bp = diagram_audit.EXTENSION_PROMPT_SPECS[action]["blueprint"]
            audited = diagram_audit.audit_blueprint_payload(
                content,
                valid_ids,
                fallback_block_ids=default_anchor,
                requested_blueprint=ext_bp,
            )
            figure = audited
            diagram_html = diagrams.render_assist_figure(audited)
            content = str(audited.get("summary") or audited.get("reading_note") or "").strip()

        summary_prefix = "全章 · " if effective_scope == "chapter" else ""
        summary_base = (clean_q or clean_text or chapter.get("title") or "").replace("\n", " ")[:90]
        summary = summary_prefix + summary_base
        if diagram_html and isinstance(figure, dict) and figure.get("caption"):
            summary = f"{summary_prefix}{figure['caption']}（{summary_base[:42]}）"
        op = self.store.append_op(
            {
                "id": op_id,
                "uid": uid,
                "n": int(n),
                "book_title": book.get("title") or "",
                "chapter_title": chapter.get("title") or "",
                "action": action,
                "block_ids": clean_blocks,
                "status": "ok",
                "model": requested_model,
                "tokens": tokens,
                "elapsed_ms": elapsed_ms,
                "summary": summary,
            }
        )
        result = {
            "id": op_id,
            "uid": uid,
            "n": "%02d" % int(n),
            "source_sha256": source_sha,
            "scope": effective_scope,
            "covered_blocks": covered_blocks,
            "sampled": sampled,
            "cached": False,
            "action": action,
            "action_label": ACTION_LABELS.get(action, action),
            "blueprint": (figure or {}).get("blueprint") or effective_bp,
            "block_ids": clean_blocks,
            "selected_text": clean_text,
            "question": clean_q,
            "content": content,
            "diagram_html": diagram_html,
            "figure": figure,
            "model": requested_model,
            "usage": usage or {},
            "tokens": tokens,
            "elapsed_ms": elapsed_ms,
            "op": op,
        }
        if effective_scope == "chapter" and not clean_q and figure is not None:
            cache_key = self._chapter_tool_cache_key(effective_rt, action, effective_bp)
            self.store.save_chapter_tool(uid, n, cache_key, result)
        return result

    # ---- 旁批钉住 / 删除 ---------------------------------------------
    def pin_assist_note(self, uid, n, payload):
        book = self.library.book(uid)
        chapter = self.library.chapter(uid, n)
        raw = self.library.read_chapter(uid, n)
        if not book or not chapter or not raw:
            raise FileNotFoundError("找不到章节")
        valid_ids = {b.get("id") for b in raw["blocks"] if b.get("id")}
        saved = self.store.pin_assist_note(
            uid,
            n,
            payload,
            source_sha=raw.get("source_sha256") or "",
            valid_block_ids=valid_ids,
        )
        pin_summary = saved.get("title") or saved.get("content", "")
        pin_summary = pin_summary.replace("**", "").replace("`", "").replace("\n", " ").strip()[:80]
        self.store.append_op(
            {
                "uid": uid,
                "n": int(n),
                "book_title": book.get("title") or "",
                "chapter_title": chapter.get("title") or "",
                "action": "pin",
                "block_ids": saved["block_ids"],
                "status": "ok",
                "summary": pin_summary,
            }
        )
        return saved

    def unpin_assist_note(self, uid, n, note_id):
        book = self.library.book(uid)
        chapter = self.library.chapter(uid, n)
        raw = self.library.read_chapter(uid, n)
        if not book or not chapter or not raw:
            raise FileNotFoundError("找不到章节")
        removed = self.store.unpin_assist_note(uid, n, note_id, source_sha=raw.get("source_sha256") or "")
        if removed is None:
            return None
        unpin_summary = removed.get("title") or removed.get("content", "")
        unpin_summary = unpin_summary.replace("**", "").replace("`", "").replace("\n", " ").strip()[:80]
        self.store.append_op(
            {
                "uid": uid,
                "n": int(n),
                "book_title": book.get("title") or "",
                "chapter_title": chapter.get("title") or "",
                "action": "unpin",
                "block_ids": removed.get("block_ids") or [],
                "status": "ok",
                "summary": unpin_summary,
            }
        )
        return {"ok": True, "deleted": note_id}

    # ---- 全章知识地图（POST /api/chapter-map/<uid>/<n>） ---------------
    def generate_chapter_map(self, uid, n, *, force=False, model=None, effort=None):
        book = self.library.book(uid)
        chapter = self.library.chapter(uid, n)
        raw = self.library.read_chapter(uid, n)
        if not book or not chapter or not raw:
            raise FileNotFoundError("找不到章节")
        blocks = raw["blocks"]
        source_sha = raw.get("source_sha256") or ""

        plans = chapmap.plan_chunks(blocks, 18000, 100)
        cap_gate = evaluate(blocks, plans)
        cap_gate.raise_if_blocked()

        if not force:
            cached = self.store.chapter_map(uid, n, source_sha=source_sha)
            if cached and not cached.get("stale") and cached.get("supported", True):
                return dict(cached, cached=True)
            short_gate = chapter_map_gate(blocks)
            if not short_gate["supported"]:
                return {
                    "schema": 1,
                    "uid": uid,
                    "n": "%02d" % int(n),
                    "source_sha256": source_sha,
                    "supported": False,
                    "can_force": True,
                    "reason": short_gate["reason"],
                    "stats": short_gate["stats"],
                }

        self._require_model_client()
        requested_model = str(model or getattr(self.client, "model", None) or DEFAULT_MODEL).strip()
        requested_effort = str(effort or getattr(self.client, "effort", None) or DEFAULT_EFFORT).strip().lower()
        op_id = f"op-{uuid.uuid4().hex[:10]}"
        started = time.perf_counter()
        log_dir = os.path.join(self.store.base, "logs")
        cache_dir = self.store.chapter_map_cache_dir(uid, n)
        try:
            with events.job_scope(log_dir, op_id):
                try:
                    model_doc, meta = chapmap.generate_chapter_model(
                        self.client,
                        book,
                        dict(chapter, source_sha256=source_sha),
                        blocks,
                        model=requested_model,
                        effort=requested_effort,
                        cache_dir=cache_dir,
                        refresh=bool(force),
                    )
                except Exception as exc:
                    events.record_exception(exc, layer="chapter_map")
                    raise
        except Exception as exc:
            self.store.append_op(
                {
                    "id": op_id,
                    "uid": uid,
                    "n": int(n),
                    "book_title": book.get("title") or "",
                    "chapter_title": chapter.get("title") or "",
                    "action": "chapter_map",
                    "block_ids": [],
                    "status": "error",
                    "model": requested_model,
                    "tokens": 0,
                    "elapsed_ms": _elapsed_ms(started),
                    "summary": chapter.get("title") or "",
                    "error": str(exc),
                }
            )
            raise

        elapsed_ms = _elapsed_ms(started)
        usage = meta.get("usage") or {}
        tokens = _usage_tokens(usage)
        view = _summarize_chapter_map(model_doc, blocks)
        payload = {
            "schema": 1,
            "uid": uid,
            "n": "%02d" % int(n),
            "source_sha256": source_sha,
            "supported": True,
            "forced": bool(force),
            "generated_at": _now_iso(),
            "model": meta.get("model") or requested_model,
            "usage": usage,
            "elapsed_ms": elapsed_ms,
            **view,
        }
        self.store.save_chapter_map(uid, n, payload)
        self.store.append_op(
            {
                "id": op_id,
                "uid": uid,
                "n": int(n),
                "book_title": book.get("title") or "",
                "chapter_title": chapter.get("title") or "",
                "action": "chapter_map",
                "block_ids": [],
                "status": "ok",
                "model": payload["model"],
                "tokens": tokens,
                "elapsed_ms": elapsed_ms,
                "summary": f"{view['stats']['sections']} 节 · {view['stats']['terms']} 术语",
            }
        )
        return payload

    # ---- 操作日志查询（GET /api/records） ----------------------------
    def records(self, *, uid=None, action=None, status=None, limit=300):
        logs = self.store.ops_logs(limit=limit, uid=uid, action=action, status=status)
        chapter_map = {}
        ok_count = 0
        err_count = 0
        pin_count = 0
        map_count = 0
        assist_count = 0
        for item in logs:
            act = item.get("action") or ""
            st = item.get("status") or "ok"
            if st == "error":
                err_count += 1
            else:
                ok_count += 1
            if act in ("pin", "unpin"):
                pin_count += 1
            elif act == "chapter_map":
                map_count += 1
            elif act in ALL_ASSIST_ACTIONS:
                assist_count += 1

            key = (item.get("uid") or "", item.get("n") or "")
            if key[0] and key[1]:
                bucket = chapter_map.setdefault(
                    key,
                    {
                        "uid": key[0],
                        "n": key[1],
                        "book_title": item.get("book_title") or "",
                        "chapter_title": item.get("chapter_title") or "",
                        "ops": 0,
                        "last_ts": item.get("ts") or "",
                        "actions": [],
                    },
                )
                bucket["ops"] += 1
                if act and act not in bucket["actions"]:
                    bucket["actions"].append(act)

        chapters = sorted(chapter_map.values(), key=lambda c: c.get("last_ts") or "", reverse=True)
        for ch in chapters:
            notes = self.store.assist_notes(ch["uid"], int(ch["n"]))
            cmap = self.store.chapter_map(ch["uid"], int(ch["n"]))
            ch["note_count"] = len(notes)
            ch["has_map"] = bool(cmap and cmap.get("supported", True))
            if not ch["book_title"]:
                bk = self.library.book(ch["uid"])
                ch["book_title"] = (bk or {}).get("title") or ch["uid"]
            if not ch["chapter_title"]:
                meta = self.library.chapter(ch["uid"], int(ch["n"]))
                ch["chapter_title"] = (meta or {}).get("title") or f"第 {ch['n']} 章"

        return {
            "logs": logs,
            "chapters": chapters,
            "summary": {
                "total": len(logs),
                "ok": ok_count,
                "error": err_count,
                "assist": assist_count,
                "chapter_map": map_count,
                "pin": pin_count,
                "chapters": len(chapters),
            },
        }
