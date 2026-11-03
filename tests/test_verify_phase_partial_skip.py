"""verify_phase 的「全过」判据：有跳过、或一项都没验到，都不能打全过。

运维的维护门用 ^全过$ 认收尾是否过关。空语料、缺基线、缺 index.db 时六个箱子
全跳过，旧版最后仍打印「全过」，维护门就假绿。跳过的箱子是没验到，不是通过。
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[1]
          / "src" / "bookcorpus" / "scripts" / "verify_phase.py")


def _load():
    spec = importlib.util.spec_from_file_location("verify_phase_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def vp(monkeypatch):
    """载入模块，六个箱子换成空操作：只验汇总判据，不读真库、不跑 qsearch。"""
    mod = _load()
    for name in ("PASSED", "SKIPPED", "FAIL"):
        assert hasattr(mod, name), "verify_phase 不再暴露 %s：汇总判据变了，重写这个测试" % name
    for i in range(1, 7):
        monkeypatch.setattr(mod, "box%d" % i, lambda *a, **k: None, raising=False)
    monkeypatch.setattr(mod.sys, "argv", ["verify_phase.py"])
    return mod


def _run(mod, capsys):
    """main() 过关时直接返回，不过关时 sys.exit(1)；两种都收成退出码。"""
    code = 0
    try:
        mod.main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    return code, capsys.readouterr().out


def _lines(out):
    return [line.strip() for line in out.splitlines() if line.strip()]


def _sets(mod, monkeypatch, passed, skipped, failed):
    monkeypatch.setattr(mod, "PASSED", list(passed))
    monkeypatch.setattr(mod, "SKIPPED", list(skipped))
    monkeypatch.setattr(mod, "FAIL", list(failed))


def test_skipped_boxes_are_not_all_pass(vp, monkeypatch, capsys):
    _sets(vp, monkeypatch, ["箱子 1 过了"], ["箱子 6 缺 index.db"], [])
    code, out = _run(vp, capsys)
    assert code == 1, out
    assert "全过" not in _lines(out), "有跳过还打了全过：\n" + out
    assert "跳过 1 项" in out and "不算全过" in out


def test_nothing_verified_is_not_all_pass(vp, monkeypatch, capsys):
    _sets(vp, monkeypatch, [], ["六个箱子全跳过"], [])
    code, out = _run(vp, capsys)
    assert code == 1, out
    assert "全过" not in _lines(out), "一项没验到还打了全过：\n" + out
    assert "没验到东西" in out


def test_failed_box_is_not_all_pass(vp, monkeypatch, capsys):
    _sets(vp, monkeypatch, ["过了 1 项"], [], ["箱子 3 奖项 resolve 断了"])
    code, out = _run(vp, capsys)
    assert code == 1, out
    assert "全过" not in _lines(out), out
    assert "门槛不过" in out


def test_all_passed_and_none_skipped_prints_all_pass(vp, monkeypatch, capsys):
    _sets(vp, monkeypatch, ["箱子 1 过了", "箱子 6 过了"], [], [])
    code, out = _run(vp, capsys)
    assert code == 0, out
    assert _lines(out)[-1] == "全过", "全过必须是单独一行，维护门按 ^全过$ 认：\n" + out
