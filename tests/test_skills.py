"""技能目录解析：工作区没有 .agents/skills 时，源码仓那份要能兜住。"""

import os

from bookcorpus.scripts import lib_common as L


def test_repo_skills_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "SKILLS", str(tmp_path / "no-skills"))
    path = L.skill_dir("ebook-to-md")
    assert os.path.isdir(path)
    assert os.path.isfile(os.path.join(path, "scripts", "ebook_to_md.py"))


def test_missing_skill_returns_workspace_path(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "SKILLS", str(tmp_path / "no-skills"))
    assert L.skill_dir("no-such-skill") == str(tmp_path / "no-skills" / "no-such-skill")
