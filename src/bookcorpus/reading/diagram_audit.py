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
_SOURCE_PREFIX_RE = re.compile(r"^(?:【(?:原文|补充)】\s*|\[(?:原文|补充)\]\s*|(?:原文|补充)\s*[:：]\s*)+")
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
CURVE_BLUEPRINTS = frozenset({"tension_curve", "sensitivity_chart", "metric_chart"})

DEFAULT_NODE_ROLE: dict[str, str] = {
    "power_hierarchy": "faction",
    "historical_causality": "mechanism",
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
SPATIAL_EDGE_KINDS = frozenset(
    {"advance", "flank", "defense", "diplomacy", "move", "gaze", "barrier", "parallel"}
)

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
    node_cap_hint = f"（图节点上限 {MAX_GRAPH_NODES} 个，建议 6–12 个）" if chosen in DAG_BLUEPRINTS else ""
    system = (
        f"你是阅读伴读助手，擅长把复杂文字转成清晰的关系图与结构化蓝图{type_hint}。\n"
        f"{scope_hint}\n"
        f"{guidance_block}"
        "严格要求：\n"
        "1. 只输出一个合法 JSON 对象，不要 markdown 代码围栏，不要多余解释。\n"
        f"{coord_rule}"
        f"3. 每个节点主标题严格控制在 16 字以内，详细解释放到 detail；边标签控制在 {edge_limit_hint}{node_cap_hint}。\n"
        '4. 每个节点或要点都必须填写来源于本段实际编号的 blocks 数组（例如 ["B02"]），'
        '并注明 source（"原文" 或 "补充"）；source 为 "补充" 时正文开头勿写"补充："。\n'
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
    scope_hint = (
        f"作用范围：整章全局（涵盖全章 {covered_blocks or '全部'} 个段落"
        + ("，已按连续场景窗口覆盖" if sampled else "")
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
    system = (
        f"你是阅读伴读专家，负责生成「{spec['title']}」结构卡片{sub_hint}。\n"
        f"{scope_hint}\n"
        f"{spoiler_rule}"
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


def _audit_dag_data(
    raw_data: dict[str, Any],
    *,
    valid_ids: set[str],
    default_blocks: list[str],
    logs: list[str],
    blueprint: str = "",
) -> dict[str, Any] | None:
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
        node_ids.add(nid)
        role = str(item.get("role") or fallback_role).strip()
        if blueprint == "power_hierarchy" and role not in POWER_ROLES:
            role = fallback_role
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
        edges.append(entry)

    return {"nodes": nodes, "edges": edges}


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
            everyday = _strip_source_prefix(str(row.get("everyday") or row.get("col1") or ""), source)
            author = _strip_source_prefix(str(row.get("author") or row.get("col2") or ""), source)
            extra = _strip_source_prefix(str(row.get("extra") or row.get("col3") or ""), source)
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
                    "source": source,
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
        quads.append({"pos": pos, "title": str(q.get("title") or pos), "items": items})
    return {
        "xAxis": str(raw_data.get("xAxis") or "横轴（低 → 高）"),
        "yAxis": str(raw_data.get("yAxis") or "纵轴（低 → 高）"),
        "quadrants": quads,
    }


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
        name = _strip_source_prefix(str(it.get("name") or ""), source)
        title = _strip_source_prefix(str(it.get("title") or it.get("text") or name), source)
        body = _strip_source_prefix(str(it.get("body") or it.get("detail") or ""), source)
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
            "source": source,
        }
        if name:
            entry["name"] = name
        role_badge = str(it.get("role_badge") or "").strip()
        if role_badge:
            entry["role_badge"] = role_badge
        stance = str(it.get("stance") or "").strip()
        if stance:
            entry["stance"] = stance
        section = str(it.get("section") or it.get("group") or "").strip()[:24]
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
        raw_list = data_obj.get("nodes") or data_obj.get("points") or data_obj.get("rows") or []
        for entry in raw_list:
            if not isinstance(entry, dict):
                continue
            source = _norm_source(entry.get("source"))
            label = _strip_source_prefix(
                str(entry.get("label") or entry.get("title") or entry.get("beat") or entry.get("key") or ""),
                source,
            )
            if not label:
                continue
            detail = _strip_source_prefix(
                str(entry.get("detail") or entry.get("note") or entry.get("emotion") or ""),
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
    logs.append("已触发 fallback_list 安全降级，确保不白屏且保留原文块锚点")
    return {
        "blueprint": "fallback_list",
        "caption": caption or "结构化阅读要点（已安全降级）",
        "reading_note": notice,
        "fallback_notice": notice,
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

    # Extension dossier / checklist branch（兼容扁平 items、分区 sections 及 figures/glossary 顶层键）
    has_ext_shape = bp in {"extension_dossier", "extension_checklist"} or (
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
