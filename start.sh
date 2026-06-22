#!/usr/bin/env bash
# TRPG Agent 启动脚本
# 用法: bash start.sh
# 需要先安装依赖: python3 -m venv venv && source venv/bin/activate && pip install openai

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
else
    echo "警告: 未找到 venv，使用系统 Python"
fi

cd "$SCRIPT_DIR"
python3 game_loop.py
