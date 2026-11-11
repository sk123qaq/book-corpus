"""日课：每天从书库里挑一章，让 pi 读上下文、写一期 300 到 500 字的稿子。

选择、提示词、校验、写盘都在这里；脚本 src/bookcorpus/scripts/daily.py 只管
命令行、日志与 systemd 安装。根目录显式传入，模块本身不猜工作区。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import random
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import pimodel

DAILY = "日课"
LOGS = "日志"
MIN_CHARS = 300
MAX_CHARS = 500
MAX_WEB = 5
REPEAT_DAYS = 14
MIN_CHAPTERS = 3
MIN_BYTES = 2_000
MAX_BYTES = 120_000
PI_TIMEOUT = 600
DEFAULT_AT = "07:00"
FICTION_HINTS = ("小说", "文学", "诗", "戏剧", "散文", "随笔", "童话", "寓言")

_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


class DailyError(Exception):
    """日课没写成，消息直接给人看。"""


@dataclass
class Chapter:
    order: int
    index: int
    label: str
    title: str
    path: str
    file: Path
    text: str = ""


@dataclass
class Book:
    uid: str
    title: str
    author: str
    subjects: list[str]
    chapters: list[Chapter]


def cn_number(text: str) -> int | None:
    """阿拉伯数字或一百以内的中文数字；认不出返回 None。"""
    text = (text or "").strip()
    if text.isdigit():
        return int(text)
    if not text:
        return None
    total = 0
    current = 0
    for char in text:
        if char in _CN_DIGITS:
            current = _CN_DIGITS[char]
        elif char == "十":
            total += (current or 1) * 10
            current = 0
        elif char == "百":
            total += (current or 1) * 100
            current = 0
        else:
            return None
    return total + current


def parse_chapter(path: Path, root: Path) -> Chapter:
    """04_第2章机会成本和禀赋效应.md -> 第2章 / 机会成本和禀赋效应。"""
    stem = path.stem
    match = re.match(r"^(\d+)\s*[_\-\s]\s*(.*)$", stem)
    order = int(match.group(1)) if match else 0
    rest = (match.group(2) if match else stem).strip()
    numbered = re.match(r"^第\s*([0-9一二三四五六七八九十百零〇两]+)\s*([章节部篇回])\s*(.*)$", rest)
    if numbered:
        index = cn_number(numbered.group(1)) or order or 1
        label = f"第{numbered.group(1)}{numbered.group(2)}"
        title = numbered.group(3).strip() or label
    else:
        index = order or 1
        label = f"第{index}章"
        title = rest or label
    relative = path.resolve().relative_to(Path(root).resolve()).as_posix()
    return Chapter(order=order, index=index, label=label, title=title, path=relative, file=path)


def short_title(raw: str | None) -> str:
    text = str(raw or "").strip()
    text = re.split(r"\s*=\s*|\s{2,}", text)[0]
    return re.sub(r"\s+", " ", text).strip() or "未命名"


def short_author(raw: str | None) -> str:
    text = str(raw or "")
    text = re.sub(r"[\[【][^\]】]*[\]】]", "", text)
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    text = re.sub(r"(?i)(?:z-?lib(?:rary)?|1lib)(?:\.\w+)?", " ", text)
    text = re.split(r"\s*(?:著|编著|主编|编|译|等)\s*", text)[0]
    text = re.sub(r"\s+", " ", text).strip(" ,，。·:：")
    return text or "佚名"


def read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def find_books(root: Path) -> list[Book]:
    books_dir = Path(root) / "books"
    if not books_dir.is_dir():
        return []
    books: list[Book] = []
    for folder in sorted(books_dir.iterdir()):
        if not folder.is_dir() or folder.name.startswith("_"):
            continue
        meta = read_json(folder / "book.json")
        if meta is None:
            continue
        md_dir = folder / "md"
        if not md_dir.is_dir():
            continue
        chapters: list[Chapter] = []
        sizes: list[int] = []
        md_files = 0
        for file in sorted(md_dir.glob("*.md")):
            if file.name.lower() in ("readme.md", "index.md") or "前置页" in file.name:
                continue
            md_files += 1
            try:
                size = file.stat().st_size
            except OSError:
                continue
            sizes.append(size)
            if not MIN_BYTES <= size <= MAX_BYTES:
                continue
            chapters.append(parse_chapter(file, root))
        if len(chapters) < MIN_CHAPTERS:
            continue
        # 一章只剩几百字节，正文被切到别处了，这种 md 不能用。
        if sorted(sizes)[len(sizes) // 2] < 1_000:
            continue
        # md 是按整本书切的（文件很大，只落下几个），不适合按章取用。
        if md_files >= 8 and len(chapters) < 5:
            continue
        chapters.sort(key=lambda chapter: (chapter.order, chapter.index))
        author = short_author(meta.get("author"))
        subjects = [str(item) for item in (meta.get("subjects") or []) if str(item).strip()]
        books.append(
            Book(
                uid=str(meta.get("uid") or folder.name),
                title=short_title(meta.get("title")),
                author=author,
                subjects=subjects,
                chapters=chapters,
            )
        )
    return books


def book_kind(book: Book) -> str:
    for subject in book.subjects:
        if any(hint in subject for hint in FICTION_HINTS):
            return "fiction"
    return "expository"


def issue_dir(root: Path) -> Path:
    return Path(root) / DAILY


def issue_day(path: Path) -> dt.date | None:
    try:
        return dt.date.fromisoformat(path.stem)
    except ValueError:
        return None


def recent_uids(root: Path, day: dt.date, days: int = REPEAT_DAYS) -> set[str]:
    cutoff = day - dt.timedelta(days=days)
    used: set[str] = set()
    directory = issue_dir(root)
    if not directory.is_dir():
        return used
    for file in directory.glob("*.json"):
        when = issue_day(file)
        if when is None or not cutoff <= when < day:
            continue
        data = read_json(file) or {}
        uid = (data.get("book") or {}).get("uid")
        if uid:
            used.add(str(uid))
    return used


def previous_kind(root: Path, day: dt.date) -> str | None:
    latest: tuple[dt.date, str | None] | None = None
    directory = issue_dir(root)
    if not directory.is_dir():
        return None
    for file in directory.glob("*.json"):
        when = issue_day(file)
        if when is None or when >= day:
            continue
        if latest is None or when > latest[0]:
            data = read_json(file) or {}
            kind = data.get("kind")
            latest = (when, kind if isinstance(kind, str) else None)
    return latest[1] if latest else None


def select(
    root: Path,
    day: dt.date,
    book_uid: str | None = None,
    chapter_no: int | None = None,
    rng: random.Random | None = None,
) -> tuple[Book, Chapter]:
    rng = rng or random.Random(day.isoformat())
    books = find_books(root)
    if not books:
        raise DailyError("书库里没有可用的 md 章节（至少 3 章，每章 2KB 到 120KB）。")
    if book_uid:
        books = [book for book in books if book.uid == book_uid]
        if not books:
            raise DailyError(f"没有这本书，或它的 md 不够三章：{book_uid}")
    else:
        used = recent_uids(root, day)
        pool = [book for book in books if book.uid not in used] or books
        previous = previous_kind(root, day)
        want = {"fiction": "expository", "expository": "fiction"}.get(previous or "")
        if want:
            preferred = [book for book in pool if book_kind(book) == want]
            pool = preferred or pool
        books = pool
    book = rng.choice(books)
    if chapter_no is not None:
        matches = [chapter for chapter in book.chapters if chapter.index == chapter_no]
        if not matches:
            matches = [chapter for chapter in book.chapters if chapter.order == chapter_no]
        if not matches:
            have = "、".join(f"{chapter.label}（文件 {chapter.order:02d}）" for chapter in book.chapters)
            raise DailyError(f"{book.uid} 没有第 {chapter_no} 章。可选：{have}")
        chapter = matches[0]
    else:
        chapter = rng.choice(book.chapters)
    return book, chapter


def read_chapter(chapter: Chapter) -> str:
    return chapter.file.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def context_chapters(book: Book, chapter: Chapter, span: int = 5) -> list[Chapter]:
    index = next(i for i, item in enumerate(book.chapters) if item is chapter)
    return book.chapters[max(0, index - span) : index + span + 1]


def build_prompt(book: Book, chapter: Chapter, neighbors: list[Chapter], day: dt.date, web: bool) -> str:
    paths = "\n".join(f"- {item.path}  {item.label} {item.title}" for item in neighbors)
    web_rule = (
        "需要外部事实时才联网，最多 5 次。用 web_search 搜，每次查询写进 research.queries，"
        "web 等于查询条数。不需要就写 0 和 []。"
        if web
        else "这次没有联网工具，只用书里的材料；research.web 写 0，queries 写 []。"
    )
    template = """你是「书库日课」的撰稿人。今天是 {day}。只写这一期，不要写系列计划。

书：《{title}》
作者：{author}
章节：{label} {chapter_title}
本章正文：{path}（{chars} 字）
全书 {count} 章，本章是第 {order} 个文件。

可以查的前后章节（含本章，共 {near} 个文件）：
{paths}

工具与取材
- 先用 read 读本章全文。要查人名、概念、伏笔，用 grep 在这些 md 文件里搜，不要读整座书库。
- 论述或事实讲述：挑一个具体机制或案例讲透，用书里的例子和数字，不要泛泛复述目录。
- 小说：挑一个精彩片段或一个有来头的设定，讲清它在故事里的位置。可以补背景，不要编情节。
- {web_rule}

写法
- 正文 300 到 500 字（含标点，不含空白），分 3 到 5 段。第一段就要有具体的人、事或数字。
- 每段只做一件事。不用破折号，不用「不是……而是……」句式，不写「首先/其次/最后」。
- headline 不超过 20 字，要具体，不用「浅谈」「启示」这类空词。
- why 用一两句话说清为什么挑这一章，不超过 80 字。
- quote 必须从本章原文逐字摘录，6 到 100 字，不要改写，不要跨段拼接。
  line 写这句话在本章 md 里的行号，拿不准就填 1，程序会按原文复核并改正。

只输出一个 JSON 对象，不要 markdown 代码围栏，不要解释：
{{"kind":"expository 或 fiction","subject":"两到八个字，例如 行为经济学",
 "headline":"...","body":["第一段","第二段","第三段"],
 "quote":{{"text":"本章原文","line":1}},"why":"...",
 "research":{{"chapters":"查了第 1 到 7 章","web":0,"queries":[],"note":"可留空"}}}}

kind 的取值：论述、事实讲述写 expository；小说写 fiction。"""
    return template.format(
        day=day.isoformat(),
        title=book.title,
        author=book.author,
        label=chapter.label,
        chapter_title=chapter.title,
        path=chapter.path,
        chars=len(chapter.text),
        count=len(book.chapters),
        order=chapter.order,
        near=len(neighbors),
        paths=paths,
        web_rule=web_rule,
    )


def extract_json(raw: str) -> dict:
    text = (raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise DailyError("回复里没有 JSON 对象。")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise DailyError(f"JSON 解析失败：{exc.msg}（第 {exc.lineno} 行）") from exc
    if not isinstance(payload, dict):
        raise DailyError("JSON 顶层必须是对象。")
    return payload


def char_count(paragraphs: list[str]) -> int:
    return len(re.sub(r"\s", "", "".join(paragraphs)))


def check(payload: dict, chapter: Chapter) -> tuple[dict | None, list[str]]:
    errors: list[str] = []

    def clean(key: str, low: int, high: int) -> str:
        value = payload.get(key)
        if not isinstance(value, str):
            errors.append(f"{key} 必须是字符串")
            return ""
        value = value.strip()
        if not low <= len(value) <= high:
            errors.append(f"{key} 要在 {low} 到 {high} 字之间（现在 {len(value)}）")
        return value

    kind = payload.get("kind")
    if kind not in ("expository", "fiction"):
        errors.append("kind 只能是 expository 或 fiction")
    subject = clean("subject", 2, 20)
    headline = clean("headline", 4, 24)
    why = clean("why", 4, 120)

    paragraphs: list[str] = []
    body = payload.get("body")
    if not isinstance(body, list) or not 2 <= len(body) <= 6:
        errors.append("body 必须是 2 到 6 段的数组")
    else:
        for index, paragraph in enumerate(body):
            if not isinstance(paragraph, str) or not paragraph.strip():
                errors.append(f"body[{index}] 不能为空")
            else:
                paragraphs.append(paragraph.strip())
        count = char_count(paragraphs)
        if not MIN_CHARS <= count <= MAX_CHARS:
            errors.append(f"正文 {count} 字，必须在 {MIN_CHARS} 到 {MAX_CHARS} 字之间")

    quote_text = ""
    quote_line = 0
    quote = payload.get("quote")
    if not isinstance(quote, dict):
        errors.append("quote 必须是对象")
    else:
        raw_text = quote.get("text")
        quote_text = raw_text.strip() if isinstance(raw_text, str) else ""
        if not 6 <= len(quote_text) <= 100:
            errors.append("quote.text 要在 6 到 100 字之间")
        elif quote_text not in chapter.text:
            errors.append("quote.text 在本章原文里找不到，必须逐字摘录")
        else:
            quote_line = chapter.text.count("\n", 0, chapter.text.find(quote_text)) + 1

    research = payload.get("research")
    chapters = ""
    web = 0
    queries: list[str] = []
    note = ""
    if not isinstance(research, dict):
        errors.append("research 必须是对象")
    else:
        raw_chapters = research.get("chapters")
        chapters = raw_chapters.strip() if isinstance(raw_chapters, str) else ""
        if not 1 <= len(chapters) <= 40:
            errors.append("research.chapters 要写查了哪几章，40 字内")
        raw_web = research.get("web", 0)
        if isinstance(raw_web, bool) or not isinstance(raw_web, int) or not 0 <= raw_web <= MAX_WEB:
            errors.append(f"research.web 必须是 0 到 {MAX_WEB} 的整数")
            web = 0
        else:
            web = raw_web
        raw_queries = research.get("queries", [])
        if not isinstance(raw_queries, list) or any(not isinstance(item, str) for item in raw_queries):
            errors.append("research.queries 必须是字符串数组")
        else:
            queries = [item.strip() for item in raw_queries if item.strip()]
            if len(queries) > MAX_WEB:
                errors.append(f"research.queries 最多 {MAX_WEB} 条")
            if web != len(queries):
                errors.append(f"research.web 是 {web}，但 queries 有 {len(queries)} 条，两者要相等")
        raw_note = research.get("note")
        note = raw_note.strip() if isinstance(raw_note, str) else ""
        if len(note) > 120:
            errors.append("research.note 最多 120 字")

    if errors:
        return None, errors
    return (
        {
            "kind": kind,
            "kindLabel": "论述" if kind == "expository" else "小说",
            "subject": subject,
            "headline": headline,
            "body": paragraphs,
            "quote": {"text": quote_text, "line": quote_line},
            "why": why,
            "research": {"chapters": chapters, "web": web, "queries": queries, "note": note},
        },
        [],
    )


def write_issue(root: Path, target: Path, issue: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(issue, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    temporary.replace(target)


def write_log(root: Path, day: dt.date, message: str) -> None:
    directory = issue_dir(root) / LOGS
    directory.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    with (directory / (day.isoformat() + ".log")).open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def find_web_extension() -> Path | None:
    override = os.environ.get("PI_WEB_EXTENSION", "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.exists() else None
    for candidate in (
        Path.home() / ".pi/agent/npm/node_modules/pi-web-access/dist/index.js",
        Path.home() / ".pi/agent/npm/node_modules/pi-web-access/index.ts",
    ):
        if candidate.exists():
            return candidate
    return None


def generate(
    root: Path,
    day: dt.date | None = None,
    book_uid: str | None = None,
    chapter_no: int | None = None,
    model: str | None = None,
    thinking: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    timeout: int = PI_TIMEOUT,
    log=None,
    web: bool | None = None,
) -> Path | None:
    root = Path(root).expanduser().resolve()
    day = day or dt.date.today()
    say = log or (lambda message: None)
    target = issue_dir(root) / (day.isoformat() + ".json")
    if target.exists() and not force:
        say(f"{day} 已经有日课，跳过：{target}")
        return target

    book, chapter = select(root, day, book_uid, chapter_no)
    chapter.text = read_chapter(chapter)
    neighbors = context_chapters(book, chapter)
    extension = find_web_extension()
    use_web = bool(extension) if web is None else bool(web and extension)
    prompt = build_prompt(book, chapter, neighbors, day, use_web)

    values = pimodel.load(str(root))["values"]
    if model:
        values["model"] = model
        if "/" in model:
            values["provider"] = ""
    level = thinking or None
    ref = pimodel.ref(values)
    tools = "grep,read" + (",web_search" if use_web else "")
    extra = ["--tools", tools]
    if use_web:
        extra = ["-e", str(extension), *extra]

    say(f"{day} 选书：{book.uid}《{book.title}》 {chapter.label} {chapter.title}（{len(chapter.text)} 字）")
    if dry_run:
        say(f"模型：{ref}  思考：{pimodel.thinking(values, level)}  工具：{tools}")
        say("----- 提示词 -----")
        say(prompt)
        return None

    errors: list[str] = []
    for attempt in (1, 2):
        ask = prompt
        if attempt == 2:
            ask += "\n\n上一版没通过校验：" + "；".join(errors) + "\n只输出修正后的 JSON，不要解释。"
        try:
            proc = subprocess.run(
                pimodel.argv(values, ask, extra, level),
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            errors = [f"pi 超时（{timeout} 秒）"]
            write_log(root, day, f"第 {attempt} 次：{errors[0]}")
            continue
        except OSError as exc:
            raise DailyError(f"起不了 pi：{exc}") from exc
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            errors = [tail[-1][:300] if tail else f"pi 退出 {proc.returncode}"]
            write_log(root, day, f"第 {attempt} 次：{errors[0]}")
            continue
        try:
            payload = extract_json(proc.stdout)
        except DailyError as exc:
            errors = [str(exc)]
            write_log(root, day, f"第 {attempt} 次：{errors[0]}")
            continue
        checked, errors = check(payload, chapter)
        if checked is None:
            write_log(root, day, f"第 {attempt} 次校验失败：{'；'.join(errors)}")
            continue
        issue = {
            "version": 1,
            "date": day.isoformat(),
            **checked,
            "book": {"uid": book.uid, "title": book.title, "author": book.author},
            "chapter": {"index": chapter.index, "label": chapter.label, "title": chapter.title, "path": chapter.path},
            "generatedAt": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "model": ref,
            "attempts": attempt,
            "webQueries": checked["research"]["queries"],
        }
        write_issue(root, target, issue)
        count = char_count(issue["body"])
        write_log(root, day, f"完成：{target}（第 {attempt} 次，{count} 字）")
        return target
    raise DailyError("两次都没写成：" + "；".join(errors))


def timer_units(repo: Path, root: Path, at: str = DEFAULT_AT) -> dict[str, str]:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", at):
        raise DailyError("--at 要写成 HH:MM，例如 07:00")
    service = f"""[Unit]
Description=book-corpus daily issue
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory={Path(repo).resolve()}
ExecStart={sys.executable} -m bookcorpus daily --root {Path(root).resolve()}
"""
    timer = f"""[Unit]
Description=Generate the book-corpus daily issue

[Timer]
OnCalendar=*-*-* {at}:00
Persistent=true

[Install]
WantedBy=timers.target
"""
    return {"service": service, "timer": timer}


def install_timer(
    repo: Path,
    root: Path,
    at: str = DEFAULT_AT,
    print_only: bool = False,
) -> tuple[dict[str, str], list[Path]]:
    units = timer_units(repo, root, at)
    if print_only:
        return units, []
    directory = Path.home() / ".config/systemd/user"
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in (
        ("book-corpus-daily.service", units["service"]),
        ("book-corpus-daily.timer", units["timer"]),
    ):
        path = directory / name
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True)
    proc = subprocess.run(
        ["systemctl", "--user", "enable", "--now", "book-corpus-daily.timer"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        raise DailyError(f"单元已写入，但 systemctl 启用失败：{detail}")
    return units, paths
