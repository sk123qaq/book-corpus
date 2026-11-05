import datetime as dt
import json
import stat

import pytest

from bookcorpus import daily as D


def write_book(root, uid="B0001", title="甲书", author="某人", subjects=("文学",), chapters=4, size=3000):
    folder = root / "books" / uid
    (folder / "md").mkdir(parents=True)
    meta = {"uid": uid, "title": title, "author": author, "subjects": list(subjects)}
    (folder / "book.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    body = "这是第1章的正文。" + "正文" * (size // 6)
    for number in range(1, chapters + 1):
        text = f"这是第{number}章的正文。\n" + body
        (folder / "md" / f"{number:02d}_第{number}章.md").write_text(text, encoding="utf-8")
    return folder


def write_issue(root, day, uid, kind):
    directory = root / "日课"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (day.isoformat() + ".json")).write_text(
        json.dumps({"date": day.isoformat(), "kind": kind, "book": {"uid": uid}}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_cn_number():
    assert D.cn_number("12") == 12
    assert D.cn_number("九") == 9
    assert D.cn_number("十二") == 12
    assert D.cn_number("二十五") == 25
    assert D.cn_number("一百") == 100
    assert D.cn_number("甲") is None


def test_parse_chapter_label_and_title(tmp_path):
    root = tmp_path / "ws"
    path = root / "books" / "B0003" / "md" / "04_第2章机会成本和禀赋效应.md"
    path.parent.mkdir(parents=True)
    path.write_text("正文", encoding="utf-8")
    chapter = D.parse_chapter(path, root)
    assert chapter.order == 4
    assert chapter.index == 2
    assert chapter.label == "第2章"
    assert chapter.title == "机会成本和禀赋效应"
    assert chapter.path == "books/B0003/md/04_第2章机会成本和禀赋效应.md"


def test_short_author_drops_download_site():
    assert D.short_author("z-library.sk, 1lib.sk, z-lib.sk") == "佚名"
    assert D.short_author("[美] 理查德・塞勒 (Richard H. Thaler) 著  王晋 译") == "理查德・塞勒"


def test_find_books_skips_broken_and_bundle_splits(tmp_path):
    root = tmp_path / "ws"
    write_book(root, uid="B0001", chapters=4, size=3000)

    broken = write_book(root, uid="B0002", chapters=10, size=3000)
    files = sorted((broken / "md").glob("*.md"))
    for index, file in enumerate(files):
        file.write_text(("短" * 200) if index < 7 else ("正" * 1500), encoding="utf-8")

    bundle = root / "books" / "B0003"
    (bundle / "md").mkdir(parents=True)
    (bundle / "book.json").write_text(json.dumps({"uid": "B0003", "title": "合集"}), encoding="utf-8")
    for number in range(1, 9):
        size = 200_000 if number <= 5 else 30_000
        (bundle / "md" / f"{number:02d}_第{number}章.md").write_text("长" * size, encoding="utf-8")

    (root / "books" / "B0001" / "md" / "README.md").write_text("不是章节", encoding="utf-8")

    books = D.find_books(root)
    assert [book.uid for book in books] == ["B0001"]
    assert len(books[0].chapters) == 4


def test_select_avoids_recent_book(tmp_path):
    root = tmp_path / "ws"
    write_book(root, uid="B0001", subjects=("文学",), chapters=4)
    write_book(root, uid="B0002", subjects=("人文社科",), chapters=4)
    today = dt.date(2026, 11, 6)
    write_issue(root, today - dt.timedelta(days=1), "B0002", "expository")
    book, _ = D.select(root, today)
    assert book.uid == "B0001"


def test_select_is_stable_for_a_day(tmp_path):
    root = tmp_path / "ws"
    write_book(root, uid="B0001", chapters=4)
    write_book(root, uid="B0002", subjects=("人文社科",), chapters=4)
    today = dt.date(2026, 11, 6)
    first = D.select(root, today)
    second = D.select(root, today)
    assert (first[0].uid, first[1].order) == (second[0].uid, second[1].order)


def make_chapter(tmp_path):
    path = tmp_path / "ws" / "books" / "B0001" / "md" / "01_第一章.md"
    path.parent.mkdir(parents=True)
    text = "第一行\n第二行\n他说：这是原文引文。\n第四行\n"
    path.write_text(text, encoding="utf-8")
    chapter = D.parse_chapter(path, tmp_path / "ws")
    chapter.text = text
    return chapter


def valid_payload():
    return {
        "kind": "fiction",
        "subject": "小说",
        "headline": "一句话的引文",
        "body": ["字" * 100, "字" * 100, "字" * 100],
        "quote": {"text": "这是原文引文。", "line": 99},
        "why": "这一章有一个具体场面，适合讲清楚。",
        "research": {"chapters": "第1到3章", "web": 0, "queries": [], "note": ""},
    }


def test_check_accepts_and_recomputes_line(tmp_path):
    chapter = make_chapter(tmp_path)
    issue, errors = D.check(valid_payload(), chapter)
    assert errors == []
    assert issue["quote"]["line"] == 3
    assert issue["kindLabel"] == "小说"
    assert D.char_count(issue["body"]) == 300


def test_check_rejects_short_body_and_missing_quote(tmp_path):
    chapter = make_chapter(tmp_path)
    payload = valid_payload()
    payload["body"] = ["太短", "也短"]
    payload["quote"]["text"] = "原文里没有这句话"
    issue, errors = D.check(payload, chapter)
    assert issue is None
    assert any("正文" in error for error in errors)
    assert any("找不到" in error for error in errors)


def test_timer_units_are_valid_units(tmp_path):
    units = D.timer_units(tmp_path, tmp_path / "ws", at="07:30")
    assert "OnCalendar=*-*-* 07:30:00" in units["timer"]
    assert "Persistent=true" in units["timer"]
    assert "daily --root" in units["service"]
    with pytest.raises(D.DailyError):
        D.timer_units(tmp_path, tmp_path / "ws", at="7:30")


def test_generate_with_fake_pi(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    write_book(root, uid="B0001", chapters=4, size=3000)
    chapter_file = root / "books" / "B0001" / "md" / "01_第1章.md"
    chapter_file.write_text("第一行\n这是原文引文。\n第三行\n" + "正文" * 1000, encoding="utf-8")

    fake = tmp_path / "fake_pi.py"
    fake.write_text(
        "import json, os\n"
        "print(json.dumps({\n"
        "  'kind': 'fiction',\n"
        "  'subject': '小说',\n"
        "  'headline': '一句话的引文',\n"
        "  'body': ['字' * 100, '字' * 100, '字' * 100],\n"
        "  'quote': {'text': '这是原文引文。', 'line': 1},\n"
        "  'why': '这一章有一个具体场面，适合讲清楚。',\n"
        "  'research': {'chapters': '第1到3章', 'web': 0, 'queries': [], 'note': ''},\n"
        "}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PI_BIN", str(fake))
    monkeypatch.delenv("BOOKS_MODEL", raising=False)
    monkeypatch.delenv("BOOKS_MODEL_PROVIDER", raising=False)

    day = dt.date(2026, 11, 6)
    target = D.generate(root, day=day, chapter_no=1)
    assert target == root / "日课" / "2026-11-06.json"
    issue = json.loads(target.read_text(encoding="utf-8"))
    assert issue["book"]["uid"] == "B0001"
    assert issue["quote"]["line"] == 2
    assert issue["attempts"] == 1
    assert issue["date"] == "2026-11-06"
    assert D.char_count(issue["body"]) == 300
