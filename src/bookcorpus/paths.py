"""包内路径。脚本目录随包走，不再挂在数据根下。"""

import os


def scripts_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")


def root():
    """工作区根：BOOKS_ROOT 优先，否则从当前目录往上找 books.toml。"""
    env = os.environ.get("BOOKS_ROOT")
    if env:
        return os.path.abspath(env)
    here = os.path.abspath(os.getcwd())
    while True:
        if os.path.isfile(os.path.join(here, "books.toml")):
            return here
        up = os.path.dirname(here)
        if up == here:
            return os.path.abspath(os.getcwd())
        here = up
