"""首页按需代起兼容门面：实现统一收口在 ``bookcorpus.app_links``。"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookcorpus.app_links import (  # noqa: F401
    DEFAULT_TARGETS,
    SERVICES,
    START_TIMEOUT,
    Service,
    command_for,
    ensure,
    is_local_target,
    join_target,
    log_path,
    port_open,
    resolve_go_redirect,
    resolve_targets,
    safe_rel_path,
    same_target,
    wait_for_port,
)
