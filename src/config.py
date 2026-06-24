"""路径、API 配置、常量"""

import os
from pathlib import Path

# ---- 路径 ----
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"
MODULE_NAME = os.environ.get("TRPG_MODULE", "mansion_of_madness")
MODULE_DIR = PROJECT_ROOT / "mod" / MODULE_NAME
STATE_FILE = MODULE_DIR / "world_state.json"
INITIAL_STATE_FILE = MODULE_DIR / "world_state_initial.json"
SAVES_DIR = PROJECT_ROOT / "saves" / MODULE_NAME
THEME_FILE = MODULE_DIR / "theme.json"
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
    "core/dice_system.skill",
    "core/no_spoiler.skill",
    "core/trpg_master.skill",
    "keeper/keeper_core.skill",
    "keeper/keeper_atmosphere.skill",
    "keeper/keeper_npc.skill",
    "keeper/keeper_clues.skill",
    "keeper/keeper_combat.skill",
    "keeper/keeper_sanity.skill",
    "keeper/keeper_magic.skill",
    "investigator/investigator_skills.skill",
    "investigator/investigator_creation.skill",
    "investigator/investigator_methods.skill",
]

MAX_TOOL_ROUNDS = 5

# 运行时模块切换
def set_active_module(name: str):
    global MODULE_NAME, MODULE_DIR, STATE_FILE, INITIAL_STATE_FILE, SAVES_DIR, THEME_FILE
    MODULE_NAME = name
    MODULE_DIR = PROJECT_ROOT / "mod" / name
    STATE_FILE = MODULE_DIR / "world_state.json"
    INITIAL_STATE_FILE = MODULE_DIR / "world_state_initial.json"
    SAVES_DIR = PROJECT_ROOT / "saves" / name
    THEME_FILE = MODULE_DIR / "theme.json"
