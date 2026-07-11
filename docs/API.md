# 接口文档

本文记录 `server.py` 当前公开的 HTTP 与 WebSocket 协议。协议尚未版本化；修改消息字段时应同步更新本文和 `frontend/src/ws.ts`。

## 1. 基本约定

| 项目 | 值 |
|---|---|
| HTTP Base URL | `http://127.0.0.1:8765` |
| WebSocket URL | `ws://127.0.0.1:8765/ws` |
| 编码 | UTF-8 |
| WebSocket 数据 | JSON text frame |
| 鉴权 | 无 |
| OpenAPI | `/docs`、`/openapi.json` |

服务端进程实际监听 `0.0.0.0:8765`，但接口按本地桌面应用设计。没有鉴权、限流、房间隔离或 TLS，不应直接暴露到公网。

WebSocket 消息都有一个字符串字段 `type`：

```json
{
  "type": "ping"
}
```

未识别的 `type` 当前会被静默忽略；非法 JSON text frame 也会被忽略。

## 2. HTTP API

### 2.1 路由总览

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/health` | 后端就绪检查 |
| `GET` | `/api/theme` | 当前活动模组主题 |
| `GET` | `/api/modules` | 模组列表与活动模组 |
| `GET` | `/api/characters` | 当前模组可选调查员 |
| `POST` | `/api/modules/switch` | 切换进程级活动模组 |
| `GET` | `/api/assets/{module_name}/{filename}` | 读取模组图片素材 |
| `GET` | `/` | 已构建前端或构建提示 |

### 2.2 `GET /api/health`

用于启动脚本和 Electron 等待后端就绪。

响应：

```json
{
  "ok": true,
  "module": "mansion_of_madness"
}
```

### 2.3 `GET /api/theme`

返回当前活动模组的 `theme.json`。文件不存在时返回最小主题。

示例：

```json
{
  "title": "疯狂宅邸",
  "subtitle": "A TRPG of Madness & Mystery",
  "description": "...",
  "colors": {
    "bg": "#14100c",
    "text": "#ddd0bc",
    "gold": "#c8a24e"
  },
  "fonts": {
    "body": "Georgia, serif",
    "mono": "Courier New, monospace"
  },
  "startButtonText": "点燃烛火，开始故事"
}
```

前端实际使用的颜色 key 映射见 `frontend/src/main.ts`。未知 key 会被忽略。

### 2.4 `GET /api/modules`

扫描 `mod/*/module.md`。`title` 与 `description` 来自各模组的 `theme.json`。

响应：

```json
{
  "modules": [
    {
      "id": "mansion_of_madness",
      "title": "疯狂宅邸",
      "description": "..."
    },
    {
      "id": "猩红文档",
      "title": "猩红文档",
      "description": "..."
    }
  ],
  "active": "mansion_of_madness"
}
```

### 2.5 `GET /api/characters`

列出当前活动模组的新游戏候选调查员。

响应结构：

```json
{
  "module": "mansion_of_madness",
  "groups": [
    {
      "id": "default",
      "title": "默认调查员",
      "characters": [
        {
          "ref": {
            "source": "default",
            "file": "黄千陆.json",
            "path": "characters/default/黄千陆.json"
          },
          "id": "default:黄千陆",
          "name": "黄千陆",
          "occupation": "侦探/警方顾问",
          "age": 32,
          "source": "default",
          "source_label": "默认调查员",
          "hp": 10,
          "max_hp": 10,
          "san": 70,
          "max_san": 70,
          "reputation": 0,
          "completed_modules": 0,
          "top_skills": [
            {"id": "spot_hidden", "value": 70}
          ],
          "description": "..."
        }
      ]
    }
  ]
}
```

固定分组及来源：

| group/source | 数据来源 |
|---|---|
| `profile` | `profiles/player_profile.json` 中的长期角色 |
| `default` | `characters/default/*.json` |
| `module` | `mod/<active>/characters/*.json` |
| `custom` | `characters/custom/*.json` |

### 2.6 `POST /api/modules/switch`

请求：

```json
{
  "module": "猩红文档"
}
```

成功响应：

```json
{
  "ok": true,
  "module": "猩红文档"
}
```

模组不存在时仍返回 HTTP 200：

```json
{
  "ok": false,
  "error": "模组'unknown'不存在"
}
```

此接口修改进程级全局配置，不会自动重置 `world_state.json`，也不会主动通知已连接的 WebSocket 客户端。桌面前端使用 WebSocket `switch_module`，因为它会同时刷新主题、角色与存档列表。

### 2.7 `GET /api/assets/{module_name}/{filename}`

返回 `mod/<module_name>/assets/<filename>`，支持中文与 URL 编码文件名。

- 成功：文件内容，`Content-Type` 由扩展名推断。
- 文件不存在：HTTP 404，`{"error":"not found"}`。
- 路径越界：HTTP 403，`{"error":"forbidden"}`。

### 2.8 静态前端

当 `frontend/dist` 存在时，它被挂载到 `/`。否则根路由返回构建提示：

```html
<h2>前端未构建。运行: cd frontend && npm run build</h2>
```

## 3. WebSocket 生命周期

连接成功后，服务端创建新的 `GameEngine`，准备 system prompt，然后按以下顺序主动发送：

1. `module_list`
2. `character_list`
3. `theme`
4. `save_list`

如果引擎初始化失败，服务端发送 `error` 并关闭连接。

前端建立连接后通常发送：

1. `ping`
2. `state`

没有协议级 request ID。请求与响应通过事件类型和客户端状态关联；一个连接内的 GM 回合由服务端串行执行。

## 4. 客户端发送消息

### 4.1 总览

| `type` | 关键字段 | 作用 |
|---|---|---|
| `ping` | 无 | 心跳 |
| `switch_module` | `module` | 切换活动模组并刷新开局数据 |
| `start` | `character_ref` | 新游戏 |
| `action` | `content` | 提交玩家动作 |
| `suggest_reply` | `confirmed` | 回复检定确认 |
| `decision_reply` | `decision_id`, `option_id` | 回复战斗等多选决定 |
| `state` | 无 | 请求角色与线索状态 |
| `character_list` | 无 | 请求角色列表 |
| `save` | `manual` | 快速保存；正式客户端使用 `manual:false` |
| `save_create` | 无 | 新建手动槽 |
| `save_list` | 无 | 请求存档列表 |
| `save_load` | `slot_id` | 加载指定槽并继续 GM 回合 |
| `save_delete` | `slot_id` | 删除手动槽 |
| `save_rename` | `slot_id`, `label` | 修改存档显示名 |
| `settle_case` | `ending_type`, `title`, `summary` | 确认结局并写入长期履历 |
| `quit` | 无 | 保存自动槽并结束当前 WS 会话 |
| `continue` | `slot_id?` | 兼容接口：加载存档并继续 |
| `load` | 无 | 兼容接口：加载最新存档，不自动触发续写 |

### 4.2 心跳

请求：

```json
{"type":"ping"}
```

响应：

```json
{"type":"pong"}
```

### 4.3 切换模组

```json
{
  "type": "switch_module",
  "module": "猩红文档"
}
```

成功后依次返回新的 `theme`、`module_list`、`character_list` 和 `save_list`。切换只更新运行路径和 system prompt，不重置世界状态；新游戏的 `start` 才会从初始状态复制。

### 4.4 开始新游戏

```json
{
  "type": "start",
  "character_ref": {
    "source": "default",
    "file": "黄千陆.json",
    "path": "characters/default/黄千陆.json"
  }
}
```

`character_ref` 可为 `null`，此时按 `profile -> default -> module -> custom` 的顺序选择第一个可用角色。

角色引用支持四种形态：

```json
{"source":"profile","id":"default:黄千陆"}
```

```json
{"source":"default","file":"黄千陆.json"}
```

```json
{"source":"custom","file":"my-investigator.json"}
```

```json
{"source":"module","module":"猩红文档","file":"黄千陆.json"}
```

`path` 是服务端返回给 UI 的说明字段，解析角色时以 `source/id/file/module` 为准。

开始新游戏会：

1. 用 `world_state_initial.json` 覆盖当前 `world_state.json`。
2. 把选中的调查员复制到 `world_state.pc`。
3. 重建 system prompt 与会话消息。
4. 返回 `gm_turn_start` 并异步运行开场 GM 回合。

### 4.5 玩家动作

```json
{
  "type": "action",
  "content": "检查书桌抽屉里是否藏着文件"
}
```

服务端不发送单独 ACK。一个典型回合为：

```text
gm_turn_start（仅新游戏/读档时）
narrative_chunk * N
tension? / suggest_check? / decision_request? / dice_result? / handout? / glm_summary?
narrative_chunk * N
done
```

普通 `action` 当前不会先发送 `gm_turn_start`；前端在发送动作时自行进入等待状态。

### 4.6 回复检定确认

服务端发送 `suggest_check` 后，客户端回复：

```json
{
  "type": "suggest_reply",
  "confirmed": true
}
```

`confirmed:false` 表示放弃。服务端工作线程最多等待 120 秒；超时按未确认处理。

### 4.7 回复多选决定

服务端发送 `decision_request` 后，客户端必须回传原决定 ID 和选项 ID：

```json
{
  "type": "decision_reply",
  "decision_id": "a9bc13d42e11",
  "option_id": "dodge"
}
```

服务端只接受当前活动决定中列出的选项。工作线程最多等待 120 秒；超时会采用服务端提供的 `default_option`，并发送 `decision_resolved`。

### 4.8 请求当前状态

```json
{"type":"state"}
```

响应为 `state_data`。注意：`data` 和 `clues` 当前是 JSON 编码后的字符串，不是直接嵌套对象，客户端需要再次 `JSON.parse`。

### 4.9 快速存档

```json
{
  "type": "save",
  "manual": false
}
```

写入当前模组的 `slot_000`，响应 `saved`。

`manual:true` 是早期兼容字段；当前持久化层会把空 `slot_id` 同样解析成自动槽。新客户端必须使用 `save_create` 创建手动槽，不应依赖 `manual:true`。

### 4.10 新建手动存档

```json
{"type":"save_create"}
```

服务端查找最小可用编号，创建 `slot_001`、`slot_002` 等，响应 `saved`。

### 4.11 加载存档

```json
{
  "type": "save_load",
  "slot_id": "slot_001"
}
```

成功顺序：

1. `loaded`
2. `gm_turn_start`
3. GM 回合事件
4. `done`

读档会恢复 `snapshot.json` 到当前 `world_state.json`，并在模型消息中加入“基于存档续写、不要重新开场”的指令。

### 4.12 删除存档

```json
{
  "type": "save_delete",
  "slot_id": "slot_001"
}
```

成功返回 `save_deleted`。`slot_000` 不允许删除，会返回 `error`。

### 4.13 重命名存档

```json
{
  "type": "save_rename",
  "slot_id": "slot_001",
  "label": "进入东翼之前"
}
```

重命名只修改 `meta.json.label`，不改槽位目录名。空字符串表示 UI 回退显示 `scene_name`。

### 4.14 结算案件

```json
{
  "type": "settle_case",
  "ending_type": "good",
  "title": "封印重归寂静",
  "summary": "调查员阻止了仪式并带回关键证据。"
}
```

常见 `ending_type`：`good`、`secret`、`neutral`、`bad`。成功后服务端：

1. 更新 `profiles/player_profile.json`。
2. 更新当前 PC 的 career。
3. 保存 `slot_000`。
4. 发送 `case_settled`。
5. 发送新的 `character_list`。
6. 当前实现额外发送一个无 payload 的 `{"type":"state"}` 兼容刷新标记；正式客户端应主动发送客户端 `state` 请求并等待 `state_data`。

### 4.15 退出当前会话

```json
{"type":"quit"}
```

服务端保存 `slot_000`，返回 `quit_ok`，然后结束当前 WebSocket 消息循环。Electron 窗口生命周期由桌面壳单独管理。

## 5. 服务端发送事件

### 5.1 初始化与目录事件

#### `module_list`

```json
{
  "type": "module_list",
  "modules": [
    {"id":"mansion_of_madness","title":"疯狂宅邸","description":"..."}
  ],
  "active": "mansion_of_madness"
}
```

#### `character_list`

```json
{
  "type": "character_list",
  "module": "mansion_of_madness",
  "groups": []
}
```

`groups` 与 HTTP `/api/characters` 相同。

#### `theme`

```json
{
  "type": "theme",
  "theme": {
    "title": "疯狂宅邸",
    "colors": {},
    "fonts": {}
  }
}
```

#### `save_list`

```json
{
  "type": "save_list",
  "saves": [
    {
      "id": "slot_000",
      "label": "入口大厅",
      "created_at": "2026-07-10T09:16:51.718992",
      "scene_id": "entrance_hall",
      "scene_name": "入口大厅",
      "character_id": "default:黄千陆",
      "character_name": "黄千陆",
      "character_source": "default",
      "character_source_path": "characters/default/黄千陆.json",
      "hp": "10/10",
      "san": "70/70",
      "clue_count": 2,
      "message_count": 46
    }
  ]
}
```

`label` 仅在重命名后存在。列表按 `created_at` 倒序。

### 5.2 回合事件

#### `gm_turn_start`

```json
{"type":"gm_turn_start"}
```

表示服务端开始新游戏或读档后的 GM 回合。前端隐藏开始界面并禁用输入。

#### `narrative_chunk`

```json
{
  "type": "narrative_chunk",
  "text": "雨水沿着宅邸的窗棂缓缓滑落。"
}
```

同一回合可发送任意数量，`text` 是增量而不是完整消息。

#### `tension`

```json
{
  "type": "tension",
  "text": "命运的齿轮开始转动……",
  "category": "dice"
}
```

`category` 常见值：`dice`、`sanity`、`combat`、`pro`。

#### `suggest_check`

```json
{
  "type": "suggest_check",
  "skill": "侦查",
  "attribute": "INT",
  "dc": 15,
  "dc_label": "中等",
  "description": "仔细检查书桌上的异常痕迹"
}
```

客户端必须用 `suggest_reply` 回复。

#### `decision_request`

战斗状态机需要玩家选择防御方式，或确认对非敌对 NPC 的不可逆暴力/武力威胁时发送。对于从玩家最新输入中明确识别出的攻击和武力威胁，`decision_request` 会在首个 `narrative_chunk` 与 `tension` 事件之前发送；取消后本轮直接发送 `done`，世界状态不变。
防御示例：

```json
{
  "type": "decision_request",
  "id": "a9bc13d42e11",
  "kind": "combat_defense",
  "title": "教徒正在攻击你",
  "description": "教徒挥拳扑来。",
  "options": [
    {"id":"dodge","label":"闪避","description":"只求避开这次攻击。"},
    {"id":"fight_back","label":"反击","description":"胜出时可造成伤害。"},
    {"id":"no_defense","label":"不防御","description":"让攻击方正常检定。"}
  ],
  "default_option": "dodge"
}
```

不可逆暴力示例：

```json
{
  "type": "decision_request",
  "id": "c28e71af34d0",
  "kind": "irreversible_violence",
  "target_id": "bryce_fallon",
  "title": "你真的要攻击布莱斯·法伦吗？",
  "description": "法伦目前并未主动敌对。调查员通常只在认为必要时使用暴力，这一次是否必要仍由你决定。攻击可能引来报警、法律、声望、案件或理智后果。",
  "options": [
    {"id":"cancel_violence","label":"暂不攻击","description":"保留行动与当前资源。"},
    {"id":"confirm_violence","label":"仍然攻击","description":"接受后果并进行结算。"}
  ],
  "default_option": "cancel_violence",
  "roleplay_context": {
    "violence_stance": "conditional",
    "violence_stance_label": "仅在必要时使用暴力",
    "beliefs": "以头脑而非暴力追查真相",
    "traits": ["克制而审慎"]
  }
}
```

`backstory.violence_stance` 支持 `avoidant`、`conditional`、`unrestrained`。它只影响确认文案和 Agent 的人物冲突叙事；三个值都会保留确认步骤，且超时一律默认取消。取消标签可能随立场显示为“克制冲动”“暂不攻击”或“改换做法”，客户端应使用服务端返回的 `label`。

武力威胁示例：

```json
{
  "type": "decision_request",
  "id": "f17c3b22aa10",
  "kind": "coercive_threat",
  "target_id": "bryce_fallon",
  "title": "你真的要用武力威胁布莱斯·法伦吗？",
  "description": "法伦目前并未主动敌对。用武器胁迫他人明显违背了调查员避免主动暴力的行为倾向。即使不开枪，这也可能破坏关系、引来报警或改变案件走向。",
  "options": [
    {"id":"cancel_threat","label":"收起武器","description":"收起武器，不消耗行动或弹药。"},
    {"id":"confirm_threat","label":"继续威胁","description":"接受关系、法律与案件后果。"}
  ],
  "default_option": "cancel_threat",
  "roleplay_context": {
    "violence_stance": "avoidant",
    "violence_stance_label": "避免主动暴力",
    "beliefs": "以头脑而非暴力追查真相",
    "traits": ["克制而审慎"]
  }
}
```

客户端用 `decision_reply` 回复。`id` 用于拒绝迟到或不属于当前请求的回复。

#### `decision_resolved`

```json
{
  "type": "decision_resolved",
  "decision_id": "a9bc13d42e11",
  "option_id": "dodge",
  "automatic": false
}
```

每次决定完成后发送。`automatic:true` 表示等待超时后使用了 `default_option`；客户端应关闭仍显示的决定弹窗。不可逆暴力和武力威胁超时分别默认返回 `cancel_violence`、`cancel_threat`，不会产生掷骰、弹药或回合消耗。

#### `dice_result`

技能检定示例：

```json
{
  "type": "dice_result",
  "summary": "侦查 70，d100 = 32，困难成功",
  "roll_data": {
    "skill": "spot_hidden",
    "skill_name": "侦查",
    "skill_value": 70,
    "d100_roll": 32,
    "tens_dice": [30],
    "ones_dice": 2,
    "bonus_dice": 0,
    "penalty_dice": 0,
    "difficulty_regular": 70,
    "difficulty_hard": 35,
    "difficulty_extreme": 14,
    "level": "hard_success",
    "success": true,
    "is_push": false
  }
}
```

通用骰示例：

```json
{
  "type": "dice_result",
  "summary": "2d6 = 9",
  "roll_data": {
    "spec": "2d6",
    "sides": 6,
    "count": 2,
    "modifier": 0,
    "advantage": false,
    "disadvantage": false,
    "rolls": [4, 5],
    "total": 9
  }
}
```

战斗对抗会把攻击与防御 d100 一起发送，`combat:true` 表示它来自战斗状态机：

```json
{
  "type": "dice_result",
  "summary": "教徒攻击调查员：防御成功",
  "roll_data": {
    "spec": "2d100",
    "sides": 100,
    "count": 2,
    "rolls": [62, 31],
    "total": 93,
    "combat": true
  }
}
```

`roll_data` 取决于工具，客户端必须容忍未知字段或空对象。

#### `glm_summary`

```json
{
  "type": "glm_summary",
  "text": "检定成功：你注意到抽屉锁曾被撬动。"
}
```

用于复杂工具的快速摘要。50 回合静默上下文压缩成功时通常不发送该事件。

#### `handout`

```json
{
  "type": "handout",
  "file": "布莱斯·法伦.png",
  "label": "法伦教授",
  "asset_data_uri": "data:image/png;base64,...",
  "asset_url": "/api/assets/猩红文档/布莱斯·法伦.png",
  "entity_type": "npc",
  "entity_id": "professor_fallon"
}
```

`entity_type` 为 `npc`、`scene` 或 `clue`。Electron 使用 `asset_data_uri`，浏览器可使用 `asset_url`。

#### `game_over`

```json
{
  "type": "game_over",
  "ending_type": "good",
  "title": "封印重归寂静",
  "summary": "..."
}
```

它是结局提议，不会立刻写入长期履历。玩家确认后客户端再发送 `settle_case`。

#### `done`

```json
{"type":"done"}
```

表示本轮叙事、工具和自动存档已经完成。前端据此恢复操作并请求最新 `state`。

### 5.3 状态事件

#### `state_data`

```json
{
  "type": "state_data",
  "data": "{\"name\":\"黄千陆\",\"hp\":10,\"max_hp\":10,\"san\":70,\"max_san\":70}",
  "clues": "{\"investigation\":[],\"event\":[],\"task\":[],\"npc\":[]}"
}
```

解析后 `data` 是当前 `world_state.pc`。`clues` 是按分类组织的对象：

```json
{
  "investigation": [
    {
      "id": "clue_001",
      "text": "抽屉锁曾被撬动",
      "type": "discovered",
      "tier": 1,
      "source": "skill_check",
      "related_npcs": [],
      "related_scenes": ["study"],
      "discovered_at": "...",
      "asset": {
        "id": "damaged_lock",
        "file": "抽屉锁.png",
        "label": "受损的锁",
        "asset_data_uri": "data:image/png;base64,...",
        "asset_url": "/api/assets/module/抽屉锁.png"
      }
    }
  ],
  "event": [],
  "task": [],
  "npc": []
}
```

已发放 NPC 图片会以 `type:"profile"` 的公开人物档案追加到 `npc` 分类；不会包含 secret、技能或其他守秘信息。

### 5.4 存档事件

#### `saved`

```json
{
  "type": "saved",
  "ok": true,
  "slot_id": "slot_000"
}
```

#### `loaded`

```json
{
  "type": "loaded",
  "ok": true,
  "slot_id": "slot_001",
  "count": 42
}
```

兼容 `load` 请求返回的 `loaded` 可能没有 `slot_id`。

#### `save_deleted`

```json
{
  "type": "save_deleted",
  "slot_id": "slot_001"
}
```

#### `save_renamed`

```json
{
  "type": "save_renamed",
  "slot_id": "slot_001",
  "label": "进入东翼之前",
  "ok": true
}
```

### 5.5 案件与退出事件

#### `case_settled`

成功：

```json
{
  "type": "case_settled",
  "ok": true,
  "character_id": "default:黄千陆",
  "case": {
    "module": "mansion_of_madness",
    "ending_type": "good",
    "title": "封印重归寂静",
    "summary": "...",
    "san_delta": -4,
    "hp_delta": 0,
    "reputation_delta": 3,
    "completed_at": "2026-07-10T10:00:00"
  },
  "career": {
    "reputation": 3,
    "titles": [],
    "known_contacts": [],
    "completed_modules": ["mansion_of_madness"],
    "case_history": [
      {
        "module": "mansion_of_madness",
        "ending_type": "good",
        "title": "封印重归寂静",
        "summary": "...",
        "san_delta": -4,
        "hp_delta": 0,
        "reputation_delta": 3,
        "completed_at": "2026-07-10T10:00:00"
      }
    ]
  }
}
```

失败：

```json
{
  "type": "case_settled",
  "ok": false,
  "error": "当前世界状态没有 pc"
}
```

#### `quit_ok`

```json
{"type":"quit_ok"}
```

### 5.6 错误事件

```json
{
  "type": "error",
  "message": "未找到存档。"
}
```

目前错误只有面向用户的 `message`，没有稳定错误码。客户端不应通过中文文案分支业务逻辑。

## 6. 推荐事件时序

### 6.1 新游戏

```mermaid
sequenceDiagram
    participant C as Client
    participant S as Server
    C->>S: start(character_ref)
    S-->>C: gm_turn_start
    loop Streaming
        S-->>C: narrative_chunk
    end
    opt Check
        S-->>C: suggest_check
        C->>S: suggest_reply
        S-->>C: dice_result
    end
    opt Combat defense
        S-->>C: decision_request
        C->>S: decision_reply
        S-->>C: decision_resolved
        S-->>C: dice_result
    end
    opt Image
        S-->>C: handout
    end
    S-->>C: done
    C->>S: state
    S-->>C: state_data
```

### 6.2 读档

```mermaid
sequenceDiagram
    participant C as Client
    participant S as Server
    C->>S: save_load(slot_id)
    S-->>C: loaded
    S-->>C: gm_turn_start
    S-->>C: narrative_chunk * N
    S-->>C: done
    C->>S: state
    S-->>C: state_data
```

### 6.3 快速存档与管理

```mermaid
sequenceDiagram
    participant C as Client
    participant S as Server
    C->>S: save(manual=false)
    S-->>C: saved(slot_000)
    C->>S: save_list
    S-->>C: save_list
```

## 7. 兼容性与已知限制

- 协议没有 `version`、request ID 或结构化 error code。
- `state_data.data` 与 `state_data.clues` 是 JSON 字符串，这是历史格式。
- `continue`、`load` 与 `save.manual` 属于兼容接口；桌面前端使用 `save_load`、`save_create` 和 `save(manual:false)`。
- 前端仍包含对 `save_available` 的兼容处理，但当前服务端不会发送该事件。
- `settle_case` 后的服务端 `state` 只是刷新标记，不包含状态数据。
- 同一进程中的多条连接共享活动模组与世界文件，不能用作隔离的多人房间。
- HTTP 切换模组不会广播；优先使用 WebSocket `switch_module`。
- API 没有鉴权。开发远程客户端前必须先增加身份、房间隔离和权限边界。
