"""桌面 `--mobile-bind` 移动网关包：鉴权服务、`.bcbook` 打包器、分片进度、离线 QR 与托管器。"""

from __future__ import annotations

from .bundler import BundleArtifact, build_bcbook_bundle, ensure_cached_bundle
from .manager import MobileGatewayManager, build_pair_uri, enumerate_host_candidates
from .progress import DeviceProgressShard, ProgressStore
from .qr import QrMatrix, encode_qr, render_svg

__all__ = [
    "BundleArtifact",
    "DeviceProgressShard",
    "MobileGatewayManager",
    "ProgressStore",
    "QrMatrix",
    "build_bcbook_bundle",
    "build_pair_uri",
    "encode_qr",
    "ensure_cached_bundle",
    "enumerate_host_candidates",
    "render_svg",
]
