"""桌面 `--mobile-bind` 移动网关包：鉴权服务、`.bcbook` 打包器与分片进度存储。"""

from __future__ import annotations

from .bundler import BundleArtifact, build_bcbook_bundle, ensure_cached_bundle
from .progress import DeviceProgressShard, ProgressStore

__all__ = [
    "BundleArtifact",
    "DeviceProgressShard",
    "ProgressStore",
    "build_bcbook_bundle",
    "ensure_cached_bundle",
]
