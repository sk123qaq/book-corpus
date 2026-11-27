#!/usr/bin/env python3
"""导出 Android 客户端唯一真源 profiles.generated.json。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：导出 profiles.generated.json 到指定文件或标准输出。"""
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))
    from bookcorpus.reading.export_profiles import export_profiles_json, write_profiles_json

    parser = argparse.ArgumentParser(description="从 book_types.py 与 classify_model.py 导出伴读视角配置 JSON")
    parser.add_argument("--out", default="", help="输出文件路径；省略时打印到 stdout")
    args = parser.parse_args(argv)
    if args.out:
        out_path = write_profiles_json(args.out)
        print(str(out_path))
    else:
        sys.stdout.write(export_profiles_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
