"""活文档同步门禁（retired.toml、ops 路由、docs-coupling）测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_tool(name: str):
    path = REPO_ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"tool_{name}", path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_retired_terms_catches_legacy_glossary_and_allows_exemptions(tmp_path: Path):
    sync = _load_tool("check_docs_sync")
    (tmp_path / "docs/reference").mkdir(parents=True)
    (tmp_path / "docs/reference/retired.toml").write_text(
        (REPO_ROOT / "docs/reference/retired.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    # 真实历史反例：旧 GLOSSARY.md 的典型写法必须被拦住
    (tmp_path / "GLOSSARY.md").write_text(
        "# 视觉读本 术语表\n\n路由 `/reader/<uid>/<n>/edition`。\n\n**读法（reading mode）**\n书级默认。\n",
        encoding="utf-8",
    )
    # 含退役/404 豁免词与外部 calibre 路径不应误报
    (tmp_path / "README.md").write_text(
        "# 书库\n\n"
        "- 视觉读本已退役，`/reader/` 路由一律 404。\n"
        "- 外部路径 `calibre/ebooks/mobi/reader/mobi6.py` 正常。\n",
        encoding="utf-8",
    )
    # 冻结的历史文档不报，但仅带「布局说明」的 draft 文档照常拦截
    (tmp_path / "docs/design").mkdir(parents=True)
    (tmp_path / "docs/design/old.md").write_text(
        "# 旧\n\n状态：historical · 2026-10-01\n\n视觉读本在 /reader/。\n", encoding="utf-8"
    )
    (tmp_path / "docs/design/draft.md").write_text(
        "# 草稿\n\n> 布局说明：旧写法。\n\n状态：draft · 2026-10-01\n\n视觉读本。\n", encoding="utf-8"
    )

    problems = sync.check_retired_terms(tmp_path)
    assert any("GLOSSARY.md:1" in p and "视觉读本" in p for p in problems), problems
    assert any("GLOSSARY.md:3" in p and "/reader/" in p for p in problems), problems
    assert any("GLOSSARY.md:5" in p and "读法（reading mode）" in p for p in problems), problems
    assert any("docs/design/draft.md" in p and "视觉读本" in p for p in problems), problems
    assert not any("README.md" in p for p in problems), problems
    assert not any("docs/design/old.md" in p for p in problems), problems


def test_ops_routes_check_uses_ops_readme(tmp_path: Path):
    sync = _load_tool("check_docs_sync")
    (tmp_path / "apps/ops").mkdir(parents=True)
    (tmp_path / "apps/ops/server.py").write_text('ROUTES = ["/api/status", "/api/sync/reconcile"]\n', encoding="utf-8")
    (tmp_path / "apps/ops/README.md").write_text(
        "# Ops\n\n```text\nGET  /api/status\nPOST /api/sync/reconcile\nPOST /api/ghost\n```\n",
        encoding="utf-8",
    )
    problems = sync.check_ops_contract(tmp_path)
    assert len(problems) == 1
    assert "/api/ghost" in problems[0]


def test_docs_coupling_flags_reading_feature_map(capsys):
    coupling = _load_tool("check_docs_coupling")
    rc_warn = coupling.run(REPO_ROOT, files=["apps/reading/server.py"], strict=False)
    out = capsys.readouterr().out
    assert rc_warn == 0
    assert ".agents/skills/verify-book-corpus/features/layered-reads.md" in out

    rc_strict = coupling.run(REPO_ROOT, files=["apps/reading/server.py"], strict=True)
    assert rc_strict == 1


def test_skills_inventory_checks_usage_guide(tmp_path: Path):
    sync = _load_tool("check_docs_sync")
    for name in ("alpha-skill", "beta-skill"):
        skill_dir = tmp_path / ".agents/skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")

    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "docs/使用指南.md").write_text(
        "# 使用指南\n\n| 技能 | 路径 | 谁在用 |\n|---|---|---|\n"
        "| `alpha-skill` | `.agents/skills/alpha-skill/` | 测试 |\n"
        "| `ghost-skill` | `.agents/skills/ghost-skill/` | 测试 |\n",
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text(
        "| `.agents/skills/` | `alpha-skill`、`beta-skill` |\n",
        encoding="utf-8",
    )

    problems = sync.check_skills_inventory(tmp_path)
    assert any("docs/使用指南.md" in p and "beta-skill" in p for p in problems), problems
    assert any("docs/使用指南.md" in p and "ghost-skill" in p for p in problems), problems
    assert not any("AGENTS.md" in p for p in problems), problems
