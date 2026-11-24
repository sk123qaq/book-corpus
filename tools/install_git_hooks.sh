#!/usr/bin/env bash
# 让 git 使用仓库里的 .githooks/：每个克隆跑一次，之后换分支、换 worktree 都不用再跑。
#
#   tools/install_git_hooks.sh
#
# 为什么不用 `pre-commit install`：它生成的钩子把当前 venv 的 python 绝对路径写死进
# .git/hooks/，换一台机器或挪一次目录就失效（本仓就踩过：钩子指向不存在的
# /home/colaqaq/Work/book-corpus-ci/.venv/bin/python，提交直接报 pre-commit not found）。
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

git config core.hooksPath .githooks
chmod +x .githooks/pre-commit .githooks/pre-push

echo "core.hooksPath = $(git config core.hooksPath)（每个 worktree 共用这份仓库配置）"
