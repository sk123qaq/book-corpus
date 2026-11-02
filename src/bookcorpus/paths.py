"""包内路径。脚本目录随包走，不再挂在数据根下。"""

import os


def scripts_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
