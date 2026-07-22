# 数据库与账号系统

服务端的事实来源是关系型数据库：生产环境使用 PostgreSQL，桌面环境默认使用 SQLite。
未设置 `TRPG_DATABASE_URL` 时，数据库文件默认位于 `TRPG_RUNTIME_ROOT/trpg-master.db`；
从源码运行时 `TRPG_RUNTIME_ROOT` 默认即项目根目录（见 `src/config.py`），因此桌面数据库就是
项目根目录下的 `trpg-master.db`，该文件已被 `.gitignore` 排除。两种环境都经过同一套
SQLAlchemy Repository；运行时不再读取 `world_state.json`、`turns/`、`saves/` 或
`player_notes.json`。

## 多人控制面与运行记录

多人功能继续把动态世界正文放在 `world_states.state` JSON/JSONB 中，同时用关系表保证身份、唯一
占用和幂等：

| 表 | 权威职责 |
|---|---|
| `world_members` | owner/player/viewer 成员关系；`world_id + user_id` 唯一 |
| `world_invites` | 邀请哈希、角色、过期、撤销和次数；不保存明文 token |
| `world_investigators` | 账号与角色模板的唯一占用，以及服务端验证后的 `character_ref` |
| `room_actions` | `world_id + action_id` 唯一，保证房间进程重建后仍不会重复执行行动 |
| `turns` / `turn_events` | 完成回合父链、有序公开事件、模型消息与恢复索引 |
| `snapshots` / `save_slots` | 不可变状态快照与存档元数据 |
| `audit_events` | 登录、邀请、加入、角色占用、成员管理和房主移交审计 |

多人世界状态中的 `investigators` 保存每位调查员的 HP、SAN、技能、物品等动态数据；旧引擎仍通过
`pc` 字段操作当前行动者。`src/investigators.py` 在每次权威行动前投影正确调查员，并在回合完成后
同步回集合。客户端提交的调查员正文或 ID 不会直接写入状态。

## 桌面启动与自动迁移

正常情况下直接运行：

```bash
bash start_desktop.sh
```

脚本会自动同步 `requirements.txt` 中缺失的后端依赖，设置桌面 SQLite URL，执行 Alembic，然后运行：

```bash
venv/bin/python tools/import_worlds_to_database.py \
  --runtime-root "$TRPG_RUNTIME_ROOT" \
  --once --replace
```

`--replace` 只会在第一次运行时生效；`--once` 会查询数据库中的
`audit_events.event_type=legacy_import_completed`。成功导入后，后续启动不会再次读取旧目录覆盖数据库。
如果数据库文件被删除，完成标记也会随数据库消失，脚本会从保留的旧 `worlds/` 重新导入。

遇到新增依赖缺失时可手动修复：

```bash
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -c 'import alembic, argon2, psycopg, sqlalchemy'
```

## PostgreSQL 生产部署

生产环境配置示例：

```bash
TRPG_DATABASE_URL=postgresql+psycopg://trpg_app:...@127.0.0.1/trpg_master
TRPG_REQUIRE_AUTH=1
TRPG_ALLOW_REGISTRATION=0
TRPG_ALLOWED_ORIGINS=https://game.example.com
TRPG_BACKUP_PASSPHRASE_FILE=/etc/trpg-master/backup-passphrase
```

把 [`deploy/postgresql-trpg-master.conf`](../deploy/postgresql-trpg-master.conf) 纳入
`postgresql.conf`，并把 [`deploy/pg_hba-trpg-master.conf`](../deploy/pg_hba-trpg-master.conf)
放在更宽泛的 `pg_hba.conf` 规则之前。配置将数据库限制在 `127.0.0.1`，应用只使用独立的
`trpg_app`/`trpg_master` 账号与数据库。实际内存小于 1 GiB 时仍应结合 VM 监控调整参数。

升级生产数据库：

```bash
venv/bin/python -m alembic upgrade head
```

首次导入云端旧世界（先创建 owner 账号）：

```bash
venv/bin/python tools/manage_users.py create account_name
venv/bin/python tools/import_worlds_to_database.py \
  --runtime-root /var/lib/trpg-master \
  --owner account_name \
  --once --replace
```

导入器是幂等的，默认跳过已存在世界。确认数据库内容与备份可恢复之前，不要删除旧目录；
旧目录在新服务中只是离线导入来源。

生产发布由 systemd 的 `ExecStartPre` 执行 Alembic。每日 timer 同时导出 PostgreSQL 和运行
目录，计算校验和后用 GPG 加密；必须定期在隔离环境执行恢复演练。

安装 timer 后执行：

```bash
systemctl enable --now trpg-master-backup.timer
```
