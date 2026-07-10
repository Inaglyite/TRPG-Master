"""路径、API 配置、常量"""

import os
import sys
from pathlib import Path

# ---- 路径 ----
def _resolve_project_root() -> Path:
    """定位项目根目录（含 mod/ 的目录）。

    PyInstaller 6.x 打包后把数据放进 _internal/ 子目录，而 Electron 壳把
    TRPG_PROJECT_ROOT 指向 exe 所在目录（便于 .env.json/存档可写）。两者
    常不一致，因此不能盲信 TRPG_PROJECT_ROOT——要找到真正含 mod/ 的目录。
    """
    candidates: list[Path] = []
    env_root = os.environ.get("TRPG_PROJECT_ROOT")
    if env_root:
        candidates.append(Path(env_root).resolve())
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        candidates.append(base / "_internal")
        candidates.append(base)
    else:
        candidates.append(Path(__file__).resolve().parent.parent)
    for c in candidates:
        if (c / "mod").is_dir():
            return c
    return candidates[-1]


PROJECT_ROOT = _resolve_project_root()
SKILLS_DIR = PROJECT_ROOT / "skills"
MODULE_NAME = os.environ.get("TRPG_MODULE", "mansion_of_madness")
MODULE_DIR = PROJECT_ROOT / "mod" / MODULE_NAME
STATE_FILE = MODULE_DIR / "world_state.json"
INITIAL_STATE_FILE = MODULE_DIR / "world_state_initial.json"
SAVES_DIR = PROJECT_ROOT / "saves" / MODULE_NAME
THEME_FILE = MODULE_DIR / "theme.json"
ASSETS_DIR = MODULE_DIR / "assets"
AUTO_SAVE_SLOT = "slot_000"
CHARACTERS_DIR = PROJECT_ROOT / "characters"
DEFAULT_CHARACTERS_DIR = CHARACTERS_DIR / "default"
CUSTOM_CHARACTERS_DIR = CHARACTERS_DIR / "custom"
PROFILES_DIR = PROJECT_ROOT / "profiles"
PLAYER_PROFILE_FILE = PROFILES_DIR / "player_profile.json"

# ---- DeepSeek API ----
API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com")
MODEL_FLASH = os.environ.get("TRPG_FLASH_MODEL", "deepseek-v4-flash")
MODEL_PRO = os.environ.get("TRPG_PRO_MODEL", "deepseek-v4-pro")
FORCE_PRO = os.environ.get("TRPG_FORCE_PRO", "") in ("1", "true", "yes")

# ---- GLM-4 Flash 快速模型（免费，检定即时摘要） ----
GLM_API_KEY = os.environ.get("GLM_API_KEY", "")
GLM_BASE_URL = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
GLM_MODEL = os.environ.get("GLM_MODEL", "glm-4-flash-250414")

# ---- Skill 加载顺序（常驻 system prompt 的 skill）----
# 以下 skill 全量塞入 system prompt,贯穿每回合。
# 场景专用 skill（keeper_combat / keeper_magic / keeper_psychology /
# investigator_skills / investigator_creation / investigator_methods）
# 不在此列表——改为运行时按需 read_file 加载,见 trpg_master.skill 路由表。
SKILL_LOAD_ORDER = [
    "core/dice_system.skill",
    "core/no_spoiler.skill",
    "core/trpg_master.skill",
    "keeper/keeper_core.skill",
    "keeper/keeper_atmosphere.skill",
    "keeper/keeper_npc.skill",
    "keeper/keeper_clues.skill",
    "keeper/keeper_sanity.skill",
]

# 按需加载的 skill：特定工具被调用时，引擎自动提示模型 read_file 加载对应 skill
OPTIONAL_SKILL_HINTS = {
    "apply_damage": "skills/keeper/keeper_combat.skill",
    "apply_heal": "skills/keeper/keeper_combat.skill",
    "combat_start": "skills/keeper/keeper_combat.skill",
    "combat_action": "skills/keeper/keeper_combat.skill",
    "sanity_loss": "skills/keeper/keeper_psychology.skill",
    "create_character": "skills/investigator/investigator_creation.skill",
}

MAX_TOOL_ROUNDS = 5

# 运行时模块切换
def set_active_module(name: str):
    global MODULE_NAME, MODULE_DIR, STATE_FILE, INITIAL_STATE_FILE, SAVES_DIR, THEME_FILE, ASSETS_DIR
    MODULE_NAME = name
    MODULE_DIR = PROJECT_ROOT / "mod" / name
    STATE_FILE = MODULE_DIR / "world_state.json"
    INITIAL_STATE_FILE = MODULE_DIR / "world_state_initial.json"
    SAVES_DIR = PROJECT_ROOT / "saves" / name
    THEME_FILE = MODULE_DIR / "theme.json"
    ASSETS_DIR = MODULE_DIR / "assets"
    # 同步到环境变量——子进程（state_manager/sanity 等）通过 os.environ 读取模块名
    os.environ["TRPG_MODULE"] = name
