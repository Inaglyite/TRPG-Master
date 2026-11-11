# English guide

[Project overview](../../README.en.md) · [中文文档索引](../INDEX.md)

This guide covers setup, play modes and scenario authoring. The application interface and bundled modules are currently primarily Chinese. The six [Chinese reference documents](#technical-reference) remain the detailed source of current behavior; this guide links to them rather than maintaining a second full protocol specification.

Updated **2026-11-11**, against verified source commit `c56e28dd`. The public stable installer remains **v1.0.2**. New source features and successful CI artifacts are separate from stable releases and production deployments; see [status](../STATUS.md#1-版本与交付范围).

## Choose how to run

| Option | Requirements | Start here |
| --- | --- | --- |
| Windows application | A release installer or portable executable | [Latest release](https://github.com/Inaglyite/TRPG-Master/releases/latest) |
| Linux desktop from source | Python 3.12+, Node.js 22, npm | `bash start_desktop.sh` in the repository root |
| Hosted browser play | An account on the selected service | [Web app](https://trpggame.xyz) |
| Local browser development | Python environment and a frontend build | Commands below |

Local play uses a local backend and local saves. AI narration still needs connectivity to the configured model service. Human Keeper mode does not require a model key. Cloud solo play and multiplayer rooms use server-side accounts, world permissions and the applicable BYOK model binding.

## Local browser setup

Clone the repository, then prepare a backend environment. Use a Python 3.12+ interpreter for `python3`:

```bash
git clone https://github.com/Inaglyite/TRPG-Master.git
cd TRPG-Master
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Build the frontend:

```bash
cd frontend
npm ci
npm run build
cd ..
```

Apply the database migrations and start the backend:

```bash
python -m alembic upgrade head
python server.py
```

Open `http://127.0.0.1:8765/?mode=local`. The query parameter selects the local browser entry. The desktop launcher performs additional setup, including a one-time import of compatible old file-backed worlds. See [development](../DEVELOPMENT.md) before moving an existing installation.

For frontend hot reload, run `npm run dev` inside `frontend/` while the backend stays on port 8765. Use the URL printed by Vite and append `?mode=local`. `VITE_TRPG_BACKEND_ORIGIN` can select a different backend origin; origin permissions must also allow the development page.

For isolated development data, set `TRPG_RUNTIME_ROOT` and `TRPG_DATABASE_URL` consistently before migrations and startup. Never point a test run at a real player's database or inherit a production database URL. The runtime root contains saves/worlds and user data; checked-in module definitions remain separate.

## Play modes

After selecting a module and investigator, open **游玩方式** (Play mode) on the investigator selection page:

| UI label | Meaning |
| --- | --- |
| 经典 AI 叙事 | Classic AI narration, using the legacy whole-turn path |
| 人类主持 | Human Keeper, using structured commands without model calls in the core play flow |
| AI 辅助主持 | AI-assisted Keeper: private drafts require human approval |
| AI 主持 | Agent Keeper using restricted structured commands |

Local/cloud location, solo/multiplayer participation and Keeper mode are separate choices. Modes do not automatically have identical coverage. In particular, a legacy AI playthrough or a human-hosted structured playthrough does not verify a complete structured Agent playthrough. See [status](../STATUS.md).

Suggested actions are optional; players can enter free-form actions. The server settles checks and state changes. A displayed narrative or animation is not by itself proof that a turn has been committed.

Under **游戏设置 → 游戏偏好** (Game settings → Game preferences), disable **显示行动提示** (Show action suggestions) to hide the “你可以——” menu and bottom suggestions. Free input and necessary confirmations/checks remain. The switch takes effect immediately, is stored in local browser storage, and can be reversed without changing saved history or model output.

Below the input, **使用技能** (Use skill) lets you choose a skill from your current investigator's sheet and describe your goal. Submitting proposes an action; the Keeper decides whether a check is needed. **整理线索** (Organize clues) extracts important information from the latest completed, displayed turn. Review or edit the draft and its original quotes before adding it to your private investigation notes. Automatic extraction uses your configured model even in human-hosted play; manual excerpts are available if extraction fails. It does not grant an acquired game clue or advance a turn.

**场景移动** (Scene travel) opens a simple graph of locations the party has reached. Select a destination and confirm a travel proposal; existing departure decisions and travel rules still apply. The server filters unvisited locations before sending the graph. Older saves may show only locations whose visits can be established from their saved state.

## Model configuration

Use **游戏设置 → 模型配置** (Game settings → Model configuration), or `python3 start.py --config` for local configuration. Older clients label the entry 模型设置 (Model settings). The other settings tabs are 游戏偏好 (Game preferences) and 上下文 (Context). Supply a compatible endpoint, model and API key. The local default context capacity is 262,144 tokens (256K); the selected model/service and an explicit configuration can impose a smaller window. A configured default does not enlarge a provider's actual capacity.

Provider presets cover DeepSeek, Kimi, Qwen, Zhipu, Doubao, Gemini, OpenAI, OpenRouter and SiliconFlow.
Choose the API format separately: **Chat Completions, Anthropic Messages, or Responses**.
Endpoints and model IDs remain editable. Connection tests verify streamed text, plus tool calling for
the judgement role; model listings are optional and populate model ID suggestions when available.
Your key must have access to the selected model. Changing destination or API format requires an
explicit key; saved keys are never displayed or automatically forwarded to a new destination.

Cloud BYOK settings are scoped to accounts/worlds. A room member does not gain access to the Keeper's key. Never commit or share `.env.json`, API keys, sessions, databases or private saves. See [operations](../OPERATIONS.md) for deployment and credential boundaries.

## Saves and branches

- Use **快速存档** (Quick save) or **存档管理** (Save management) during play. Loading has progress feedback; restoring a committed local save does not need a new model-generated story.
- Classic AI history can branch from supported completed turns. Old plain-text saves without a reliable turn source cannot be given an invented branch point.
- Structured solo play branches from the current committed state. Restoring it reconciles pending work; it is not a promise to restore every intermediate interaction exactly.
- Do not edit a save to change a frozen module or Skill version. Existing worlds retain their own rule/version bindings unless a supported migration applies.

## Module authoring

### Module management

Start-screen tools are grouped in a single row of icons in the top-right corner. Hover for names; desktop clients also provide an exit control. **导入模组** (Import module) installs a `.trpgmod` package for selection in a new world.

In **local mode**, **模组管理** (Module management) lists installed versions, timelines and save counts. To replace an imported version, first select another module and close any windows still using it, then confirm deletion. Deletion permanently removes that version's module files, every associated world/branch (including archived branches), automatic/manual saves and turn history. Reimporting does not restore those records. The character library, workshop projects and other module versions remain. Built-in modules cannot be deleted; this management API is not available in account-based cloud mode.

### Visual workshop

Click **模组工坊** (Module Workshop) on the start screen. The desktop client opens the editor in a browser; the local backend serves it at `/editor/`. Edit your project, export a `.trpgmod` package and import it into the game. Use a separate world for playtesting.

Desktop versions that include the component updater can offer compatible editor updates independently of a full game release. Use **文件 → 检查编辑器更新** (File → Check editor updates) to check manually. The current page is not forcibly replaced; a successfully activated update is used on the next visit. Hosted deployments update through their deployment process.

### Template and command line

From the repository root with the Python environment active:

```bash
TRPG_AUTHOR_WORK="$(dirname "$PWD")/mid_product/$(basename "$PWD")/work/module-authoring"
mkdir -p -m 700 "$TRPG_AUTHOR_WORK"
cp -r examples/module-template "$TRPG_AUTHOR_WORK/my-module"
python tools/module_packager.py compile "$TRPG_AUTHOR_WORK/my-module"
python tools/module_packager.py pack "$TRPG_AUTHOR_WORK/my-module" "$TRPG_AUTHOR_WORK/my-module.trpgmod"
python tools/module_packager.py validate "$TRPG_AUTHOR_WORK/my-module.trpgmod"
```

Use a fresh authoring directory for the initial copy; do not copy over an existing project. Temporary packages and compiled outputs stay outside the checkout. Start with `manifest.json`, `module.json` and `keeper.md`. A `.trpgmod` file is a ZIP package; supported v1/v2 definitions cover scenes, NPCs, clues, rules and endings. Runtime progress lives in the database, not in the exported authoring package. Deleting a module version is not a backup operation.

Check references, asset paths, public/secret visibility, discovery conditions, failure outcomes, repeat actions and reachable endings. A schema field does not guarantee every execution mode implements that behavior. Maps, tokens, fog of war and arbitrary party splitting are not enabled merely by adding unrecognized fields.

The exact format is defined by [schemas](../../schemas/trpgmod/), [module_format.py](../../src/modules/module_format.py) and the [authoring reference](../MODULE_FORMAT.md).

## Development checks

Backend, from the repository root with the environment active:

```bash
ruff check .
python tools/check_architecture.py
python -m pytest -q
```

Frontend, from `frontend/`:

```bash
npm test
npm run format:check
npm run build
npx playwright install --with-deps chromium
node node_modules/electron/install.js
npm run test:e2e
```

Some checks need PostgreSQL, browser dependencies or explicitly authorized live-model scenarios. Record skips and limitations separately from passing checks. Gameplay changes also need the applicable real playthrough checks; UI changes need actual screenshots at the required sizes. Use isolated local data and consult [development](../DEVELOPMENT.md).

Electron's install scripts are disabled during `npm ci`; the explicit runtime installation above is required before Electron E2E tests.

On Linux CI, the full E2E suite runs under `xvfb-run --auto-servernum`. The workflows also prefetch the official Electron release asset through GitHub's API, verify it against the npm package's bundled SHA256 values and use bounded retries for transient download failures. See [development](../DEVELOPMENT.md#electron-下载与-windows-打包).

To build Windows artifacts on Windows, use `packaging/build_windows.ps1`, optionally with `-UseChinaMirrors`. It verifies/installs Electron and passes the installed distribution to the packager, avoiding a second runtime/checksum download. Building artifacts does not publish them. Deployment and release require their own validation and authorization.

## Diagnostic logs

The backend writes ordinary operational messages to `trpg.log` and bounded per-turn diagnostic events to `diagnostics.jsonl`. Directory precedence, fixed at backend startup:

1. `TRPG_LOG_DIR`, if explicitly set.
2. `TRPG_RUNTIME_ROOT/logs`, when a runtime root is configured. Packaged desktop clients normally use their Electron user-data `runtime/logs` directory.
3. Without a runtime root, a packaged backend uses `logs` beside its executable; source runs use the repository's sibling `mid_product/<repository-name>/work/runtime-logs/` directory.

Run the query tool from the source checkout; use `--log-dir` for a different instance or packaged client:

```bash
python tools/diagnose_turn.py --log-dir /path/to/runtime/logs --list-turns
python tools/diagnose_turn.py --log-dir /path/to/runtime/logs --search "text from the screenshot"
python tools/diagnose_turn.py --log-dir /path/to/runtime/logs --world WORLD_ID --turn TURN_ID --show-content
```

Search a distinctive part of the displayed text, then inspect that turn by ID; a search result alone is not its entire timeline. `dialogue.raw`, `dialogue.rejected`, `dialogue.accepted`, `dialogue.emitted` and `turn.output` distinguish model output, validation, backend callbacks and committed text. `dialogue.emitted` does not prove browser receipt/rendering. `model.completed` needs a successful `status`; a completed model call is not necessarily a committed game turn.

The tool scans retained rotations, including earlier turns. It cannot reconstruct outputs from before the logging upgrade or after rotations expire; use the authorized instance's database turn history for older committed records. JSONL files rotate at about 5 MiB, with three backups. Narrative evidence is enabled by default, is redacted and capped at 65,536 characters per event, and can still contain spoilers or personal information. `TRPG_DIAGNOSTIC_CONTENT=0` disables storing text while retaining lengths/hashes; `TRPG_DIAGNOSTICS=0` disables JSONL. The query tool hides text unless `--show-content` is requested, including JSON output. Never share an entire log without reviewing it. Full coverage and privacy limits are documented in [development](../DEVELOPMENT.md#按回合查看开发日志).

## Technical reference

| Reference (Chinese) | Scope |
| --- | --- |
| [Architecture](../ARCHITECTURE.md) | Legacy vs structured execution, authoritative state, transactions, context and memory |
| [Protocol](../PROTOCOL.md) | Requests, commands, events, permissions and recovery semantics |
| [Development](../DEVELOPMENT.md) | Environment, quality checks, collaboration and documentation |
| [Module format](../MODULE_FORMAT.md) | Authoring workflow, schemas, compatibility and playtesting |
| [Operations](../OPERATIONS.md) | Credentials, backups, environment boundaries, releases and rollback |
| [Project status](../STATUS.md) | Dated release evidence, validated capabilities and open work |

The README start-screen screenshot was captured on 2026-11-08; the other images are from 2026-10-22. All use isolated local data. See [provenance](../screenshots/README.md). They illustrate the interface and do not certify a released version or a complete gameplay test. When the workflow changes, update this guide and both READMEs together.
