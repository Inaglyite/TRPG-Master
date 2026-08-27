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
        # src/app/config.py -> repository (or PyInstaller _internal) root.
        candidates.append(Path(__file__).resolve().parents[2])
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


def _bounded_float_env(name: str, default: float, low: float, high: float) -> float:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(low, min(high, value))


# 模型 HTTP 调用超时（秒）。默认与 openai SDK 默认一致（600s），运维可用
# TRPG_MODEL_TIMEOUT 收紧，范围 1-3600。
MODEL_TIMEOUT = _bounded_float_env("TRPG_MODEL_TIMEOUT", 600.0, 1.0, 3600.0)


def model_timeout_seconds() -> float:
    """运行时读取模型超时，便于测试与运维动态调整（未设置时回退到启动值）。"""
    return _bounded_float_env("TRPG_MODEL_TIMEOUT", MODEL_TIMEOUT, 1.0, 3600.0)
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
# 回合事务审计（judgement 模型兜底提交场景/线索/NPC 等权威变更）默认开启：
# 检定回合叙事模型拿不到工具，确定性匹配一旦漏判就只有这道兜底能收敛状态。
ENABLE_TURN_AUDIT = os.environ.get("TRPG_ENABLE_TURN_AUDIT", "1").lower() not in (
    "0", "false", "no", "off",
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


def _enabled_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


# H1 rollout controls.  V2 wraps the existing handler implementations rather
# than changing their rules or transaction boundary; setting it to false keeps
# the H0 graph loop available for a reversible rollout.  Shadow mode validates
# the V2 plan while that legacy loop executes and never runs a second handler.
ENABLE_TOOL_PIPELINE_V2 = _enabled_env("TRPG_TOOL_PIPELINE_V2", True)
ENABLE_TOOL_PIPELINE_SHADOW = _enabled_env("TRPG_TOOL_PIPELINE_SHADOW", False)


def tool_pipeline_v2_enabled() -> bool:
    """Read the feature flag at execution time for test and staged rollout."""
    return _enabled_env("TRPG_TOOL_PIPELINE_V2", ENABLE_TOOL_PIPELINE_V2)


def tool_pipeline_shadow_enabled() -> bool:
    return _enabled_env("TRPG_TOOL_PIPELINE_SHADOW", ENABLE_TOOL_PIPELINE_SHADOW)


def tool_execution_timeout_ms() -> int:
    """Cooperative handler deadline, bounded to avoid accidental zero/huge values."""
    raw = os.environ.get("TRPG_TOOL_EXECUTION_TIMEOUT_MS", "5000")
    try:
        value = int(raw)
    except ValueError:
        value = 5000
    return max(1, min(value, 120_000))

STORY_THINKING_MODE = os.environ.get(
    "TRPG_STORY_THINKING", "auto"
).strip().lower()
if STORY_THINKING_MODE not in {"auto", "disabled", "enabled", "provider"}:
    STORY_THINKING_MODE = "auto"

# ---- GLM-4 Flash 快速模型（免费，检定即时摘要） ----
GLM_API_KEY = os.environ.get("GLM_API_KEY", "")
GLM_BASE_URL = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
GLM_MODEL = os.environ.get("GLM_MODEL", "glm-4-flash-250414")

# ---- Skill Catalog（H3）----
# Skill 元数据唯一来源是 skills/catalog.json（src/ai/skills/skill_manifest.py 解析）；
# 常驻/确定性/按需三类的内容与 digest 以 world_skill_pins 为世界级冻结快照
# （src/ai/skills/skill_pins.py），运行时绝不读全局常量表。

MAX_TOOL_ROUNDS = 5


# ---- H2 context capacity（提供方窗口 + 主动压缩目标）----
# 只读容量底座：根据估算的输入 token 数给出 target/hard 阈值与状态，绝不
# 删除任何内容（压缩决策由调用方在 shadow/诊断模式下落地）。

# Provider context window（输入 + 最大输出的总上限）。默认 65536：现有完整
# COC 规则 spine 约 25k tokens，32k 会在正常开局时就进入不可约区间。实际
# 网关容量不同，部署者可用 TRPG_CONTEXT_WINDOW_TOKENS 覆盖；越界或非法值
# 回退到这个保守运行基线。
CONTEXT_WINDOW_TOKENS_DEFAULT = 65_536
CONTEXT_WINDOW_TOKENS_MIN = 8_192
CONTEXT_WINDOW_TOKENS_MAX = 1_048_576


def _bounded_int_env(
    name: str,
    default: int,
    low: int,
    high: int,
) -> int:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(low, min(high, value))


def context_window_tokens() -> int:
    """Provider input window; bounded to avoid accidental tiny/huge values."""
    return _bounded_int_env(
        "TRPG_CONTEXT_WINDOW_TOKENS",
        CONTEXT_WINDOW_TOKENS_DEFAULT,
        CONTEXT_WINDOW_TOKENS_MIN,
        CONTEXT_WINDOW_TOKENS_MAX,
    )


# Active-compaction target ratio of the window (0.50..0.90, default 0.78).
# Estimated input at/above ``window * target`` should compact *before* the
# provider rejects the request; at/above ``window * hard`` it is irreducible
# (never delete content to make a request fit).
CONTEXT_TARGET_RATIO_DEFAULT = 0.78
CONTEXT_TARGET_RATIO_MIN = 0.50
CONTEXT_TARGET_RATIO_MAX = 0.90


def context_target_ratio() -> float:
    """Fraction of the window that triggers proactive compaction."""
    return _bounded_float_env(
        "TRPG_CONTEXT_TARGET_RATIO",
        CONTEXT_TARGET_RATIO_DEFAULT,
        CONTEXT_TARGET_RATIO_MIN,
        CONTEXT_TARGET_RATIO_MAX,
    )


# Headroom reserved for the model's own output: hard capacity never allows
# input + max_output to exceed the window, so a maximal reply can still fit.
MAX_OUTPUT_TOKENS_DEFAULT = 4096


def max_output_tokens() -> int:
    raw = os.environ.get("TRPG_MAX_OUTPUT_TOKENS", str(MAX_OUTPUT_TOKENS_DEFAULT))
    try:
        value = int(raw)
    except ValueError:
        value = MAX_OUTPUT_TOKENS_DEFAULT
    value = max(1, min(value, 131_072))
    # Keep a real compaction band between ``target`` and the hard provider
    # limit.  Without this cross-setting clamp an accidental tiny context
    # window plus huge output reservation makes every request irreducible,
    # even when a safe shorter reply would work.
    window = context_window_tokens()
    target = int(window * context_target_ratio())
    safe_max = max(1, window - target - 1)
    return min(value, safe_max)

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
