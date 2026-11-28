#!/usr/bin/env python3
"""书库共享工具。所有脚本从这里取路径、书名清洗、作者猜测、学科预填与 CSV 读写。"""

import contextlib
import csv
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import unicodedata

try:
    from .layout import (
        RESERVED,
        SCHEMA,
        Layout,
        find_root,
    )
except ImportError:
    # mypy 不认 try/except 两路导入，把 fallback 当成重复定义。
    from layout import (  # type: ignore[no-redef]
        RESERVED,  # noqa: F401 对外再导出，本模块自身不用
        SCHEMA,  # noqa: F401 对外再导出，本模块自身不用
        Layout,
        find_root,
    )

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = HERE


ROOT = find_root() or os.path.abspath(os.getcwd())
PATHS = Layout(ROOT)
BOOKS = PATHS.books
COLLECTIONS = PATHS.collections
TABLES = PATHS.tables
BUILD = PATHS.build
WORK = PATHS.work
SKILLS = os.environ.get("BOOKS_SKILLS_DIR") or os.path.join(ROOT, ".agents", "skills")
# 工作区通常不带 .agents/skills（init 不铺），源码仓根下才有。装成包后这条
# 路径不存在，skill_dir 仍会回落到用户目录。
REPO_SKILLS = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".agents", "skills"))
RG = os.environ.get("BOOKS_RG") or shutil.which("rg") or "rg"


def store_path(*parts):
    """落进 db 与索引的根相对路径。一律正斜杠，v2 下以 books/ 或 collections/ 开头。"""
    return "/".join(p.strip("/") for p in parts if p)


def md_rel(uid):
    return "books/%s/md" % uid


def source_rel(owner, name):
    """源文件的根相对路径。owner 是书的 uid 或合集的 cuid。"""
    return store_path("collections" if owner.startswith("C") else "books", owner, "source", name)


def skill_dir(name):
    """技能目录：工作区 .agents/skills、源码仓 .agents/skills、用户目录，按序找。"""
    local = os.path.join(SKILLS, name)
    roots = [SKILLS, REPO_SKILLS, os.path.expanduser(os.path.join("~", ".agents", "skills"))]
    for root in dict.fromkeys(roots):
        path = os.path.join(root, name)
        if os.path.isdir(path):
            return path
    return local


# 收件箱 / 上传 / 导入共用的后缀白名单：单一来源，别处不要再抄一份
ACCEPT_EXTS = frozenset({".epub", ".pdf", ".mobi", ".azw3", ".azw", ".txt", ".md", ".docx", ".xlsx", ".xls", ".pptx"})
# 明确不接受：老 Office 二进制、表格、DjVu（转不出可用 md）
REJECT_EXTS = frozenset({".doc", ".ppt", ".csv", ".djvu"})
# 能直接/经 markitdown 转成 md 的源格式
DOC_FORMATS = frozenset({"txt", "md", "docx", "xlsx", "xls", "pptx"})
# 需走 split_md_batch 拆章转 md 的电子书格式
EBOOK_FORMATS = frozenset({"epub", "mobi", "azw3", "azw"})
EXT = set(ACCEPT_EXTS)
CONTENT_POOLS = [
    "原书",
    "原书pdf",
    "建筑",
    "漫画与动画",
    "人文社科",
    "文学",
    "写作与编剧",
    "艺术",
    "游戏设定",
    "游戏设计",
]
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
    (("余华",), ("文学", "严肃文学小说")),
    (("王小波",), ("文学", "严肃文学小说")),
    (("莫言",), ("文学", "严肃文学小说")),
    (("渡边淳一",), ("文学", "严肃文学小说")),
    (("米兰·昆德拉",), ("文学", "严肃文学小说")),
    (("到灯塔去", "伍尔芙"), ("文学", "严肃文学小说")),
    (("老人与海", "海明威"), ("文学", "严肃文学小说")),
    (("钢琴教师", "耶利内克"), ("文学", "严肃文学小说")),
    (("奥尔加", "托卡尔丘克"), ("文学", "严肃文学小说")),
    (("汉德克",), ("文学", "严肃文学小说")),
    (("契弗",), ("文学", "严肃文学小说")),
    (("阿来", "山珍三部"), ("文学", "严肃文学小说")),
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
    (("建筑", "古建", "寺", "祠", "庙", "石窟", "长城", "古城", "园林", "文物保护"), ("建筑与古迹", "")),
    (("美术", "艺术", "画册", "摄影", "写真", "雕塑", "佛像", "菩萨", "壁画", "书法"), ("艺术与图像", "")),
    (
        ("史", "政治", "经济", "哲学", "宗教", "社会", "考古", "人类学", "文化", "神话", "制度", "战争"),
        ("人文社科", ""),
    ),
    (
        ("科学", "数学", "物理", "生物", "认知", "算法", "计算", "技术", "工程", "心理", "思维", "博弈"),
        ("科学技术", ""),
    ),
    (
        ("竞争", "管理", "投资", "股东", "公司", "创新", "战略", "营销", "品牌", "商业", "金融", "财务"),
        ("商业与管理", ""),
    ),
]
FALLBACK_SUBJECT = ("其他", "待归类")

# 下载站 / 镜像站域名与来源标签（单一规则源：供文件名噪声剥离与作者清理共用）
DOWNLOAD_SITE_TOKEN_RE = re.compile(
    r"(?<![a-z0-9-])(?:www\.)?(?:"
    r"z-?library(?:\.[a-z0-9-]{2,15})*"
    r"|z-lib(?:\.[a-z0-9-]{2,15})*"
    r"|zlib(?:\.[a-z0-9-]{2,15})+"
    r"|1lib(?:\.[a-z0-9-]{2,15})+"
    r"|singlelogin(?:\.[a-z0-9-]{2,15})+"
    r"|b-ok(?:\.[a-z0-9-]{2,15})+"
    r"|book(?:4you|sc|fi)(?:\.[a-z0-9-]{2,15})+"
    r"|libgen(?:\.[a-z0-9-]{2,15})+"
    r"|annas-archive(?:\.[a-z0-9-]{2,15})+"
    r")(?![a-z0-9-])",
    re.I,
)
ZLIB = re.compile(r"\s*\(z-library\.sk,\s*1lib\.sk,\s*z-lib\.sk\)$", re.I)
TRUNC = re.compile(r"\s*\(\d+\)$")
STRONG_SEP = re.compile(r"[=＝]|——")
COUNTRY_LIKE = {
    "中国",
    "美国",
    "英国",
    "法国",
    "德国",
    "日本",
    "韩国",
    "俄罗斯",
    "俄国",
    "苏联",
    "意大利",
    "西班牙",
    "葡萄牙",
    "奥地利",
    "瑞士",
    "荷兰",
    "比利时",
    "丹麦",
    "挪威",
    "芬兰",
    "瑞典",
    "波兰",
    "捷克",
    "匈牙利",
    "希腊",
    "印度",
    "埃及",
    "以色列",
    "加拿大",
    "澳大利亚",
    "巴西",
    "阿根廷",
    "墨西哥",
    "智利",
    "秘鲁",
    "土耳其",
    "伊朗",
    "黎巴嫩",
    "尼日利亚",
    "南非",
    "爱尔兰",
    "冰岛",
    "哥伦比亚",
    "古巴",
    "新西兰",
    "越南",
    "泰国",
    "新加坡",
    "古希腊",
    "古罗马",
    "古代",
    "中世纪",
}
AUTHOR_LABEL = re.compile(r"^(?:作者|著者|编者|译者|主编|著)\s*[:：]\s*")
AUTHOR_BRACKET = re.compile(r"^[\[（(【][^\]）)】]{1,10}[\]）)】]\s*")
AUTHOR_JUNK = {
    "etc",
    "etc.",
    "unknown",
    "pdgpic",
    "pdg2pic",
    "套装共册",
    "套装共卷",
    "著",
    "作者",
    "佚名",
    "无",
    "none",
    "null",
    "-",
    "—",
}


def strip_tail_paren(stem):
    """剥离字符串末尾一对配平的半角/全角括号，返回 (前缀, 括号内文本或 None)。"""
    end = len(stem) - 1
    while end >= 0 and stem[end].isspace():
        end -= 1
    if end < 0 or stem[end] not in (")", "）"):
        return stem, None
    depth = 0
    for i in range(end, -1, -1):
        c = stem[i]
        if c in (")", "）"):
            depth += 1
        elif c in ("(", "（"):
            depth -= 1
            if depth == 0:
                return stem[:i].rstrip(), stem[i + 1 : end].strip()
    return stem, None


def is_download_site_noise(text):
    """整段文本是否全由电子书下载站/镜像站域名或来源标签组成。"""
    tokens = [tok for tok in re.split(r"[,，;；/|\s]+", (text or "").strip()) if tok]
    return bool(tokens) and all(DOWNLOAD_SITE_TOKEN_RE.fullmatch(tok) for tok in tokens)


def strip_download_site_noise(stem):
    """循环剥离 stem 末尾的下载站/镜像站噪声括号（兼容半角/全角、多域名、多层堆叠）。"""
    cur = (stem or "").rstrip()
    while True:
        head, inner = strip_tail_paren(cur)
        if inner is None or not is_download_site_noise(inner):
            return cur
        cur = head


def clean_author(a):
    """作者清洗：去「作者：」标签、去国家括注、挡掉下载站域名与出版社/扫描仪等非人名兜底值。"""
    a = AUTHOR_LABEL.sub("", (a or "").strip()).strip()
    a = AUTHOR_BRACKET.sub("", a).strip()
    if not a or a.lower() in AUTHOR_JUNK or a in COUNTRY_LIKE or is_download_site_noise(a):
        return ""
    if re.fullmatch(r"[\d\s\-—_·.、,，]+", a):
        return ""
    return a if len(a) > 1 else ""


def rel(path):
    return os.path.relpath(path, ROOT)


def norm(s):
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", s or "").lower(), flags=re.U)


def clean_title(filename):
    """文件名到目录名候选。去下载站后缀、去作者括注、去版本词尾巴、去省略号。"""
    base = os.path.splitext(os.path.basename(filename))[0]
    base = TRUNC.sub("", base)
    base = strip_download_site_noise(base)
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
    stem = strip_download_site_noise(TRUNC.sub("", stem))
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
        except Exception as e:
            return True, "读取异常:" + type(e).__name__
        return False, ""
    if fmt == "pdf":
        with open(path, "rb") as fh:
            head = fh.read(8)
            fh.seek(max(0, size - 65536))
            tail = fh.read()
        if not head.startswith(b"%PDF"):
            return True, "缺少 %PDF 头"
        if b"%%EOF" not in tail:
            return True, "结尾 64KB 内没有 %%EOF"
        return False, ""
    return False, ""


def read_csv(path):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def read_tsv(path):
    """词表 TSV：utf-8-sig 去掉表头 BOM，tab 分列。"""
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


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

LEDGER_DB = "ledger.db"

LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS 分类(
  uid TEXT PRIMARY KEY,
  一级 TEXT NOT NULL,
  二级 TEXT NOT NULL DEFAULT '',
  作者层 TEXT NOT NULL DEFAULT '0',
  保留 TEXT NOT NULL DEFAULT 'keep' CHECK(保留 IN ('keep','review','drop')),
  正式书名 TEXT NOT NULL DEFAULT '',
  备注 TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS ledger_meta(
  key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
"""


def ledger_path(root=None):
    """tables/ledger.db 的路径；root 给了就指向那个根下的 tables。"""
    tables = os.path.join(os.path.abspath(os.path.expanduser(root)), "tables") if root else TABLES
    return os.path.join(tables, LEDGER_DB)


def ledger_connect(mode="rw", root=None):
    """mode='ro' 只读打开（库不存在会抛错，先 _ensure_ledger）；'rw' 可写，30s 忙等。"""
    path = ledger_path(root)
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True) if mode == "ro" else sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _ledger_ready(root=None):
    """库文件在且核心表能查才算可用。空文件、半建库、坏文件都返回 False。

    只看 os.path.isfile 会把 0 字节库当成好的，后续查询永远抛 no such table，
    读账的人（操作台、脚本）就永久读不了。
    """
    path = ledger_path(root)
    if not os.path.isfile(path):
        return False
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=5)
    except sqlite3.Error:
        return False
    try:
        con.execute("SELECT 1 FROM 分类 LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False
    finally:
        con.close()


def _ensure_schema(root=None):
    """建/补 tables/ledger.db 的 schema。不是 SQLite 库的文件改名留档后重建。"""
    path = ledger_path(root)
    tables = os.path.dirname(path)
    if not os.path.isdir(tables):
        try:
            os.makedirs(tables, exist_ok=True)
        except OSError as exc:
            sys.stderr.write("ledger.db 建库失败：%r\n" % (exc,))
            return False
    for attempt in (1, 2):
        try:
            con = sqlite3.connect(path, timeout=30)
            try:
                con.executescript(LEDGER_SCHEMA)
                con.commit()
            finally:
                con.close()
            return True
        except (OSError, sqlite3.Error) as exc:
            msg = str(exc).lower()
            # 只有「文件不是 SQLite 库/已损坏」才改名重建。database is locked、
            # 磁盘只读这类是暂时/环境错误，把活库 os.replace 走会丢数据。
            corrupt = isinstance(exc, sqlite3.DatabaseError) and any(
                s in msg for s in ("not a database", "malformed", "file is encrypted")
            )
            if not corrupt or attempt == 2:
                sys.stderr.write("ledger.db 建库失败：%r\n" % (exc,))
                return False
            backup = "%s.broken-%s" % (path, time.strftime("%Y%m%d%H%M%S%f"))
            try:
                os.replace(path, backup)
            except OSError as exc2:
                sys.stderr.write("ledger.db 不能用（%r），改名留档也失败：%r\n" % (exc, exc2))
                return False
            sys.stderr.write("ledger.db 不是可用的 SQLite 库（%r），已改名留档 %s 并重建\n" % (exc, backup))
    return False


def ledger_init(root=None):
    """把 ledger.db 建出来：有种子 CSV 就导进去，没有就只建空 schema。

    init 建工作区时调它，别等人账第一次被写才建库——那之前读的都是 CSV 种子，
    两本账一旦分叉，doctor 和审计都会读错。
    """
    return _ensure_ledger(root, create=True)


def ledger_exists(root=None):
    """库文件在、schema 也在。doctor 靠它决定报「ledger.db」还是「CSV 种子」。"""
    return os.path.isfile(ledger_path(root)) and _ledger_ready(root)


def batch_dir(prefix, root=None, create=False):
    """工作区/批次 下最近一个 <prefix>YYYY-MM-DD 目录；没有就用今天。

    批次目录名带日期是惯例（pdf-普查-2026-10-19），别在代码里写死某一天：
    新建的库没有那天的目录，读出来永远是空表。
    """
    root = root or ROOT
    base = os.path.join(root, "工作区", "批次")
    pat = re.compile(r"^%s\d{4}-\d{2}-\d{2}$" % re.escape(prefix))
    names = []
    with contextlib.suppress(OSError):
        names = sorted(n for n in os.listdir(base) if pat.match(n) and os.path.isdir(os.path.join(base, n)))
    path = os.path.join(base, names[-1] if names else prefix + time.strftime("%Y-%m-%d"))
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _ensure_ledger(root=None, create=False):
    """DB 缺失或没 schema 时自举：有分类表.csv 用它，否则从 books/*/book.json 恢复，空库建空表。

    root 给了就自举那个根（--root 场景、doctor、测试工作区），不给用 import 时的全局根。
    默认 create=False：只读场景不落盘，缺库时让调用方退回读 CSV。
    建不了（只读盘等）返回 False，读会退回 CSV。
    """
    if _ledger_ready(root):
        return True
    if not create:
        return False
    if not _ensure_schema(root):
        return False
    tables = os.path.dirname(ledger_path(root))
    cat_csv = os.path.join(tables, "分类表.csv")
    csv_rows = read_csv(cat_csv) if os.path.isfile(cat_csv) else []
    books = [] if csv_rows else iter_book_json(root)
    if not csv_rows and not books:
        # 没有种子也没有 book.json：空 schema 就够了（init 后的全新工作区走这里）
        return True
    try:
        con = sqlite3.connect(ledger_path(root), timeout=30)
        try:
            now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            if csv_rows:
                for row in csv_rows:
                    con.execute(
                        "INSERT OR IGNORE INTO 分类(uid,一级,二级,作者层,保留,正式书名,备注,updated_at)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (
                            row.get("uid", ""),
                            row.get("一级", ""),
                            row.get("二级", ""),
                            row.get("作者层", ""),
                            row.get("保留", "keep"),
                            row.get("正式书名", ""),
                            row.get("备注", ""),
                            now,
                        ),
                    )
                con.execute("INSERT OR REPLACE INTO ledger_meta VALUES('imported_from', ?)", (now,))
            else:
                for uid, doc in books:
                    raw_subs = doc.get("subjects")
                    subs = raw_subs if isinstance(raw_subs, list) else []
                    s1 = (str(subs[0]).strip() if len(subs) >= 1 and subs[0] else "") or "其他"
                    s2 = str(subs[1]).strip() if len(subs) >= 2 and subs[1] else ""
                    ret = str(doc.get("retention") or "").strip()
                    if ret not in ("keep", "review", "drop"):
                        ret = "keep"
                    con.execute(
                        "INSERT OR IGNORE INTO 分类(uid,一级,二级,作者层,保留,正式书名,备注,updated_at)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (
                            uid,
                            s1,
                            s2,
                            "0",
                            ret,
                            str(doc.get("title") or "").strip(),
                            str(doc.get("notes") or "").strip(),
                            now,
                        ),
                    )
                con.execute("INSERT OR REPLACE INTO ledger_meta VALUES('imported_from', ?)", (now,))
                con.execute("INSERT OR REPLACE INTO ledger_meta VALUES('recovered_from', ?)", ("book.json",))
            con.commit()
        finally:
            con.close()
        return True
    except (OSError, sqlite3.Error) as exc:
        sys.stderr.write("ledger.db 自举失败：%r\n" % (exc,))
        return False


def categories(root=None, create=False):
    """人账全表，uid 序。缺库且不建时退回读旧分类表.csv（只读兜底）。"""
    if _ensure_ledger(root, create):
        con = ledger_connect("ro", root)
        try:
            return [dict(r) for r in con.execute("SELECT * FROM 分类 ORDER BY uid")]
        finally:
            con.close()
    return read_csv(legacy_csv(root))


def legacy_csv(root=None):
    """切换前的活账 CSV 路径（现在只是 ledger.db 的种子与文件档）。"""
    return os.path.join(os.path.dirname(ledger_path(root)), "分类表.csv")


def categories_count(root=None, create=False):
    if _ensure_ledger(root, create):
        con = ledger_connect("ro", root)
        try:
            return con.execute("SELECT count(*) FROM 分类").fetchone()[0]
        finally:
            con.close()
    return len(read_csv(legacy_csv(root)))


def _resolve_writable_root(root):
    """写人账前认根：给定的（或 cwd 抓默认的）目录必须有工作区标志。

    防呆：测试与脚本忘了传 --root 时，默认 cwd 若不是工作区就拒写，
    账库永远不会在工具仓或用户家目录里凭空长出来。
    """
    root = os.path.abspath(os.path.expanduser(root)) if root else os.path.abspath(os.getcwd())
    if any(os.path.exists(os.path.join(root, marker)) for marker in ("books.toml", "books", "tables")):
        return root
    raise SystemExit("这不是书库工作区（缺 books.toml/books/tables），拒绝写人账：%s" % root)


def writer_lock_path(root=None):
    """写任务与人账写入共用的 .writer.lock 路径。"""
    base = os.path.abspath(os.path.expanduser(root)) if root else ROOT
    return os.path.join(base, "工作区", "运维", ".writer.lock")


def _writer_lock_held():
    """同一线程的 .writer.lock 重入计数；挂在 threading 上以兼容双别名导入。"""
    tls = getattr(threading, "_book_corpus_writer_lock_tls", None)
    if tls is None:
        tls = threading.local()
        threading._book_corpus_writer_lock_tls = tls
    held = getattr(tls, "held", None)
    if held is None:
        held = {}
        tls.held = held
    return held


@contextlib.contextmanager
def writer_lock(root=None):
    """与操作台 Runner / HTTP 账表写入共用 工作区/运维/.writer.lock（LOCK_EX）。

    同线程重入直接计数放行；由 ops 写任务拉起的子进程若已在父进程持有同一把锁，
    通过 BOOK_CORPUS_WRITER_LOCK_PATH 声明继承，避免子进程阻塞在父进程的 flock 上。
    """
    lock_path = writer_lock_path(root)
    real_lock = os.path.realpath(lock_path)
    env_lock = os.environ.get("BOOK_CORPUS_WRITER_LOCK_PATH") or ""
    if env_lock and os.path.realpath(env_lock) == real_lock:
        yield
        return
    held = _writer_lock_held()
    depth = held.get(real_lock, 0)
    if depth > 0:
        held[real_lock] = depth + 1
        try:
            yield
        finally:
            if held.get(real_lock, 0) <= 1:
                held.pop(real_lock, None)
            else:
                held[real_lock] -= 1
        return
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        held[real_lock] = 1
        try:
            yield
        finally:
            held.pop(real_lock, None)
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def category_put(row, updated_by="", root=None):
    """插入或整行更新一条人账。写时戳：操作时间；updated_by 只进日志不进库。"""
    root = _resolve_writable_root(root)
    with writer_lock(root):
        _ensure_ledger(root, create=True) or raise_systemexit_ledger()
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        con = ledger_connect("rw", root)
        try:
            con.execute(
                "INSERT INTO 分类(uid,一级,二级,作者层,保留,正式书名,备注,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)"
                " ON CONFLICT(uid) DO UPDATE SET 一级=excluded.一级, 二级=excluded.二级,"
                " 作者层=excluded.作者层, 保留=excluded.保留, 正式书名=excluded.正式书名,"
                " 备注=excluded.备注, updated_at=excluded.updated_at",
                (
                    row["uid"],
                    row.get("一级", ""),
                    row.get("二级", ""),
                    row.get("作者层", "0"),
                    row.get("保留", "keep") or "keep",
                    row.get("正式书名", ""),
                    row.get("备注", ""),
                    now,
                ),
            )
            con.commit()
        finally:
            con.close()
        return now


def category_put_many(rows, updated_by="", root=None):
    """整批 upsert，一个事务。返回写时戳。"""
    root = _resolve_writable_root(root)
    with writer_lock(root):
        _ensure_ledger(root, create=True) or raise_systemexit_ledger()
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        con = ledger_connect("rw", root)
        try:
            for row in rows:
                con.execute(
                    "INSERT INTO 分类(uid,一级,二级,作者层,保留,正式书名,备注,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(uid) DO UPDATE SET 一级=excluded.一级, 二级=excluded.二级,"
                    " 作者层=excluded.作者层, 保留=excluded.保留, 正式书名=excluded.正式书名,"
                    " 备注=excluded.备注, updated_at=excluded.updated_at",
                    (
                        row["uid"],
                        row.get("一级", ""),
                        row.get("二级", ""),
                        row.get("作者层", "0"),
                        row.get("保留", "keep") or "keep",
                        row.get("正式书名", ""),
                        row.get("备注", ""),
                        now,
                    ),
                )
            con.commit()
        finally:
            con.close()
        return now


def categories_delete(uids, root=None):
    """整批删行，一个事务。返回删掉的行数。"""
    root = _resolve_writable_root(root)
    uids = [(u or "").strip() for u in uids if (u or "").strip()]
    if not uids:
        return 0
    with writer_lock(root):
        _ensure_ledger(root, create=True) or raise_systemexit_ledger()
        con = ledger_connect("rw", root)
        try:
            n = 0
            for uid in uids:
                n += con.execute("DELETE FROM 分类 WHERE uid=?", (uid,)).rowcount
            con.commit()
        finally:
            con.close()
        return n


def raise_systemexit_ledger():
    raise SystemExit("tables/ledger.db 建不起来，先查 tables/ 可写性")


def path_safe(s):
    return re.sub(r"[\\/:*?\"<>|\n\t]", "／", (s or "").strip()).strip(" .")


def book_key(uid):
    """书在 books/ 下的目录名。v2 就是 uid，不再由学科与书名拼出来。"""
    return uid


def iter_book_json(root=None):
    """books/**/book.json -> [(uid, doc)]。下划线开头的目录不算书。"""
    books_dir = os.path.join(os.path.abspath(os.path.expanduser(root)), "books") if root else BOOKS
    out = []
    if not os.path.isdir(books_dir):
        return out
    for dirpath, dirnames, filenames in os.walk(books_dir):
        if dirpath == books_dir:
            dirnames[:] = [d for d in dirnames if not d.startswith("_")]
        else:
            dirnames[:] = [d for d in dirnames if not d.startswith("_") and d not in ("md", "html", "source", "images")]
        if "book.json" not in filenames:
            continue
        dirnames[:] = []
        uid = os.path.basename(dirpath)
        try:
            with open(os.path.join(dirpath, "book.json"), encoding="utf-8") as fh:
                out.append((uid, json.load(fh)))
        except (OSError, json.JSONDecodeError) as e:
            raise SystemExit("book.json 读不了: %s (%s)" % (os.path.join(dirpath, "book.json"), e)) from None
    out.sort(key=lambda x: x[0])
    return out


def book_doc(
    uid,
    title,
    title_from_file,
    author,
    subjects,
    series,
    volume,
    container,
    retention,
    notes,
    sources,
    awards,
    derived=None,
):
    """book.json 的唯一形状。derived 是磁盘状态，只在迁移脚本里显式写入。"""
    doc = {
        "uid": uid,
        "title": title,
        "title_from_file": title_from_file,
        "author": author or "",
        "subjects": [x for x in (subjects or []) if x],
        "series": series or None,
        "volume": volume or None,
        "container": container or None,
        "retention": retention or "keep",
        "sources": sources,
        "awards": awards,
        "notes": notes or "",
    }
    if derived is not None:
        doc["derived"] = derived
    return doc


def write_json_atomic(path, doc):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
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


# 本模块即「L」。有的脚本写 from lib_common import L，有的写 import lib_common as L，
# 两种都得到这一个模块对象。
L = sys.modules[__name__]
