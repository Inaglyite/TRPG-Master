# English guide

[Project overview](../../README.en.md) · [中文文档索引](../INDEX.md)

This guide covers setup, play modes and scenario authoring. The application interface and bundled modules are currently primarily Chinese. The six [Chinese reference documents](#technical-reference) remain the detailed source of current behavior; this guide links to them rather than maintaining a second full protocol specification.

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
| 人类主持 | Human Keeper, using structured commands without model calls |
| AI 辅助主持 | AI-assisted Keeper: private drafts require human approval |
| AI 主持 | Agent Keeper using restricted structured commands |

Local/cloud location, solo/multiplayer participation and Keeper mode are separate choices. Modes do not automatically have identical coverage. In particular, a legacy AI playthrough or a human-hosted structured playthrough does not verify a complete structured Agent playthrough. See [status](../STATUS.md).

Suggested actions are optional; players can enter free-form actions. The server settles checks and state changes. A displayed narrative or animation is not by itself proof that a turn has been committed.

## Model configuration

Use **模型设置** (Model settings) in the application, or `python3 start.py --config` for local configuration. Supply a compatible endpoint, model and API key. The local default context capacity is 262,144 tokens (256K); the selected model/service and an explicit configuration can impose a smaller window. A configured default does not enlarge a provider's actual capacity.

Cloud BYOK settings are scoped to accounts/worlds. A room member does not gain access to the Keeper's key. Never commit or share `.env.json`, API keys, sessions, databases or private saves. See [operations](../OPERATIONS.md) for deployment and credential boundaries.

## Saves and branches

- Use **快速存档** (Quick save) or **存档管理** (Save management) during play. Loading has progress feedback; restoring a committed local save does not need a new model-generated story.
- Classic AI history can branch from supported completed turns. Old plain-text saves without a reliable turn source cannot be given an invented branch point.
- Structured solo play branches from the current committed state. Restoring it reconciles pending work; it is not a promise to restore every intermediate interaction exactly.
- Do not edit a save to change a frozen module or Skill version. Existing worlds retain their own rule/version bindings unless a supported migration applies.

## Module authoring

### Visual workshop

Click **模组工坊** (Module Workshop) on the start screen. The desktop client opens the editor in a browser; the local backend serves it at `/editor/`. Edit your project, export a `.trpgmod` package and import it into the game. Use a separate world for playtesting.

Desktop versions that include the component updater can offer compatible editor updates independently of a full game release. Use **文件 → 检查编辑器更新** (File → Check editor updates) to check manually. The current page is not forcibly replaced; a successfully activated update is used on the next visit. Hosted deployments update through their deployment process.

### Template and command line

From the repository root with the Python environment active:

```bash
cp -r examples/module-template my-module
python tools/module_packager.py compile my-module
python tools/module_packager.py pack my-module dist/my-module.trpgmod
python tools/module_packager.py validate dist/my-module.trpgmod
```

Start with `manifest.json`, `module.json` and `keeper.md`. A `.trpgmod` file is a ZIP package; supported v1/v2 definitions cover scenes, NPCs, clues, rules and endings. Runtime progress lives in the database, not in the exported authoring package.

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
npx playwright install chromium
npm run test:e2e
```

Some checks need PostgreSQL, browser dependencies or explicitly authorized live-model scenarios. Record skips and limitations separately from passing checks. Gameplay changes also need the applicable real playthrough checks; UI changes need actual screenshots at the required sizes. Use isolated local data and consult [development](../DEVELOPMENT.md).

To build Windows artifacts on Windows, use `packaging/build_windows.ps1`, optionally with `-UseChinaMirrors`. Building artifacts does not publish them. Deployment and release require their own validation and authorization.

## Technical reference

| Reference (Chinese) | Scope |
| --- | --- |
| [Architecture](../ARCHITECTURE.md) | Legacy vs structured execution, authoritative state, transactions, context and memory |
| [Protocol](../PROTOCOL.md) | Requests, commands, events, permissions and recovery semantics |
| [Development](../DEVELOPMENT.md) | Environment, quality checks, collaboration and documentation |
| [Module format](../MODULE_FORMAT.md) | Authoring workflow, schemas, compatibility and playtesting |
| [Operations](../OPERATIONS.md) | Credentials, backups, environment boundaries, releases and rollback |
| [Project status](../STATUS.md) | Dated release evidence, validated capabilities and open work |

Screenshots in the READMEs are from a local development build captured on 2026-10-22 with public demonstration dialogue. They illustrate the interface and do not certify a released version or a complete gameplay test. When the workflow changes, update this guide and both READMEs together.
