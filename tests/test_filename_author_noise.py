"""文件名末尾下载站/镜像站域名噪声剥离与作者/书名解析回归测试。"""

import json
import sys
from pathlib import Path

import pytest

from conftest import run_cli

HERE = Path(__file__).resolve().parents[1]
SCRIPTS = HERE / "src" / "bookcorpus" / "scripts"
SRC = HERE / "src"
for p in (str(SCRIPTS), str(SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from bookcorpus.reading.library import _clean_author as reading_clean_author
from bookcorpus.scripts import ingest, lib_common


def test_real_sample_zlibrary_filename_extracts_author_and_clean_title():
    name = (
        "孤独社会  即将到来的第五消费时代 = 永続孤独社会  分断か、つながりか "
        "([日] 三浦展 著  谢文博 译) (z-library.sk, 1lib.sk, z-lib.sk).epub"
    )
    title, author = ingest.parse_name(name)
    assert title == "孤独社会  即将到来的第五消费时代 = 永続孤独社会  分断か、つながりか"
    assert author == "[日] 三浦展 著  谢文博 译"
    assert "z-library" not in title.lower()
    assert "z-library" not in author.lower()


@pytest.mark.parametrize(
    "tag",
    [
        "(z-library.sk, 1lib.sk, z-lib.sk)",
        "（z-library.sk，1lib.sk，z-lib.sk）",
        "(Z-Library)",
        "(z-lib.org)",
        "(1lib.sk)",
        "(z-lib.sk)",
        "(z-lib.rs)",
        "(z-lib.gs)",
        "(z-lib.id)",
        "(singlelogin.re)",
        "(b-ok.cc)",
        "(b-ok.org)",
        "（Z-Library）",
    ],
)
def test_parse_name_strips_download_site_noise_without_author(tag):
    title, author = ingest.parse_name(f"样书名 {tag}.epub")
    assert title == "样书名"
    assert author == ""


def test_parse_name_strips_stacked_download_site_tags_and_keeps_author():
    title, author = ingest.parse_name("测试书名 (张三 著) (z-lib.rs)（Z-Library, b-ok.cc）.epub")
    assert title == "测试书名"
    assert author == "张三 著"

    title2, author2 = ingest.parse_name("只有噪声 (z-lib.org) (singlelogin.re) (1lib.sk).epub")
    assert title2 == "只有噪声"
    assert author2 == ""


def test_parse_name_preserves_normal_brackets_and_subtitles():
    assert ingest.parse_name("计算机系统 (第2版).epub") == ("计算机系统 (第2版)", "")
    assert ingest.parse_name("现代汉语 (修订本) (z-library.sk, 1lib.sk, z-lib.sk).epub") == ("现代汉语 (修订本)", "")
    assert ingest.parse_name("社会学导论（副标题说明） (李四 译) (Z-Library).epub") == (
        "社会学导论（副标题说明）",
        "李四 译",
    )
    assert ingest.parse_name("东方快车谋杀案 (阿加莎·克里斯蒂) (z-lib.org).epub") == (
        "东方快车谋杀案",
        "阿加莎·克里斯蒂",
    )
    assert ingest.parse_name("压缩算法导论 (zlib).epub") == ("压缩算法导论 (zlib)", "")


def test_lib_common_and_reading_share_download_site_noise_rules():
    sample = "测试作品 (山田太郎 著) (z-lib.rs, singlelogin.re) (Z-Library).epub"
    assert lib_common.clean_title(sample) == "测试作品"
    assert lib_common.author_guess(sample, "测试作品") == "山田太郎"
    assert lib_common.author_guess("只有书名 (z-library.sk, 1lib.sk, z-lib.sk).epub", "只有书名") == ""
    assert lib_common.clean_author("z-library.sk, 1lib.sk, z-lib.sk") == ""
    assert lib_common.clean_author("b-ok.org") == ""
    assert reading_clean_author("z-library.sk, 1lib.sk, z-lib.sk") == ""
    assert reading_clean_author("z-lib.org, singlelogin.re, b-ok.cc") == ""
    assert reading_clean_author("[日] 三浦展 著  谢文博 译") == "[日] 三浦展 著 谢文博 译"


def test_ingest_scan_end_to_end_extracts_clean_author_for_zlibrary_file(workspace):
    inbox = workspace / "books" / "_收件箱"
    inbox.mkdir(parents=True, exist_ok=True)
    fname = (
        "孤独社会  即将到来的第五消费时代 = 永続孤独社会  分断か、つながりか "
        "([日] 三浦展 著  谢文博 译) (z-library.sk, 1lib.sk, z-lib.sk).epub"
    )
    (inbox / fname).write_bytes(b"synthetic-epub-bytes" * 20)

    r = run_cli("--root", workspace, "run", "ingest", "scan", "--json", "--no-classify")
    assert r.returncode == 0, r.stdout + r.stderr
    items = json.loads(r.stdout)
    row = next(i for i in items if i["file"] == fname)
    assert row["action"] == "add"
    assert row["title"] == "孤独社会  即将到来的第五消费时代 = 永続孤独社会  分断か、つながりか"
    assert row["author"] == "[日] 三浦展 著  谢文博 译"
