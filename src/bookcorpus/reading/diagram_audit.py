"""分层读图表蓝图路由、提示词构造、后端审计分流与畸形降级兜底。"""

from __future__ import annotations

import json
import re
from typing import Any

NODE_LABEL_LIMIT = 18
EDGE_LABEL_LIMIT = 6
MAX_GRAPH_NODES = 12

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

DAG_BLUEPRINTS = frozenset(
    {
        "argument_map",
        "lineage_dag",
        "power_hierarchy",
        "spatial_route",
        "relation_graph",
        "flow_swimlane",
        "decision_tree",
        "derivation_dag",
        "system_flow",
        "causal_loop",
    }
)
MATRIX_BLUEPRINTS = frozenset({"term_contrast", "concept_matrix", "element_matrix"})
CURVE_BLUEPRINTS = frozenset({"tension_curve", "sensitivity_chart", "metric_chart"})

LEGACY_FAMILY_TO_BLUEPRINT = {
    "flow": "flow_swimlane",
    "tree": "decision_tree",
    "argument": "argument_map",
    "network": "relation_graph",
    "timeline": "flow_swimlane",
    "matrix": "concept_matrix",
    "quantity": "metric_chart",
}

BLUEPRINT_SCHEMA_HINTS: dict[str, str] = {
    "argument_map": (
        '"blueprint":"argument_map","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"nodes":[{"id":"A1","role":"premise|inference|rebuttal|conclusion","label":"≤16字短标题",'
        '"detail":"≤45字展开说明","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"A1","to":"A2","label":"≤5字关系"}]}'
    ),
    "term_contrast": (
        '"blueprint":"term_contrast","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"columns":["核心术语","日常汉语直觉","作者专属定义","出处"],'
        '"rows":[{"key":"术语","everyday":"日常义","author":"作者专义","blocks":["B01"],"source":"原文|补充"}]}'
    ),
    "lineage_dag": (
        '"blueprint":"lineage_dag","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"nodes":[{"id":"L1","role":"rebuttal|inference|conclusion","label":"≤16字学者或流派",'
        '"detail":"≤45字观点演变","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"L1","to":"L2","label":"≤5字批判/继承"}]}'
    ),
    "parallel_timeline": (
        '"blueprint":"parallel_timeline","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"lanes":["泳道1","泳道2","泳道3"],'
        '"rows":[{"time":"时间点","cells":[{"title":"≤14字事件","note":"≤35字说明","blocks":["B01"],"source":"原文|补充"},null]}]}'
    ),
    "power_hierarchy": (
        '"blueprint":"power_hierarchy","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"nodes":[{"id":"H1","role":"premise|inference|rebuttal|conclusion","label":"≤16字权力节点",'
        '"detail":"≤45字职责与掣肘","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"H1","to":"H2","label":"≤5字制衡/隶属"}]}'
    ),
    "spatial_route": (
        '"blueprint":"spatial_route","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"nodes":[{"id":"S1","role":"condition|inference|rebuttal|conclusion","label":"≤16字地点节点",'
        '"detail":"≤45字态势说明","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"S1","to":"S2","label":"≤5字动线"}]}'
    ),
    "relation_graph": (
        '"blueprint":"relation_graph","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"nodes":[{"id":"P1","role":"premise|condition|rebuttal|inference","label":"≤16字人物与立场",'
        '"detail":"≤45字关系变化","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"P1","to":"P2","label":"≤5字羁绊/冲突"}]}'
    ),
    "tension_curve": (
        '"blueprint":"tension_curve","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"yLabel":"张力强度","points":[{"beat":"≤8字节拍","tension":65,"emotion":"≤12字情绪状态",'
        '"blocks":["B01"],"source":"原文"}]}'
    ),
    "flow_swimlane": (
        '"blueprint":"flow_swimlane","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"nodes":[{"id":"F1","role":"premise|inference|condition|conclusion","label":"≤16字步骤",'
        '"detail":"≤45字做法细节","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"F1","to":"F2","label":"≤5字流转"}]}'
    ),
    "decision_tree": (
        '"blueprint":"decision_tree","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"nodes":[{"id":"D1","role":"condition|inference|rebuttal|conclusion","label":"≤16字条件或动作",'
        '"detail":"≤45字判断依据","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"D1","to":"D2","label":"≤5字分支条件"}]}'
    ),
    "quadrant_matrix": (
        '"blueprint":"quadrant_matrix","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"xAxis":"横轴（低 → 高）","yAxis":"纵轴（低 → 高）",'
        '"quadrants":[{"pos":"q-top-left|q-top-right|q-bot-left|q-bot-right","title":"象限名称",'
        '"items":[{"text":"≤26字条目","blocks":["B01"],"source":"原文|补充"}]}]}'
    ),
    "derivation_dag": (
        '"blueprint":"derivation_dag","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"nodes":[{"id":"M1","role":"axiom|lemma|condition|theorem","label":"≤16字公理/引理/定理",'
        '"detail":"≤45字推导含义","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"M1","to":"M2","label":"≤5字依据"}]}'
    ),
    "sensitivity_chart": (
        '"blueprint":"sensitivity_chart","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"yLabel":"变化幅度(%)","points":[{"beat":"≤8字参数档位","tension":45,"emotion":"≤14字物理/统计表现",'
        '"blocks":["B01"],"source":"原文|补充"}]}'
    ),
    "system_flow": (
        '"blueprint":"system_flow","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"nodes":[{"id":"C1","role":"condition|rebuttal|theorem|lemma","label":"≤16字机制部件",'
        '"detail":"≤45字作用过程","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"C1","to":"C2","label":"≤5字作用"}]}'
    ),
    "causal_loop": (
        '"blueprint":"causal_loop","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"nodes":[{"id":"S1","role":"condition|inference|rebuttal","label":"≤16字变量节点",'
        '"detail":"≤45字因果作用","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"S1","to":"S2","label":"+增强 或 -抑制"}]}'
    ),
    "concept_matrix": (
        '"blueprint":"concept_matrix","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"columns":["学派/作者口径","核心界定","关键机制","盲区/反例","出处"],'
        '"rows":[{"key":"流派","everyday":"概念界定","author":"解释机制","extra":"局限盲区","blocks":["B01"],"source":"原文|补充"}]}'
    ),
    "metric_chart": (
        '"blueprint":"metric_chart","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"yLabel":"指数(0-100)","points":[{"beat":"≤8字阶段","tension":50,"emotion":"≤14字实证变化",'
        '"blocks":["B01"],"source":"原文|补充"}]}'
    ),
    "element_matrix": (
        '"blueprint":"element_matrix","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"columns":["视觉维度","文化/文物原型","材质色彩特征","美术作用","出处"],'
        '"rows":[{"key":"维度","everyday":"原型","author":"材质特征","extra":"作用","blocks":["B01"],"source":"原文|补充"}]}'
    ),
}

EXTENSION_PROMPT_SPECS: dict[str, dict[str, str]] = {
    "checklist": {
        "blueprint": "extension_checklist",
        "title": "行动自检清单",
        "instruction": (
            '输出严格 JSON：{"blueprint":"extension_checklist","caption":"≤18字清单名","reading_note":"适用前置条件",'
            '"summary":"2句总体行动建议","items":[{"text":"≤26字可执行检查项","detail":"≤45字避坑或边界说明",'
            '"checked":false,"blocks":["B01"],"source":"原文|补充"}]}'
        ),
    },
    "imagery": {
        "blueprint": "extension_dossier",
        "title": "意象与舞台词典",
        "instruction": (
            '输出严格 JSON：{"blueprint":"extension_dossier","caption":"≤18字意象/舞台主题",'
            '"reading_note":"阅读体验要点","summary":"2句基调总结","items":[{"title":"≤20字意象或调度标签",'
            '"body":"≤65字意象冲突/潜台词/重音解析","blocks":["B01"],"source":"原文|补充"}]}'
        ),
    },
    "compare_context": {
        "blueprint": "extension_dossier",
        "title": "多史家视角与世界史锚点",
        "instruction": (
            '输出严格 JSON：{"blueprint":"extension_dossier","caption":"≤18字史学对照题",'
            '"reading_note":"识别筛选偏见提示","summary":"2句多视角摘要","items":[{"title":"≤20字视角或横向年份锚点",'
            '"body":"≤70字不同归因或同时期世界史对照","blocks":["B01"],"source":"原文|补充"}]}'
        ),
    },
    "boundary_check": {
        "blueprint": "extension_dossier",
        "title": "演进脉络与适用边界",
        "instruction": (
            '输出严格 JSON：{"blueprint":"extension_dossier","caption":"≤18字前提与边界",'
            '"reading_note":"公式/规律失效提示","summary":"2句核心推导思路","items":[{"title":"≤20字演进/类比/极端条件",'
            '"body":"≤70字推翻何种旧认知、生活类比或失效条件","blocks":["B01"],"source":"原文|补充"}]}'
        ),
    },
    "sparring_dossier": {
        "blueprint": "extension_dossier",
        "title": "学术交锋档案",
        "instruction": (
            '输出严格 JSON：{"blueprint":"extension_dossier","caption":"≤18字思想交锋题",'
            '"reading_note":"先看清作者到底在反驳谁","summary":"2句立场综述","items":[{"title":"≤20字论敌或思想实验",'
            '"body":"≤70字反驳目标与跨学派对照","blocks":["B01"],"source":"原文|补充"}]}'
        ),
    },
    "method_audit": {
        "blueprint": "extension_dossier",
        "title": "研究方法与样本局限",
        "instruction": (
            '输出严格 JSON：{"blueprint":"extension_dossier","caption":"≤18字实证与局限",'
            '"reading_note":"切忌把局部样本直接绝对化","summary":"2句方法论综述",'
            '"items":[{"title":"≤20字研究方法或局限维度",'
            '"body":"≤70字定性定量依据、样本偏差或现实映射","blocks":["B01"],"source":"原文|补充"}]}'
        ),
    },
    "craft_notes": {
        "blueprint": "extension_dossier",
        "title": "工艺流派考据",
        "instruction": (
            '输出严格 JSON：{"blueprint":"extension_dossier","caption":"≤18字技法考据题",'
            '"reading_note":"观图时留心年代与材料工艺","summary":"2句风格综述",'
            '"items":[{"title":"≤20字工艺/流派维度",'
            '"body":"≤70字文物源流或技法背景","blocks":["B01"],"source":"原文|补充"}]}'
        ),
    },
}


def prompt_for_diagram(
    *,
    blueprint: str | None,
    read_type: str = "",
    book: dict[str, Any] | None = None,
    chapter: dict[str, Any] | None = None,
    selected_text: str = "",
    block_ids: list[str] | None = None,
    context_text: str = "",
    scope: str = "selection",
    covered_blocks: int = 0,
    sampled: bool = False,
) -> list[dict[str, str]]:
    """构建带蓝图硬约束的图解 Prompt（保留“关系图”关键词以兼容 stub）。"""
    chosen = (blueprint or "").strip()
    if chosen not in BLUEPRINT_SCHEMA_HINTS:
        chosen = "argument_map"
    schema_hint = BLUEPRINT_SCHEMA_HINTS[chosen]
    type_hint = f"（当前阅读视角：{read_type}）" if read_type else ""
    scope_hint = (
        f"当前作用范围：整章全局（涵盖全章 {covered_blocks or '全部'} 个段落"
        + ("，长章已均匀抽取各节骨架" if sampled else "")
        + "）。请从整章宏观逻辑出发抽取结构，不要只停留在开头几段，每个节点 blocks 必须准确引用对应段落真实块号。"
        if scope == "chapter"
        else f"当前作用范围：读者选中的段落（{', '.join(block_ids or []) or '当前片段'}）。"
    )
    system = (
        f"你是阅读伴读助手，擅长把复杂文字转成清晰的关系图与结构化蓝图{type_hint}。\n"
        f"{scope_hint}\n"
        "严格要求：\n"
        "1. 只输出一个合法 JSON 对象，不要 markdown 代码围栏，不要多余解释。\n"
        "2. 严禁输出任何像素坐标、宽高、颜色或 HTML/SVG 代码。\n"
        "3. 每个节点主标题严格控制在 16 字以内，详细解释放到 detail；边标签控制在 5 字以内。\n"
        '4. 每个节点或要点都必须填写来源于本段实际编号的 blocks 数组（例如 ["B02"]），'
        '并注明 source（"原文" 或 "补充"）。\n'
        f"请按以下 JSON 槽位输出：{{{schema_hint}}}"
    )
    book_title = (book or {}).get("title") or ""
    chapter_title = (chapter or {}).get("title") or ""
    ids_str = "全章全局" if scope == "chapter" else (", ".join(block_ids or []) or "整章")
    user = f"《{book_title}》· {chapter_title}（范围：{ids_str}）\n"
    if scope == "chapter":
        user += f"\n全章正文骨架（带真实块号）：\n{context_text or selected_text}"
    else:
        if context_text:
            user += f"\n相邻上下文：\n{context_text}\n"
        user += f"\n读者选中的原文：\n{selected_text}" if selected_text else "\n请针对当前所选内容提取蓝图结构。"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def prompt_for_extension(
    action: str,
    *,
    sub_kind: str = "",
    book: dict[str, Any] | None = None,
    chapter: dict[str, Any] | None = None,
    selected_text: str = "",
    block_ids: list[str] | None = None,
    context_text: str = "",
    scope: str = "selection",
    covered_blocks: int = 0,
    sampled: bool = False,
) -> list[dict[str, str]]:
    """构建书型专属扩展动作的 JSON 结构化 Prompt。"""
    spec = EXTENSION_PROMPT_SPECS[action]
    sub_hint = f"（文学子类侧重点：{sub_kind}）" if sub_kind else ""
    scope_hint = (
        f"作用范围：整章全局（涵盖全章 {covered_blocks or '全部'} 个段落"
        + ("，已均匀覆盖全章骨架" if sampled else "")
        + "）。请归纳全章而不只看开头。"
        if scope == "chapter"
        else f"作用范围：读者选中的段落（{', '.join(block_ids or []) or '局部'}）。"
    )
    system = (
        f"你是阅读伴读专家，负责生成「{spec['title']}」结构卡片{sub_hint}。\n"
        f"{scope_hint}\n"
        "严格区分 [原文] 与 [补充]，严禁输出坐标或样式，只输出一个严格 JSON 对象（不要围栏）：\n"
        f"{spec['instruction']}"
    )
    book_title = (book or {}).get("title") or ""
    chapter_title = (chapter or {}).get("title") or ""
    ids_str = "全章全局" if scope == "chapter" else (", ".join(block_ids or []) or "整章")
    user = f"《{book_title}》· {chapter_title}（范围：{ids_str}）\n"
    if scope == "chapter":
        user += f"\n全章正文骨架（带真实块号）：\n{context_text or selected_text}"
    else:
        if context_text:
            user += f"\n相邻上下文：\n{context_text}\n"
        user += f"\n重点原文：\n{selected_text}" if selected_text else "\n请围绕上述章节片段输出卡片。"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_json_payload(raw: Any) -> dict[str, Any] | None:
    """尽最大努力剥离代码围栏并解析 JSON 对象，失败返回 None。"""
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return None
    cleaned = _FENCE_RE.sub("", text).strip()
    for candidate in (cleaned, text):
        try:
            loaded = json.loads(candidate)
            if isinstance(loaded, dict):
                return loaded
        except ValueError:
            pass
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            try:
                loaded = json.loads(candidate[start : end + 1])
                if isinstance(loaded, dict):
                    return loaded
            except ValueError:
                pass
    return None


def _clean_blocks(
    raw_blocks: Any,
    *,
    valid_ids: set[str],
    default_blocks: list[str],
    hint: str,
    logs: list[str],
) -> list[str]:
    cleaned: list[str] = []
    if isinstance(raw_blocks, str):
        candidates = [raw_blocks]
    elif isinstance(raw_blocks, list):
        candidates = [str(item).strip() for item in raw_blocks if str(item).strip()]
    else:
        candidates = []

    for bid in candidates:
        if not valid_ids or bid in valid_ids:
            if bid not in cleaned:
                cleaned.append(bid)
        else:
            logs.append(f'过滤不存在块号 "{bid}"（条目：{hint}）')

    if not cleaned and default_blocks:
        cleaned = [default_blocks[0]]
        if candidates:
            logs.append(f'"{hint}" 锚点无效，已自动回退到 {cleaned[0]}')
    return cleaned


def _norm_source(val: Any) -> str:
    txt = str(val or "").strip()
    return "补充" if txt in {"补充", "ext", "external"} else "原文"


def _audit_dag_data(
    raw_data: dict[str, Any],
    *,
    valid_ids: set[str],
    default_blocks: list[str],
    logs: list[str],
) -> dict[str, Any] | None:
    raw_nodes = raw_data.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        return None
    if len(raw_nodes) > MAX_GRAPH_NODES:
        logs.append(f"节点数 {len(raw_nodes)} 超出单图上限 {MAX_GRAPH_NODES}，触发结构化清单降级")
        return None

    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for idx, item in enumerate(raw_nodes):
        if not isinstance(item, dict):
            continue
        nid = str(item.get("id") or f"N{idx + 1}").strip()
        raw_label = str(item.get("label") or item.get("title") or nid).strip()
        detail = str(item.get("detail") or item.get("note") or "").strip()
        if len(raw_label) > NODE_LABEL_LIMIT:
            detail = f"{raw_label} · {detail}".strip(" ·") if detail else raw_label
            label = raw_label[:NODE_LABEL_LIMIT] + "…"
            logs.append(f'节点 "{nid}" 标题超过 {NODE_LABEL_LIMIT} 字，已自动分流至 detail')
        else:
            label = raw_label
        blocks = _clean_blocks(
            item.get("blocks") or item.get("block"),
            valid_ids=valid_ids,
            default_blocks=default_blocks,
            hint=label,
            logs=logs,
        )
        node_ids.add(nid)
        nodes.append(
            {
                "id": nid,
                "role": str(item.get("role") or "inference").strip(),
                "label": label,
                "detail": detail,
                "blocks": blocks,
                "source": _norm_source(item.get("source")),
            }
        )
    if not nodes:
        return None

    edges: list[dict[str, str]] = []
    for edge in raw_data.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("from") or "").strip()
        dst = str(edge.get("to") or "").strip()
        if src not in node_ids or dst not in node_ids:
            continue
        raw_lbl = str(edge.get("label") or "").strip()
        note = str(edge.get("note") or "").strip()
        if len(raw_lbl) > EDGE_LABEL_LIMIT:
            note = f"{raw_lbl} · {note}".strip(" ·") if note else raw_lbl
            short_lbl = raw_lbl[:EDGE_LABEL_LIMIT] + "…"
            logs.append(f'边 "{src}→{dst}" 标签超长，已收缩为 "{short_lbl}"')
        else:
            short_lbl = raw_lbl
        edges.append({"from": src, "to": dst, "label": short_lbl, "note": note})

    return {"nodes": nodes, "edges": edges}


def _audit_matrix_data(
    raw_data: dict[str, Any],
    *,
    valid_ids: set[str],
    default_blocks: list[str],
    logs: list[str],
) -> dict[str, Any] | None:
    cols = [str(c).strip() for c in (raw_data.get("columns") or []) if str(c).strip()]
    raw_rows = raw_data.get("rows")
    if not cols or not isinstance(raw_rows, list) or not raw_rows:
        return None
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        if isinstance(row, dict):
            key = str(row.get("key") or row.get("term") or "").strip()
            everyday = str(row.get("everyday") or row.get("col1") or "").strip()
            author = str(row.get("author") or row.get("col2") or "").strip()
            extra = str(row.get("extra") or row.get("col3") or "").strip()
            blocks = _clean_blocks(
                row.get("blocks"), valid_ids=valid_ids, default_blocks=default_blocks, hint=key or "矩阵行", logs=logs
            )
            rows.append(
                {
                    "key": key,
                    "everyday": everyday,
                    "author": author,
                    "extra": extra,
                    "blocks": blocks,
                    "source": _norm_source(row.get("source")),
                }
            )
        elif isinstance(row, list) and row:
            cells = [str(c) for c in row]
            rows.append(
                {
                    "key": cells[0],
                    "everyday": cells[1] if len(cells) > 1 else "",
                    "author": cells[2] if len(cells) > 2 else "",
                    "extra": " | ".join(cells[3:]) if len(cells) > 3 else "",
                    "blocks": list(default_blocks[:1]),
                    "source": "原文",
                }
            )
    if not rows:
        return None
    return {"columns": cols, "rows": rows}


def _audit_curve_data(
    raw_data: dict[str, Any],
    *,
    valid_ids: set[str],
    default_blocks: list[str],
    logs: list[str],
) -> dict[str, Any] | None:
    # Also accept legacy quantity {"items":[{"label","value"}]}
    raw_points = raw_data.get("points")
    if not isinstance(raw_points, list) and isinstance(raw_data.get("items"), list):
        raw_points = [
            {
                "beat": str(it.get("label") or f"点{i + 1}"),
                "tension": float(it.get("value") or 0),
                "emotion": str(it.get("note") or it.get("label") or ""),
                "blocks": it.get("blocks"),
                "source": it.get("source"),
            }
            for i, it in enumerate(raw_data["items"])
            if isinstance(it, dict)
        ]
    if not isinstance(raw_points, list) or not raw_points:
        return None
    points: list[dict[str, Any]] = []
    for idx, pt in enumerate(raw_points):
        if not isinstance(pt, dict):
            continue
        beat = str(pt.get("beat") or f"节拍{idx + 1}").strip()[:12]
        raw_val = pt.get("tension", pt.get("value", 50))
        try:
            val = float(50.0 if raw_val is None else raw_val)
        except (TypeError, ValueError):
            val = 50.0
        val = max(0.0, min(100.0, val))
        emotion = str(pt.get("emotion") or pt.get("note") or "").strip()[:28]
        blocks = _clean_blocks(
            pt.get("blocks"), valid_ids=valid_ids, default_blocks=default_blocks, hint=beat, logs=logs
        )
        points.append(
            {
                "beat": beat,
                "tension": val,
                "emotion": emotion,
                "blocks": blocks,
                "source": _norm_source(pt.get("source")),
            }
        )
    if not points:
        return None
    return {"yLabel": str(raw_data.get("yLabel") or "强度"), "points": points}


def _audit_timeline_data(
    raw_data: dict[str, Any],
    *,
    valid_ids: set[str],
    default_blocks: list[str],
    logs: list[str],
) -> dict[str, Any] | None:
    lanes = [str(lane).strip() for lane in (raw_data.get("lanes") or []) if str(lane).strip()]
    raw_rows = raw_data.get("rows")
    if not lanes or not isinstance(raw_rows, list) or not raw_rows:
        return None
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        time_lbl = str(row.get("time") or "").strip() or "时间点"
        cells: list[dict[str, Any] | None] = []
        raw_cells = row.get("cells") or []
        for idx in range(len(lanes)):
            cell = raw_cells[idx] if idx < len(raw_cells) else None
            if not isinstance(cell, dict):
                cells.append(None)
                continue
            title = str(cell.get("title") or "").strip() or "事件"
            note = str(cell.get("note") or "").strip()
            blocks = _clean_blocks(
                cell.get("blocks"), valid_ids=valid_ids, default_blocks=default_blocks, hint=title, logs=logs
            )
            cells.append(
                {
                    "title": title,
                    "note": note,
                    "blocks": blocks,
                    "source": _norm_source(cell.get("source")),
                }
            )
        rows.append({"time": time_lbl, "cells": cells})
    return {"lanes": lanes, "rows": rows} if rows else None


def _audit_quadrant_data(
    raw_data: dict[str, Any],
    *,
    valid_ids: set[str],
    default_blocks: list[str],
    logs: list[str],
) -> dict[str, Any] | None:
    raw_quads = raw_data.get("quadrants")
    if not isinstance(raw_quads, list) or not raw_quads:
        return None
    valid_pos = ("q-top-left", "q-top-right", "q-bot-left", "q-bot-right")
    quads: list[dict[str, Any]] = []
    for idx, q in enumerate(raw_quads[:4]):
        if not isinstance(q, dict):
            continue
        pos = str(q.get("pos") or valid_pos[idx])
        if pos not in valid_pos:
            pos = valid_pos[idx]
        items: list[dict[str, Any]] = []
        for it in q.get("items") or []:
            if not isinstance(it, dict):
                continue
            txt = str(it.get("text") or "").strip()
            if not txt:
                continue
            blocks = _clean_blocks(
                it.get("blocks"), valid_ids=valid_ids, default_blocks=default_blocks, hint=txt[:10], logs=logs
            )
            items.append({"text": txt, "blocks": blocks, "source": _norm_source(it.get("source"))})
        quads.append({"pos": pos, "title": str(q.get("title") or pos), "items": items})
    return {
        "xAxis": str(raw_data.get("xAxis") or "横轴（低 → 高）"),
        "yAxis": str(raw_data.get("yAxis") or "纵轴（低 → 高）"),
        "quadrants": quads,
    }


def _build_fallback_payload(
    *,
    caption: str,
    summary: str,
    raw_text: str,
    parsed: dict[str, Any] | None,
    default_blocks: list[str],
    logs: list[str],
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
        for node in parsed["data"].get("nodes") or []:
            if isinstance(node, dict) and (node.get("label") or node.get("title")):
                items.append(
                    {
                        "label": str(node.get("label") or node.get("title")),
                        "detail": str(node.get("detail") or node.get("note") or ""),
                        "blocks": list(default_blocks[:1]),
                        "warning": "原图节点超限或结构不完整，已安全降级为锚点清单",
                    }
                )
    if not items:
        snippet = (summary or raw_text or "模型未返回完整图表结构").strip()[:160]
        items.append(
            {
                "label": snippet,
                "detail": "点击下方块号可直跳对应原文复核。",
                "blocks": list(default_blocks[:1]),
                "warning": "JSON 解析或字段校验未通过，已执行防白屏结构化降级",
            }
        )
    logs.append("已触发 fallback_list 安全降级，确保不白屏且保留原文块锚点")
    return {
        "blueprint": "fallback_list",
        "caption": caption or "结构化阅读要点（已安全降级）",
        "reading_note": "由于模型返回结构残缺或过密，系统已自动降级为带原文锚点的结构化清单。",
        "summary": summary,
        "audit_log": logs,
        "items": items,
    }


def audit_blueprint_payload(
    raw_or_parsed: Any,
    valid_block_ids: set[str] | list[str] | tuple[str, ...],
    *,
    fallback_block_ids: list[str] | None = None,
    requested_blueprint: str = "",
) -> dict[str, Any]:
    """审计、清洗并规范化图解/扩展 JSON；严重残缺时返回 fallback_list。"""
    valid_ids = {str(b) for b in valid_block_ids if str(b)}
    default_blocks = [b for b in (fallback_block_ids or []) if not valid_ids or b in valid_ids]
    if not default_blocks and valid_ids:
        default_blocks = [sorted(valid_ids)[0]]

    logs: list[str] = []
    raw_text = raw_or_parsed if isinstance(raw_or_parsed, str) else ""
    parsed = parse_json_payload(raw_or_parsed)
    if parsed is None:
        return _build_fallback_payload(
            caption="模型输出未能解析为 JSON",
            summary=raw_text[:240],
            raw_text=raw_text,
            parsed=None,
            default_blocks=default_blocks,
            logs=logs,
        )

    caption = str(parsed.get("caption") or "").strip()[:36] or "结构图解"
    reading_note = str(parsed.get("reading_note") or "").strip()[:120]
    summary = str(parsed.get("summary") or reading_note or "").strip()

    bp = str(parsed.get("blueprint") or "").strip()
    legacy_family = str(parsed.get("family") or "").strip()
    if not bp:
        bp = LEGACY_FAMILY_TO_BLUEPRINT.get(legacy_family, requested_blueprint or "flow_swimlane")
        if legacy_family:
            logs.append(f"兼容旧版 family='{legacy_family}'，已映射至蓝图 '{bp}'")

    # Extension dossier / checklist branch
    if bp in {"extension_dossier", "extension_checklist"} or (
        isinstance(parsed.get("items"), list) and "data" not in parsed
    ):
        raw_items = parsed.get("items") or []
        items: list[dict[str, Any]] = []
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or it.get("text") or "").strip()
            body = str(it.get("body") or it.get("detail") or "").strip()
            if not title and not body:
                continue
            blocks = _clean_blocks(
                it.get("blocks"), valid_ids=valid_ids, default_blocks=default_blocks, hint=title or "扩展项", logs=logs
            )
            entry: dict[str, Any] = {
                "title": title,
                "text": title,
                "body": body,
                "detail": body,
                "blocks": blocks,
                "source": _norm_source(it.get("source")),
            }
            if bp == "extension_checklist":
                entry["checked"] = bool(it.get("checked", False))
            items.append(entry)
        if not items:
            return _build_fallback_payload(
                caption=caption,
                summary=summary,
                raw_text=raw_text,
                parsed=parsed,
                default_blocks=default_blocks,
                logs=logs,
            )
        if not logs:
            logs.append("扩展卡片校验通过。")
        return {
            "blueprint": bp or "extension_dossier",
            "caption": caption,
            "reading_note": reading_note,
            "summary": summary,
            "audit_log": logs,
            "items": items,
        }

    raw_data = parsed.get("data")
    if not isinstance(raw_data, dict):
        return _build_fallback_payload(
            caption=caption, summary=summary, raw_text=raw_text, parsed=parsed, default_blocks=default_blocks, logs=logs
        )

    audited_data: dict[str, Any] | None = None
    if bp in DAG_BLUEPRINTS:
        audited_data = _audit_dag_data(raw_data, valid_ids=valid_ids, default_blocks=default_blocks, logs=logs)
    elif bp in MATRIX_BLUEPRINTS:
        audited_data = _audit_matrix_data(raw_data, valid_ids=valid_ids, default_blocks=default_blocks, logs=logs)
    elif bp in CURVE_BLUEPRINTS:
        audited_data = _audit_curve_data(raw_data, valid_ids=valid_ids, default_blocks=default_blocks, logs=logs)
    elif bp == "parallel_timeline":
        audited_data = _audit_timeline_data(raw_data, valid_ids=valid_ids, default_blocks=default_blocks, logs=logs)
    elif bp == "quadrant_matrix":
        audited_data = _audit_quadrant_data(raw_data, valid_ids=valid_ids, default_blocks=default_blocks, logs=logs)
    else:
        # Unknown blueprint name -> try DAG first
        audited_data = _audit_dag_data(raw_data, valid_ids=valid_ids, default_blocks=default_blocks, logs=logs)
        if audited_data is not None:
            bp = "flow_swimlane"

    if audited_data is None:
        return _build_fallback_payload(
            caption=caption, summary=summary, raw_text=raw_text, parsed=parsed, default_blocks=default_blocks, logs=logs
        )

    if not logs:
        logs.append("蓝图字段、文本长度与正文锚点全部审计通过。")
    result: dict[str, Any] = {
        "blueprint": bp,
        "caption": caption,
        "reading_note": reading_note,
        "summary": summary,
        "audit_log": logs,
        "data": audited_data,
    }
    if legacy_family:
        result["family"] = legacy_family
    return result
