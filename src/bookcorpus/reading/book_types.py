"""阅读视角与书型工具策略映射：将人账学科标签映射为《如何阅读一本书》6+1 阅读策略。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

DEFAULT_READ_TYPE = "social"


@dataclass(frozen=True)
class BlueprintHint:
    """书型推荐图表蓝图元信息。"""

    blueprint: str
    label: str
    description: str


@dataclass(frozen=True)
class ExtensionHint:
    """书型专属扩展伴读动作元信息。"""

    action: str
    label: str
    hint: str
    cross_search: bool = False


@dataclass(frozen=True)
class ReadProfileMeta:
    """某一类阅读策略的规则、置顶蓝图与扩展动作定义。"""

    read_type: str
    name: str
    adler_category: str
    goal: str
    blueprints: tuple[BlueprintHint, ...]
    extensions: tuple[ExtensionHint, ...]


READ_PROFILES: dict[str, ReadProfileMeta] = {
    "practical": ReadProfileMeta(
        read_type="practical",
        name="实用型书",
        adler_category="1. 实用型书",
        goal="锁定「目的 → 手段 → 成立条件」，并转化为可执行动作",
        blueprints=(
            BlueprintHint("flow_swimlane", "SOP 流程图", "把散落建议串成先后执行步骤"),
            BlueprintHint("decision_tree", "条件决策树", "明确什么场景用哪一招"),
            BlueprintHint("quadrant_matrix", "优先级四象限", "区分高杠杆动作与低效忙碌"),
        ),
        extensions=(ExtensionHint("checklist", "行动自检清单", "抽成可勾选行动清单与失效边界提醒"),),
    ),
    "literature": ReadProfileMeta(
        read_type="literature",
        name="想象文学",
        adler_category="2. 想象文学",
        goal="重沉浸与体验：把握叙事张力、人物阵营、舞台/空间动线、伏笔视角与细读意象",
        blueprints=(
            BlueprintHint("tension_curve", "叙事张力曲线", "提炼 6–9 个关键戏剧节拍与情绪起伏"),
            BlueprintHint("relation_graph", "人物与阵营图谱", "按阵营分群展示核心人物关系、不对称信息与变化"),
            BlueprintHint("spatial_route", "空间与舞台动线", "自适应二维地点动线或封闭舞台调度图"),
        ),
        extensions=(
            ExtensionHint(
                "narrative_clues",
                "叙事视角与伏笔线索",
                "梳理视角切换、时间结构、草蛇灰线与信息差（严禁剧透）",
            ),
            ExtensionHint(
                "imagery",
                "意象、潜台词与细读",
                "分三区提纯核心意象、舞台潜台词与声景名句细读",
            ),
        ),
    ),
    "history": ReadProfileMeta(
        read_type="history",
        name="历史书",
        adler_category="3. 历史书",
        goal="多线并行时空对照、权力阵营拆解、历史因果演进与跨史家视角比对",
        blueprints=(
            BlueprintHint("parallel_timeline", "多线平行时间轴", "同一时期不同行为体、阵营或地区并置对照"),
            BlueprintHint("power_hierarchy", "权力与阵营结构", "拆解中枢、派系、盟友、敌对与制度约束"),
            BlueprintHint("spatial_route", "地理与空间态势", "展示真实地理方位、行军动线与地缘格局"),
            BlueprintHint("historical_causality", "历史因果演进图", "拆解深层结构、触发事件、催化机制与转折"),
        ),
        extensions=(
            ExtensionHint(
                "historical_dossier",
                "人物与名物档案",
                "速查本章核心人物阵营与官制、条约、名物术语",
                cross_search=False,
            ),
            ExtensionHint(
                "compare_context",
                "多史家视角对照",
                "三段式对照史观归因、史料盲区与同代世界史锚点",
                cross_search=True,
            ),
        ),
    ),
    "science": ReadProfileMeta(
        read_type="science",
        name="科学与数学",
        adler_category="4. 科学与数学",
        goal="理清核心问题、公理/实验前提、推导依赖与成立边界",
        blueprints=(
            BlueprintHint("derivation_dag", "公理-定理推导图", "看清结论如何从前提假设长出"),
            BlueprintHint("sensitivity_chart", "变量敏感度曲线", "直观展示参数变化带来的效应偏误"),
            BlueprintHint("system_flow", "机制与装置流图", "空间化拆解物理/统计或实验过程"),
        ),
        extensions=(ExtensionHint("boundary_check", "演进与适用边界", "交代推翻了什么旧认知、生活类比与极端失效条件"),),
    ),
    "philosophy": ReadProfileMeta(
        read_type="philosophy",
        name="哲学书",
        adler_category="5. 哲学书",
        goal="抠紧作者专属词义边界，剥离前提 → 推论 → 结论、批判谱系、论战交锋与反例边界",
        blueprints=(
            BlueprintHint("argument_map", "论证逻辑树", "剥离核心论点、并行前提、推论与潜在反驳"),
            BlueprintHint("term_contrast", "词义辨析双栏表", "对比日常汉语直觉与作者专属定义（附原词）"),
            BlueprintHint("lineage_dag", "批判谱系图", "呈现思想家、学派之间的继承、改造与批判关系"),
        ),
        extensions=(
            ExtensionHint("sparring_dossier", "学术交锋档案", "拆解核心论战交锋并剖析经典思想实验与隐喻"),
            ExtensionHint("counterexample_lab", "反例与现实检验", "揪出隐含前提、构造极端反例并检验当代适用边界"),
        ),
    ),
    "social": ReadProfileMeta(
        read_type="social",
        name="社会科学",
        adler_category="6. 社会科学",
        goal="以议题为中心：厘清混杂术语、多变量因果反馈与跨作者对照",
        blueprints=(
            BlueprintHint("causal_loop", "因果反馈回路图", "标出 + / - 反馈与滞后锁定效应"),
            BlueprintHint("concept_matrix", "跨学派概念矩阵", "梳理不同学者对同一术语的界定差异"),
            BlueprintHint("metric_chart", "实证趋势图", "还原支撑观点的长周期或对比数据"),
        ),
        extensions=(
            ExtensionHint(
                "method_audit",
                "方法与样本局限",
                "标出定性/定量依据与抽样偏差，并联动书库跨书综述",
                cross_search=True,
            ),
        ),
    ),
    "visual": ReadProfileMeta(
        read_type="visual",
        name="图册与视觉",
        adler_category="补充类（图册与设定集）",
        goal="建立构图元素、文物/工艺原型与时代流派索引",
        blueprints=(BlueprintHint("element_matrix", "构图元素索引表", "整理视觉维度、原型出处与美术气氛"),),
        extensions=(ExtensionHint("craft_notes", "工艺流派考据", "补足图注背后缺少的年代、技法与源流背景"),),
    ),
}

LITERATURE_SUBKIND_HINTS: dict[str, str] = {
    "fiction": "小说：宜把握情节连贯与人物命运演变，先沉浸再结构复盘。",
    "drama": "戏剧：把文本还原为舞台，留意人物动作、停顿神态与潜台词。",
    "poetry": "抒情诗：先连读捕获全篇基调，再重音体会高频意象与节奏冲突。",
    "essay": "散文随笔（边界体裁）：默认按文学意象与抒情结构伴读，亦可一键切为「历史书」或「哲学书」。",
}

_PRACTICAL_SUBJECT1 = {"商业与管理", "生活与实用", "写作与编剧"}
_PRACTICAL_TECH_SUB2 = {"计算机与技术", "工程与制造"}
_PRACTICAL_GAME_SUB2 = {"游戏设计", "开发与工程", "攻略与资料"}

_LIT_FICTION_SUB2 = {"严肃文学小说", "类型小说", "网络文学", "儿童与青少年文学"}

_HISTORY_TECH_ART_SUB2 = {"美术史", "佛教美术"}
_HISTORY_SOCIAL_SUB2 = {"历史", "人类学与考古"}

_SCIENCE_SUB2 = {"数学与统计", "物理与天文", "生命与医学", "科普", "不细分", ""}

_PHILOSOPHY_SUB2 = {"思想与哲学", "宗教与神话"}

_SOCIAL_SUB2 = {"社会与经济", "政治与法律", "心理与认知", "语言与文字", "不细分", ""}

_VISUAL_ART_SUB2 = {"绘画与概念设计", "摄影", "设计", "书法篆刻"}


def _clean_pair(subjects: list[str] | tuple[str, ...] | None) -> tuple[str, str]:
    items = [str(item or "").strip() for item in (subjects or []) if str(item or "").strip()]
    sub1 = items[0] if items else ""
    sub2 = items[1] if len(items) > 1 else ""
    return sub1, sub2


def _infer_pair(subjects: list[str] | tuple[str, ...] | None) -> tuple[str, str, str]:
    sub1, sub2 = _clean_pair(subjects)

    if sub1 in _PRACTICAL_SUBJECT1:
        return "practical", "", ""
    if sub1 == "科学技术":
        if sub2 in _PRACTICAL_TECH_SUB2:
            return "practical", "", ""
        if sub2 in _SCIENCE_SUB2:
            return "science", "", ""
        return "science", "", ""
    if sub1 == "游戏":
        if sub2 == "设定集与美术集":
            return "visual", "", ""
        if sub2 in _PRACTICAL_GAME_SUB2:
            return "practical", "", ""
        return "social", "", ""
    if sub1 == "文学":
        if sub2 == "戏剧":
            return "literature", "drama", ""
        if sub2 == "诗歌":
            return "literature", "poetry", ""
        if sub2 == "散文随笔":
            return (
                "literature",
                "essay",
                "散文随笔常兼有纪实或哲思气质；当前默认「想象文学」，可在上方切换「历史书」或「哲学书」。",
            )
        if sub2 in _LIT_FICTION_SUB2 or not sub2 or sub2 == "不细分":
            return "literature", "fiction", ""
        return "literature", "fiction", ""
    if sub1 == "建筑与古迹":
        return "history", "", ""
    if sub1 == "艺术与图像":
        if sub2 in _HISTORY_TECH_ART_SUB2:
            return "history", "", ""
        if sub2 in _VISUAL_ART_SUB2 or not sub2 or sub2 == "不细分":
            return "visual", "", ""
        return "visual", "", ""
    if sub1 == "影视动画漫画":
        if sub2 == "电影与影史":
            return "history", "", ""
        return "visual", "", ""
    if sub1 == "人文社科":
        if sub2 in _HISTORY_SOCIAL_SUB2:
            return "history", "", ""
        if sub2 in _PHILOSOPHY_SUB2:
            return "philosophy", "", ""
        if sub2 in _SOCIAL_SUB2:
            return "social", "", ""
        return "social", "", ""
    if sub1 == "其他":
        if sub2 == "图册与写真":
            return "visual", "", ""
        return (
            DEFAULT_READ_TYPE,
            "",
            "当前学科未细分或属工具/待归类，暂按「社会科学」通用论证视图展示，可按需手动切换。",
        )
    if sub1 == "拿不准" or not sub1:
        return (
            DEFAULT_READ_TYPE,
            "",
            "该书尚未确定学科分类，默认提供「社会科学」通用阅读视角，可在上方一键切换。",
        )
    return DEFAULT_READ_TYPE, "", ""


def get_profile_spec(read_type: str, sub_kind: str = "") -> dict[str, Any]:
    """返回指定 read_type 的完整前端元数据。"""
    meta = READ_PROFILES.get(read_type) or READ_PROFILES[DEFAULT_READ_TYPE]
    return {
        "read_type": meta.read_type,
        "name": meta.name,
        "adler_category": meta.adler_category,
        "goal": meta.goal,
        "sub_kind": sub_kind,
        "sub_kind_hint": LITERATURE_SUBKIND_HINTS.get(sub_kind, "") if meta.read_type == "literature" else "",
        "blueprints": [asdict(bp) for bp in meta.blueprints],
        "extensions": [asdict(ext) for ext in meta.extensions],
    }


def list_read_profiles() -> list[dict[str, Any]]:
    """列出全部 6+1 阅读策略元数据，供前端切换下拉框消费。"""
    return [get_profile_spec(key) for key in READ_PROFILES]


def resolve_read_profile(subjects: list[str] | tuple[str, ...] | None, *, title: str = "") -> dict[str, Any]:
    """根据 book.json 的 subjects（[一级, 二级]）确定性推导阅读策略配置。"""
    sub1, sub2 = _clean_pair(subjects)
    read_type, sub_kind, boundary_hint = _infer_pair(subjects)
    spec = get_profile_spec(read_type, sub_kind=sub_kind)
    spec["ledger_subject"] = f"{sub1} / {sub2}" if sub2 else (sub1 or "未分类")
    spec["boundary_hint"] = boundary_hint
    spec["book_title"] = title
    spec["available_profiles"] = [
        {"read_type": meta.read_type, "name": meta.name, "adler_category": meta.adler_category}
        for meta in READ_PROFILES.values()
    ]
    return spec
