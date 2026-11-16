"""阅读工作台编排服务：完整正文直出、划词伴读、知识地图与操作日志。"""

from __future__ import annotations

import os
import time
import uuid

from .. import preflight as env_preflight
from ..scripts import lib_common as L
from . import chapmap, diagrams, events, profile
from .library import Library
from .model import DEFAULT_EFFORT, DEFAULT_MODEL, ModelClient, _loads_content
from .preflight import chapter_map_gate, evaluate
from .store import Store

ASSIST_ACTIONS = ("diagram", "explain", "translate", "critique", "ask")
ACTION_LABELS = {
    "chapter_map": "知识地图",
    "diagram": "图解此段",
    "explain": "讲人话举例",
    "translate": "翻译原意",
    "critique": "拆解前提",
    "ask": "自由追问",
    "pin": "钉住旁批",
    "unpin": "移除旁批",
}
ACTION_PRIORITY = {
    "argument": ("diagram", "critique", "explain", "translate", "ask"),
    "practical": ("diagram", "explain", "ask", "translate", "critique"),
    "narrative": ("explain", "translate", "diagram", "ask", "critique"),
    "reference": ("diagram", "explain", "translate", "ask", "critique"),
}
WORDS_PER_MINUTE = 480


class MissingProfileError(ValueError):
    """本书尚未设定默认读法时给出明确提示，不再阻断章节页阅读。"""

    def __init__(self, uid):
        self.uid = uid
        super().__init__("这本书还没有设定默认读法")


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


def _toolbar_for_mode(mode):
    order = ACTION_PRIORITY.get(mode, ("diagram", "explain", "translate", "critique", "ask"))
    return [{"action": act, "label": ACTION_LABELS[act]} for act in order]


def _assist_prompts(action, *, mode, book, chapter, selected_text, block_ids, context_text, question=""):
    book_title = (book or {}).get("title") or ""
    chapter_title = (chapter or {}).get("title") or ""
    mode_meta = profile.MODE_META.get(mode) or profile.MODE_META["argument"]
    loc = f"《{book_title}》· {chapter_title}（段落 {', '.join(block_ids) or '整章'}）"
    context_block = f"相邻上下文：\n{context_text}\n\n" if context_text else ""
    focus_block = f"读者选中的原文：\n{selected_text}" if selected_text else "读者针对当前章节提问。"

    if action == "diagram":
        system = (
            "你是阅读伴读助手，擅长把复杂文字转成清晰的关系图。"
            "只输出一个 JSON 对象，不要 markdown 代码围栏，不要多余解释。\n"
            "JSON 字段：\n"
            '{"caption":"简短图题（≤18字）","family":"flow|tree|matrix|quantity|timeline|network|argument",'
            '"reading_note":"一句话读图要点（≤40字）",'
            '"summary":"2-3句简明文字说明（区分 [原文] 与 [补充]）",'
            '"data":{...}}\n'
            "- flow/tree/timeline/network/argument：data 为 "
            '{"nodes":[{"id":"N1","label":"节点名(≤14字)","note":"简注(≤16字)"}],"edges":[{"from":"N1","to":"N2","label":"关系"}]}\n'
            '- matrix：data 为 {"columns":["维度","A","B"],"rows":[["行名","值1","值2"]]}\n'
            '- quantity：data 为 {"items":[{"label":"项名","value":12.5}]}'
        )
        user = f"{loc}\n书型参考：{mode_meta['label']}\n\n{context_block}{focus_block}"
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

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
            "你是双语及古今汉语对读编辑。请根据选中文本的语言特征自动选择最合适的辅助方式：\n"
            "- 如果是文言/古文/半文言：给出流畅的【现代汉语直译】，再列出【关键实词/典故注】；\n"
            "- 如果是中译本（翻译腔或术语）：给出更自然的重述，并尽可能还原对应的"
            "【英文原词/概念】，解释中文译名可能遮蔽的语境；\n"
            "- 如果是英文原文：给出信达雅的中文翻译，并标注关键学术/行业术语。\n"
            "原文词义标 `[原文]`，语言或背景延展标 `[补充]`。"
        )
    elif action == "critique":
        system = (
            "你是严谨的审稿同伴。读者主动要求拆解所选段落背后的隐含前提。\n"
            "请克制、具体、不空泛地列出：\n"
            "1. **明面结论** `[原文]`\n"
            "2. **未明说的隐含前提** `[补充]`（作者默认了什么条件才成立？）\n"
            "3. **适用边界 / 反例情景** `[补充]`（什么情况下这句话会失效或需修正？）"
        )
    else:  # ask
        system = (
            "你是随身伴读助手。紧扣读者选中的原文与前后上下文回答问题。"
            "回答直接切中要点；引用书中已有事实请标 `[原文]`，涉及书外背景知识或推理补充请标 `[补充]`。"
        )

    user = f"{loc}\n阅读偏好参考：{mode_meta['label']}（{mode_meta['desc']}）\n\n{context_block}{focus_block}"
    if question:
        user += f"\n\n读者追问：{question}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


class ReadingService:
    """8771 阅读工作台服务层。"""

    def __init__(self, root=None, annotate=None, client=None):
        self.library = Library(root)
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
        chapters = sum(book["chapters"] for book in books)
        chars = sum(book["chars"] for book in books)
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
                "readable": len(books),
                "chapters": chapters,
                "chars": chars,
                "packs": counts["chapters"],
                "notes": counts["notes"],
                "maps": counts["maps"],
            },
            "total": self.library.total(),
            "books": books,
            "recent": recent,
        }

    def catalog(self, q="", subject=""):
        counts = self.store.activity_counts()
        data = self.library.catalog(q, subject)
        data["readable"] = [self._book_view(book, counts) for book in data.get("readable", [])]
        data["books"] = data["readable"]
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
        return {"book": view, "chapters": chapters, "reading": self.book_reading(uid)}

    # ---- 后台读法提示账（不阻断正文阅读） -----------------------------
    def _profile_doc(self, uid):
        return profile.from_rows(L.reading_get(uid, root=self.library.root))

    def book_reading(self, uid):
        doc = self._profile_doc(uid)
        if not doc:
            return {"book_mode": None, "panels": [], "chapters": {}}
        return {"book_mode": doc.get("mode"), "panels": doc.get("panels") or [], "chapters": doc.get("chapters") or {}}

    def set_book_reading(self, uid, mode):
        if not self.library.book(uid):
            raise FileNotFoundError("找不到这本书")
        clean = profile.normalize_mode(mode)
        if not clean:
            raise ValueError("mode 必须是 %s" % "/".join(profile.MODES))
        L.reading_set_book(uid, clean, root=self.library.root)
        return self.book_reading(uid)

    def reading(self, uid, n, raw=None):
        raw = raw or self.library.read_chapter(uid, n) or {}
        doc = self._profile_doc(uid)
        suggestion = profile.suggestion_for(uid, n, self.library, self.library.root, None)
        return profile.resolve(doc, n, raw.get("source_sha256") or "", suggestion)

    def set_reading(self, uid, n, action, mode=None):
        raw = self.library.read_chapter(uid, n)
        if raw is None:
            raise FileNotFoundError("找不到章节")
        if action == "book_default":
            clean = profile.normalize_mode(mode)
            if not clean:
                raise ValueError("mode 必须是 %s" % "/".join(profile.MODES))
            L.reading_set_book(uid, clean, root=self.library.root)
        elif action == "override":
            clean = profile.normalize_mode(mode)
            if not clean:
                raise ValueError("mode 必须是 %s" % "/".join(profile.MODES))
            book = L.reading_get(uid, "*", root=self.library.root)
            if not book:
                raise MissingProfileError(uid)
            if clean == profile.normalize_mode(book.get("mode")):
                L.reading_delete(uid, str(int(n)), root=self.library.root)
            else:
                L.reading_put(
                    uid,
                    clean,
                    chapter=str(int(n)),
                    source_sha256=raw.get("source_sha256") or "",
                    root=self.library.root,
                )
        elif action == "follow":
            L.reading_delete(uid, str(int(n)), root=self.library.root)
        else:
            raise ValueError("action 必须是 follow/override/book_default")
        return self.reading(uid, n, raw=raw)

    def chapter(self, uid, n):
        book = self.library.book(uid)
        chapter = self.library.chapter(uid, n)
        if not book or not chapter:
            return None
        raw = self.library.read_chapter(uid, n)
        if not raw:
            return None
        source_sha = raw.get("source_sha256") or ""
        reading = self.reading(uid, n, raw=raw)
        blocks = raw["blocks"]
        map_gate = chapter_map_gate(blocks)
        cap_gate = evaluate(blocks, chapmap.plan_chunks(blocks, 18000, 100)).to_dict()
        notes = self.store.assist_notes(uid, n, source_sha=source_sha)
        cmap = self.store.chapter_map(uid, n, source_sha=source_sha)
        ch_view = dict(chapter)
        ch_view["n"] = "%02d" % int(chapter["n"])
        ch_view["minutes"] = max(1, round(chapter.get("chars", 0) / WORDS_PER_MINUTE))
        ch_view["source_sha256"] = source_sha
        return {
            "book": self._book_view(book),
            "chapter": ch_view,
            "blocks": blocks,
            "reading": reading,
            "toolbar": _toolbar_for_mode(reading.get("mode")),
            "preflight": {"chapter_map": map_gate, "capacity": cap_gate},
            "assist_notes": notes,
            "chapter_map": cmap,
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

    def assist(
        self,
        uid,
        n,
        action,
        *,
        block_ids=None,
        selected_text="",
        question="",
        model=None,
        effort=None,
        on_delta=None,
    ):
        action = str(action or "").strip().lower()
        if action not in ASSIST_ACTIONS:
            raise ValueError("action 必须是 %s" % "/".join(ASSIST_ACTIONS))
        book = self.library.book(uid)
        chapter = self.library.chapter(uid, n)
        raw = self.library.read_chapter(uid, n)
        if not book or not chapter or not raw:
            raise FileNotFoundError("找不到章节")
        self._require_model_client()

        blocks = raw["blocks"]
        valid_ids = {b.get("id") for b in blocks if b.get("id")}
        clean_blocks = [str(bid).strip() for bid in (block_ids or []) if str(bid).strip()]
        invalid = [bid for bid in clean_blocks if bid not in valid_ids]
        if invalid:
            raise ValueError("block_ids 引用了不存在的段落：" + ", ".join(invalid))
        clean_text = str(selected_text or "").strip()
        clean_q = str(question or "").strip()
        if not clean_text and clean_blocks:
            blk_map = {b.get("id"): b for b in blocks if b.get("id")}
            clean_text = "\n\n".join(
                _block_preview_text(blk_map[bid]) for bid in clean_blocks if bid in blk_map
            ).strip()
        if not clean_text and not clean_q:
            raise ValueError("请先选中一段正文，或输入具体问题")

        reading = self.reading(uid, n, raw=raw)
        requested_model = str(model or getattr(self.client, "model", None) or DEFAULT_MODEL).strip()
        requested_effort = str(effort or getattr(self.client, "effort", None) or DEFAULT_EFFORT).strip().lower()
        context_text = self._context_for_blocks(blocks, clean_blocks)
        messages = _assist_prompts(
            action,
            mode=reading.get("mode") or "argument",
            book=book,
            chapter=chapter,
            selected_text=clean_text,
            block_ids=clean_blocks,
            context_text=context_text,
            question=clean_q,
        )

        op_id = f"op-{uuid.uuid4().hex[:10]}"
        started = time.perf_counter()
        log_dir = os.path.join(self.store.base, "logs")
        try:
            with events.job_scope(log_dir, op_id):
                try:
                    # diagram 需要严格 JSON 来渲染 SVG/表格，因此不直接把原始 JSON 字符推给用户正文区。
                    raw_content, usage = self.client.complete(
                        messages,
                        model=requested_model,
                        effort=requested_effort,
                        json_mode=(action == "diagram"),
                        on_delta=None if action == "diagram" else on_delta,
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
                    "summary": (clean_q or clean_text)[:120],
                    "error": str(exc),
                }
            )
            raise

        elapsed_ms = _elapsed_ms(started)
        tokens = _usage_tokens(usage)
        diagram_html = ""
        figure = None
        content = str(raw_content or "").strip()
        if action == "diagram":
            try:
                parsed = _loads_content(content)
                if isinstance(parsed, dict) and parsed.get("data"):
                    figure = parsed
                    diagram_html = diagrams.render_assist_figure(parsed)
                    content = str(parsed.get("summary") or parsed.get("reading_note") or "").strip()
            except ValueError:
                # 模型偶尔没吐出合法 JSON 时直接保留其可读文字，不报错丢内容。
                figure = None
                diagram_html = ""

        summary = (clean_q or clean_text).replace("\n", " ")[:90]
        if diagram_html and isinstance(figure, dict) and figure.get("caption"):
            summary = f"{figure['caption']}（{summary[:50]}）"
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
        return {
            "id": op_id,
            "uid": uid,
            "n": "%02d" % int(n),
            "action": action,
            "action_label": ACTION_LABELS[action],
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
            elif act in ASSIST_ACTIONS:
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
