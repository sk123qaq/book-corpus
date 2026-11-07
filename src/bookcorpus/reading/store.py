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

    def save(self, uid, n, manifest, annotation, audit, chapter_html, exports, editorial=None):
        """先写 staging，再目录级替换；manifest 最后写。失败保留旧包。"""
        base = self.pack_dir(uid, n)
        parent = os.path.dirname(base)
        os.makedirs(parent, exist_ok=True)
        stage = tempfile.mkdtemp(prefix=".tmp-", dir=parent)
        # 备份名每次唯一：两个进程/线程同时写同一章时不会互相删掉对方的 .bak。
        backup = os.path.join(parent, ".bak-" + uuid.uuid4().hex[:8])
        try:
            if annotation is not None:
                self._write(
                    os.path.join(stage, "annotation.json"), json.dumps(annotation, ensure_ascii=False, indent=1)
                )
            if editorial is not None:
                self._write(os.path.join(stage, "editorial.json"), json.dumps(editorial, ensure_ascii=False, indent=1))
            elif os.path.exists(os.path.join(base, "editorial.json")):
                shutil.copy2(os.path.join(base, "editorial.json"), os.path.join(stage, "editorial.json"))
            if audit is not None:
                self._write(os.path.join(stage, "audit.json"), json.dumps(audit, ensure_ascii=False, indent=1))
            for tier, html in exports.items():
                self._write(os.path.join(stage, "export", "%s.html" % tier), html)
            # 四读重生成不能带走视觉读本与 Agent 标准档。
            for name in ("edition", "agent-standard.html"):
                source = os.path.join(base, name)
                if os.path.isdir(source):
                    shutil.copytree(source, os.path.join(stage, name))
                elif os.path.isfile(source):
                    shutil.copy2(source, os.path.join(stage, name))
            self._write(os.path.join(stage, "chapter.html"), chapter_html)
            self._write(os.path.join(stage, "manifest.json"), json.dumps(manifest, ensure_ascii=False, indent=1))
            if os.path.isdir(backup):
                shutil.rmtree(backup)
            if os.path.exists(base):
                os.replace(base, backup)
            os.replace(stage, base)
            if os.path.isdir(backup):
                shutil.rmtree(backup)
        except Exception:
            if not os.path.exists(base) and os.path.exists(backup):
                os.replace(backup, base)
            shutil.rmtree(stage, ignore_errors=True)
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
