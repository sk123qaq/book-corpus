"""`book-corpus docs check` 的契约与失败路径。

每例都在临时仓里造最小的代码 + 文档，不碰真仓库。
"""

from pathlib import Path

from bookcorpus import cli, docfacts


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src/bookcorpus/scripts").mkdir(parents=True)
    (repo / "src/bookcorpus/__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.11"\n', encoding="utf-8")
    (repo / "src/bookcorpus/scripts/alpha.py").write_text('"""Alpha 脚本。"""\n', encoding="utf-8")
    (repo / "apps/demo").mkdir(parents=True)
    (repo / "apps/demo/server.py").write_text(
        'import argparse\np = argparse.ArgumentParser()\np.add_argument("--port", type=int, default=9999)\n',
        encoding="utf-8",
    )
    (repo / "docs/adr").mkdir(parents=True)
    (repo / "docs/design").mkdir(parents=True)
    (repo / "docs/briefs").mkdir(parents=True)
    (repo / "docs/design/a.md").write_text(
        "# A\n\n状态：implemented · 2026-01-01\n验收：`pytest -q`\n", encoding="utf-8"
    )
    (repo / "docs/design/b.md").write_text("# B\n\n状态：draft · 2026-01-02\n", encoding="utf-8")
    (repo / "docs/design/c.md").write_text(
        "# C\n\n状态：superseded · 2026-01-03\n替代：`docs/design/a.md`\n", encoding="utf-8"
    )
    (repo / "README.md").write_text("# R\n\n服务在 `127.0.0.1:9999`。\n", encoding="utf-8")
    (repo / "CONTRIBUTING.md").write_text("# C\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# A\n", encoding="utf-8")
    (repo / "GLOSSARY.md").write_text("# G\n", encoding="utf-8")
    docfacts.write(repo)
    return repo


def test_generated_repo_is_clean(tmp_path):
    repo = _repo(tmp_path)
    report = docfacts.check_repo(repo)
    assert report.fails == [], report.fails
    assert "9.9.9" in (repo / docfacts.FACTS).read_text(encoding="utf-8")


def test_facts_detect_port_drift(tmp_path):
    repo = _repo(tmp_path)
    (repo / "apps/demo/server.py").write_text(
        'import argparse\np = argparse.ArgumentParser()\np.add_argument("--port", type=int, default=9998)\n',
        encoding="utf-8",
    )
    report = docfacts.check_repo(repo)
    assert any("facts" in f and "9999" in f for f in report.fails), report.fails
    docfacts.write(repo)
    assert any("ports" in f for f in docfacts.check_repo(repo).fails)
    (repo / "README.md").write_text("# R\n\n服务在 `127.0.0.1:9998`。\n", encoding="utf-8")
    assert docfacts.check_repo(repo).fails == []


def test_unknown_status_value_is_reported(tmp_path):
    repo = _repo(tmp_path)
    (repo / "docs/design/b.md").write_text("# B\n\n状态：done · 2026-01-02\n", encoding="utf-8")
    report = docfacts.check_repo(repo)
    assert any("b.md" in f and "状态值不在枚举里" in f for f in report.fails), report.fails


def test_implemented_requires_acceptance(tmp_path):
    repo = _repo(tmp_path)
    (repo / "docs/design/a.md").write_text("# A\n\n状态：implemented · 2026-01-01\n", encoding="utf-8")
    assert any("验收" in f for f in docfacts.check_repo(repo).fails)


def test_superseded_requires_existing_target(tmp_path):
    repo = _repo(tmp_path)
    (repo / "docs/design/c.md").write_text("# C\n\n状态：superseded · 2026-01-03\n", encoding="utf-8")
    assert any("替代" in f for f in docfacts.check_repo(repo).fails)
    (repo / "docs/design/c.md").write_text(
        "# C\n\n状态：superseded · 2026-01-03\n替代：`docs/design/gone.md`\n", encoding="utf-8"
    )
    assert any("替代目标不存在" in f for f in docfacts.check_repo(repo).fails)


def test_retired_path_exemptions(tmp_path):
    repo = _repo(tmp_path)
    body = "# C\n\n旧脚本在 `工作区/脚本/x.py`。\n"
    (repo / "CONTRIBUTING.md").write_text(body, encoding="utf-8")
    assert any("工作区/脚本/" in f for f in docfacts.check_repo(repo).fails)
    (repo / "CONTRIBUTING.md").write_text("状态：historical · 2026-01-01\n\n" + body, encoding="utf-8")
    assert docfacts.check_repo(repo).fails == []
    # 布局说明不再豁免检查，避免 draft 文档被悄悄跳过
    (repo / "CONTRIBUTING.md").write_text("> 布局说明：历史写法。\n\n" + body, encoding="utf-8")
    assert any("工作区/脚本/" in f for f in docfacts.check_repo(repo).fails)


def test_status_scope_living_and_reports(tmp_path):
    repo = _repo(tmp_path)
    (repo / "docs/sub").mkdir(parents=True)
    (repo / "docs/sub/guide.md").write_text("# 指南\n\n状态：living · 2026-01-04\n", encoding="utf-8")
    (repo / "reports").mkdir(parents=True)
    (repo / "reports/audit.md").write_text("# 审计\n\n正文。\n", encoding="utf-8")
    fails = docfacts.check_status(repo)
    assert any("guide.md" in f and "失效源" in f for f in fails), fails
    assert any("audit.md" in f and "缺 `状态：" in f for f in fails), fails

    (repo / "docs/sub/guide.md").write_text(
        "# 指南\n\n状态：living · 2026-01-04\n失效源：apps/demo/**\n", encoding="utf-8"
    )
    (repo / "reports/audit.md").write_text("# 审计\n\n状态：historical · 2026-01-04\n", encoding="utf-8")
    docfacts.write(repo)
    report = docfacts.check_repo(repo)
    assert report.fails == [], report.fails
    index_text = (repo / docfacts.INDEX).read_text(encoding="utf-8")
    assert "[reports/audit.md](../reports/audit.md)" in index_text


def test_changed_files_tracks_living_docs_and_known_reds(tmp_path):
    repo = _repo(tmp_path)
    (repo / "README.md").write_text(
        "# R\n\n<!-- 失效源：src/bookcorpus/cli.py -->\n\n服务在 `127.0.0.1:9999`。\n", encoding="utf-8"
    )
    feat_dir = repo / ".agents/skills/verify-book-corpus/features"
    feat_dir.mkdir(parents=True)
    (feat_dir / "layered-reads.md").write_text("# 分层读\n\n失效源：apps/reading/**\n", encoding="utf-8")
    (feat_dir / "README.md").write_text(
        "# 验证地图\n\n| 条目 | 现状 | 失效源 |\n|---|---|---|\n"
        "| `shot_ops.py` 死在 RETIRE_MODAL | 漂移 | `tests/manual/ops/shot_ops.py` |\n"
        "| `shot.py` 写死 chrome | 缺浏览器 | `tests/manual/demo/shot.py` |\n"
        "| README 仍旧写法 | 缺 uv run | `README.md`, `apps/demo/README.md` |\n",
        encoding="utf-8",
    )
    warns = docfacts.check_changed_files(
        repo,
        {
            "apps/reading/server.py",
            "tests/manual/ops/shot_ops.py",
            "tests/manual/demo/shot.py",
            "README.md",
        },
    )
    assert any("layered-reads.md" in w for w in warns), warns
    assert any("shot_ops.py 死在 RETIRE_MODAL" in w for w in warns), warns
    assert any("shot.py 写死 chrome" in w for w in warns), warns
    assert any("README 仍旧写法" in w for w in warns), warns
    # 同一改动同时改了文档本身时，不再重复提醒该文档
    warns_synced = docfacts.check_changed_files(
        repo,
        {"apps/reading/server.py", ".agents/skills/verify-book-corpus/features/layered-reads.md"},
    )
    assert not any("layered-reads.md" in w for w in warns_synced), warns_synced


def test_links_skip_fences_and_workspace_paths(tmp_path):
    repo = _repo(tmp_path)
    (repo / "CONTRIBUTING.md").write_text("[a](工作区/批次/说明.md)\n", encoding="utf-8")
    assert docfacts.check_repo(repo).fails == []
    (repo / "CONTRIBUTING.md").write_text("```\n[a](nope.md)\n```\n", encoding="utf-8")
    assert docfacts.check_repo(repo).fails == []
    (repo / "CONTRIBUTING.md").write_text("[a](docs/nope.md)\n", encoding="utf-8")
    assert any("链接目标不存在" in f for f in docfacts.check_repo(repo).fails)


def test_cli_exit_code(tmp_path):
    repo = _repo(tmp_path)
    assert cli.main(["docs", "check", "--repo", str(repo)]) == 0
    (repo / "apps/demo/server.py").write_text("x = 1\n", encoding="utf-8")
    assert cli.main(["docs", "check", "--repo", str(repo)]) == 1
