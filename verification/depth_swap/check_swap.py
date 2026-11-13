#!/usr/bin/env python3
"""换 depth/ 的两步 replace 与崩溃窗口的现场验证。

只读使用 Store 的公开方法，不建 Library、不连服务器：

- Store.pack_dir     src/bookcorpus/reading/store.py:42-44
- Store.save_depth   src/bookcorpus/reading/store.py:225-242
- Store.depth_state  src/bookcorpus/reading/store.py:259-292
- Store.chapter_route src/bookcorpus/reading/store.py:307-326

三步：
1. 盘上 target/backup/stage 的 8 种组合逐个对照 depth_state 与 chapter_route；
2. 在两次 os.replace 之间注入 SystemExit，复现 depth/ 消失、备份还在的窗口；
3. 再正常保存一次，验证 _heal_swap_crash（store.py:216-223）把包放回来；
4. 再崩一次后调 heal_pending_swaps，验证重启时放回备份、清掉 stage。

用法：
    uv run --frozen python verification/depth_swap/check_swap.py [--root DIR]

退出码 0 表示所有对照与注入都符合预期，1 表示有一条不符。
"""

from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from bookcorpus.reading.store import Store

UID = "B0001"
N = 1
OLD_HTML = "<!doctype html><html><body><p>旧包</p></body></html>"
NEW_HTML = "<!doctype html><html><body><p>新包</p></body></html>"


def _write_ready(path: Path, html: str) -> None:
    """造一个 depth_state 认得的 ready 包：state.json + chapter.html。"""
    path.mkdir(parents=True, exist_ok=True)
    (path / "state.json").write_text(json.dumps({"status": "ready"}, ensure_ascii=False), encoding="utf-8")
    (path / "chapter.html").write_text(html, encoding="utf-8")


def _depth_files(html: str) -> dict[str, str | dict[str, str]]:
    return {"state.json": {"status": "ready"}, "chapter.html": html}


def _report(ok: bool, label: str, detail: str) -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}（{detail}）")
    return ok


def check_conformance(store: Store) -> tuple[int, bool]:
    """8 种盘上组合：status == ready 当且仅当 target 存在。"""
    pack = Path(store.pack_dir(UID, N))
    print("\n== 状态对照：target/backup/stage 的 8 种组合 ==")
    print("state | target | backup | stage | depth_state | route | OK/FAIL")
    checked = 0
    failures = 0
    for has_target, has_backup, has_stage in itertools.product((False, True), repeat=3):
        shutil.rmtree(pack, ignore_errors=True)
        pack.mkdir(parents=True, exist_ok=True)
        if has_target:
            _write_ready(pack / "depth", OLD_HTML)
        if has_backup:
            _write_ready(pack / ".depth-bak-00000001", OLD_HTML)
        if has_stage:
            _write_ready(pack / ".depth-abc123", NEW_HTML)

        state = store.depth_state(UID, N)
        route = store.chapter_route(UID, N)
        status = state.get("status")
        ok = (status == "ready") == has_target
        if not ok:
            failures += 1
        checked += 1
        label = f"{int(has_target)}{int(has_backup)}{int(has_stage)}"
        target_name = "OLD" if has_target else "NONE"
        backup_name = "OLD" if has_backup else "NONE"
        stage_name = "NEW" if has_stage else "NONE"
        print(
            f"{label}    | {target_name:6} | {backup_name:6} | {stage_name:5} "
            f"| {status!s:11} | {route!s:35} | {'OK' if ok else 'FAIL'}"
        )
    return checked, failures == 0


def check_crash(store: Store) -> bool:
    """第二次 replace（stage -> depth）处断电，复现只剩备份的窗口。"""
    pack = Path(store.pack_dir(UID, N))
    shutil.rmtree(pack, ignore_errors=True)
    pack.mkdir(parents=True, exist_ok=True)
    _write_ready(pack / "depth", OLD_HTML)

    real_replace = os.replace

    def crash_on_swap(src, dst):
        source = Path(src)
        if source.parent == pack and source.name.startswith(".depth-") and Path(dst) == pack / "depth":
            raise SystemExit("模拟两次 replace 之间断电")
        return real_replace(src, dst)

    print("\n== 崩溃注入：两次 replace 之间 ==")
    crashed = False
    with mock.patch("bookcorpus.reading.store.os.replace", side_effect=crash_on_swap):
        try:
            store.save_depth(UID, N, _depth_files(NEW_HTML))
        except SystemExit:
            crashed = True

    depth_dir = pack / "depth"
    backups = sorted(pack.glob(".depth-bak-*"))
    backup_html = backups[0] / "chapter.html" if backups else None
    state = store.depth_state(UID, N)
    route = store.chapter_route(UID, N)

    ok_injected = _report(crashed, "注入点命中", "'.depth-*' -> depth/ 时抛 SystemExit")
    ok_a = _report(not depth_dir.exists(), "(a) 包里没有 depth/", str(depth_dir))
    ok_b = _report(
        backup_html is not None and backup_html.is_file() and backup_html.read_text(encoding="utf-8") == OLD_HTML,
        "(b) 备份里有旧 chapter.html",
        str(backup_html),
    )
    ok_c = _report(state.get("status") == "missing", "(c) depth_state", f"status={state.get('status')}")
    ok_d = _report(route == ("none", "尚未生成"), "(d) chapter_route", f"route={route}")

    confirmed = all((ok_injected, ok_a, ok_b, ok_c, ok_d))
    print(f"CRASH-WINDOW: {'CONFIRMED' if confirmed else 'NOT CONFIRMED'}")
    return confirmed


def check_heal(store: Store) -> bool:
    """下一次正常保存：_heal_swap_crash 把备份放回 target。"""
    pack = Path(store.pack_dir(UID, N))
    print("\n== 自愈：下一次 save_depth ==")
    store.save_depth(UID, N, _depth_files(NEW_HTML))
    state = store.depth_state(UID, N)
    route = store.chapter_route(UID, N)

    ok_depth = _report((pack / "depth").is_dir(), "depth/ 回来", str(pack / "depth"))
    ok_status = _report(state.get("status") == "ready", "depth_state", f"status={state.get('status')}")
    ok_route = _report(route == ("depth", None), "chapter_route", f"route={route}")

    healed = all((ok_depth, ok_status, ok_route))
    print(f"HEAL: {'PASS' if healed else 'FAIL'}")
    return healed


def check_startup_heal(store: Store) -> bool:
    """重启时调 heal_pending_swaps：备份放回 target，死掉的 stage 清掉。"""
    pack = Path(store.pack_dir(UID, N))
    shutil.rmtree(pack, ignore_errors=True)
    pack.mkdir(parents=True, exist_ok=True)
    _write_ready(pack / "depth", OLD_HTML)

    real_replace = os.replace

    def crash_on_swap(src, dst):
        source = Path(src)
        if source.parent == pack and source.name.startswith(".depth-") and Path(dst) == pack / "depth":
            raise SystemExit("模拟两次 replace 之间断电")
        return real_replace(src, dst)

    print("\n== 启动自愈：heal_pending_swaps ==")
    with mock.patch("bookcorpus.reading.store.os.replace", side_effect=crash_on_swap), contextlib.suppress(SystemExit):
        store.save_depth(UID, N, _depth_files(NEW_HTML))
    crashed = store.depth_state(UID, N).get("status")

    report = store.heal_pending_swaps()
    state = store.depth_state(UID, N)
    route = store.chapter_route(UID, N)
    backups = list(pack.glob(".depth-bak-*"))
    stages = [p for p in pack.glob(".depth-*") if not p.name.startswith(".depth-bak-")]
    html = pack / "depth" / "chapter.html"

    ok_pre = _report(crashed == "missing", "自愈前是缺失态", f"status={crashed}")
    ok_report = _report(
        report == {"healed": ["B0001/01"], "cleaned": ["B0001/01"], "errors": []},
        "只修这一章",
        str(report),
    )
    ok_target = _report(
        html.is_file() and html.read_text(encoding="utf-8") == OLD_HTML,
        "depth/ 是旧包",
        str(html),
    )
    ok_state = _report(state.get("status") == "ready", "depth_state", f"status={state.get('status')}")
    ok_route = _report(route == ("depth", None), "chapter_route", f"route={route}")
    ok_left = _report(not backups and not stages, "没有备份与 stage 残留", f"backup={backups} stage={stages}")
    again = store.heal_pending_swaps()
    ok_again = _report(again == {"healed": [], "cleaned": [], "errors": []}, "再调一次是空", str(again))

    healed = all((ok_pre, ok_report, ok_target, ok_state, ok_route, ok_left, ok_again))
    print(f"STARTUP-HEAL: {'PASS' if healed else 'FAIL'}")
    return healed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="换 depth/ 的崩溃窗口现场验证")
    parser.add_argument("--root", help="工作根目录；默认 tempfile.mkdtemp(prefix='bc-swap-')")
    args = parser.parse_args(argv)

    auto_root = args.root is None
    root = Path(tempfile.mkdtemp(prefix="bc-swap-")) if auto_root else Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    store = Store(SimpleNamespace(work=str(root)))
    print(f"root: {root}")
    print(f"pack: {store.pack_dir(UID, N)}")

    states, conformance_ok = check_conformance(store)
    crash_ok = check_crash(store)
    heal_ok = check_heal(store)
    startup_ok = check_startup_heal(store)
    ok = conformance_ok and crash_ok and heal_ok and startup_ok

    print(
        f"\nstates: {states} | conformance: {'PASS' if conformance_ok else 'FAIL'} "
        f"| crash-window: {'CONFIRMED' if crash_ok else 'NOT CONFIRMED'} "
        f"| heal: {'PASS' if heal_ok else 'FAIL'} "
        f"| startup-heal: {'PASS' if startup_ok else 'FAIL'}"
    )
    if auto_root and ok:
        shutil.rmtree(root, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
