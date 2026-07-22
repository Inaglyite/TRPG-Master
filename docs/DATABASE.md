# 数据库与账号系统

服务端的事实来源是关系型数据库：生产环境使用 PostgreSQL，桌面环境默认使用 SQLite。
桌面环境未设置 `TRPG_DATABASE_URL` 时使用 `TRPG_RUNTIME_ROOT/trpg-master.db`，但仍经过同一套
SQLAlchemy Repository；运行时不再读取
`world_state.json`、`turns/`、`saves/` 或 `player_notes.json`。

## 桌面启动与自动迁移

正常情况下直接运行：

```bash
bash start_desktop.sh
```

脚本会自动同步 `requirements.txt` 中缺失的后端依赖，设置桌面 SQLite URL，执行 Alembic，然后运行：

```bash
python tools/import_worlds_to_database.py \
  --runtime-root "$TRPG_RUNTIME_ROOT" \
  --once --replace
```

`--replace` 只会在第一次运行时生效；`--once` 会查询数据库中的
`audit_events.event_type=legacy_import_completed`。成功导入后，后续启动不会再次读取旧目录覆盖数据库。
如果数据库文件被删除，完成标记也会随数据库消失，脚本会从保留的旧 `worlds/` 重新导入。

遇到新增依赖缺失时可手动修复：

```bash
source venv/bin/activate
python -m pip install -r requirements.txt
python -c 'import alembic, argon2, psycopg, sqlalchemy'
```

当前桌面数据库默认位置是项目根目录的 `trpg-master.db`，该文件已被 `.gitignore` 排除。

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
alembic upgrade head
```

首次导入云端旧世界（先创建 owner 账号）：

```bash
python tools/manage_users.py create account_name
python tools/import_worlds_to_database.py \
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
