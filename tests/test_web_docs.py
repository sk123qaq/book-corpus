"""前端「文档转 md」卡片（多格式来源支持 T3）的静态断言。

前端没有构建步骤、也没有 JS 测试框架，所以照 tests/test_layout_literals.py 的做法
直接读 app.js：标签、白名单、调用路径写错一个字符，页面就静悄悄地不动了。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "ops" / "web" / "app.js"

ACCEPT = ".epub,.pdf,.mobi,.azw3,.azw,.txt,.md,.docx,.xlsx,.xls,.pptx"
REASONS = (
    "too-large",
    "timeout",
    "convert-error",
    "empty-output",
    "source-missing",
    "source-changed",
    "venv-missing",
    "unsupported",
)


def app_js() -> str:
    return APP.read_text(encoding="utf-8")


def docs_card() -> str:
    """docsCard() 的函数体：卡片上的东西都在这里断言。"""
    src = app_js()
    start = src.index("function docsCard()")
    end = src.index("async function submitDocs(", start)
    return src[start:end]


def test_kind_word_has_docs_run() -> None:
    assert "'docs.run': '文档转 md'" in app_js()


def test_upload_accepts_only_whitelist() -> None:
    src = app_js()
    assert f'accept="{ACCEPT}"' in src
    # 拖拽区的提示也要跟上，别让人拖 .doc 进来才发现不收
    assert "不收 .doc / .ppt / .csv / .djvu" in src
    assert "DJVU" not in src.split('accept="')[-1]


def test_upload_shows_server_reason() -> None:
    src = app_js()
    # 服务端 400 的中文原因要露出来，不能只弹「上传失败 HTTP 400」
    assert "bad.push(file.name + '：' + why)" in src
    assert "个没上传成功：" in src


def test_card_hidden_when_idle() -> None:
    body = docs_card()
    # 没任务、也没有待补转的书：整卡不出现
    assert "if (!body && !pending) return '';" in body
    assert 'id="docs-card"' in body


def test_card_state_and_progress() -> None:
    body = docs_card()
    for word in ("排队", "转换中", "完成", "失败", "跳过"):
        assert word in app_js()  # 词表在模块级的 DOCS_STATE / DOCS_ITEM 里
    assert 'class="progress"' in body  # 复用现有进度条样式
    assert "本次新书" in body
    assert "库内补转" in body
    assert "scope" in body
    assert "prog.current" in body


def test_card_failure_table_and_buttons() -> None:
    body = docs_card()
    assert 'class="pill ' in body
    assert 'data-act="docs-retry"' in body
    assert "data-uid=" in body
    assert 'data-write data-act="docs-retry"' in body.replace("\n        ", " ")
    assert 'data-write data-act="docs-backfill"' in body
    # 两个按钮都走写路由
    assert "/api/docs/run" in app_js()


def test_retry_and_backfill_post_to_run() -> None:
    src = app_js()
    assert "post('/api/docs/run'" in src
    assert "uids: [uid]" in src
    assert "submitDocs({}" in src


def test_live_slot_and_cheap_polling() -> None:
    src = app_js()
    assert '[data-live="docs"]' in src
    assert "${docsCard()}" in src
    # 运行中 2.5 秒重绘只换卡片，别整页刷
    assert "function paintDocs()" in src
    assert "if (sig === S.docsSig) return;" in src
    # 没任务时缓存「没有 docs 任务」，不每跳打 404
    assert "if (!target && S.docsNone) return;" in src
    assert "S.docsNone = true;" in src


def test_session_job_and_import_handoff() -> None:
    src = app_js()
    assert "bookcorpus.docs-job" in src
    assert "sessionStorage" in src
    assert "detail.docs_job" in src
    assert "已自动接转文档 md" in src
    assert 'data-act="docs-goto"' in src


def test_expand_state_survives_repaint() -> None:
    src = app_js()
    assert 'data-docs="items"' in src
    assert "S.docsOpen = !!el.open;" in src
    assert "S.docsOpen ? ' open' : ''" in src


def test_reason_words_cover_backend_enum() -> None:
    src = app_js()
    start = src.index("const DOCS_REASON_WORD")
    table = src[start : src.index("};", start)]
    for code in REASONS:
        assert f"'{code}'" in table


def test_template_literals_balanced() -> None:
    # 粗检：反引号成对，括号配平（node --check 是主检，这里只是别让 diff 逃过测试）
    src = app_js()
    assert src.count("`") % 2 == 0
    assert src.count("{") == src.count("}")
