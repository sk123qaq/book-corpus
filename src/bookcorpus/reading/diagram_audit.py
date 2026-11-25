"""伴读图表蓝图路由、提示词构造、后端审计分流与畸形降级兜底。"""

from __future__ import annotations

import json
import re
from typing import Any

NODE_LABEL_LIMIT = 18
EDGE_LABEL_LIMIT = 6
SPATIAL_EDGE_LABEL_LIMIT = 14
MAX_GRAPH_NODES = 16

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_SOURCE_PREFIX_RE = re.compile(
    r"^(?:【(?:原文|补充)】\s*|\[(?:原文|补充)\]\s*|(?:原文|补充对照|补充|延伸)\s*[:：]\s*)+"
)
_EMPTY_TERM_VALUES = frozenset({"无", "暂无", "无对应", "无原词", "none", "n/a", "na", "-", "—", "/"})
_SPARRING_PLACEHOLDER_NOTE = "先看清作者到底在反驳谁"
_BOUNDARY_PLACEHOLDER_NOTE = "公式/规律失效提示"
_BOUNDARY_PREFIX_RE = re.compile(r"^(?:公式/规律失效提示\s*[:：]\s*)+")

ARGUMENT_ROLES = frozenset({"premise", "inference", "rebuttal", "conclusion", "condition"})
LINEAGE_ROLES = frozenset({"predecessor", "ally", "mediator", "target", "author"})
LINEAGE_RELATIONS = frozenset({"inherit", "transform", "critique", "contrast"})
FLOW_ROLES = frozenset({"input", "action", "gate", "pitfall", "output"})
DECISION_ROLES = frozenset({"diagnosis", "condition", "strategy", "fallback"})
QUADRANT_MAX_ITEMS = 4
_CHECKLIST_PLACEHOLDER_NOTES = ("适用前置条件", "动作转化", "行动自检清单")
_POSTMORTEM_PLACEHOLDER_NOTES = ("避坑指南", "拆解反面案例", "反例与失效复盘")
DERIVATION_ROLES = frozenset(
    {"axiom", "condition", "lemma", "theorem", "observation", "hypothesis", "experiment", "finding"}
)
DERIVATION_PARADIGMS = frozenset({"deductive", "experimental"})
SYSTEM_FLOW_ROLES = frozenset({"input_energy", "component", "process", "bottleneck", "output_effect"})
SENSITIVITY_TONES = frozenset({"stable", "critical", "breakdown"})

EXTENSION_SECTION_META: dict[str, tuple[tuple[str, str], ...]] = {
    "checklist": (
        ("before", "启动前准备"),
        ("during", "执行与核验"),
        ("redline", "失效边界与踩坑红线"),
    ),
    "sparring_dossier": (
        ("debate", "核心论战交锋"),
        ("thought_experiment", "思想实验与核心隐喻"),
    ),
    "counterexample_lab": (
        ("hidden_premise", "隐含未言明前提"),
        ("counterexample", "哲学反例与极端压力测试"),
        ("modern_boundary", "当代现实映射与适用边界"),
    ),
    "boundary_check": (
        ("paradigm_shift", "旧认知推翻与范式演进"),
        ("intuition_analogy", "直观类比与物理直觉"),
        ("breakdown_boundary", "极端失效条件与未解边界"),
    ),
}

_TIME_BC_RE = re.compile(r"(?:公元前|前)\s*(\d{1,4})\s*年?")
_TIME_AD_RE = re.compile(r"(-?\d{1,4})\s*年")
_LITERATURE_ANTI_SPOILER = (
    "【严禁剧透硬规则】严禁调用训练记忆剧透本章之后尚未揭晓的真凶、血缘秘密、人物结局或隐藏反转；"
    "所有分析严格限定在本章已呈现的文本事实内。"
)
_BEAT_KINDS = frozenset({"setup", "foreshadow", "relief", "escalation", "climax", "aftermath"})
_LITERATURE_SPATIAL_SCHEMA_HINT = (
    '"blueprint":"spatial_route","caption":"≤18字图题","reading_note":"≤35字看图引导","summary":"2句核心解读（不与reading_note重复）",'
    '"data":{"mode":"route_map|stage_blocking|not_applicable","stage_desc":"≤30字舞台或空间格局",'
    '"not_applicable_reason":"仅mode为not_applicable时填写2句原因，否则留空",'
    '"nodes":[{"id":"S1","role":"home|public|threshold|transit|destination|prop|zone|outside",'
    '"zone":"≤10字片区或舞台区位","x":50,"y":50,"label":"≤14字地点或舞台锚点","detail":"≤45字态势说明","blocks":["B01"],"source":"原文|补充"}],'
    '"edges":[{"from":"S1","to":"S2","kind":"move|gaze|barrier|parallel","actor":"人物路线名","label":"≤6字动线","note":"≤30字说明"}]}'
)

DAG_BLUEPRINTS = frozenset(
    {
        "argument_map",
        "lineage_dag",
        "power_hierarchy",
        "spatial_route",
        "historical_causality",
        "relation_graph",
        "flow_swimlane",
        "decision_tree",
        "derivation_dag",
        "system_flow",
        "causal_loop",
    }
)
MATRIX_BLUEPRINTS = frozenset({"term_contrast", "concept_matrix", "element_matrix"})
CURVE_BLUEPRINTS = frozenset({"tension_curve", "metric_chart"})

DEFAULT_NODE_ROLE: dict[str, str] = {
    "power_hierarchy": "faction",
    "historical_causality": "mechanism",
    "flow_swimlane": "action",
    "decision_tree": "condition",
    "derivation_dag": "lemma",
    "system_flow": "process",
}

POWER_ROLES = frozenset({"sovereign", "faction", "ally", "rival", "constraint"})
POWER_RELATIONS = frozenset({"subordinate", "balance", "alliance", "hostile", "constraint"})
SPATIAL_ROLES = frozenset(
    {
        "capital",
        "fortress",
        "corridor",
        "neutral",
        "front",
        "home",
        "public",
        "threshold",
        "transit",
        "destination",
        "prop",
        "zone",
        "outside",
    }
)
SPATIAL_EDGE_KINDS = frozenset({"advance", "flank", "defense", "diplomacy", "move", "gaze", "barrier", "parallel"})

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
        '"blueprint":"argument_map","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读（勿复读reading_note）",'
        '"data":{"nodes":[{"id":"A1","role":"premise|inference|rebuttal|conclusion|condition","label":"≤16字短标题",'
        '"detail":"≤55字展开论证与依据","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"A1","to":"A2","label":"≤5字逻辑关系"}]}'
    ),
    "term_contrast": (
        '"blueprint":"term_contrast","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读（勿复读reading_note）",'
        '"data":{"columns":["核心术语","日常汉语直觉","作者专属定义","出处"],'
        '"rows":[{"key":"术语","original_term":"原文外文或古文原词(无则留空)","everyday":"日常义","author":"作者专义","blocks":["B01"],"source":"原文|补充"}]}'
    ),
    "lineage_dag": (
        '"blueprint":"lineage_dag","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读（勿复读reading_note）",'
        '"data":{"mode":"lineage|not_applicable","not_applicable_reason":"仅当mode为not_applicable时写2句说明",'
        '"nodes":[{"id":"L1","role":"predecessor|ally|mediator|target|author","label":"≤16字学者/学派/作者立场",'
        '"detail":"≤55字思想关联与张力","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"L1","to":"L2","relation":"inherit|transform|critique|contrast","label":"≤5字关系"}]}'
    ),
    "parallel_timeline": (
        '"blueprint":"parallel_timeline","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"lanes":["行为体/阵营/地区1","行为体/阵营/地区2"],'
        '"rows":[{"sort_year":317,"time":"展示时间","cells":[{"title":"≤14字事件","note":"≤35字说明","blocks":["B01"],"source":"原文|补充"},null]}],'
        '"aftermath":[{"title":"≤18字后世回响/史论","body":"≤55字说明","blocks":["B01"],"source":"原文|补充"}]}'
    ),
    "power_hierarchy": (
        '"blueprint":"power_hierarchy","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"nodes":[{"id":"H1","role":"sovereign|faction|ally|rival|constraint","label":"≤16字权力实体/机构/约束",'
        '"detail":"≤45字权责与制衡","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"H1","to":"H2","relation_type":"subordinate|balance|alliance|hostile|constraint","label":"≤5字关系"}]}'
    ),
    "spatial_route": (
        '"blueprint":"spatial_route","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"mode":"campaign|geopolitics|not_applicable","scope_note":"≤24字地理范围",'
        '"not_applicable_reason":"仅当mode为not_applicable时填写2句说明",'
        '"nodes":[{"id":"S1","label":"≤12字真实地名/要塞/区域","x":50,"y":70,"zone":"阵营/战区",'
        '"role":"capital|fortress|corridor|neutral|front","detail":"≤45字地理与战略态势","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"S1","to":"S2","label":"≤12字动线(含兵力/日程)","kind":"advance|flank|defense|diplomacy","route_group":"路线组名"}]}'
    ),
    "historical_causality": (
        '"blueprint":"historical_causality","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句文字解读",'
        '"data":{"nodes":[{"id":"C1","role":"structural|trigger|mechanism|turning_point|consequence","label":"≤16字因果节点",'
        '"detail":"≤45字因果机制说明","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"C1","to":"C2","label":"≤5字(引发/催化/倒逼/反噬)"}]}'
    ),
    "relation_graph": (
        '"blueprint":"relation_graph","caption":"≤18字图题","reading_note":"≤35字看图引导","summary":"2句核心解读（不与reading_note重复）",'
        '"data":{"nodes":[{"id":"P1","role":"protagonist|ally|antagonist|catalyst|observer","camp":"阵营/家庭/阶层",'
        '"label":"≤14字人物与身份","arc":"≤25字本章心理或处境变化","detail":"≤55字行为动机与交互","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"P1","to":"P2","rel_kind":"bond|conflict|asymmetry|family","label":"≤6字关系","note":"≤35字说明"}]}'
    ),
    "tension_curve": (
        '"blueprint":"tension_curve","caption":"≤18字图题","reading_note":"≤35字看图引导","summary":"2句核心解读（不与reading_note重复）",'
        '"data":{"yLabel":"张力强度","points":[{"beat":"≤8字戏剧节拍",'
        '"beat_kind":"setup|foreshadow|relief|escalation|climax|aftermath","tension":65,'
        '"emotion":"≤12字情绪状态","note":"25–45字解释这一拍为何拉高或压低张力","blocks":["B01"],"source":"原文"}]}'
    ),
    "flow_swimlane": (
        '"blueprint":"flow_swimlane","caption":"≤18字SOP图题","reading_note":"≤40字实操导读(点出适用场景与前提)",'
        '"summary":"2句实操总评（勿复读reading_note）",'
        '"data":{"mode":"flow|not_applicable","not_applicable_reason":"仅当mode为not_applicable时写2句原因，否则留空",'
        '"nodes":[{"id":"F1","role":"input|action|gate|pitfall|output","label":"≤16字动作步骤",'
        '"detail":"≤55字具体做法、工具或判断标准","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"F1","to":"F2","label":"≤5字流转"}]}'
    ),
    "decision_tree": (
        '"blueprint":"decision_tree","caption":"≤18字决策树题","reading_note":"≤40字决策场景导读",'
        '"summary":"2句决策总括（勿复读reading_note）",'
        '"data":{"nodes":[{"id":"D1","role":"diagnosis|condition|strategy|fallback","label":"≤16字诊断问题或策略动作",'
        '"detail":"≤55字判断标准、适用条件或止损阈值","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"D1","to":"D2","label":"≤5字判断条件"}]}'
    ),
    "quadrant_matrix": (
        '"blueprint":"quadrant_matrix","caption":"≤18字四象限图题","reading_note":"≤40字坐标取舍导读",'
        '"summary":"2句取舍总评（勿复读reading_note）",'
        '"data":{"xAxis":"横轴维度（低 → 高）","yAxis":"纵轴维度（低 → 高）",'
        '"quadrants":[{"pos":"q-top-left|q-top-right|q-bot-left|q-bot-right","title":"≤10字象限名称",'
        '"action_hint":"≤14字行动口诀(如 立即重仓投入)",'
        '"items":[{"text":"≤26字高杠杆条目","blocks":["B01"],"source":"原文|补充"}]}]}'
    ),
    "derivation_dag": (
        '"blueprint":"derivation_dag","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句核心推导解读（勿复读reading_note）",'
        '"data":{"paradigm":"deductive|experimental",'
        '"nodes":[{"id":"M1","role":"axiom|condition|lemma|theorem|observation|hypothesis|experiment|finding",'
        '"label":"≤16字核心节点","detail":"≤55字推导或检验含义","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"M1","to":"M2","label":"≤5字依据"}]}'
    ),
    "sensitivity_chart": (
        '"blueprint":"sensitivity_chart","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句临界阈值或参数响应解读（勿复读reading_note）",'
        '"data":{"mode":"regime_threshold|numeric_curve|not_applicable",'
        '"parameter":"控制变量名与单位(regime_threshold时必填)",'
        '"regimes":[{"range":"区间如 < 1.5 M☉","threshold":"临界阈值(无则留空)","state":"≤12字相态或结果",'
        '"mechanism":"≤55字物理或数学机制","tone":"stable|critical|breakdown","blocks":["B01"],"source":"原文|补充"}],'
        '"xLabel":"自变量与单位(仅numeric_curve)","yLabel":"因变量与单位(仅numeric_curve)","unit":"真实物理单位",'
        '"points":[{"x":"自变量真实刻度","y":0.0,"note":"≤45字物理意义","blocks":["B01"],"source":"原文"}],'
        '"not_applicable_reason":"仅mode为not_applicable时写2句原因",'
        '"highlights":[{"title":"≤18字定性制约","detail":"≤55字说明","blocks":["B01"],"source":"原文"}]}'
    ),
    "system_flow": (
        '"blueprint":"system_flow","caption":"≤18字图题","reading_note":"≤40字读图引导","summary":"2句机制运作解读（勿复读reading_note）",'
        '"data":{"mode":"flow|not_applicable","not_applicable_reason":"仅当mode为not_applicable时写2句说明",'
        '"nodes":[{"id":"C1","role":"input_energy|component|process|bottleneck|output_effect",'
        '"label":"≤16字装置部件/物理或演化步骤","detail":"≤55字作用机制与参数","blocks":["B01"],"source":"原文|补充"}],'
        '"edges":[{"from":"C1","to":"C2","label":"≤5字流转"}]}'
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
        "guidance": (
            "结构与口径要求（三分区、共 8–12 条高密度可勾选清单）：\n"
            '1. 第一区 section="before"（启动前准备，2–3 条）：动手前必须确认的前提条件、数据或资源准备；\n'
            '2. 第二区 section="during"（执行与核验，4–5 条）：关键操作动作与过程中的核验标准；\n'
            '3. 第三区 section="redline"（失效边界与踩坑红线，2–4 条）：何时立即止损停手、常见误用与失效红线；\n'
            "4. 每条 blocks 精选 1–3 个最直接块号；"
            "reading_note 必须针对本章主题定制（严禁照抄「适用前置条件」占位句）；\n"
            "5. summary 与卡片正文互补，禁止机械复读 reading_note；detail 开头禁止写「原文：」「补充：」。"
        ),
        "instruction": (
            '输出严格 JSON：{"blueprint":"extension_checklist","caption":"≤18字清单名",'
            '"reading_note":"≤40字本章定制导读（勿抄占位句）","summary":"2句落地总评（勿复读reading_note）",'
            '"items":[{"section":"before|during|redline","text":"≤26字可执行检查项",'
            '"detail":"≤45字核验标准或踩坑边界","checked":false,"blocks":["B01"],"source":"原文|补充"}]}'
        ),
    },
    "failure_postmortem": {
        "blueprint": "extension_dossier",
        "title": "反例与失效复盘",
        "guidance": (
            "结构与口径要求（4–6 张高密度结构化复盘卡）：\n"
            "1. 专拆书内真实反面案例、常见执行误区、失效根因与可执行纠偏动作，严禁泛泛而谈或复述成功做法；\n"
            "2. 每张卡必须同时填写四个结构化字段：pitfall（常见误区行为，≤35 字）、case（真实反例与后果，≤55 字）、"
            "root_cause（底层失效根因，≤45 字）、fix（纠偏动作，≤45 字），并保留 body 简要汇总；\n"
            "3. 每张卡 blocks 精选 1–3 个最直接原文块号；\n"
            "4. reading_note 必须点出本章最致命的一类失效陷阱（勿抄占位句），"
            "summary 须与卡片、reading_note 错位互补；\n"
            '5. 严格区分来源，source 为 "补充" 时字段开头禁止写「补充：」「补充对照：」。'
        ),
        "instruction": (
            '输出严格 JSON：{"blueprint":"extension_dossier","caption":"≤18字失效复盘题",'
            '"reading_note":"≤40字本章定制警示导读","summary":"2句总括规律（勿复读reading_note或卡片）",'
            '"items":[{"title":"≤20字失效模式标签","pitfall":"≤35字常见误区行为",'
            '"case":"≤55字书内反例与直接后果","root_cause":"≤45字底层失效根因","fix":"≤45字纠偏动作",'
            '"body":"≤75字兼容摘要","blocks":["B01"],"source":"原文|补充"}]}'
        ),
    },
    "narrative_clues": {
        "blueprint": "extension_dossier",
        "title": "叙事视角与伏笔线索",
        "instruction": (
            f"{_LITERATURE_ANTI_SPOILER}\n"
            "按以下三个分区共输出 6–9 条卡片（每条必须填写 section 字段，取下列三个分区名之一）：\n"
            "1. 「叙事视角与时间结构」（1–3 条）：叙述者身份、视角切换、倒叙/插叙/双线结构或记忆矛盾。\n"
            "2. 「关键伏笔、线索与信息差」（3–5 条）：草蛇灰线、物证/异常细节、谁知情谁不知情的信息差。\n"
            "3. 「本章悬念与待观察线」（1–2 条）：本章留下的合理悬念，严禁提前泄底。\n"
            '凡直接出自本章文本的视角、线索与悬念均标 source="原文"；仅引用书外创作背景或类型史对照时'
            '才标 "补充"，且 body 严禁以「补充：」开头。\n'
            '输出严格 JSON：{"blueprint":"extension_dossier","caption":"≤18字视角与线索题",'
            '"reading_note":"≤35字观察引导","summary":"2句总体线索综述（不与reading_note重复）",'
            '"items":[{"section":"叙事视角与时间结构|关键伏笔、线索与信息差|本章悬念与待观察线",'
            '"title":"≤20字视角或线索标签","body":"≤75字具体细节、信息差或悬念解析","blocks":["B01"],"source":"原文|补充"}]}'
        ),
    },
    "imagery": {
        "blueprint": "extension_dossier",
        "title": "意象、潜台词与细读",
        "instruction": (
            f"{_LITERATURE_ANTI_SPOILER}\n"
            "提纯为三个分区、共 6–9 张精品卡，严禁混入普通剧情复述，同时覆盖小说、戏剧、散文与诗歌：\n"
            "1. 「核心意象与象征物」（3–4 条）：表层物象 → 深层隐喻或前后呼应。\n"
            "2. 「潜台词、反讽与舞台重音」（2–3 条）：话里有话、动作停顿、情感错位。\n"
            "3. 「声景氛围、叙事修辞与名句细读」（2–3 条）：声景/光影调度、值得停步细品的原句或诗文韵律留白。\n"
            '严格校准 source：凡直接出自本章文本的意象、台词、声景与修辞解析均标 "原文"；'
            '仅引用书外文学史典故或创作背景时才标 "补充"，且 body 严禁以「补充：」开头。\n'
            '输出严格 JSON：{"blueprint":"extension_dossier","caption":"≤18字意象与细读题",'
            '"reading_note":"≤35字细读引导","summary":"2句基调总结（不与reading_note重复）",'
            '"items":[{"section":"核心意象与象征物|潜台词、反讽与舞台重音|声景氛围、叙事修辞与名句细读",'
            '"title":"≤20字意象或潜台词标签","body":"≤75字意象隐喻/潜台词/修辞细读","blocks":["B01"],"source":"原文|补充"}]}'
        ),
    },
    "historical_dossier": {
        "blueprint": "extension_dossier",
        "title": "人物与名物档案",
        "instruction": (
            '输出严格 JSON：{"blueprint":"extension_dossier","caption":"≤18字档案题",'
            '"reading_note":"≤40字导读提示","summary":"2句本章人物与制度总览",'
            '"sections":['
            '{"id":"figures","title":"出场人物与阵营速查","items":[{"name":"人物姓名","role_badge":"身份/阵营",'
            '"body":"≤60字在本章的关键作用","blocks":["B01"],"source":"原文|补充"}]},'
            '{"id":"glossary","title":"关键制度·条约·名物注","items":[{"name":"专名/制度/条约",'
            '"body":"≤60字简洁白话释义","blocks":["B01"],"source":"原文|补充"}]}'
            "]}\n"
            "要求：分区一列出 6–10 位核心人物；分区二列出 4–8 个核心专名/旧制/条约/器物/典故。"
            '严格区分原文与补充；当 source 为"补充"时，body 开头禁止重复写"补充："二字。'
        ),
    },
    "compare_context": {
        "blueprint": "extension_dossier",
        "title": "多史家视角对照",
        "instruction": (
            '输出严格 JSON：{"blueprint":"extension_dossier","caption":"≤18字史学对照题",'
            '"reading_note":"≤40字史料视角提醒","summary":"2句多视角摘要",'
            '"sections":['
            '{"id":"historiography","title":"史观与归因对撞","items":[{"stance":"立场方/学派","title":"≤18字核心论断",'
            '"body":"≤70字核心说法与隐含前提/局限","blocks":["B01"],"source":"原文"}]},'
            '{"id":"blindspots","title":"作者视角与史料盲区","items":[{"title":"≤18字盲区/偏重焦点",'
            '"body":"≤70字指出本章史料偏重与省略的声音","blocks":["B01"],"source":"原文|补充"}]},'
            '{"id":"world_anchors","title":"同代世界史横向锚点","items":[{"title":"≤20字精确年份与域外坐标",'
            '"body":"≤70字横向对照意义","blocks":["B01"],"source":"补充"}]}'
            "]}\n"
            "要求：三段总计严格控制在 6–9 条（分区一 3–4 条、分区二 1–2 条、分区三 2–3 条），"
            "严禁塞入普通章内事实复述或流水账；"
            '同代世界史锚点必须保证年份准确，且 source 为"补充"时 body 开头禁止重复写"补充："二字。'
        ),
    },
    "concept_glossary": {
        "blueprint": "extension_glossary",
        "title": "核心概念与公式词典",
        "guidance": (
            "结构与口径要求（6–9 条高密度科学词条）：\n"
            "1. 每条提炼一个推动本章推导或机制的核心概念、定律、关键物理量或博弈策略；\n"
            "2. symbol 填写原文明确出现或科学界公认的符号/公式/量纲"
            '（如 "M > 1.5 M☉"、"R_ab l^a l^b ≥ 0"、"≤ 0.05 mm"）；'
            '若该概念无明确公式/符号则留空 ""，严禁杜撰！\n'
            "3. intuition 用一句大白话直觉或类比讲透；definition 给出精确科学定义及在本章中的作用；\n"
            "4. 每条精选 1–3 个最直接块号，严禁堆砌长串块号；reading_note 须针对本章核心概念定制。"
        ),
        "instruction": (
            '输出严格 JSON：{"blueprint":"extension_glossary","caption":"≤18字科学词典题",'
            '"reading_note":"≤40字本章概念速查导读","summary":"2句总体概念主线（勿复读reading_note）",'
            '"items":[{"term":"≤18字核心概念或定律名","symbol":"可选符号/公式/量纲(无则留空)",'
            '"intuition":"≤40字一句话直觉或类比","definition":"≤75字精确科学定义与在本章中的作用",'
            '"blocks":["B01"],"source":"原文|补充"}]}'
        ),
    },
    "boundary_check": {
        "blueprint": "extension_dossier",
        "title": "演进与适用边界",
        "guidance": (
            "结构与口径要求（三分区、共 6–9 张高密度卡片）：\n"
            '1. 第一区 section="paradigm_shift"（旧认知推翻与范式演进，2–3 条）：'
            "点明作者/科学家推翻或修正了什么旧理论、直觉误区或工程路线；\n"
            '2. 第二区 section="intuition_analogy"（直观类比与物理直觉，1–2 条）：'
            "提炼帮助建立物理或数学直觉的关键类比（如地球经线类比共轭点、软木塞类比引力波、雪花类比晶体管产量）；\n"
            '3. 第三区 section="breakdown_boundary"（极端失效条件与未解边界，3–4 条）：'
            "界定定律、实验装置或模型在何种极端尺度、假设前提或外部约束下失效或尚待解决；\n"
            "4. reading_note 必须针对本章核心理论与适用条件定制，严禁照抄「公式/规律失效提示：」等模板前缀！\n"
            '5. 每卡精选 1–3 个块号；source 为 "补充" 时 body 开头严禁加「补充：」。'
        ),
        "instruction": (
            '输出严格 JSON：{"blueprint":"extension_dossier","caption":"≤18字演进与边界题",'
            '"reading_note":"≤40字本章定制边界提醒(勿抄模板前缀)","summary":"2句核心边界综述（勿复读reading_note）",'
            '"items":[{"section":"paradigm_shift|intuition_analogy|breakdown_boundary",'
            '"title":"≤20字演进/类比/失效边界标签","stance":"≤8字标签(如 范式演进/直觉类比/失效边界)",'
            '"body":"≤80字具体推翻对象、直观类比或极端失效条件","blocks":["B01"],"source":"原文|补充"}]}'
        ),
    },
    "sparring_dossier": {
        "blueprint": "extension_dossier",
        "title": "学术交锋档案",
        "guidance": (
            "结构与口径要求（两分区、共 6–8 条）：\n"
            "1. reading_note 必须针对本章具体论战对象与思想脉络定制"
            "（例如点出反驳谁、核心张力何在），严禁照抄泛泛占位句！\n"
            '2. 第一区 section="debate"（核心论战交锋，3–4 条）：'
            "按「论敌/靶子观点 → 作者反击 → 未决张力」高密度拆解；\n"
            '3. 第二区 section="thought_experiment"（思想实验与核心隐喻，2–4 条）：'
            "专拆本章推动论证的经典思想实验或核心隐喻，讲清实验设定与所证命题；\n"
            '4. 严格区分来源：出自本章正文与章末自带脚注的内容 source 标 "原文"；'
            '仅跨书对比或外部思想史参照标 "补充"，且 body 开头禁止写"补充对照："或"补充："。\n'
            "5. summary 须提炼 2 句总评，不得机械复读 reading_note。"
        ),
        "instruction": (
            '输出严格 JSON：{"blueprint":"extension_dossier","caption":"≤18字思想交锋题",'
            '"reading_note":"≤40字本章定制导读（点出具体交锋对象，勿抄占位句）",'
            '"summary":"2句总评（勿复读reading_note）",'
            '"items":[{"section":"debate|thought_experiment","title":"≤20字论战对象或思想实验名",'
            '"stance":"≤8字标签(如 论战靶子/思想实验/核心隐喻)",'
            '"body":"≤90字高密度拆解（靶子观点→作者反击→未决张力，或实验设定→所证命题）",'
            '"blocks":["B01"],"source":"原文|补充"}]}'
        ),
    },
    "counterexample_lab": {
        "blueprint": "extension_dossier",
        "title": "反例与现实检验",
        "guidance": (
            "结构与口径要求（三分区、共 5–7 条）：\n"
            '1. 第一区 section="hidden_premise"（隐含未言明前提，2 条）：'
            '揭示作者论证默认成立但未明说的前提假设，锚定原文块号（source 标 "原文"）；\n'
            '2. 第二区 section="counterexample"（哲学反例与极端压力测试，2–3 条）：'
            '构造直击论证薄弱点的极端反例或反直觉场景（source 标 "补充"，blocks 锚定被检验的原文段落）；\n'
            '3. 第三区 section="modern_boundary"（当代现实映射与适用边界，1–2 条）：'
            '界定该论证在当代科技、制度与伦理语境下仍有效之处与失效边界（source 标 "补充"）；\n'
            "4. reading_note 须针对本章核心命题定制；summary 须与卡片正文、reading_note 错位互补，"
            '严禁机械复读；body 开头禁止写"补充对照："或"补充："。'
        ),
        "instruction": (
            '输出严格 JSON：{"blueprint":"extension_dossier","caption":"≤18字反例检验题",'
            '"reading_note":"≤40字本章定制导读","summary":"2句检验结论（勿复读reading_note或正文）",'
            '"items":[{"section":"hidden_premise|counterexample|modern_boundary","title":"≤20字前提/反例/边界名",'
            '"stance":"≤8字标签(如 隐含前提/极端反例/当代边界)","body":"≤95字具体分析（勿加补充前缀）",'
            '"blocks":["B01"],"source":"原文|补充"}]}'
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


BLUEPRINT_GUIDANCE: dict[str, str] = {
    "parallel_timeline": (
        "专项约束（多线平行时间轴）：\n"
        "- lanes 必须按并行的历史行为体、阵营或地区划分（2–4 条），"
        "严禁把“作者评说/后世议论”当成一条时间行塞进年表中间。\n"
        "- 每行必须给出整数 sort_year（公元前用负数，例如 -221）与展示 time，并严格按时间从早到晚排列。\n"
        "- 史家评断或后世影响统一放在可选末尾 aftermath 数组，不要破坏时间线顺次。"
    ),
    "power_hierarchy": (
        "专项约束（权力与阵营结构）：\n"
        "- 只放权力实体、核心机构、派系阵营或硬性制度/条约约束，严禁把普通事件或史家评论当成权力节点。\n"
        "- 节点 role 仅限 sovereign（中枢/君主）、faction（核心机构/派系）、"
        "ally（盟友/根基）、rival（敌对/制衡）、constraint（条约/制度约束）。\n"
        "- 边方向严格遵循施动者→受动者，并标明 relation_type（subordinate/balance/alliance/hostile/constraint）。\n"
        "- 建议节点数 6–12 个，严禁超过 16 个。"
    ),
    "spatial_route": (
        "专项约束（地理与空间态势）：\n"
        "- 节点必须是真实地理区域、城市、要塞、关隘或通道，严禁拿段落大纲或抽象论点充数。\n"
        "- 每个节点必须提供归一化相对地理坐标 x（0–100，西→东）与 y（0–100，北→南），"
        "按真实上北下南、左西右东定标并拉开二维分布。\n"
        "- 边标注 kind（advance/flank/defense/diplomacy）与 route_group，label 可含兵力与日程（如 右翼16军·第12日）。\n"
        "- 若本章确实没有地理空间推进或地缘格局约束，请设 mode 为 not_applicable "
        "并用 2 句 not_applicable_reason 说明，绝不硬造假地图。\n"
        "- 建议节点数 5–12 个，严禁超过 16 个。"
    ),
    "historical_causality": (
        "专项约束（历史因果演进图）：\n"
        "- 聚焦“为何走到这一步”的深层结构与因果催化链，与地理空间、静态权力树解耦。\n"
        "- 节点 role 仅限 structural（深层结构）、trigger（触发事件）、"
        "mechanism（催化机制）、turning_point（关键转折）、consequence（长期后果）。\n"
        "- 边标签标明因果性质（如 引发、催化、倒逼、反噬）。建议节点数 6–12 个，严禁超过 16 个。"
    ),
    "tension_curve": (
        "张力曲线要求：\n"
        "- 请提炼 6–9 个关键戏剧节拍（不要按段落机械均分进度条），捕捉铺垫(setup)、伏笔(foreshadow)、"
        "虚假舒缓或骤降(relief)、冲突升级(escalation)、爆发顶点(climax)与留白余韵(aftermath)。\n"
        "- 每个节拍必须填写 beat_kind，并用 note（25–45 字）解释这一拍为何拉高或压低张力。\n"
        "- reading_note 写一句≤35字的读图视角引导，summary 写2句总体戏剧节奏解读（勿与 reading_note 重复）。"
    ),
    "relation_graph": (
        "人物与阵营图谱要求：\n"
        "- role 只能用文学人物角色：protagonist(核心人物)、ally(亲近/同阵营)、antagonist(对立/施压方)、"
        "catalyst(关键变数/催化者)、observer(旁观/叙述者/群像)，严禁使用 premise/condition/rebuttal/conclusion。\n"
        "- 严禁把天气、自然现象或纯事件当成人物节点；建议 6–12 位核心人物或群体。\n"
        "- 每节点填写 camp（阵营/家庭/阶层，如「鲁家」「周家」）、arc（本章心理/处境变化）"
        "与 1–3 个最关键代表块号 blocks。\n"
        "- 每条边填写 rel_kind：bond(亲情/爱恋)、conflict(冲突/对抗)、asymmetry(隐瞒/错位/不平等)、family(血缘/姻亲)。"
    ),
    "argument_map": (
        "专项约束（论证逻辑树）：\n"
        "- 必须按「核心论点/结论 ← 多条并行前提/理由 ← 潜在质疑与作者回应」的广度汇流树抽取"
        "（建议 6–10 节点，硬上限 16 个）。\n"
        "- 严禁按自然段顺序串成 9–10 层单链“段落大纲”，"
        "严禁把译者注、编者注或纯文献出处注画成正式论证节点。\n"
        "- 每节点 blocks 精选 1–3 个最直接支撑段落（禁止把连续多句诗行或整页块号全堆成一串）。\n"
        "- 节点 role 仅限 premise（前提）、inference（推论）、"
        "rebuttal（质疑/反驳）、conclusion（核心结论）、condition（限定条件）。"
    ),
    "term_contrast": (
        "专项约束（词义辨析双栏表）：\n"
        "- 严格收录 6–9 个核心哲学概念或作者专属术语，明确过滤普通观点句、结论句或生活格言。\n"
        "- original_term 填写原文明确出现或哲学史公认的外文/古文原词"
        "（如 Bourgeois、Anerkennung / Recognition、L'abyme de la philosophie）；"
        '若原文未出现且无公认对应原词则留空 ""，严禁杜撰。\n'
        "- 突出「日常汉语直觉」与「作者专属定义」之间的语义张力，每条配 1–3 个最直接出处块号。"
    ),
    "lineage_dag": (
        "专项约束（批判谱系图 — 与论证大纲彻底解耦）：\n"
        "- 每个节点必须是具体的思想家、哲学学派、思潮或作者自身立场"
        "（建议 6–12 节点，硬上限 16 个），严禁出现章节大纲句或一般命题摘要！\n"
        "- 节点 role 仅限 predecessor（前辈渊源）、ally（同调佐证）、"
        "mediator（转译改造）、target（批驳靶子）、author（作者立场）。\n"
        "- 每条边必须填 relation（inherit 继承 / transform 改造 / critique 批驳 / contrast 对照）"
        "及 ≤5 字简短 label（避免冗余交叉边）。\n"
        '- 若本章完全不涉及任何外部思想家、哲学学派或思潮对话，请设 data.mode="not_applicable" '
        "并用 2 句 not_applicable_reason 说明，绝不强行编造或拿章节大纲充数。"
    ),
    "flow_swimlane": (
        "专项约束（SOP 流程图 — 严禁画成章节大纲流水账）：\n"
        "- 必须从全章提炼可复用的先后执行 SOP 步骤（建议 6–10 个节点，硬上限 16 个），"
        "严禁按自然段顺序复述案例剧情或“第1节→第2节”大纲！\n"
        "- 节点 role 仅限 input（前置输入/材料）、action（核心动作）、"
        "gate（检查/分流门禁）、pitfall（避坑警报）、output（阶段产出）。\n"
        "- 每节点 blocks 精选 1–3 个最直接支撑该步骤的块号；边标签严格 ≤5 字。\n"
        '- 若全章纯粹属于理念探讨或无任何可复用的先后操作流程，请设 data.mode="not_applicable" '
        "并用 2 句 not_applicable_reason 说明，绝不硬编伪步骤。"
    ),
    "decision_tree": (
        "专项约束（条件决策树 — 必须呈真正树形分叉）：\n"
        "- 建议 6–12 个节点（硬上限 16 个）：根节点（diagnosis）为本章「核心决策场景 / 诊断问题」，"
        "必须分叉出 ≥2 条明确判断路径（condition），"
        "叶节点落到具体「推荐动作/策略（strategy）」或「止损/回退红线（fallback）」。\n"
        "- 严禁画成 10+ 层无分叉单链叙事或案例流水账！把“某公司/某设计发生了什么”转化为读者可直接套用的"
        "“若满足 X → 用 Y；否则 → Z 止损”。\n"
        "- 每条分叉出边 label 必须是 ≤5 字明确判断条件（如 若低成本、破坏性技术、缺失率低、已越红线）。\n"
        "- 每节点 blocks 精选 1–3 个最直接支撑块号。"
    ),
    "quadrant_matrix": (
        "专项约束（优先级四象限 — 严禁注水堆砌）：\n"
        "- 每个象限严格精选 2–4 条最高杠杆动作或决策（全图合计严格控制在 8–14 条），"
        "严禁把全章每段知识点塞满四象限！\n"
        "- 每个象限必须给出 action_hint（≤14 字行动口诀，如 立即重仓投入 / 小步试点验证 / 坚决舍弃）。\n"
        "- xAxis 与 yAxis 用清晰可比对的双维度（如 实施成本（低 → 高）、战略杠杆（低 → 高））；"
        "每条目精选 1–3 个最核心块号。"
    ),
    "derivation_dag": (
        "专项约束（公理-定理推导图 — 广度汇流推导树）：\n"
        "- 必须按「多条并行前提/观测/边界条件 → 中间引理/实验检验 → 核心定理/科学结论」的广度汇流推导树组织"
        "（建议 6–12 节点，硬上限 16 个），严禁画成 12–14 层单链章节流水账！\n"
        "- 按章节科学范式二选一（严禁将工程发明史硬套“引理→定理”）：\n"
        '  1) 公理演绎模式（理论物理/数学，paradigm="deductive"）：role 仅限 axiom（公理/假设）、'
        "condition（边界条件）、lemma（引理/中间推论）、theorem（核心定理）；\n"
        '  2) 实验推理模式（科普/科学史/工程实验，paradigm="experimental"）：role 仅限 observation（观测现象）、'
        "hypothesis（假说模型）、experiment（实验检验）、finding（科学结论）。\n"
        "- 每节点精选 1–3 个最核心块号，边标签 ≤5 字。"
    ),
    "sensitivity_chart": (
        "专项约束（临界条件与参数响应图 — 严禁编造假百分比！）：\n"
        "- 最高铁律：严禁编造 0–100 假百分比或主观打分！绝不输出“变化幅度(%)”或虚构分数！\n"
        "- 按章节实证特征在三种真实科学模式中三选一填入 data.mode：\n"
        '  1) mode="regime_threshold"（科学书最核心！临界相变/参数区间分档）：'
        "用于存在物理/数学/工程临界阈值引起定性相变或策略分化时"
        "（如恒星质量 <1.5M☉ 白矮星 → ~1.5–3M☉ 中子星 → >钱德拉塞卡极限 黑洞；"
        "电极间距 ≤0.05mm 功率放大 4.5× vs >0.05mm 失效；合作者比例 ≥5% 壮大 vs <5% 消亡）。"
        "必须填写控制变量 parameter 与 2–6 个 regimes"
        "（range、threshold、state、mechanism、tone=stable|critical|breakdown）；\n"
        '  2) mode="numeric_curve"（真实定量曲线）：仅当原文明确给出同一自变量的多组真实数值时选用，'
        "保留真实 xLabel / yLabel / unit 与原始数值（支持负值、小数、科学记数）；\n"
        '  3) mode="not_applicable"（纯公理/概念章退场）：若全章既无临界相变阈值也无定量参数响应，'
        '诚实设 mode="not_applicable"，填写 not_applicable_reason '
        '与 2–3 条原文定性制约 highlights（标 source="原文"）。'
    ),
    "system_flow": (
        "专项约束（机制与装置流图 — 彻底与推导树解耦！）：\n"
        "- 严禁使用 axiom/lemma/theorem 等逻辑证明角色，严禁克隆公理-定理推导链！\n"
        "- 专画「物理过程 / 实验装置结构 / 能量与信息流动 / 博弈或算法演化迭代机制」"
        "（建议 6–12 节点，硬上限 16 个）。\n"
        "- 节点 role 仅限 input_energy（输入/激励）、component（部件/介质）、"
        "process（物理/演化过程）、bottleneck（阻断/约束）、output_effect（宏观效应）。\n"
        "- 每节点精选 1–3 个最核心块号，边标签 ≤5 字。\n"
        '- 若本章属纯数学公理推导且完全无实体装置、物理演化过程或动态迭代机制，请设 data.mode="not_applicable" '
        "并用 2 句 not_applicable_reason 说明，绝不硬造。"
    ),
}

_LITERATURE_SPATIAL_GUIDANCE = (
    "空间与舞台动线三模式自适应规则（三选一填入 data.mode）：\n"
    '1. 单一封闭室内或戏剧舞台（如戏剧幕次、固定室内场）：mode="stage_blocking"，节点为房间区域、门窗与核心道具锚点'
    "（如中间木窗、窗外暗处、左门、右门、圆桌洋灯、床头箱子），"
    "坐标 x（0–100 左→右）、y（0–100 后景/窗=0 → 台口/观众=100）；\n"
    '边用 kind="move"(进出走位)/"gaze"(窥视偷听)/"barrier"(阻隔压迫) + actor 标出人物调度，'
    "绝不编造不存在的场景切换。\n"
    '2. 多地点移动的小说：mode="route_map"，节点为真实地点，role 取 home/public/threshold/transit/destination，'
    'x（0–100 西→东）、y（0–100 北→南），边用 kind="move"/"barrier"/"parallel" + actor。\n'
    '3. 无显著空间转换或舞台调度的抒情诗、议论散文、纯心理独白：mode="not_applicable"，nodes 与 edges 留空数组 []，'
    "在 not_applicable_reason 诚实写 2 句原因，绝不硬编。"
)


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
    is_lit_spatial = chosen == "spatial_route" and read_type == "literature"
    schema_hint = _LITERATURE_SPATIAL_SCHEMA_HINT if is_lit_spatial else BLUEPRINT_SCHEMA_HINTS[chosen]
    type_hint = f"（当前阅读视角：{read_type}）" if read_type else ""
    scope_hint = (
        f"当前作用范围：整章全局（涵盖全章 {covered_blocks or '全部'} 个正文段落"
        + ("，长章已按连续场景窗口抽样覆盖" if sampled else "")
        + "）。请覆盖全章各节真实脉络，不要只停留在开头几段，每个节点 blocks 必须准确引用对应段落真实块号。"
        if scope == "chapter"
        else f"当前作用范围：读者选中的段落（{', '.join(block_ids or []) or '当前片段'}）。"
    )
    extra_lines: list[str] = []
    if read_type == "literature":
        extra_lines.append(_LITERATURE_ANTI_SPOILER)
    if is_lit_spatial:
        extra_lines.append(_LITERATURE_SPATIAL_GUIDANCE)
    elif chosen in BLUEPRINT_GUIDANCE:
        extra_lines.append(BLUEPRINT_GUIDANCE[chosen])
    guidance_block = ("\n".join(extra_lines) + "\n") if extra_lines else ""
    coord_rule = (
        "2. 严禁输出 HTML/SVG 代码或像素宽高；必须给出归一化相对地理方位坐标 x（0–100，西→东）与 y（0–100，北→南）。\n"
        if chosen == "spatial_route"
        else "2. 严禁输出任何像素坐标、宽高、颜色或 HTML/SVG 代码。\n"
    )
    edge_limit_hint = "12 字以内" if (chosen == "spatial_route" and not is_lit_spatial) else "5 字以内"
    node_cap_hint = f"（图节点上限 {MAX_GRAPH_NODES} 个，建议见专项约束或 6–12 个）" if chosen in DAG_BLUEPRINTS else ""
    system = (
        f"你是阅读伴读助手，擅长把复杂文字转成清晰的关系图与结构化蓝图{type_hint}。\n"
        f"{scope_hint}\n"
        f"{guidance_block}"
        "严格要求：\n"
        "1. 只输出一个合法 JSON 对象，不要 markdown 代码围栏，不要多余解释。\n"
        f"{coord_rule}"
        f"3. 每个节点主标题严格控制在 16 字以内，详细解释放到 detail；边标签控制在 {edge_limit_hint}{node_cap_hint}。\n"
        '4. 每个节点或要点都必须填写来源于本段实际编号的 blocks 数组（例如 ["B02"]），'
        '并注明 source（"原文" 或 "补充"）；source 为 "补充" 时正文开头勿写"补充："，summary 勿复读 reading_note。\n'
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
    """构建书型专属扩展动作的 JSON 结构化 Prompt。"""
    spec = EXTENSION_PROMPT_SPECS[action]
    sub_hint = f"（文学子类侧重点：{sub_kind}）" if sub_kind else ""
    type_hint = f"（阅读视角：{read_type}）" if read_type else ""
    scope_hint = (
        f"作用范围：整章全局（涵盖全章 {covered_blocks or '全部'} 个正文段落"
        + ("，已连续场景窗口覆盖" if sampled else "")
        + "）。请归纳全章而不只看开头。"
        if scope == "chapter"
        else f"作用范围：读者选中的段落（{', '.join(block_ids or []) or '局部'}）。"
    )
    spoiler_rule = (
        f"{_LITERATURE_ANTI_SPOILER}\n"
        if (read_type == "literature" or action in {"imagery", "narrative_clues"})
        and _LITERATURE_ANTI_SPOILER not in spec["instruction"]
        else ""
    )
    guidance = spec.get("guidance") or ""
    guidance_block = f"{guidance}\n" if guidance else ""
    system = (
        f"你是阅读伴读专家，负责生成「{spec['title']}」结构卡片{type_hint}{sub_hint}。\n"
        f"{scope_hint}\n"
        f"{spoiler_rule}"
        f"{guidance_block}"
        '严格区分 [原文] 与 [补充]（source 为 "补充" 时 body 开头禁止写"补充对照："或"补充："），'
        "严禁输出坐标或样式，只输出一个严格 JSON 对象（不要围栏）：\n"
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


def _strip_source_prefix(text: str, source: str = "") -> str:
    return _SOURCE_PREFIX_RE.sub("", str(text or "")).strip()


def _parse_sort_year(raw_val: Any, time_lbl: str) -> int | None:
    if raw_val is not None and not isinstance(raw_val, bool):
        try:
            return int(float(str(raw_val).strip()))
        except (TypeError, ValueError):
            pass
    m_bc = _TIME_BC_RE.search(time_lbl or "")
    if m_bc:
        return -int(m_bc.group(1))
    m_ad = _TIME_AD_RE.search(time_lbl or "")
    if m_ad:
        return int(m_ad.group(1))
    return None


def _norm_lineage_role(raw_role: Any) -> str:
    role = str(raw_role or "").strip().lower()
    if role in LINEAGE_ROLES:
        return role
    mapping = {
        "premise": "predecessor",
        "axiom": "predecessor",
        "condition": "mediator",
        "inference": "mediator",
        "rebuttal": "target",
        "rival": "target",
        "conclusion": "author",
    }
    return mapping.get(role, "mediator")


def _norm_lineage_relation(raw_rel: Any, label: str) -> str:
    rel = str(raw_rel or "").strip().lower()
    if rel in LINEAGE_RELATIONS:
        return rel
    lbl = str(label or "")
    if any(k in lbl for k in ("批", "驳", "反", "敌", "拒", "破")):
        return "critique"
    if any(k in lbl for k in ("改", "转", "修", "重", "化", "发展")):
        return "transform"
    if any(k in lbl for k in ("对", "比", "异", "分", "参")):
        return "contrast"
    return "inherit"


def _norm_flow_role(raw_role: Any) -> str:
    role = str(raw_role or "").strip().lower()
    if role in FLOW_ROLES:
        return role
    mapping = {
        "premise": "input",
        "axiom": "input",
        "structural": "input",
        "step": "action",
        "inference": "action",
        "lemma": "action",
        "condition": "gate",
        "mediator": "gate",
        "rebuttal": "pitfall",
        "risk": "pitfall",
        "target": "pitfall",
        "conclusion": "output",
        "theorem": "output",
        "outcome": "output",
        "author": "output",
    }
    return mapping.get(role, "action")


def _norm_decision_role(raw_role: Any) -> str:
    role = str(raw_role or "").strip().lower()
    if role in DECISION_ROLES:
        return role
    mapping = {
        "premise": "diagnosis",
        "axiom": "diagnosis",
        "sovereign": "diagnosis",
        "structural": "diagnosis",
        "input": "diagnosis",
        "condition": "condition",
        "inference": "condition",
        "gate": "condition",
        "mediator": "condition",
        "conclusion": "strategy",
        "theorem": "strategy",
        "action": "strategy",
        "output": "strategy",
        "author": "strategy",
        "rebuttal": "fallback",
        "risk": "fallback",
        "pitfall": "fallback",
        "target": "fallback",
    }
    return mapping.get(role, "condition")


def _norm_derivation_role(raw_role: Any) -> str:
    role = str(raw_role or "").strip().lower()
    if role in DERIVATION_ROLES:
        return role
    mapping = {
        "premise": "axiom",
        "inference": "lemma",
        "conclusion": "theorem",
        "rebuttal": "condition",
        "constraint": "condition",
        "structural": "axiom",
        "trigger": "observation",
        "mechanism": "experiment",
        "consequence": "finding",
    }
    return mapping.get(role, "lemma")


def _norm_system_flow_role(raw_role: Any) -> str:
    role = str(raw_role or "").strip().lower()
    if role in SYSTEM_FLOW_ROLES:
        return role
    mapping = {
        "axiom": "input_energy",
        "premise": "input_energy",
        "observation": "input_energy",
        "trigger": "input_energy",
        "condition": "bottleneck",
        "constraint": "bottleneck",
        "rebuttal": "bottleneck",
        "inference": "process",
        "lemma": "process",
        "mechanism": "process",
        "experiment": "process",
        "step": "process",
        "conclusion": "output_effect",
        "theorem": "output_effect",
        "finding": "output_effect",
        "consequence": "output_effect",
        "outcome": "output_effect",
    }
    return mapping.get(role, "process")


def _audit_dag_data(
    raw_data: dict[str, Any],
    *,
    valid_ids: set[str],
    default_blocks: list[str],
    logs: list[str],
    blueprint: str = "",
) -> dict[str, Any] | None:
    if (
        blueprint in {"lineage_dag", "flow_swimlane", "system_flow"}
        and str(raw_data.get("mode") or "").strip().lower() == "not_applicable"
    ):
        default_na = {
            "flow_swimlane": "本章属理念综述或概念阐发，未包含可复用的先后操作步骤。",
            "system_flow": "本章属纯公理或概念推演，未涉及实体实验装置、物理过程或演化迭代机制。",
        }.get(blueprint, "本章集中展开内部论证或对话，未涉及外部思想家或学派谱系。")
        reason = str(raw_data.get("not_applicable_reason") or raw_data.get("reason") or default_na).strip()
        logs.append(f"{blueprint} 判定当前章节不适用该流程模型，已返回 not_applicable 提示")
        return {
            "mode": "not_applicable",
            "reason": reason,
            "not_applicable_reason": reason,
            "nodes": [],
            "edges": [],
        }

    raw_nodes = raw_data.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        return None
    if len(raw_nodes) > MAX_GRAPH_NODES:
        logs.append(f"节点数 {len(raw_nodes)} 超出单图上限 {MAX_GRAPH_NODES}，触发结构化清单降级")
        return None

    fallback_role = DEFAULT_NODE_ROLE.get(blueprint, "inference")
    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for idx, item in enumerate(raw_nodes):
        if not isinstance(item, dict):
            continue
        nid = str(item.get("id") or f"N{idx + 1}").strip()
        source = _norm_source(item.get("source"))
        raw_label = _strip_source_prefix(str(item.get("label") or item.get("title") or nid), source)
        detail = _strip_source_prefix(str(item.get("detail") or item.get("note") or ""), source)
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
        raw_role = str(item.get("role") or fallback_role).strip()
        if blueprint == "argument_map":
            role = raw_role.lower() if raw_role.lower() in ARGUMENT_ROLES else "inference"
        elif blueprint == "lineage_dag":
            role = _norm_lineage_role(raw_role)
        elif blueprint == "flow_swimlane":
            role = _norm_flow_role(raw_role)
        elif blueprint == "decision_tree":
            role = _norm_decision_role(raw_role)
        elif blueprint == "derivation_dag":
            role = _norm_derivation_role(raw_role)
        elif blueprint == "system_flow":
            role = _norm_system_flow_role(raw_role)
        elif blueprint == "power_hierarchy" and raw_role not in POWER_ROLES:
            role = fallback_role
        else:
            role = raw_role or fallback_role
        node_ids.add(nid)
        node_entry: dict[str, Any] = {
            "id": nid,
            "role": role,
            "label": label,
            "detail": detail,
            "blocks": blocks,
            "source": source,
        }
        camp = str(item.get("camp") or "").strip()[:24]
        if camp:
            node_entry["camp"] = camp
        arc = str(item.get("arc") or "").strip()[:60]
        if arc:
            node_entry["arc"] = arc
        nodes.append(node_entry)
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
        entry: dict[str, str] = {"from": src, "to": dst, "label": short_lbl, "note": note}
        rel_type = str(edge.get("relation_type") or "").strip()
        if rel_type in POWER_RELATIONS:
            entry["relation_type"] = rel_type
        rel_kind = str(edge.get("rel_kind") or "").strip()
        if rel_kind:
            entry["rel_kind"] = rel_kind
        if blueprint == "lineage_dag":
            entry["relation"] = _norm_lineage_relation(
                edge.get("relation") or edge.get("relation_type"),
                short_lbl,
            )
        edges.append(entry)

    if blueprint == "decision_tree" and len(nodes) >= 5:
        out_deg: dict[str, int] = {}
        for ed in edges:
            out_deg[ed["from"]] = out_deg.get(ed["from"], 0) + 1
        if not any(cnt >= 2 for cnt in out_deg.values()):
            logs.append("决策树未检出多分支条件节点，已保留结构供核对")

    out: dict[str, Any] = {"nodes": nodes, "edges": edges}
    if blueprint == "lineage_dag":
        out["mode"] = "lineage"
    elif blueprint in {"flow_swimlane", "system_flow"}:
        out["mode"] = "flow"
    elif blueprint == "derivation_dag":
        paradigm = str(raw_data.get("paradigm") or "").strip().lower()
        if paradigm in DERIVATION_PARADIGMS:
            out["paradigm"] = paradigm
        else:
            exp_roles = {"observation", "hypothesis", "experiment", "finding"}
            out["paradigm"] = "experimental" if any(n["role"] in exp_roles for n in nodes) else "deductive"
    return out


def _clean_original_term(val: Any) -> str:
    txt = str(val or "").strip()
    if not txt or txt.lower() in _EMPTY_TERM_VALUES:
        return ""
    return txt[:80]


def _clamp_pct(val: Any, default: float = 50.0) -> float:
    try:
        num = float(default if val is None else val)
    except (TypeError, ValueError):
        num = default
    return round(max(0.0, min(100.0, num)), 1)


def _audit_spatial_data(
    raw_data: dict[str, Any],
    *,
    valid_ids: set[str],
    default_blocks: list[str],
    logs: list[str],
) -> dict[str, Any] | None:
    mode = str(raw_data.get("mode") or "").strip().lower()
    raw_nodes = raw_data.get("nodes")
    if mode == "not_applicable" or (raw_data.get("not_applicable_reason") and not raw_nodes):
        reason = (
            str(raw_data.get("not_applicable_reason") or "").strip()
            or "本章无显著物理空间转换、地缘约束或舞台调度，无需强行绘制空间图。"
        )[:180]
        return {"mode": "not_applicable", "not_applicable_reason": reason, "nodes": [], "edges": []}

    lit_mode = mode in {"route_map", "stage_blocking"}
    has_coords = isinstance(raw_nodes, list) and any(
        isinstance(nd, dict) and nd.get("x") is not None and nd.get("y") is not None for nd in raw_nodes
    )
    if not lit_mode and not has_coords:
        if not isinstance(raw_nodes, list) or not raw_nodes:
            return None
        logs.append("兼容旧版无坐标空间图缓存，按拓扑模式渲染")
        return _audit_dag_data(
            raw_data,
            valid_ids=valid_ids,
            default_blocks=default_blocks,
            logs=logs,
            blueprint="spatial_route",
        )

    if not isinstance(raw_nodes, list) or not raw_nodes:
        return None
    if len(raw_nodes) > MAX_GRAPH_NODES:
        logs.append(f"空间节点数 {len(raw_nodes)} 超出单图上限 {MAX_GRAPH_NODES}，触发结构化清单降级")
        return None

    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    xs: list[float] = []
    ys: list[float] = []
    for idx, item in enumerate(raw_nodes):
        if not isinstance(item, dict):
            continue
        raw_x_val = item.get("x")
        raw_y_val = item.get("y")
        if not lit_mode:
            if raw_x_val is None or raw_y_val is None or isinstance(raw_x_val, bool) or isinstance(raw_y_val, bool):
                logs.append(f"空间节点 {item.get('id') or idx + 1} 坐标缺失或非数字，已跳过")
                continue
            try:
                x = _clamp_pct(float(raw_x_val))
                y = _clamp_pct(float(raw_y_val))
            except (TypeError, ValueError):
                logs.append(f"空间节点 {item.get('id') or idx + 1} 坐标缺失或非数字，已跳过")
                continue
        else:
            default_x = 18.0 + (idx % 4) * 21.0
            default_y = 20.0 + (idx // 4) * 28.0
            x = _clamp_pct(raw_x_val, default_x)
            y = _clamp_pct(raw_y_val, default_y)
        nid = str(item.get("id") or f"S{idx + 1}").strip()
        source = _norm_source(item.get("source"))
        raw_label = _strip_source_prefix(str(item.get("label") or item.get("title") or nid), source)
        detail = _strip_source_prefix(str(item.get("detail") or item.get("note") or ""), source)
        if len(raw_label) > NODE_LABEL_LIMIT:
            detail = f"{raw_label} · {detail}".strip(" ·") if detail else raw_label
            label = raw_label[:NODE_LABEL_LIMIT] + "…"
            logs.append(f'空间节点 "{nid}" 标题超过 {NODE_LABEL_LIMIT} 字，已自动分流至 detail')
        else:
            label = raw_label
        blocks = _clean_blocks(
            item.get("blocks") or item.get("block"),
            valid_ids=valid_ids,
            default_blocks=default_blocks,
            hint=label,
            logs=logs,
        )
        default_role = "zone" if mode == "stage_blocking" else ("public" if lit_mode else "corridor")
        role = str(item.get("role") or default_role).strip()
        if role not in SPATIAL_ROLES:
            role = default_role
        zone = str(item.get("zone") or "").strip()[:20]
        node_ids.add(nid)
        xs.append(x)
        ys.append(y)
        node_entry: dict[str, Any] = {
            "id": nid,
            "label": label,
            "x": x,
            "y": y,
            "role": role,
            "detail": detail,
            "blocks": blocks,
            "source": source,
        }
        if zone or not lit_mode:
            node_entry["zone"] = zone
        nodes.append(node_entry)

    if not nodes:
        return None
    if len(nodes) >= 3 and (max(xs) - min(xs) < 8.0 and max(ys) - min(ys) < 8.0):
        logs.append("空间节点坐标过度聚拢且缺少二维分布，触发结构化清单降级")
        return None

    edge_limit = SPATIAL_EDGE_LABEL_LIMIT if not lit_mode else EDGE_LABEL_LIMIT
    default_kind = "move" if lit_mode else "advance"
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
        if len(raw_lbl) > edge_limit:
            note = f"{raw_lbl} · {note}".strip(" ·") if note else raw_lbl
            short_lbl = raw_lbl[:edge_limit] + "…"
            if lit_mode:
                logs.append(f'边 "{src}→{dst}" 标签超长，已收缩为 "{short_lbl}"')
        else:
            short_lbl = raw_lbl
        kind = str(edge.get("kind") or default_kind).strip()
        if kind not in SPATIAL_EDGE_KINDS:
            kind = default_kind
        edge_entry: dict[str, str] = {
            "from": src,
            "to": dst,
            "label": short_lbl,
            "note": note,
            "kind": kind,
        }
        route_group = str(edge.get("route_group") or "").strip()
        if route_group or not lit_mode:
            edge_entry["route_group"] = route_group
        actor = str(edge.get("actor") or "").strip()[:16]
        if actor:
            edge_entry["actor"] = actor
        edges.append(edge_entry)

    clean_mode = mode if mode in {"campaign", "geopolitics", "route_map", "stage_blocking"} else "geopolitics"
    out: dict[str, Any] = {
        "mode": clean_mode,
        "nodes": nodes,
        "edges": edges,
    }
    scope_note = str(raw_data.get("scope_note") or "").strip()
    if scope_note:
        out["scope_note"] = scope_note
    stage_desc = str(raw_data.get("stage_desc") or "").strip()[:60]
    if stage_desc:
        out["stage_desc"] = stage_desc
    return out


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
            source = _norm_source(row.get("source"))
            key = _strip_source_prefix(str(row.get("key") or row.get("term") or ""), source)
            original_term = _clean_original_term(row.get("original_term") or row.get("orig_term"))
            everyday = _strip_source_prefix(str(row.get("everyday") or row.get("col1") or ""), source)
            author = _strip_source_prefix(str(row.get("author") or row.get("col2") or ""), source)
            extra = _strip_source_prefix(str(row.get("extra") or row.get("col3") or ""), source)
            blocks = _clean_blocks(
                row.get("blocks"), valid_ids=valid_ids, default_blocks=default_blocks, hint=key or "矩阵行", logs=logs
            )
            entry: dict[str, Any] = {
                "key": key,
                "everyday": everyday,
                "author": author,
                "extra": extra,
                "blocks": blocks,
                "source": source,
            }
            if original_term or "original_term" in row or "orig_term" in row:
                entry["original_term"] = original_term
            rows.append(entry)
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
        source = _norm_source(pt.get("source"))
        beat = _strip_source_prefix(str(pt.get("beat") or f"节拍{idx + 1}"), source)[:12]
        raw_val = pt.get("tension", pt.get("value", 50))
        try:
            val = float(50.0 if raw_val is None else raw_val)
        except (TypeError, ValueError):
            val = 50.0
        val = max(0.0, min(100.0, val))
        emotion = _strip_source_prefix(str(pt.get("emotion") or pt.get("note") or ""), source)[:28]
        note = _strip_source_prefix(str(pt.get("note") or ""), source)[:120]
        beat_kind = str(pt.get("beat_kind") or "").strip().lower()
        if beat_kind not in _BEAT_KINDS:
            beat_kind = ""
        blocks = _clean_blocks(
            pt.get("blocks"), valid_ids=valid_ids, default_blocks=default_blocks, hint=beat, logs=logs
        )
        point_entry: dict[str, Any] = {
            "beat": beat,
            "tension": val,
            "emotion": emotion,
            "blocks": blocks,
            "source": source,
        }
        if beat_kind:
            point_entry["beat_kind"] = beat_kind
        if note and note != emotion:
            point_entry["note"] = note
        points.append(point_entry)
    if not points:
        return None
    return {"yLabel": str(raw_data.get("yLabel") or "强度"), "points": points}


def _norm_sensitivity_tone(raw_tone: Any, state: str = "") -> str:
    tone = str(raw_tone or "").strip().lower()
    if tone in SENSITIVITY_TONES:
        return tone
    txt = f"{tone} {state}"
    if any(k in txt for k in ("崩溃", "失效", "坍缩", "灭绝", "消亡", "奇点", "breakdown")):
        return "breakdown"
    if any(k in txt for k in ("临界", "阈值", "极限", "相变", "转折", "critical")):
        return "critical"
    return "stable"


def _audit_sensitivity_data(
    raw_data: dict[str, Any],
    *,
    valid_ids: set[str],
    default_blocks: list[str],
    logs: list[str],
) -> dict[str, Any] | None:
    mode = str(raw_data.get("mode") or "").strip().lower()
    raw_regimes = raw_data.get("regimes")
    raw_points = raw_data.get("points")

    if mode == "not_applicable" or (raw_data.get("not_applicable_reason") and not raw_regimes and not raw_points):
        reason = str(
            raw_data.get("not_applicable_reason")
            or raw_data.get("reason")
            or "本章集中展开定性公理或概念推演，未出现定量参数响应或相变临界阈值。"
        ).strip()[:200]
        highlights: list[dict[str, Any]] = []
        for idx, hl in enumerate(raw_data.get("highlights") or []):
            if not isinstance(hl, dict):
                continue
            source = _norm_source(hl.get("source"))
            title = _strip_source_prefix(str(hl.get("title") or hl.get("label") or f"定性制约 {idx + 1}"), source)[:24]
            detail = _strip_source_prefix(str(hl.get("detail") or hl.get("body") or hl.get("note") or ""), source)[:90]
            blocks = _clean_blocks(
                hl.get("blocks"), valid_ids=valid_ids, default_blocks=default_blocks, hint=title, logs=logs
            )
            highlights.append({"title": title, "detail": detail, "blocks": blocks, "source": source})
        logs.append("本章无定量曲线或临界阈值，已按 not_applicable 呈现定性制约")
        return {
            "mode": "not_applicable",
            "reason": reason,
            "not_applicable_reason": reason,
            "highlights": highlights,
        }

    if mode == "regime_threshold" or (isinstance(raw_regimes, list) and raw_regimes):
        if not isinstance(raw_regimes, list) or not raw_regimes:
            return None
        parameter = str(raw_data.get("parameter") or "控制参数").strip()[:40]
        regimes: list[dict[str, Any]] = []
        for idx, rg in enumerate(raw_regimes):
            if not isinstance(rg, dict):
                continue
            source = _norm_source(rg.get("source"))
            rng = _strip_source_prefix(str(rg.get("range") or rg.get("condition") or f"区间 {idx + 1}"), source)[:28]
            threshold = _strip_source_prefix(str(rg.get("threshold") or ""), source)[:28]
            state = _strip_source_prefix(str(rg.get("state") or rg.get("outcome") or ""), source)[:24]
            mechanism = _strip_source_prefix(str(rg.get("mechanism") or rg.get("detail") or ""), source)[:100]
            if not state and not mechanism:
                continue
            blocks = _clean_blocks(
                rg.get("blocks"), valid_ids=valid_ids, default_blocks=default_blocks, hint=rng, logs=logs
            )
            regimes.append(
                {
                    "range": rng,
                    "threshold": threshold,
                    "state": state or rng,
                    "mechanism": mechanism,
                    "tone": _norm_sensitivity_tone(rg.get("tone"), f"{state} {threshold}"),
                    "blocks": blocks,
                    "source": source,
                }
            )
        if not regimes:
            return None
        return {"mode": "regime_threshold", "parameter": parameter, "regimes": regimes}

    if isinstance(raw_points, list) and raw_points:
        y_label = str(raw_data.get("yLabel") or "").strip()
        x_label = str(raw_data.get("xLabel") or "").strip()
        unit = str(raw_data.get("unit") or "").strip()[:20]
        has_real_xy = any(isinstance(pt, dict) and ("y" in pt or "x" in pt) for pt in raw_points)
        looks_fake_pct = not has_real_xy and ("变化幅度" in y_label or y_label in {"", "强度", "变化幅度(%)"})
        if looks_fake_pct:
            # 旧版 0–100 伪量化载荷或模型回退为 tension：剥除假分数，转为定性参数档位
            converted: list[dict[str, Any]] = []
            for idx, pt in enumerate(raw_points):
                if not isinstance(pt, dict):
                    continue
                source = _norm_source(pt.get("source"))
                rng = _strip_source_prefix(str(pt.get("beat") or f"档位 {idx + 1}"), source)[:28]
                state = _strip_source_prefix(str(pt.get("emotion") or rng), source)[:24]
                mech = _strip_source_prefix(str(pt.get("note") or ""), source)[:100]
                blocks = _clean_blocks(
                    pt.get("blocks"), valid_ids=valid_ids, default_blocks=default_blocks, hint=rng, logs=logs
                )
                converted.append(
                    {
                        "range": rng,
                        "threshold": "",
                        "state": state,
                        "mechanism": mech,
                        "tone": _norm_sensitivity_tone("", state),
                        "blocks": blocks,
                        "source": source,
                    }
                )
            if converted:
                logs.append("检测到无实测单位的 0–100 假分数，已剥除伪量化并转为临界分档视图")
                return {
                    "mode": "regime_threshold",
                    "parameter": x_label or "参数变化档位",
                    "regimes": converted,
                }
            return None

        points: list[dict[str, Any]] = []
        for idx, pt in enumerate(raw_points):
            if not isinstance(pt, dict):
                continue
            source = _norm_source(pt.get("source"))
            x_val = _strip_source_prefix(str(pt.get("x") or pt.get("beat") or f"点{idx + 1}"), source)[:24]
            raw_y = pt.get("y", pt.get("value"))
            if raw_y is None or isinstance(raw_y, bool):
                continue
            try:
                y_val = float(raw_y)
            except (TypeError, ValueError):
                continue
            note = _strip_source_prefix(str(pt.get("note") or pt.get("emotion") or ""), source)[:100]
            blocks = _clean_blocks(
                pt.get("blocks"), valid_ids=valid_ids, default_blocks=default_blocks, hint=x_val, logs=logs
            )
            points.append({"x": x_val, "y": y_val, "note": note, "blocks": blocks, "source": source})
        if not points:
            return None
        return {
            "mode": "numeric_curve",
            "xLabel": x_label or "自变量",
            "yLabel": y_label or "响应量",
            "unit": unit,
            "points": points,
        }
    return None


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
        sort_year = _parse_sort_year(row.get("sort_year"), time_lbl)
        cells: list[dict[str, Any] | None] = []
        raw_cells = row.get("cells") or []
        for idx in range(len(lanes)):
            cell = raw_cells[idx] if idx < len(raw_cells) else None
            if not isinstance(cell, dict):
                cells.append(None)
                continue
            source = _norm_source(cell.get("source"))
            title = _strip_source_prefix(str(cell.get("title") or ""), source) or "事件"
            note = _strip_source_prefix(str(cell.get("note") or ""), source)
            blocks = _clean_blocks(
                cell.get("blocks"), valid_ids=valid_ids, default_blocks=default_blocks, hint=title, logs=logs
            )
            cells.append(
                {
                    "title": title,
                    "note": note,
                    "blocks": blocks,
                    "source": source,
                }
            )
        entry: dict[str, Any] = {"time": time_lbl, "cells": cells}
        if sort_year is not None:
            entry["sort_year"] = sort_year
        rows.append(entry)
    if not rows:
        return None

    # 校验并修正时间逆序（保留无年份旧缓存的原有顺序）
    dated_years = [r["sort_year"] for r in rows if "sort_year" in r]
    if len(dated_years) >= 2 and any(dated_years[i] < dated_years[i - 1] for i in range(1, len(dated_years))):
        rows = [
            item
            for _, item in sorted(
                enumerate(rows),
                key=lambda pair: (
                    0 if "sort_year" in pair[1] else 1,
                    pair[1].get("sort_year", 0),
                    pair[0],
                ),
            )
        ]
        logs.append("时间轴检测到年份逆序，已按 sort_year 自动重排")

    result: dict[str, Any] = {"lanes": lanes, "rows": rows}
    raw_aftermath = raw_data.get("aftermath")
    if isinstance(raw_aftermath, list):
        aftermath: list[dict[str, Any]] = []
        for af in raw_aftermath:
            if not isinstance(af, dict):
                continue
            source = _norm_source(af.get("source"))
            title = _strip_source_prefix(str(af.get("title") or af.get("name") or ""), source)
            body = _strip_source_prefix(str(af.get("body") or af.get("detail") or af.get("note") or ""), source)
            if not title and not body:
                continue
            blocks = _clean_blocks(
                af.get("blocks"),
                valid_ids=valid_ids,
                default_blocks=default_blocks,
                hint=title or "后世回响",
                logs=logs,
            )
            aftermath.append({"title": title, "body": body, "blocks": blocks, "source": source})
        if aftermath:
            result["aftermath"] = aftermath
    return result


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
        title = str(q.get("title") or pos).strip()
        action_hint = _strip_source_prefix(str(q.get("action_hint") or q.get("action") or ""), "")[:20]
        items: list[dict[str, Any]] = []
        for it in q.get("items") or []:
            if not isinstance(it, dict):
                continue
            source = _norm_source(it.get("source"))
            txt = _strip_source_prefix(str(it.get("text") or ""), source)
            if not txt:
                continue
            blocks = _clean_blocks(
                it.get("blocks"), valid_ids=valid_ids, default_blocks=default_blocks, hint=txt[:10], logs=logs
            )
            items.append({"text": txt, "blocks": blocks, "source": source})
        if len(items) > QUADRANT_MAX_ITEMS:
            logs.append(f'象限 "{title}" 原有 {len(items)} 条，已精简截断为最高杠杆前 {QUADRANT_MAX_ITEMS} 条')
            items = items[:QUADRANT_MAX_ITEMS]
        q_entry: dict[str, Any] = {"pos": pos, "title": title, "items": items}
        if action_hint:
            q_entry["action_hint"] = action_hint
        quads.append(q_entry)
    return {
        "xAxis": str(raw_data.get("xAxis") or "横轴（低 → 高）"),
        "yAxis": str(raw_data.get("yAxis") or "纵轴（低 → 高）"),
        "quadrants": quads,
    }


def _norm_checklist_section(raw_section: str) -> str:
    sec = raw_section.strip().lower()
    if not sec:
        return ""
    if sec in {"before", "during", "redline"}:
        return sec
    if any(k in sec for k in ("前", "准备", "启动", "prereq", "pre")):
        return "before"
    if any(k in sec for k in ("红线", "失效", "避坑", "边界", "警告", "stop", "risk", "pitfall")):
        return "redline"
    if any(k in sec for k in ("中", "执行", "核验", "步骤", "操作", "check", "exec")):
        return "during"
    return "during"


def _audit_extension_items(
    raw_items: list[Any],
    *,
    bp: str,
    valid_ids: set[str],
    default_blocks: list[str],
    logs: list[str],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for it in raw_items or []:
        if not isinstance(it, dict):
            continue
        source = _norm_source(it.get("source"))
        term = _strip_source_prefix(str(it.get("term") or ""), source)
        name = _strip_source_prefix(str(it.get("name") or ""), source)
        title = _strip_source_prefix(str(it.get("title") or it.get("text") or term or name), source)
        pitfall = _strip_source_prefix(str(it.get("pitfall") or ""), source)
        case = _strip_source_prefix(str(it.get("case") or ""), source)
        root_cause = _strip_source_prefix(str(it.get("root_cause") or ""), source)
        fix = _strip_source_prefix(str(it.get("fix") or ""), source)
        raw_body = _strip_source_prefix(str(it.get("body") or it.get("detail") or ""), source)
        intuition = _strip_source_prefix(str(it.get("intuition") or it.get("analogy") or ""), source)
        definition = _strip_source_prefix(str(it.get("definition") or ""), source)
        symbol = _clean_original_term(it.get("symbol") or it.get("formula") or it.get("original_term"))
        if bp == "extension_glossary" or term or definition or intuition:
            if not term:
                term = title
            if not definition:
                definition = raw_body
            body = (
                f"{intuition} — {definition}".strip(" —")
                if (intuition and definition)
                else (definition or intuition or raw_body)
            )
        else:
            body = _BOUNDARY_PREFIX_RE.sub("", raw_body).strip()
        if not body and (pitfall or case or root_cause or fix):
            summary_parts = []
            if pitfall:
                summary_parts.append(f"误区：{pitfall}")
            if case:
                summary_parts.append(f"反例：{case}")
            if root_cause:
                summary_parts.append(f"根因：{root_cause}")
            if fix:
                summary_parts.append(f"纠偏：{fix}")
            body = " · ".join(summary_parts)
        if not title and not body and not (pitfall or case or root_cause or fix):
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
            "source": source,
        }
        if bp == "extension_glossary" or term or definition or intuition:
            entry["term"] = term
            entry["symbol"] = symbol
            entry["intuition"] = intuition
            entry["definition"] = definition
        if name:
            entry["name"] = name
        if pitfall:
            entry["pitfall"] = pitfall
        if case:
            entry["case"] = case
        if root_cause:
            entry["root_cause"] = root_cause
        if fix:
            entry["fix"] = fix
        role_badge = str(it.get("role_badge") or "").strip()
        if role_badge:
            entry["role_badge"] = role_badge
        stance = str(it.get("stance") or "").strip()
        if stance:
            entry["stance"] = stance
        raw_section = str(it.get("section") or it.get("group") or "").strip()[:24]
        section = _norm_checklist_section(raw_section) if bp == "extension_checklist" else raw_section
        if section:
            entry["section"] = section
        if bp == "extension_checklist":
            entry["checked"] = bool(it.get("checked", False))
        items.append(entry)
    return items


def _build_fallback_payload(
    *,
    caption: str,
    summary: str,
    raw_text: str,
    parsed: dict[str, Any] | None,
    valid_ids: set[str],
    default_blocks: list[str],
    logs: list[str],
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    notice = ""
    if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
        data_obj = parsed["data"]
        raw_list = (
            data_obj.get("nodes")
            or data_obj.get("regimes")
            or data_obj.get("points")
            or data_obj.get("rows")
            or data_obj.get("highlights")
            or []
        )
        for entry in raw_list:
            if not isinstance(entry, dict):
                continue
            source = _norm_source(entry.get("source"))
            label = _strip_source_prefix(
                str(
                    entry.get("label")
                    or entry.get("title")
                    or entry.get("state")
                    or entry.get("range")
                    or entry.get("beat")
                    or entry.get("x")
                    or entry.get("key")
                    or ""
                ),
                source,
            )
            if not label:
                continue
            detail = _strip_source_prefix(
                str(
                    entry.get("detail")
                    or entry.get("mechanism")
                    or entry.get("note")
                    or entry.get("emotion")
                    or " · ".join(p for p in (str(entry.get("everyday") or ""), str(entry.get("author") or "")) if p)
                ),
                source,
            )
            blocks = _clean_blocks(
                entry.get("blocks") or entry.get("block"),
                valid_ids=valid_ids,
                default_blocks=default_blocks,
                hint=label[:12],
                logs=logs,
            )
            items.append(
                {
                    "label": label,
                    "detail": detail,
                    "blocks": blocks,
                    "source": source,
                }
            )
        if items:
            notice = "原图节点超限或结构不完整，已安全降级为带原文锚点的结构化清单。"
    if not items:
        snippet = (summary or raw_text or "模型未返回完整图表结构").strip()[:160]
        notice = "JSON 解析或字段校验未通过，已执行防白屏结构化降级。"
        items.append(
            {
                "label": snippet,
                "detail": "点击下方块号可直跳对应原文复核。",
                "blocks": list(default_blocks[:1]),
                "source": "原文",
            }
        )
    notice = "由于原图节点超限或字段不完整，系统已自动降级为保留原段落锚点的结构化清单。"
    logs.append("已触发 fallback_list 安全降级，确保不白屏且保留各条原文块锚点")
    return {
        "blueprint": "fallback_list",
        "caption": caption or "结构化阅读要点（已安全降级）",
        "reading_note": notice,
        "fallback_notice": notice,
        "summary": summary,
        "audit_log": logs,
        "items": items,
    }


def _tailor_reading_note(
    reading_note: str, *, action: str, caption: str, items: list[dict[str, Any]], logs: list[str]
) -> str:
    note = reading_note.strip()
    titles = [
        str(it.get("title") or it.get("text") or "").strip()
        for it in items[:2]
        if str(it.get("title") or it.get("text") or "").strip()
    ]
    if (action == "sparring_dossier" or _SPARRING_PLACEHOLDER_NOTE in note) and (
        not note or _SPARRING_PLACEHOLDER_NOTE in note
    ):
        custom = (
            f"围绕「{' / '.join(titles)}」把握本章论战靶子与思想实验"[:40]
            if titles
            else f"聚焦「{caption}」拆解论战交锋与核心隐喻"[:40]
        )
        if _SPARRING_PLACEHOLDER_NOTE in note:
            logs.append("检测到泛泛占位 reading_note，已按本章交锋对象定制替换")
        return custom
    if action == "checklist" and (not note or any(ph in note for ph in _CHECKLIST_PLACEHOLDER_NOTES)):
        custom = (
            f"围绕「{' / '.join(titles)}」核验启动条件与踩坑红线"[:40]
            if titles
            else f"对照「{caption}」逐项核验落地前置条件与红线"[:40]
        )
        if note:
            logs.append("检测到模板占位 reading_note，已按本章自检重点定制替换")
        return custom
    if action == "failure_postmortem" and (not note or any(ph in note for ph in _POSTMORTEM_PLACEHOLDER_NOTES)):
        custom = (
            f"聚焦「{' / '.join(titles)}」反例，先排查常见误区再行动"[:40]
            if titles
            else f"对照「{caption}」反例定位失效根因与纠偏动作"[:40]
        )
        if note:
            logs.append("检测到模板占位 reading_note，已按本章失效案例定制替换")
        return custom
    if action == "boundary_check" or _BOUNDARY_PLACEHOLDER_NOTE in note:
        stripped = _BOUNDARY_PREFIX_RE.sub("", note).strip()
        if not stripped or stripped == _BOUNDARY_PLACEHOLDER_NOTE:
            custom = (
                f"围绕「{' / '.join(titles)}」把握范式演进与失效边界"[:40]
                if titles
                else f"聚焦「{caption}」厘清适用条件与极端边界"[:40]
            )
            logs.append("已剥离模板前缀「公式/规律失效提示」，定制本章边界导读")
            return custom
        if stripped != note:
            logs.append("已剥离模板前缀「公式/规律失效提示：」")
        return stripped
    return note


def _group_extension_sections(items: list[dict[str, Any]], *, requested_action: str) -> list[dict[str, Any]]:
    if requested_action == "checklist" and not any(str(it.get("section") or "").strip() for it in items):
        return []
    meta = EXTENSION_SECTION_META.get(requested_action)
    if not meta:
        item_secs = {str(it.get("section") or "").strip() for it in items if it.get("section")}
        for act_meta in EXTENSION_SECTION_META.values():
            known = {sid for sid, _ in act_meta}
            if item_secs & known:
                meta = act_meta
                break
    if not meta:
        return []
    if requested_action == "boundary_check" and not any(str(it.get("section") or "").strip() for it in items):
        return []
    title_by_id = dict(meta)
    buckets: dict[str, list[dict[str, Any]]] = {sid: [] for sid, _ in meta}
    fallback_sid = meta[0][0]
    for it in items:
        sid = str(it.get("section") or "").strip()
        if sid not in buckets:
            sid = fallback_sid
        buckets[sid].append(it)
    return [{"id": sid, "title": title_by_id[sid], "items": buckets[sid]} for sid, _ in meta if buckets[sid]]


def audit_blueprint_payload(
    raw_or_parsed: Any,
    valid_block_ids: set[str] | list[str] | tuple[str, ...],
    *,
    fallback_block_ids: list[str] | None = None,
    requested_blueprint: str = "",
    requested_action: str = "",
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
            valid_ids=valid_ids,
            default_blocks=default_blocks,
            logs=logs,
        )

    caption = str(parsed.get("caption") or "").strip()[:36] or "结构图解"
    reading_note = str(parsed.get("reading_note") or "").strip()[:120]
    summary = str(parsed.get("summary") or "").strip()

    bp = str(parsed.get("blueprint") or "").strip()
    legacy_family = str(parsed.get("family") or "").strip()
    if not bp:
        bp = LEGACY_FAMILY_TO_BLUEPRINT.get(legacy_family, requested_blueprint or "flow_swimlane")
        if legacy_family:
            logs.append(f"兼容旧版 family='{legacy_family}'，已映射至蓝图 '{bp}'")

    # Extension dossier / checklist / glossary branch（兼容扁平 items、分区 sections 及 figures/glossary 顶层键）
    has_ext_shape = bp in {"extension_dossier", "extension_checklist", "extension_glossary"} or (
        "data" not in parsed
        and any(isinstance(parsed.get(k), list) for k in ("sections", "items", "figures", "glossary"))
    )
    if has_ext_shape:
        raw_sections = parsed.get("sections")
        if not isinstance(raw_sections, list) and (
            isinstance(parsed.get("figures"), list) or isinstance(parsed.get("glossary"), list)
        ):
            raw_sections = []
            if isinstance(parsed.get("figures"), list):
                raw_sections.append({"id": "figures", "title": "出场人物与阵营速查", "items": parsed["figures"]})
            if isinstance(parsed.get("glossary"), list):
                raw_sections.append({"id": "glossary", "title": "关键制度·条约·名物注", "items": parsed["glossary"]})

        if isinstance(raw_sections, list):
            sections: list[dict[str, Any]] = []
            flat_items: list[dict[str, Any]] = []
            for s_idx, sec in enumerate(raw_sections):
                if not isinstance(sec, dict):
                    continue
                sec_items = _audit_extension_items(
                    sec.get("items") or [],
                    bp=bp,
                    valid_ids=valid_ids,
                    default_blocks=default_blocks,
                    logs=logs,
                )
                if sec_items:
                    flat_items.extend(sec_items)
                    sections.append(
                        {
                            "id": str(sec.get("id") or f"sec-{s_idx + 1}").strip(),
                            "title": str(sec.get("title") or f"分区 {s_idx + 1}").strip(),
                            "items": sec_items,
                        }
                    )
            if not sections:
                return _build_fallback_payload(
                    caption=caption,
                    summary=summary,
                    raw_text=raw_text,
                    parsed=parsed,
                    valid_ids=valid_ids,
                    default_blocks=default_blocks,
                    logs=logs,
                )
            reading_note = _tailor_reading_note(
                reading_note, action=requested_action, caption=caption, items=flat_items, logs=logs
            )
            if not logs:
                logs.append("扩展分区卡片校验通过。")
            return {
                "blueprint": bp or "extension_dossier",
                "caption": caption,
                "reading_note": reading_note,
                "summary": summary,
                "audit_log": logs,
                "sections": sections,
            }

        items = _audit_extension_items(
            parsed.get("items") or [],
            bp=bp,
            valid_ids=valid_ids,
            default_blocks=default_blocks,
            logs=logs,
        )
        if not items:
            return _build_fallback_payload(
                caption=caption,
                summary=summary,
                raw_text=raw_text,
                parsed=parsed,
                valid_ids=valid_ids,
                default_blocks=default_blocks,
                logs=logs,
            )
        reading_note = _tailor_reading_note(
            reading_note, action=requested_action, caption=caption, items=items, logs=logs
        )
        if not logs:
            logs.append("扩展卡片校验通过。")
        ext_res: dict[str, Any] = {
            "blueprint": bp or "extension_dossier",
            "caption": caption,
            "reading_note": reading_note,
            "summary": summary,
            "audit_log": logs,
            "items": items,
        }
        sections = _group_extension_sections(items, requested_action=requested_action)
        if sections:
            ext_res["sections"] = sections
        return ext_res

    raw_data = parsed.get("data")
    if not isinstance(raw_data, dict):
        return _build_fallback_payload(
            caption=caption,
            summary=summary,
            raw_text=raw_text,
            parsed=parsed,
            valid_ids=valid_ids,
            default_blocks=default_blocks,
            logs=logs,
        )

    audited_data: dict[str, Any] | None = None
    if bp == "spatial_route":
        audited_data = _audit_spatial_data(raw_data, valid_ids=valid_ids, default_blocks=default_blocks, logs=logs)
    elif bp in DAG_BLUEPRINTS:
        audited_data = _audit_dag_data(
            raw_data, valid_ids=valid_ids, default_blocks=default_blocks, logs=logs, blueprint=bp
        )
    elif bp in MATRIX_BLUEPRINTS:
        audited_data = _audit_matrix_data(raw_data, valid_ids=valid_ids, default_blocks=default_blocks, logs=logs)
    elif bp == "sensitivity_chart":
        audited_data = _audit_sensitivity_data(raw_data, valid_ids=valid_ids, default_blocks=default_blocks, logs=logs)
    elif bp in CURVE_BLUEPRINTS:
        audited_data = _audit_curve_data(raw_data, valid_ids=valid_ids, default_blocks=default_blocks, logs=logs)
    elif bp == "parallel_timeline":
        audited_data = _audit_timeline_data(raw_data, valid_ids=valid_ids, default_blocks=default_blocks, logs=logs)
    elif bp == "quadrant_matrix":
        audited_data = _audit_quadrant_data(raw_data, valid_ids=valid_ids, default_blocks=default_blocks, logs=logs)
    else:
        # Unknown blueprint name -> try DAG first
        audited_data = _audit_dag_data(
            raw_data, valid_ids=valid_ids, default_blocks=default_blocks, logs=logs, blueprint=bp
        )
        if audited_data is not None:
            bp = "flow_swimlane"

    if audited_data is None:
        return _build_fallback_payload(
            caption=caption,
            summary=summary,
            raw_text=raw_text,
            parsed=parsed,
            valid_ids=valid_ids,
            default_blocks=default_blocks,
            logs=logs,
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
