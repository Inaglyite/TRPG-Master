# TRPG Master

**[中文版 README](README.zh-CN.md)**

An AI Keeper (game master) for Call-of-Cthulhu-style tabletop role-playing. TRPG Master pairs an LLM narrator with deterministic d100 rules tooling: the model writes the story and interprets your intent, while Python code rolls the dice, tracks the world, and enforces the rules. It runs as a local desktop app (Electron) or as a self-hosted, account-based server.

Two playable modules are bundled: **Mansion of Madness** (疯狂宅邸) and **猩红文档** (The Scarlet Documents). The game UI and narrative are in Chinese.

<p align="center">
  <img src="docs/screenshots/menu.png" alt="Start menu with module selection" width="48%"/>
  <img src="docs/screenshots/character-select.png" alt="Investigator selection" width="48%"/>
  <img src="docs/screenshots/gameplay.png" alt="A keeper narrative turn with structured choices" width="48%"/>
  <img src="docs/screenshots/character-panel.png" alt="Gameplay with the investigator panel open" width="48%"/>
</p>

## Features

- **LLM narrates, Python decides.** The model handles prose and intent; skill checks, dice, damage, SAN loss, world state, saves and asset reveals are all resolved by deterministic tools — no hallucinated rules.
- **Server-authoritative combat.** A dedicated state machine handles initiative, opposed d100 rolls, damage, firearm ammo and player defense choices. First lethal aggression against non-hostile NPCs is confirmed with you before the story commits.
- **Modules you can write and share.** Modules are safe, sandboxed `.trpgmod` ZIP packages (JSON + Markdown + assets) with JSON Schema validation, one-click import, side-by-side versions and a v2 format that guarantees the main investigation can never dead-end on a failed roll. A ready-to-copy [template](examples/module-template/manifest.json) is included.
- **Lorebook-powered context.** Character Card V3 lorebooks retrieve module lore per turn with budgets, groups and cooldowns; tiered information boundaries keep the model from spoiling secrets it shouldn't know yet.
- **Saves, journals and timeline branches.** Per-world save slots, a persistent turn journal that survives disconnects, and branching timelines: rewind to any decision point and play out a different choice without rerolling the past.
- **Desktop or self-hosted.** Electron app for Linux/Windows out of the box; for a server deployment you get Argon2id accounts, revocable sessions, per-world permissions, audit events and PostgreSQL persistence.

## Quick Start

### Requirements

- Python 3.10+
- Node.js 20 LTS or newer
- An API key for any OpenAI-compatible endpoint (DeepSeek by default)
- Optional: a Zhipu GLM API key for fast summaries and context compression

### Install

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cd frontend
npm install
npm run build
cd ..
```

### Configure the model

Interactive setup (writes `.env.json` in the project root; the file is git-ignored):

```bash
python3 start.py --config
```

Or create `.env.json` manually — only the first two fields are strictly required:

```json
{
  "api_key": "your-api-key",
  "base_url": "https://api.deepseek.com",
  "flash_model": "deepseek-v4-flash",
  "pro_model": "deepseek-v4-pro",
  "narrative_model": "deepseek-v4-pro",
  "judgement_model": "deepseek-v4-pro",
  "glm_api_key": "optional-glm-key"
}
```

Environment variables take precedence over the file. The full list — model-role presets, lorebook/prompt toggles, database URL, auth and origins for server deployments — is in the fold-out below.

<details>
<summary><strong>Full environment variable reference</strong></summary>

| Variable | Purpose | Default |
|---|---|---|
| `OPENAI_API_KEY` | Main model API key | empty |
| `OPENAI_BASE_URL` | OpenAI-compatible endpoint | `https://api.deepseek.com` |
| `TRPG_FLASH_MODEL` | Model ID for the "Flash" preset in settings | `deepseek-v4-flash` |
| `TRPG_PRO_MODEL` | Model ID for the "Pro" preset in settings | `deepseek-v4-pro` |
| `TRPG_NARRATIVE_MODEL` | Exploration, social and opening narration | `deepseek-v4-pro` |
| `TRPG_JUDGEMENT_MODEL` | Combat, complex tool follow-ups, audits and summary fallback | `deepseek-v4-pro` |
| `TRPG_FORCE_PRO` | Legacy switch; explicitly `0/false/no/off` with no role models set makes both roles use Flash | unset |
| `TRPG_ENABLE_TURN_AUDIT` | Per-turn model audit for diagnostics, `1/true/yes` | off |
| `TRPG_ENABLE_LOREBOOK` | Module lorebook retrieval, `0/false/no/off` to disable | on |
| `TRPG_PROMPT_PROFILE` | `hybrid` uses the module's story spine, falls back to `full` when absent | `hybrid` |
| `TRPG_DYNAMIC_TOOLS` | Send only relevant tool schemas per turn | on |
| `TRPG_STORY_THINKING` | Narration thinking mode: `auto/disabled/enabled/provider` | `auto` |
| `GLM_API_KEY` | Optional summary model API key | empty |
| `GLM_BASE_URL` | GLM endpoint | `https://open.bigmodel.cn/api/paas/v4/` |
| `GLM_MODEL` | GLM model name | `glm-4-flash-250414` |
| `TRPG_MODULE` | Module directory used at startup | `mansion_of_madness` |
| `TRPG_PROJECT_ROOT` | Read-only root for modules, rules and skills | auto-detected |
| `TRPG_RUNTIME_ROOT` | Writable root for `worlds/`, custom characters and profiles | project root in source mode; backend dir when packaged |
| `TRPG_DATABASE_URL` | SQLAlchemy database URL; PostgreSQL required for cloud deployments | desktop defaults to SQLite at `TRPG_RUNTIME_ROOT/trpg-master.db` |
| `TRPG_REQUIRE_AUTH` | Enable account, HTTP and WebSocket permission gates | `0`; the production service sets `1` |
| `TRPG_ALLOWED_ORIGINS` | Origins allowed to carry the login cookie over HTTP/WebSocket | must be set explicitly in production |
| `TRPG_WORLD_ID` | World instance opened by tool subprocesses; usually injected by the engine | the current module's default local world |

</details>

### Run the desktop app

```bash
./start_desktop.sh
```

The launcher activates the venv, installs missing backend dependencies, applies database migrations, and imports legacy save data on first run, then starts the backend and the Electron window. Closing the last window stops the backend automatically.

### Run in the terminal

```bash
python3 start.py
```

### Frontend development mode

```bash
# Terminal 1 — backend on http://127.0.0.1:8765 (WebSocket: ws://127.0.0.1:8765/ws)
source venv/bin/activate
python3 server.py

# Terminal 2 — Vite dev server on http://127.0.0.1:5173
cd frontend && npm run dev

# Terminal 3 — Electron shell
cd frontend && npm run electron:dev
```

## Playing the Game

- **Quick save** overwrites the auto slot `slot_000` of the current world; every completed keeper turn also updates it.
- **Save manager** handles manual slots: load, create, rename, delete.
- **Character / clues** shows your investigator's stats, items, clues and revealed handouts.
- **Model settings** picks narration and judgement models independently (all-Pro, balanced, all-Flash, or custom model IDs).
- **New game** returns to the module and investigator selection flow.

## Documentation

The project documentation is written in Chinese:

- [架构文档](docs/ARCHITECTURE.md) — processes, modules, turn lifecycle, data ownership, extension points
- [接口文档](docs/API.md) — HTTP routes, WebSocket protocol, event ordering, payload schemas
- [数据库与账号](docs/DATABASE.md) — migrations, legacy import, PostgreSQL, backup & restore
- [模组格式](docs/MODULE_FORMAT.md) — the `.trpgmod` v1/v2 package specification for module authors
- [前端架构](docs/FRONTEND_ARCHITECTURE.md) — React components, Zustand stores, protocol boundaries
- [回合性能](docs/PERFORMANCE.md) — turn latency design, metrics and benchmarking
- [开发路线图](docs/ROADMAP.md) — current baseline and the path to multiplayer rooms
- [模组编辑器规划](docs/MODULE_EDITOR_PLAN.md) — internal plan for the module editor (not a user manual)
- [设计依据](docs/DESIGN_RATIONALE.md) — external references behind key design choices
- Historical handoffs and playtest reports live in [docs/archive/](docs/archive/).

## Development

Run these checks before submitting changes:

```bash
venv/bin/python -m unittest discover -s tests -v
venv/bin/python -m ruff check src server.py tools tests
venv/bin/python -m compileall -q src tools server.py tests
cd frontend
npm test
npm run format:check
npm run build
bash -n ../start_desktop.sh
```

Protocol changes must be reflected in [docs/API.md](docs/API.md); changes to save, character or module state structures belong in the data-ownership chapter of [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### Project structure (top level)

```text
trpg-master/
├── server.py        # FastAPI HTTP + WebSocket adapter
├── src/             # engine, LangGraph turn workflow, combat, persistence, module tooling
├── tools/           # deterministic CLI tools (dice, combat, damage, SAN, module packager)
├── skills/          # keeper constraint prompts, loaded on demand
├── rules/           # structured rules data
├── mod/             # bundled modules
├── schemas/trpgmod/ # shared JSON Schemas for the module format
├── examples/        # module project template
├── frontend/        # React + Vite + TypeScript UI and Electron shell
└── docs/            # project documentation (Chinese)
```

The full module map lives in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### Windows packaging

```powershell
powershell -ExecutionPolicy Bypass -File packaging/build_windows.ps1
```

Builds `trpg-server.exe` with PyInstaller, then the NSIS installer and portable builds with electron-builder. Output lands in `frontend/release/`. `.env.json` is never bundled — the Electron setup window collects the endpoint and key on first run.

## Current Limitations

- Single player per world today. Worlds are isolated by `world_id`, but each WebSocket connection still owns a private keeper history; shared GM rooms are the next milestone (see [docs/ROADMAP.md](docs/ROADMAP.md)).
- Desktop mode ships with auth disabled and must not be exposed to the public internet as-is. Server deployments must set `TRPG_REQUIRE_AUTH=1`, TLS and explicit allowed origins (see [docs/DATABASE.md](docs/DATABASE.md)).

## Contributing

Contributions are welcome. Please keep protocol, architecture and module-format documentation in sync with code changes, and make sure the checks above pass before opening a PR. Note that the codebase, in-game content and most documentation are in Chinese.

## License

Code is released under the [MIT License](LICENSE). Bundled module content (narrative text and assets under `mod/`) is included for play and study; check each module's own `license` field before redistributing it.
