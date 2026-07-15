"""路径、API 配置、常量"""

import os
import sys
from pathlib import Path


# ---- 路径 ----
def _resolve_project_root() -> Path:
    """定位项目根目录（含 mod/ 的目录）。

    PyInstaller 6.x 打包后把只读定义放进 _internal/ 子目录，而 Electron 壳可能把
    TRPG_PROJECT_ROOT 指向 exe 所在目录。两者常不一致，因此这里定位真正含 mod/
    的定义目录；可写 worlds/ 根目录由 RUNTIME_ROOT 单独决定。
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
if os.environ.get("TRPG_RUNTIME_ROOT"):
    RUNTIME_ROOT = Path(os.environ["TRPG_RUNTIME_ROOT"]).resolve()
elif getattr(sys, "frozen", False):
    RUNTIME_ROOT = Path(sys.executable).resolve().parent
else:
    RUNTIME_ROOT = PROJECT_ROOT
SKILLS_DIR = PROJECT_ROOT / "skills"
DEFAULT_MODULE_NAME = os.environ.get("TRPG_MODULE", "mansion_of_madness")
# 旧入口暂时保留为只读兼容常量。运行中模块必须来自 RuntimeContext。
MODULE_NAME = DEFAULT_MODULE_NAME
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
WORLDS_DIR = RUNTIME_ROOT / "worlds"

# ---- DeepSeek API ----
API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com")
MODEL_FLASH = os.environ.get("TRPG_FLASH_MODEL", "deepseek-v4-flash")
MODEL_PRO = os.environ.get("TRPG_PRO_MODEL", "deepseek-v4-pro")
_legacy_force_pro = os.environ.get("TRPG_FORCE_PRO")
_default_role_model = (
    MODEL_FLASH
    if _legacy_force_pro is not None
    and _legacy_force_pro.strip().lower() in ("0", "false", "no", "off")
    else MODEL_PRO
)
NARRATIVE_MODEL = os.environ.get(
    "TRPG_NARRATIVE_MODEL", _default_role_model
).strip() or _default_role_model
JUDGEMENT_MODEL = os.environ.get(
    "TRPG_JUDGEMENT_MODEL",
    os.environ.get("TRPG_JUDGMENT_MODEL", _default_role_model),
).strip() or _default_role_model
# Compatibility aliases for older integrations. New code should use the two
# role-specific models above instead of inferring behavior from FORCE_PRO.
FORCE_PRO = NARRATIVE_MODEL == MODEL_PRO and JUDGEMENT_MODEL == MODEL_PRO
PRIMARY_MODEL = NARRATIVE_MODEL
ENABLE_TURN_AUDIT = os.environ.get("TRPG_ENABLE_TURN_AUDIT", "") in (
    "1", "true", "yes",
)
ENABLE_LOREBOOK = os.environ.get("TRPG_ENABLE_LOREBOOK", "1").lower() not in (
    "0", "false", "no", "off",
)

# DeepSeek supports usage in the final streaming chunk. Keep auto-detection so
# other OpenAI-compatible providers do not receive an unsupported parameter.
_stream_usage_setting = os.environ.get("TRPG_STREAM_USAGE", "auto").lower()
ENABLE_STREAM_USAGE = (
    "deepseek.com" in BASE_URL.lower()
    if _stream_usage_setting == "auto"
    else _stream_usage_setting in ("1", "true", "yes", "on")
)
PROMPT_PROFILE = os.environ.get("TRPG_PROMPT_PROFILE", "hybrid").strip().lower()
if PROMPT_PROFILE not in {"full", "hybrid"}:
    PROMPT_PROFILE = "full"
ENABLE_DYNAMIC_TOOLS = os.environ.get("TRPG_DYNAMIC_TOOLS", "1").lower() not in (
    "0", "false", "no", "off",
)
STORY_THINKING_MODE = os.environ.get(
    "TRPG_STORY_THINKING", "auto"
).strip().lower()
if STORY_THINKING_MODE not in {"auto", "disabled", "enabled", "provider"}:
    STORY_THINKING_MODE = "auto"

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
    "core/trpg_master.skill",
    "core/no_spoiler.skill",
    "core/dice_system.skill",
    "keeper/keeper_core.skill",
    "keeper/keeper_atmosphere.skill",
    "keeper/keeper_npc.skill",
    "keeper/keeper_clues.skill",
    "keeper/keeper_sanity.skill",
]

# 按需加载的 skill：特定工具被调用时，引擎直接把规则内容注入上下文。
OPTIONAL_SKILL_HINTS = {
    "apply_damage": "skills/keeper/keeper_combat.skill",
    "apply_heal": "skills/keeper/keeper_combat.skill",
    "combat_start": "skills/keeper/keeper_combat.skill",
    "combat_action": "skills/keeper/keeper_combat.skill",
    "use_item": "skills/keeper/keeper_items.skill",
    "sanity_event": "skills/keeper/keeper_psychology.skill",
    "sanity_loss": "skills/keeper/keeper_psychology.skill",
    "create_character": "skills/investigator/investigator_creation.skill",
}

MAX_TOOL_ROUNDS = 5

# 旧调用方兼容入口；新业务代码应切换 RuntimeContext，不应调用本函数。
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
