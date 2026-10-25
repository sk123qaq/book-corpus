#!/usr/bin/env python3
"""书库共享工具。所有脚本从这里取路径、书名清洗、作者猜测、学科预填与 CSV 读写。"""
import csv
import hashlib
import json
import os
import re
import unicodedata

ROOT = "/home/shike/文档/books"
LIB = os.path.join(ROOT, "库")
CAT = os.path.join(ROOT, "目录")
WORK = os.path.join(ROOT, "工作区")
SCRIPTS = os.path.join(WORK, "脚本")
BASELINE = os.path.join(WORK, "中间态", "基线")

EXT = {".epub", ".pdf", ".mobi", ".azw3", ".azw", ".txt", ".djvu"}
CONTENT_POOLS = ["原书", "原书pdf", "建筑", "漫画与动画", "人文社科", "文学",
                 "写作与编剧", "艺术", "游戏设定", "游戏设计"]
AWARD_DIR = "奖项书"

# 来源目录 -> (一级, 二级)
DIR_SUBJECT = {
    "建筑": ("建筑与古迹", ""),
    "艺术": ("艺术与图像", ""),
    "游戏设定": ("游戏", "设定集与美术集"),
    "游戏设计": ("游戏", "游戏设计"),
    "漫画与动画": ("影视动画漫画", ""),
    "人文社科": ("人文社科", ""),
    "文学": ("文学", ""),
    "写作与编剧": ("写作与编剧", ""),
}
# 奖项 -> (一级, 二级)
AWARD_SUBJECT = {
    "国际安徒生奖": ("文学", "儿童与青少年文学"),
}
AWARD_SUBJECT_DEFAULT = ("文学", "严肃文学小说")

# 系列与典型书的显式归类，优先于关键词。键是清洗后书名或作者里的子串
SERIES_SUBJECT = [
    (("阿加莎·克里斯蒂",), ("文学", "类型小说")),
    (("东野圭吾",), ("文学", "类型小说")),
    (("盗墓笔记",), ("文学", "网络文学")),
    (("鬼吹灯",), ("文学", "网络文学")),
    (("冰与火之歌",), ("文学", "类型小说")),
    (("猎魔人",), ("文学", "类型小说")),
    (("三体",), ("文学", "类型小说")),
    (("克苏鲁神话",), ("文学", "类型小说")),
    (("银河帝国",), ("文学", "类型小说")),
    (("黄易",), ("文学", "类型小说")),
    (("余华",), ("文学", "严重文学小说")),
    (("王小波",), ("文学", "严重文学小说")),
    (("莫言",), ("文学", "严重文学小说")),
    (("渡边淳一",), ("文学", "严重文学小说")),
    (("米兰·昆德拉",), ("文学", "严重文学小说")),
    (("到灯塔去", "伍尔芙"), ("文学", "严重文学小说")),
    (("老人与海", "海明威"), ("文学", "严重文学小说")),
    (("钢琴教师", "耶利内克"), ("文学", "严重文学小说")),
    (("奥尔加", "托卡尔丘克"), ("文学", "严重文学小说")),
    (("汉德克",), ("文学", "严重文学小说")),
    (("契弗",), ("文学", "严重文学小说")),
    (("阿来", "山珍三部"), ("文学", "严重文学小说")),
    (("三毛",), ("文学", "散文随笔")),
    (("施蟄存", "北山散文"), ("文学", "散文随笔")),
    (("辛波斯卡",), ("文学", "诗歌")),
    (("摩诃婆罗多",), ("文学", "诗歌")),
    (("尼尔斯骑鹅",), ("文学", "儿童与青少年文学")),
    (("明朝那些事儿",), ("人文社科", "历史")),
    (("毛泽东选集",), ("人文社科", "政治与法律")),
    (("理想国译丛",), ("人文社科", "思想与哲学")),
    (("乌合之众",), ("人文社科", "社会与经济")),
    (("乡土中国",), ("人文社科", "社会与经济")),
    (("宗教与神话", "神话史"), ("人文社科", "宗教与神话")),
    (("创造的进化论",), ("人文社科", "思想与哲学")),
    (("金字塔原理",), ("商业与管理", "管理与组织")),
    (("吉姆·柯林斯",), ("商业与管理", "管理与组织")),
    (("韦尔奇",), ("商业与管理", "管理与组织")),
    (("竞争战略", "竞争优势", "国家竞争优势"), ("商业与管理", "战略")),
]
# 关键词预填，按顺序匹配，先命中先用
KEYWORD_SUBJECT = [
    (("游戏设计", "游戏数值", "游戏引擎", "互动叙事", "关卡", "游戏机制", "通关"), ("游戏", "游戏设计")),
    (("游戏设定", "设定集", "原画", "概念艺术", "攻略", "画集"), ("游戏", "设定集与美术集")),
    (("电影", "动画", "漫画", "分镜", "特典"), ("影视动画漫画", "")),
    (("编剧", "对白", "写作", "创作法", "故事"), ("写作与编剧", "")),
    (("建筑", "古建", "寺", "祠", "庙", "石窟", "长城", "古城", "园林", "文物保护"),
     ("建筑与古迹", "")),
    (("美术", "艺术", "画册", "摄影", "写真", "雕塑", "佛像", "菩萨", "壁画", "书法"),
     ("艺术与图像", "")),
    (("史", "政治", "经济", "哲学", "宗教", "社会", "考古", "人类学", "文化", "神话", "制度", "战争"),
     ("人文社科", "")),
    (("科学", "数学", "物理", "生物", "认知", "算法", "计算", "技术", "工程", "心理", "思维", "博弈"),
     ("科学技术", "")),
    (("竞争", "管理", "投资", "股东", "公司", "创新", "战略", "营销", "品牌", "商业", "金融", "财务"),
     ("商业与管理", "")),
]
FALLBACK_SUBJECT = ("其他", "待归类")

ZLIB = re.compile(r"\s*\(z-library\.sk,\s*1lib\.sk,\s*z-lib\.sk\)$", re.I)
TRUNC = re.compile(r"\s*\(\d+\)$")
STRONG_SEP = re.compile(r"[=＝]|——")
COUNTRY_LIKE = {
    "中国", "美国", "英国", "法国", "德国", "日本", "韩国", "俄罗斯", "俄国", "苏联",
    "意大利", "西班牙", "葡萄牙", "奥地利", "瑞士", "荷兰", "比利时", "丹麦", "挪威",
    "芬兰", "瑞典", "波兰", "捷克", "匈牙利", "希腊", "印度", "埃及", "以色列",
    "加拿大", "澳大利亚", "巴西", "阿根廷", "墨西哥", "智利", "秘鲁", "土耳其", "伊朗",
    "黎巴嫩", "尼日利亚", "南非", "爱尔兰", "冰岛", "哥伦比亚", "古巴", "新西兰",
    "越南", "泰国", "新加坡", "古希腊", "古罗马", "古代", "中世纪",
}
AUTHOR_LABEL = re.compile(r"^(?:作者|著者|编者|译者|主编|著)\s*[:：]\s*")
AUTHOR_BRACKET = re.compile(r"^[\[（(【][^\]）)】]{1,10}[\]）)】]\s*")
AUTHOR_JUNK = {"etc", "etc.", "unknown", "pdgpic", "pdg2pic", "套装共册", "套装共卷",
               "著", "作者", "佚名", "无", "none", "null", "-", "—"}


def clean_author(a):
    """作者清洗：去「作者：」标签、去国家括注、挡掉出版社/扫描仪等非人名兜底值。"""
    a = AUTHOR_LABEL.sub("", (a or "").strip()).strip()
    a = AUTHOR_BRACKET.sub("", a).strip()
    if not a or a.lower() in AUTHOR_JUNK or a in COUNTRY_LIKE:
        return ""
    if re.fullmatch(r"[\d\s\-—_·.、,，]+", a):
        return ""
    return a if len(a) > 1 else ""


def rel(path):
    return os.path.relpath(path, ROOT)


def norm(s):
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", s or "").lower(), flags=re.U)


def clean_title(filename):
    """文件名到目录名候选。去 z-library 后缀、去作者括注、去版本词尾巴、去省略号。"""
    base = os.path.splitext(os.path.basename(filename))[0]
    base = TRUNC.sub("", base)
    base = ZLIB.sub("", base).strip()
    base = re.sub(r"\s*\([^()]*\)$", "", base).strip()
    for o, c in (("(", ")"), ("（", "）"), ("[", "]"), ("【", "】")):
        while base.count(o) > base.count(c):
            i = base.rfind(o)
            if i <= 0:
                break
            base = base[:i].rstrip()
    if STRONG_SEP.search(base):
        base = STRONG_SEP.split(base)[0]
    base = base.strip(" -–—_·,，。")
    base = re.sub(r"[\\/:*?\"<>|]", "／", base)
    if len(base) > 60:
        base = base[:60].rstrip()
    return base or "未命名"


def author_guess(filename, title_clean):
    """从文件名猜作者。取第一个括注里可用的人名，其次取「作者-书名」前缀。"""
    stem = os.path.splitext(os.path.basename(filename))[0]
    stem = ZLIB.sub("", TRUNC.sub("", stem))
    m = re.search(r"[（(]([^（）()]+)[）)]", stem)
    if m:
        inner = m.group(1)
        for sep in ("、", "，", ",", "；", ";"):
            inner = inner.split(sep)[0]
        inner = re.sub(r"[\d\[\]【】]", "", inner)
        inner = re.sub(r"(著|譯|译|编|編|主编|作者|等)$", "", inner).strip()
        if 2 <= len(inner) <= 20 and not re.search(r"(出版社|出版|公司|文庫|文库)", inner):
            cand = clean_author(inner)
            if cand:
                return cand
    if "-" in title_clean:
        head = title_clean.split("-")[0].strip()
        if 2 <= len(head) <= 12:
            return head
    return ""


def prefill_subject(pool, filename, award=None, title="", author=""):
    """(一级, 二级, 依据)。顺序是奖项 > 来源目录 > 系列表 > 标题与作者关键词。"""
    if award:
        s1, s2 = AWARD_SUBJECT.get(award, AWARD_SUBJECT_DEFAULT)
        return s1, s2, "奖项"
    if pool in DIR_SUBJECT:
        s1, s2 = DIR_SUBJECT[pool]
        return s1, s2, "来源目录"
    short = (title or filename) + " " + (author or "")
    for keys, (s1, s2) in SERIES_SUBJECT:
        for k in keys:
            if k in short:
                return s1, s2, "系列:" + k
    low = (title or filename).lower()
    for keys, (s1, s2) in KEYWORD_SUBJECT:
        for k in keys:
            if k.lower() in low:
                return s1, s2, "关键词:" + k
    return FALLBACK_SUBJECT[0], FALLBACK_SUBJECT[1], "未匹配"


def iter_source_files():
    """书级源文件，相对路径。池子的顶层文件，加奖项书的奖项子目录。"""
    out = []
    for pool in CONTENT_POOLS:
        d = os.path.join(ROOT, pool)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            fp = os.path.join(d, fn)
            if os.path.isfile(fp) and os.path.splitext(fn)[1].lower() in EXT:
                out.append((pool, None, rel(fp)))
    aw = os.path.join(ROOT, AWARD_DIR)
    if os.path.isdir(aw):
        for award in sorted(os.listdir(aw)):
            d = os.path.join(aw, award)
            if not os.path.isdir(d):
                continue
            for fn in sorted(os.listdir(d)):
                fp = os.path.join(d, fn)
                if os.path.isfile(fp) and os.path.splitext(fn)[1].lower() in EXT:
                    out.append((AWARD_DIR + "/" + award, award, rel(fp)))
    return out


def sha256_file(path, chunk=1 << 22):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def check_file(path, fmt):
    """返回 (是否可疑, 原因)。epub 走 zip 完整性，pdf 看头尾，其余只看非空。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return True, "无法读取:" + str(e)
    if size == 0:
        return True, "空文件"
    if fmt == "epub":
        import zipfile
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                if "META-INF/container.xml" not in names:
                    return True, "缺少 META-INF/container.xml"
                bad = z.testzip()
                if bad:
                    return True, "zip 校验失败:" + bad
        except zipfile.BadZipFile as e:
            return True, "不是完整 zip:" + str(e)
        except Exception as e:  # noqa: BLE001
            return True, "读取异常:" + type(e).__name__
        return False, ""
    if fmt == "pdf":
        with open(path, "rb") as fh:
            head = fh.read(8)
            fh.seek(max(0, size - 2048))
            tail = fh.read()
        if not head.startswith(b"%PDF"):
            return True, "缺少 %PDF 头"
        if b"%%EOF" not in tail:
            return True, "结尾 2KB 内没有 %%EOF"
        return False, ""
    return False, ""


def read_csv(path):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path, rows, header):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})
    os.replace(tmp, path)


LEDGER_HEADER = ["uid", "一级", "二级", "作者层", "保留", "正式书名", "备注"]
INVENTORY_HEADER = ["uid", "pool", "award", "src_path", "file", "format", "bytes", "sha256",
                    "title_clean", "author_guess", "md_dir", "md_chapters", "md_bytes",
                    "html_book", "collection", "一级", "二级", "正式书名", "备注"]


def path_safe(s):
    return re.sub(r"[\\/:*?\"<>|\n\t]", "／", (s or "").strip()).strip(" .")


def book_relpath(led, inv):
    """书在 库/ 下的相对路径。人账给学科与作者层，机器清单给作者名、容器与正式书名。"""
    parts = [led["一级"], led["二级"]]
    if led.get("作者层") == "1" and (inv or {}).get("作者"):
        parts.append(path_safe(inv["作者"]))
    if (inv or {}).get("容器"):
        parts.append(path_safe(inv["容器"]))
    parts.append(path_safe(led["正式书名"]))
    return os.path.join(*parts)


def iter_book_json():
    """库/**/book.json -> [(库下相对目录, doc)]。_ 开头的目录不算书（_收件箱、_冷宫）。"""
    out = []
    if not os.path.isdir(LIB):
        return out
    for dirpath, _dirnames, filenames in os.walk(LIB):
        if "book.json" not in filenames:
            continue
        rel = os.path.relpath(dirpath, LIB)
        if rel.split(os.sep)[0].startswith("_"):
            continue
        try:
            with open(os.path.join(dirpath, "book.json"), encoding="utf-8") as fh:
                out.append((rel, json.load(fh)))
        except (OSError, json.JSONDecodeError) as e:
            raise SystemExit("book.json 读不了: %s (%s)" % (os.path.join(dirpath, "book.json"), e))
    out.sort(key=lambda x: x[0])
    return out


def book_doc(uid, title, title_from_file, author, subjects, path, series, volume, container,
             retention, notes, sources, awards, derived):
    """book.json 的唯一形状。build_library 与 migrate_layout 都从这里取。"""
    return {
        "uid": uid, "title": title, "title_from_file": title_from_file,
        "author": author or "", "subjects": [x for x in (subjects or []) if x], "path": path,
        "series": series or None, "volume": volume or None, "container": container or None,
        "retention": retention or "keep",
        "sources": sources, "awards": awards, "derived": derived, "notes": notes or "",
    }


def write_json_atomic(path, doc):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def count_md_dir(md_dir):
    """(章节数, md 字节数)。README.md 不计章节，但计入字节，与基线口径一致。"""
    chapters = bytes_ = 0
    for fn in sorted(os.listdir(md_dir)):
        fp = os.path.join(md_dir, fn)
        if os.path.isfile(fp) and fn.endswith(".md"):
            bytes_ += os.path.getsize(fp)
            if fn != "README.md":
                chapters += 1
    return chapters, bytes_


def read_md_manifest(md_dir):
    p = os.path.join(md_dir, ".manifest.json")
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh) or {}
        except (OSError, json.JSONDecodeError):
            return {}
    return {}
