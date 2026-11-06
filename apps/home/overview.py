"""把书库数据收成一页首页所需的形状，并渲染成 HTML。

只读：书目来自 Library，日课来自 apps/daily/feed.py 的同一套校验。
页面在服务端渲染，不依赖客户端 fetch，也不改任何数据。
"""

from __future__ import annotations

import datetime as dt
import html
import importlib.util
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
DAILY_FEED = HERE.parent / "daily" / "feed.py"
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")

DEFAULT_URLS = {
    "search": "/go/search",
    "reading": "/go/reading",
    "daily": "/go/daily",
    "ops": "/go/ops",
}


def _count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return html.escape(str(value), quote=False)


def _url(value: Any) -> str:
    return html.escape(str(value), quote=True)


@lru_cache(maxsize=1)
def _daily_loader():
    """按文件路径加载日课加载器，避免把 apps/daily 塞进 sys.path。"""
    spec = importlib.util.spec_from_file_location("bookcorpus_daily_feed", DAILY_FEED)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"找不到日课加载器：{DAILY_FEED}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_feed


def build_overview(library, root, now=None, feed=None) -> dict[str, Any]:
    """读一次书目与日课，返回模板所需的普通值。"""
    if now is None:
        today = dt.date.today()
    elif isinstance(now, dt.datetime):
        today = now.date()
    elif isinstance(now, dt.date):
        today = now
    else:
        raise TypeError("now 必须是 date、datetime 或 None。")

    books = library.books()
    readable = _count(len(books))
    chapters = sum(_count(book.get("chapters")) for book in books)
    chars = sum(_count(book.get("chars")) for book in books)

    payload = feed if feed is not None else _daily_loader()(Path(root))
    if not isinstance(payload, dict):
        payload = {}
    issues = payload.get("issues")
    if not isinstance(issues, list):
        issues = []
    issue = next((item for item in issues if isinstance(item, dict)), None)
    errors = payload.get("errors")
    if not isinstance(errors, list):
        errors = []

    return {
        "today": today.strftime("%Y.%m.%d"),
        "today_iso": today.isoformat(),
        "weekday": WEEKDAYS[today.weekday()],
        "readable_books": readable,
        "chapters": chapters,
        "chars": chars,
        "issue_count": len(issues),
        "issue": issue,
        "feed_errors": errors,
    }


def _aside_note(issue, today_iso: str) -> str:
    if not issue:
        return "日课生成后，这里会更新。"
    if str(issue.get("date") or "") == today_iso:
        return "不知道读什么的时候，从今天这一期开始。"
    return "不知道读什么的时候，从最近这一期开始。"


def _daily_card(issue, today_display: str, today_iso: str, daily_url: str) -> str:
    link = _url(daily_url)
    if not issue:
        return (
            '<article class="slip is-empty">'
            '<div class="slip-head"><span>今日日课</span><span class="issue">尚无</span></div>'
            '<p class="kicker">日课还没有生成</p>'
            "<h2>今天还没有日课</h2>"
            '<p class="excerpt">等今天的这一期写出来，这里会出现一章和一段话。</p>'
            f'<a class="read" href="{link}" target="_blank" rel="noopener">'
            '去日课页看看 <span aria-hidden="true">→</span></a>'
            f'<div class="slip-foot"><span>{_text(today_display)}</span><span>每日一章</span></div>'
            "</article>"
        )

    book = issue.get("book") if isinstance(issue.get("book"), dict) else {}
    chapter = issue.get("chapter") if isinstance(issue.get("chapter"), dict) else {}
    quote = issue.get("quote") if isinstance(issue.get("quote"), dict) else {}

    book_title = str(book.get("title") or "").strip() or "书库"
    if not book_title.startswith("《"):
        book_title = f"《{book_title}》"
    chapter_title = str(chapter.get("title") or "").strip()
    kicker = f"{book_title} · {chapter_title}" if chapter_title else book_title

    issue_date = str(issue.get("date") or "").strip()
    issue_number = _count(issue.get("issue"))
    issue_label = f"第 {issue_number:03d} 期" if issue_number else "日课"
    slip_label = "今日日课" if issue_date == today_iso else "最近一期"
    display_date = issue_date.replace("-", ".") if issue_date else today_display

    headline = str(issue.get("headline") or "").strip() or "今天这一期"
    quote_text = str(quote.get("text") or "").strip()
    if quote_text and not quote_text.startswith(("「", "“", "『")):
        quote_text = f"「{quote_text}」"
    quote_html = f'<blockquote class="quote">{_text(quote_text)}</blockquote>' if quote_text else ""

    body = issue.get("body")
    excerpt = ""
    if isinstance(body, list) and body:
        excerpt = str(body[0]).strip()
    if not excerpt:
        excerpt = "这一期没有摘要。"

    return (
        '<article class="slip">'
        f'<div class="slip-head"><span>{_text(slip_label)}</span>'
        f'<span class="issue">{_text(issue_label)}</span></div>'
        f'<p class="kicker">{_text(kicker)}</p>'
        f"<h2>{_text(headline)}</h2>"
        f"{quote_html}"
        f'<p class="excerpt">{_text(excerpt)}</p>'
        f'<a class="read" href="{link}" target="_blank" rel="noopener">'
        '读这一期 <span aria-hidden="true">→</span></a>'
        f'<div class="slip-foot"><span>{_text(display_date)}</span><span>每日一章</span></div>'
        "</article>"
    )


def render_page(template: str, overview: dict[str, Any], urls=None) -> str:
    """把 {{KEY}} 一次替换完；替换进去的内容不会再被扫描。"""
    merged = dict(DEFAULT_URLS)
    if urls:
        merged.update({key: str(value) for key, value in urls.items()})
    issue = overview.get("issue")
    today_iso = str(overview.get("today_iso") or "")
    today_display = str(overview.get("today") or "")
    values = {
        "TODAY": _text(today_display),
        "WEEKDAY": _text(overview.get("weekday") or ""),
        "READABLE": f"{_count(overview.get('readable_books')):,}",
        "ISSUE_COUNT": f"{_count(overview.get('issue_count')):,}",
        "SEARCH_URL": _url(merged["search"]),
        "READING_URL": _url(merged["reading"]),
        "DAILY_URL": _url(merged["daily"]),
        "OPS_URL": _url(merged["ops"]),
        "DAILY_CARD": _daily_card(issue, today_display, today_iso, merged["daily"]),
        "ASIDE_NOTE": _text(_aside_note(issue, today_iso)),
    }

    def replace(match):
        key = match.group(1)
        if key not in values:
            raise ValueError(f"模板占位符没有值：{key}")
        return values[key]

    return PLACEHOLDER.sub(replace, template)
