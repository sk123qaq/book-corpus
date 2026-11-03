import json
import os

from conftest import run_cli


def _root(tmp_path):
    root = tmp_path / "ws"
    (root / "工作区").mkdir(parents=True)
    (root / "books.toml").write_text('schema = 2\ntool = "book-corpus test"\n', encoding="utf-8")
    return root


def test_load_falls_back_to_builtin_default(tmp_path, monkeypatch):
    from bookcorpus import pimodel

    for name in ("BOOKS_MODEL", "BOOKS_MODEL_PROVIDER", "BOOKS_THINKING"):
        monkeypatch.delenv(name, raising=False)
    cfg = pimodel.load(str(_root(tmp_path)))
    assert cfg["ref"] == "opencode-go/deepseek-v4.1-flash"
    assert cfg["values"]["thinking"] == "high"
    assert cfg["sources"]["model"] == "内置默认"


def test_env_overrides_file(tmp_path, monkeypatch):
    from bookcorpus import pimodel

    root = _root(tmp_path)
    pimodel.save(str(root), {"provider": "acme", "model": "m1", "thinking": "low"})
    monkeypatch.setenv("BOOKS_MODEL", "m2")
    cfg = pimodel.load(str(root))
    assert cfg["ref"] == "acme/m2"
    assert cfg["values"]["thinking"] == "low"
    assert cfg["sources"]["model"] == "环境变量 BOOKS_MODEL"


def test_save_keeps_other_keys_and_skips_empty(tmp_path):
    from bookcorpus import pimodel

    root = _root(tmp_path)
    pimodel.save(str(root), {"provider": "acme", "model": "m1", "base_url": "http://x/v1",
                             "api_key_env": "ACME_KEY"})
    pimodel.save(str(root), {"base_url": ""})
    text = (root / "books.toml").read_text(encoding="utf-8")
    assert "schema = 2" in text and "tool = " in text
    assert "base_url" not in text and "$" not in text
    assert "[model]" in text and 'model = "m1"' in text


def test_model_flags_order_and_api_key(tmp_path, monkeypatch):
    from bookcorpus import pimodel

    root = _root(tmp_path)
    pimodel.save(str(root), {"provider": "acme", "model": "m1", "thinking": "medium",
                             "api_key_env": "ACME_KEY"})
    monkeypatch.setenv("ACME_KEY", "sk-test")
    cfg = pimodel.load(str(root))
    flags = pimodel.model_flags(cfg["values"])
    assert flags == ["--thinking", "medium", "--model", "acme/m1", "--api-key", "sk-test"]
    argv = pimodel.argv(cfg["values"], "PROMPT")
    assert argv[-1] == "PROMPT" and "--model" in argv


def test_thinking_only_accepts_levels(tmp_path):
    from bookcorpus import pimodel

    cfg = pimodel.load(str(_root(tmp_path)))
    assert pimodel.thinking(cfg["values"], "MAX") == "max"
    assert pimodel.thinking(cfg["values"], "nonsense") == "high"


def test_setup_pi_writes_models_json_with_backup(tmp_path, monkeypatch):
    from bookcorpus import pimodel

    path = tmp_path / "agent" / "models.json"
    monkeypatch.setattr(pimodel, "PI_CONFIG", str(path))
    values = {"provider": "acme", "model": "acme/m1", "base_url": "http://x/v1",
              "api_key_env": "ACME_KEY", "api": ""}
    changed, detail = pimodel.setup_pi(values)
    assert changed, detail
    first = json.loads(path.read_text(encoding="utf-8"))
    entry = first["providers"]["acme"]
    assert entry["baseUrl"] == "http://x/v1"
    assert entry["apiKey"] == "$ACME_KEY"
    assert entry["models"] == [{"id": "m1"}]
    assert entry["api"] == "openai-completions"
    changed, _ = pimodel.setup_pi(values)
    assert changed
    assert list(path.parent.glob("models.json.bak.*"))


def test_setup_pi_without_base_url_changes_nothing(tmp_path, monkeypatch):
    from bookcorpus import pimodel

    monkeypatch.setattr(pimodel, "PI_CONFIG", str(tmp_path / "models.json"))
    changed, detail = pimodel.setup_pi({"provider": "acme", "model": "m1", "base_url": ""})
    assert not changed and "base_url" in detail
    assert not (tmp_path / "models.json").exists()


def test_cli_model_shows_and_sets(tmp_path):
    root = _root(tmp_path)
    shown = run_cli("model", "--root", root)
    assert shown.returncode == 0, shown.stdout + shown.stderr
    assert "opencode-go/deepseek-v4.1-flash" in shown.stdout
    assert "内置默认" in shown.stdout

    bad = run_cli("model", "set", "--thinking", "turbo", "--root", root)
    assert bad.returncode != 0
    assert "--thinking" in bad.stderr

    saved = run_cli("model", "set", "--provider", "acme", "--model", "m1",
                    "--thinking", "low", "--root", root)
    assert saved.returncode == 0, saved.stdout + saved.stderr
    text = (root / "books.toml").read_text(encoding="utf-8")
    assert 'provider = "acme"' in text and 'model = "m1"' in text

    stub = tmp_path / "pi_stub.py"
    stub.write_text("import sys\nprint('ok')\n", encoding="utf-8")
    checked = run_cli("model", "check", "--root", root, env={"PI_BIN": str(stub)})
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "回：ok" in checked.stdout
    assert "--model acme/m1" in checked.stdout
