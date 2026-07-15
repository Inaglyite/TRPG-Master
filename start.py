#!/usr/bin/env python3
"""TRPG Agent 启动器 —— 跨平台，自动处理 venv、依赖和配置。

用法:
    python3 start.py              # 使用默认配置启动
    python3 start.py --setup      # 首次运行：创建 venv 并安装依赖
    python3 start.py --config     # 交互式配置 API key 和模型

环境变量（优先级高于配置文件）:
    OPENAI_API_KEY     API 密钥
    OPENAI_BASE_URL    API 地址（默认 https://api.deepseek.com）
    TRPG_FLASH_MODEL   Flash 模型名（默认 deepseek-v4-flash）
    TRPG_PRO_MODEL     Pro 模型名（默认 deepseek-v4-pro）
"""

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_DIR = PROJECT_ROOT / "venv"
CONFIG_FILE = PROJECT_ROOT / ".env.json"

IS_WIN = sys.platform == "win32"
PYTHON_EXE = sys.executable
VENV_PYTHON = (
    VENV_DIR / "Scripts" / "python.exe"
    if IS_WIN
    else VENV_DIR / "bin" / "python3"
)
VENV_PIP = (
    VENV_DIR / "Scripts" / "pip.exe"
    if IS_WIN
    else VENV_DIR / "bin" / "pip"
)


def banner():
    print("=" * 55)
    print("  TRPG Agent 内核 — 启动器")
    print("  DeepSeek V4 Flash + Pro (1M 上下文)")
    print("=" * 55)


def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {}


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"配置已保存到 {CONFIG_FILE}")


def setup_venv():
    if VENV_DIR.exists():
        print(f"venv 已存在: {VENV_DIR}")
    else:
        print("创建虚拟环境...")
        subprocess.run([PYTHON_EXE, "-m", "venv", str(VENV_DIR)], check=True)
    print("安装 Python 依赖...")
    subprocess.run([str(VENV_PIP), "install", "openai", "langgraph"], check=True)
    print("完成！")


def interactive_config():
    cfg = load_config()
    print("\n配置 API 连接（回车保留当前值）:\n")

    current_key = cfg.get("api_key", "")
    display_key = (current_key[:20] + "...") if len(current_key) > 20 else "未设置"
    api_key = input(f"API Key [{display_key}]: ").strip()
    if api_key:
        cfg["api_key"] = api_key

    base_url = input(f"Base URL [{cfg.get('base_url', 'https://api.deepseek.com')}]: ").strip()
    if base_url:
        cfg["base_url"] = base_url

    flash_model = input(f"Flash 模型 [{cfg.get('flash_model', 'deepseek-v4-flash')}]: ").strip()
    if flash_model:
        cfg["flash_model"] = flash_model

    pro_model = input(f"Pro 模型 [{cfg.get('pro_model', 'deepseek-v4-pro')}]: ").strip()
    if pro_model:
        cfg["pro_model"] = pro_model

    print("\n--- GLM-4 Flash（免费快速模型，检定摘要用）---")
    glm_key = input(f"GLM API Key [{cfg.get('glm_api_key', '未设置')[:20]}...]: ").strip()
    if glm_key:
        cfg["glm_api_key"] = glm_key
    glm_model = input(f"GLM 模型 [{cfg.get('glm_model', 'glm-4-flash-250414')}]: ").strip()
    if glm_model:
        cfg["glm_model"] = glm_model

    save_config(cfg)


def run_game():
    cfg = load_config()

    env = os.environ.copy()
    env.setdefault("OPENAI_API_KEY", cfg.get("api_key", ""))
    env.setdefault("OPENAI_BASE_URL", cfg.get("base_url", "https://api.deepseek.com"))
    env.setdefault("TRPG_FLASH_MODEL", cfg.get("flash_model", "deepseek-v4-flash"))
    env.setdefault("TRPG_PRO_MODEL", cfg.get("pro_model", "deepseek-v4-pro"))
    if cfg.get("glm_api_key"):
        env.setdefault("GLM_API_KEY", cfg["glm_api_key"])
    env.setdefault("GLM_MODEL", cfg.get("glm_model", "glm-4-flash-250414"))

    if not env["OPENAI_API_KEY"]:
        print("\n错误: 未配置 API Key")
        print("  方式 1: export OPENAI_API_KEY=your-key && python3 start.py")
        print("  方式 2: python3 start.py --config")
        sys.exit(1)

    python_exe = str(VENV_PYTHON) if VENV_PYTHON.exists() else PYTHON_EXE

    print(f"Flash: {env['TRPG_FLASH_MODEL']}")
    print(f"Pro:   {env['TRPG_PRO_MODEL']}")
    print(f"API:   {env['OPENAI_BASE_URL']}")
    print()

    os.chdir(PROJECT_ROOT)
    subprocess.run([python_exe, str(PROJECT_ROOT / "game_loop.py")], env=env)


def main():
    banner()

    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()

        if arg == "--setup":
            setup_venv()
            print("\n接下来: python3 start.py --config  配置 API Key")
            return

        if arg == "--config":
            interactive_config()
            return

        print(f"未知参数: {arg}")
        print("用法: python3 start.py [--setup | --config]")
        return

    if not VENV_PYTHON.exists():
        print("提示: 首次运行建议先 python3 start.py --setup\n")

    run_game()


if __name__ == "__main__":
    main()
