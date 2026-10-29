#!/usr/bin/env python3
"""TRPG Game 终端版启动器 —— 跨平台处理 venv、依赖和配置。

用法:
    python3 start.py              # 使用默认配置启动
    python3 start.py --setup      # 首次运行：创建 venv 并安装依赖
    python3 start.py --config     # 交互式配置 API key 和模型

环境变量（优先级高于配置文件）:
    OPENAI_API_KEY     API 密钥
    OPENAI_BASE_URL    API 地址（默认 https://api.deepseek.com）
    TRPG_FLASH_MODEL   Flash 模型名（默认 deepseek-flash）
    TRPG_PRO_MODEL     Pro 模型名（默认 deepseek-v4-pro）
"""

import getpass
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
    print("  TRPG Game 终端版")
    print("  OpenAI 兼容模型 + 确定性规则引擎")
    print("=" * 55)


def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {}


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    CONFIG_FILE.chmod(0o600)
    print(f"配置已保存到 {CONFIG_FILE}")


def setup_venv():
    if VENV_DIR.exists():
        print(f"venv 已存在: {VENV_DIR}")
    else:
        print("创建虚拟环境...")
        subprocess.run([PYTHON_EXE, "-m", "venv", str(VENV_DIR)], check=True)
    print("安装 Python 依赖...")
    subprocess.run(
        [str(VENV_PIP), "install", "-r", str(PROJECT_ROOT / "requirements.txt")],
        check=True,
    )
    print("完成！")


def interactive_config():
    cfg = load_config()
    print("\n配置 API 连接（回车保留当前值）:\n")

    current_key = cfg.get("api_key", "")
    display_key = "已配置" if current_key else "未设置"
    api_key = getpass.getpass(f"API Key [{display_key}，输入不回显]: ").strip()
    if api_key:
        cfg["api_key"] = api_key

    base_url = input(f"Base URL [{cfg.get('base_url', 'https://api.deepseek.com')}]: ").strip()
    if base_url:
        cfg["base_url"] = base_url

    flash_model = input(f"Flash 模型 [{cfg.get('flash_model', 'deepseek-flash')}]: ").strip()
    if flash_model:
        cfg["flash_model"] = flash_model

    pro_model = input(f"Pro 模型 [{cfg.get('pro_model', 'deepseek-v4-pro')}]: ").strip()
    if pro_model:
        cfg["pro_model"] = pro_model

    print("\n--- 可选的快速摘要服务 ---")
    glm_status = "已配置" if cfg.get("glm_api_key") else "未设置"
    glm_key = getpass.getpass(f"GLM API Key [{glm_status}，输入不回显]: ").strip()
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
    env.setdefault("TRPG_FLASH_MODEL", cfg.get("flash_model", "deepseek-flash"))
    env.setdefault("TRPG_PRO_MODEL", cfg.get("pro_model", "deepseek-v4-pro"))
    for config_key, env_key in {
        "narrative_model": "TRPG_NARRATIVE_MODEL",
        "judgement_model": "TRPG_JUDGEMENT_MODEL",
        "glm_base_url": "GLM_BASE_URL",
        "story_thinking_mode": "TRPG_STORY_THINKING",
    }.items():
        if cfg.get(config_key):
            env.setdefault(env_key, str(cfg[config_key]))
    if cfg.get("glm_api_key"):
        env.setdefault("GLM_API_KEY", cfg["glm_api_key"])
    env.setdefault("GLM_MODEL", cfg.get("glm_model", "glm-4-flash-250414"))
    if cfg.get("context_window_tokens"):
        env.setdefault("TRPG_CONTEXT_WINDOW_TOKENS", str(cfg["context_window_tokens"]))
    if cfg.get("max_output_tokens"):
        env.setdefault("TRPG_MAX_OUTPUT_TOKENS", str(cfg["max_output_tokens"]))

    # Respect an activated .venv instead of silently switching to another venv.
    active_venv = sys.prefix != sys.base_prefix
    python_exe = PYTHON_EXE if active_venv else (str(VENV_PYTHON) if VENV_PYTHON.exists() else PYTHON_EXE)

    print("本地模型设置优先；未自定义的角色使用 .env.json / 环境变量。")
    print()
    sys.stdout.flush()

    os.chdir(PROJECT_ROOT)
    try:
        return subprocess.run([python_exe, str(PROJECT_ROOT / "game_loop.py")], env=env).returncode
    except KeyboardInterrupt:
        return 130


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
        return 2

    if not VENV_PYTHON.exists() and sys.prefix == sys.base_prefix:
        print("提示: 首次运行建议先 python3 start.py --setup\n")

    return run_game()


if __name__ == "__main__":
    sys.exit(main())
