r"""verify_phase 的门槛判据：四个箱子，没有「跳过」。

新库没有迁移基线、奖项下载批次、旧路径对照表，这些东西已经不在门槛里。
箱子只看每个库都有的东西：奖项 resolve、顶层与 book.json、从零重建、检索层。
所以「全过」就是真全过，末行必须是独占一行的「全过：N 项」，
维护门（apps/ops/server.py 的 verify 步骤 marker）按 ^全过：\d+ 项 认。
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import run_cli

SRC = Path(__file__).resolve().parents[1] / "src"
SCRIPT = SRC / "bookcorpus" / "scripts" / "verify_phase.py"


def _load():
    spec = importlib.util.spec_from_file_location("verify_phase_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def vp(monkeypatch):
    """载入模块，四个箱子换成空操作：只验汇总判据，不读真库、不跑 qsearch。"""
    mod = _load()
    assert not hasattr(mod, "SKIPPED"), "verify_phase 又有了「跳过」概念：门槛要可判定"
    for i in range(1, 5):
        monkeypatch.setattr(mod, "box%d" % i, lambda *a, **k: None, raising=False)
    monkeypatch.setattr(mod.sys, "argv", ["verify_phase.py"])
    return mod


def _run(mod, capsys):
    code = 0
    try:
        mod.main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    return code, capsys.readouterr().out


def _lines(out):
    return [line.strip() for line in out.splitlines() if line.strip()]


def _sets(mod, monkeypatch, passed, failed):
    monkeypatch.setattr(mod, "PASSED", list(passed))
    monkeypatch.setattr(mod, "FAIL", list(failed))


def test_failed_box_exits_1(vp, monkeypatch, capsys):
    _sets(vp, monkeypatch, ["过了 1 项"], ["箱子 1 奖项 resolve 断了"])
    code, out = _run(vp, capsys)
    assert code == 1, out
    assert "门槛不过" in out
    assert not any(line.startswith("全过") for line in _lines(out))


def test_all_passed_prints_all_pass(vp, monkeypatch, capsys):
    _sets(vp, monkeypatch, ["箱子 1 过了", "箱子 4 过了"], [])
    code, out = _run(vp, capsys)
    assert code == 0, out
    assert _lines(out)[-1] == "全过：2 项", "维护门按 ^全过：N 项 认：\n" + out


def test_box_out_of_range_is_rejected(vp, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["verify_phase.py", "--box", "5"])
    with pytest.raises(SystemExit) as exc:
        vp.main()
    assert exc.value.code != 0


def test_fresh_library_passes_without_skips(tmp_path):
    """init + 样书之后，四个箱子全部真跑并且全过。"""
    ws = tmp_path / "ws"
    run_cli("init", str(ws), "--no-pdf-venv")
    run_cli("sample", "--root", str(ws))
    out = subprocess.run([sys.executable, str(SCRIPT)], cwd=str(ws), capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "全过：" in out.stdout
    assert "跳过" not in out.stdout
    assert "SKIP" not in out.stdout
    assert "对照表" not in out.stdout
