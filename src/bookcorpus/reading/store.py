"""读本包与账：唯一写区 ``工作区/阅读``。原子写、manifest 最后落。"""

import json
import os
import re
import shutil
import tempfile
import uuid

UID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class Store:
    def __init__(self, layout):
        self.layout = layout
        self.base = os.path.join(layout.work, "阅读")

    def _check(self, uid, n):
        if not UID_RE.match(uid or ""):
            raise ValueError("非法 uid")
        n = int(n)
        if n <= 0:
            raise ValueError("非法章号")
        return uid, n

    def pack_dir(self, uid, n):
        uid, n = self._check(uid, n)
        return os.path.join(self.base, uid, "ch", "%02d" % n)

    def manifest(self, uid, n):
        path = os.path.join(self.pack_dir(uid, n), "manifest.json")
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    def annotation(self, uid, n):
        path = os.path.join(self.pack_dir(uid, n), "annotation.json")
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    def editorial(self, uid, n):
        path = os.path.join(self.pack_dir(uid, n), "editorial.json")
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    def audit(self, uid, n):
        path = os.path.join(self.pack_dir(uid, n), "audit.json")
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    def save_editorial(self, uid, n, editorial):
        path = os.path.join(self.pack_dir(uid, n), "editorial.json")
        self._write(path, json.dumps(editorial, ensure_ascii=False, indent=1))
        return editorial

    def edition_dir(self, uid, n):
        return os.path.join(self.pack_dir(uid, n), "edition")

    def edition(self, uid, n):
        path = os.path.join(self.edition_dir(uid, n), "content.json")
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    def save_edition(self, uid, n, content, html_text, audit, brief):
        """edition 子目录整体替换；失败保留旧的视觉读本。"""
        base = self.pack_dir(uid, n)
        target = os.path.join(base, "edition")
        os.makedirs(base, exist_ok=True)
        stage = tempfile.mkdtemp(prefix=".edition-", dir=base)
        backup = os.path.join(base, ".edition-bak-" + uuid.uuid4().hex[:8])
        try:
            self._write(os.path.join(stage, "content.json"), json.dumps(content, ensure_ascii=False, indent=1))
            self._write(os.path.join(stage, "index.html"), html_text)
            self._write(os.path.join(stage, "audit.json"), json.dumps(audit, ensure_ascii=False, indent=1))
            self._write(os.path.join(stage, "brief.json"), json.dumps(brief, ensure_ascii=False, indent=1))
            if os.path.isdir(backup):
                shutil.rmtree(backup)
            if os.path.exists(target):
                os.replace(target, backup)
            os.replace(stage, target)
            if os.path.isdir(backup):
                shutil.rmtree(backup)
        except Exception:
            if not os.path.exists(target) and os.path.exists(backup):
                os.replace(backup, target)
            shutil.rmtree(stage, ignore_errors=True)
            raise
        return content

    def chapter_html_path(self, uid, n):
        return os.path.join(self.pack_dir(uid, n), "chapter.html")

    def export_path(self, uid, n, tier):
        return os.path.join(self.pack_dir(uid, n), "export", "%s.html" % tier)

    def asset_dir(self, uid, n):
        return os.path.join(self.pack_dir(uid, n), "assets")

    def tier_states(self, uid, n, source_sha=None):
        manifest = self.manifest(uid, n)
        states = {}
        for tier in ("skim", "standard", "deep", "research"):
            states[tier] = {"status": "missing", "stats": None, "generated_at": None}
        if not manifest:
            return {"manifest": None, "tiers": states, "status": "missing"}
        stale = bool(source_sha and manifest.get("source_sha256") != source_sha)
        for tier, info in (manifest.get("tiers") or {}).items():
            if tier not in states:
                continue
            path = self.export_path(uid, n, tier)
            if os.path.exists(path):
                states[tier] = {
                    "status": "stale" if stale else "available",
                    "stats": info.get("stats"),
                    "generated_at": info.get("generated_at") or manifest.get("generated_at"),
                }
        status = "stale" if stale else manifest.get("status", "ok")
        return {"manifest": manifest, "tiers": states, "status": status}

    def _depth_path(self, root, name):
        if not isinstance(name, str) or not name or os.path.isabs(name) or "\\" in name:
            raise ValueError("非法 depth 路径：%s" % name)
        parts = name.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError("非法 depth 路径：%s" % name)
        return os.path.join(root, *parts)

    def _stage_dir(self, base, prefix):
        os.makedirs(base, exist_ok=True)
        return tempfile.mkdtemp(prefix=prefix, dir=base)

    def _backup_path(self, base, prefix=".bak-"):
        return os.path.join(base, prefix + uuid.uuid4().hex[:8])

    def _swap_dir(self, target, stage, backup):
        if os.path.isdir(backup):
            shutil.rmtree(backup)
        if os.path.exists(target):
            os.replace(target, backup)
        os.replace(stage, target)
        if os.path.isdir(backup):
            shutil.rmtree(backup, ignore_errors=True)

    def _recover_dir(self, target, stage, backup):
        if not os.path.exists(target) and os.path.exists(backup):
            os.replace(backup, target)
        shutil.rmtree(stage, ignore_errors=True)

    def _copy_depth_cache(self, depth_root, stage_depth):
        source = os.path.join(depth_root, "cache")
        if os.path.isdir(source):
            shutil.copytree(source, os.path.join(stage_depth, "cache"))

    def _write_depth_files(self, root, files):
        if not isinstance(files, dict):
            raise ValueError("depth files 必须是对象")
        if "state.json" not in files:
            raise ValueError("depth files 缺少 state.json")
        os.makedirs(root, exist_ok=True)
        order = [name for name in ("chapter_model.json", "ledger.json") if name in files]
        order += sorted(name for name in files if name not in order and name != "state.json")
        order += ["state.json"]
        for name in order:
            value = files[name]
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1)
            self._write(self._depth_path(root, name), text)

    def save_depth(self, uid, n, files):
        """只替换 depth/；先写模型与账，state.json 最后落，失败保留旧 depth。"""
        base = self.pack_dir(uid, n)
        os.makedirs(base, exist_ok=True)
        if not isinstance(files, dict) or "state.json" not in files:
            raise ValueError("depth files 缺少 state.json")
        stage = self._stage_dir(base, ".depth-")
        target = os.path.join(base, "depth")
        backup = self._backup_path(base, ".depth-bak-")
        try:
            self._copy_depth_cache(target, stage)
            self._write_depth_files(stage, files)
            self._swap_dir(target, stage, backup)
        except Exception:
            self._recover_dir(target, stage, backup)
            raise
        return files

    def depth_cache_dir(self, uid, n):
        return os.path.join(self.pack_dir(uid, n), "depth", "cache")

    def depth_path(self, uid, n, name):
        """depth 目录内某个文件的绝对路径；分段校验同名读取口一致。"""
        return self._depth_path(os.path.join(self.pack_dir(uid, n), "depth"), name)

    def depth_json(self, uid, n, name):
        path = self._depth_path(os.path.join(self.pack_dir(uid, n), "depth"), name)
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    def depth_state(self, uid, n, source_sha=None):
        state = self.depth_json(uid, n, "state.json")
        if not isinstance(state, dict):
            return {
                "status": "missing",
                "state": None,
                "model_sha": None,
                "prompt_version": None,
                "schema": None,
            }
        base = {
            "state": state,
            "model_sha": state.get("chapter_model_sha256") or state.get("chapter_model_sha"),
            "prompt_version": state.get("prompt_version"),
            "schema": state.get("schema"),
        }
        if state.get("layers_sha"):
            base["layers_sha"] = state["layers_sha"]
        if source_sha and state.get("source_sha256") != source_sha:
            return dict(base, status="stale")
        if state.get("status") == "model":
            return dict(base, status="model")
        if state.get("status") == "ready":
            # ready 必须以可服务的页面为准；旧版 --skip-render 留下的
            # ready 状态没有 chapter.html，按 model 处理而不是给 404。
            if not os.path.isfile(self.depth_path(uid, n, "chapter.html")):
                return dict(base, status="model")
            return dict(base, status="ready")
        return dict(base, status="stale")

    def chapter_route(self, uid, n, source_sha=None, manifest=None):
        """这一章能从哪条路径打开；只报真能打开的那条，细节放进 reason。"""
        status = self.depth_state(uid, n, source_sha).get("status")
        if status == "ready":
            return "depth", None
        if manifest is None:
            manifest = self.manifest(uid, n)
        if manifest:
            return "legacy", "旧包按旧路径打开"
        if status == "model":
            return "none", "结构模型已建，深度页未出"
        if status == "stale":
            return "none", "源文已改，深度页过期"
        return "none", "尚未生成"

    def _write(self, path, data, mode="w"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        if "b" in mode:
            with open(tmp, mode) as fh:
                fh.write(data)
        else:
            with open(tmp, mode, encoding="utf-8") as fh:
                fh.write(data)
        os.replace(tmp, path)

    def save(
        self,
        uid,
        n,
        manifest,
        annotation,
        audit,
        chapter_html,
        exports,
        editorial=None,
        depth=None,
        drop_editorial=False,
    ):
        """先写 staging，再目录级替换；manifest 最后写。失败保留旧包。

        ``depth=None`` 原样带走旧 depth/；传入字典时整体替换 depth/。
        ``drop_editorial=True`` 时不带走旧 editorial.json（标注重做后旧编辑层已失效）。
        """
        base = self.pack_dir(uid, n)
        parent = os.path.dirname(base)
        os.makedirs(parent, exist_ok=True)
        stage = self._stage_dir(parent, ".tmp-")
        # 备份名每次唯一：两个进程/线程同时写同一章时不会互相删掉对方的 .bak。
        backup = self._backup_path(parent)
        try:
            preserved = set()
            if depth is not None:
                # 深度任务只换 depth/，根旧产物原样带走。
                for name in (
                    "annotation.json",
                    "editorial.json",
                    "audit.json",
                    "chapter.html",
                    "agent-standard.html",
                ):
                    source = os.path.join(base, name)
                    if os.path.isfile(source):
                        shutil.copy2(source, os.path.join(stage, name))
                        preserved.add(name)
                for name in ("export", "edition"):
                    source = os.path.join(base, name)
                    if os.path.isdir(source):
                        shutil.copytree(source, os.path.join(stage, name))
                        preserved.add(name)
            if annotation is not None and "annotation.json" not in preserved:
                self._write(
                    os.path.join(stage, "annotation.json"), json.dumps(annotation, ensure_ascii=False, indent=1)
                )
            if editorial is not None and "editorial.json" not in preserved:
                self._write(os.path.join(stage, "editorial.json"), json.dumps(editorial, ensure_ascii=False, indent=1))
            elif (
                not drop_editorial
                and "editorial.json" not in preserved
                and os.path.exists(os.path.join(base, "editorial.json"))
            ):
                shutil.copy2(os.path.join(base, "editorial.json"), os.path.join(stage, "editorial.json"))
            if audit is not None and "audit.json" not in preserved:
                self._write(os.path.join(stage, "audit.json"), json.dumps(audit, ensure_ascii=False, indent=1))
            if "export" not in preserved:
                for tier, html in exports.items():
                    self._write(os.path.join(stage, "export", "%s.html" % tier), html)
            # 四读重生成不能带走视觉读本与 Agent 标准档。
            if "edition" not in preserved:
                source = os.path.join(base, "edition")
                if os.path.isdir(source):
                    shutil.copytree(source, os.path.join(stage, "edition"))
            if "agent-standard.html" not in preserved:
                source = os.path.join(base, "agent-standard.html")
                if os.path.isfile(source):
                    shutil.copy2(source, os.path.join(stage, "agent-standard.html"))
            old_depth = os.path.join(base, "depth")
            if depth is None:
                if os.path.isdir(old_depth):
                    shutil.copytree(old_depth, os.path.join(stage, "depth"))
            else:
                self._copy_depth_cache(old_depth, os.path.join(stage, "depth"))
                self._write_depth_files(os.path.join(stage, "depth"), depth)
            if "chapter.html" not in preserved:
                self._write(os.path.join(stage, "chapter.html"), chapter_html)
            self._write(os.path.join(stage, "manifest.json"), json.dumps(manifest, ensure_ascii=False, indent=1))
            if os.path.isdir(backup):
                shutil.rmtree(backup)
            self._swap_dir(base, stage, backup)
        except Exception:
            self._recover_dir(base, stage, backup)
            raise
        return manifest

    def recorded_job(self, uid, n):
        manifest = self.manifest(uid, n)
        if not manifest:
            return None
        return {
            "uid": uid,
            "n": n,
            "status": manifest.get("status", "ok"),
            "draft": manifest.get("draft", False),
            "generated_at": manifest.get("generated_at"),
            "generator": manifest.get("generator"),
            "tiers": list((manifest.get("tiers") or {}).keys()),
        }
