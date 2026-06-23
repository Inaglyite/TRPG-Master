"""路径、API 配置、常量"""

import os
from pathlib import Path

# ---- 路径 ----
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"
SAVES_DIR = PROJECT_ROOT / "saves"
STATE_FILE = PROJECT_ROOT / "mod" / "mansion_of_madness" / "world_state.json"
INITIAL_STATE_FILE = STATE_FILE  # 新游戏时从此文件复制初始状态
AUTO_SAVE_SLOT = "slot_000"

# ---- DeepSeek API ----
API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com")
MODEL_FLASH = os.environ.get("TRPG_FLASH_MODEL", "deepseek-v4-flash")
MODEL_PRO = os.environ.get("TRPG_PRO_MODEL", "deepseek-v4-pro")

# ---- GLM-4 Flash 快速模型（免费，检定即时摘要） ----
GLM_API_KEY = os.environ.get("GLM_API_KEY", "")
GLM_BASE_URL = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
GLM_MODEL = os.environ.get("GLM_MODEL", "glm-4-flash-250414")

# ---- Skill 加载顺序 ----
SKILL_LOAD_ORDER = [
    "no_spoiler.skill",
    "narrative.skill",
    "skill_check.skill",
    "combat.skill",
    "trpg_master.skill",
]

MAX_TOOL_ROUNDS = 5
