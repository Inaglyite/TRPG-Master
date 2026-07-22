# Azure 部署与恢复

本文记录当前单进程权威房间服务的部署约束。多人版本必须先进入隔离 staging；生产只从通过质量
门禁的 `master` 发布。

## 运行拓扑

| 环境 | 应用目录 | 数据目录 | 服务 | 回环端口 | Cookie |
|---|---|---|---|---|---|
| staging | `/opt/trpg-master-staging` | `/var/lib/trpg-master-staging` | `trpg-master-staging.service` | 8766 | `trpg_staging_session` |
| production | `/opt/trpg-master` | `/var/lib/trpg-master` | `trpg-master.service` | 8765 | `trpg_session` |

两个环境必须使用不同 PostgreSQL 数据库/最小权限用户、不同 `TRPG_DATABASE_URL`、不同
`TRPG_ALLOWED_ORIGINS` 和不同备份目标。任何发布脚本都只切换 `/opt/.../current` 符号链接，不把
数据库、用户模组、世界目录或日志放进 release 目录。

多人第一版固定 `uvicorn --workers 1`。`RoomManager` 在进程内保证一世界一引擎；在引入跨进程房间
租约和事件总线前不得用增加 worker 的方式扩容。小内存 VM 的 staging 默认最多同时加载两个房间，
生产可通过 `TRPG_MAX_ACTIVE_ROOMS` 按实测资源调整。

## 首次安装 staging

1. 创建独立数据库和只拥有该数据库对象权限的应用用户。
2. 写入 `/etc/trpg-master/staging.env`，至少包含：

   ```bash
   TRPG_DATABASE_URL=postgresql+psycopg://trpg_staging:...@127.0.0.1/trpg_master_staging
   TRPG_ALLOWED_ORIGINS=https://服务器地址:8443
   TRPG_BACKUP_PASSPHRASE_FILE=/etc/trpg-master/staging-backup-passphrase
   ```

3. 安装 `deploy/trpg-master-staging.service`、`deploy/nginx-trpg-master-staging.conf` 和
   `deploy/install-staging-release.sh`，后者固定安装为
   `/usr/local/sbin/trpg-install-staging-release`。
4. 执行 `nginx -t` 后才 reload；确认 Azure NSG 仅向测试来源开放 8443。
5. 从 GitHub 手动运行 `deploy-multiplayer-staging`。它不会由分支 push 自动触发，也不会修改生产
   symlink。

发布包必须包含 `alembic.ini`、`migrations/` 和运行时使用的 `tools/`。systemd 在每次启动前运行
`alembic upgrade head`；迁移失败会阻止新版本启动。

手动 staging 工作流在上传前会启动一次性 PostgreSQL 17 service，实际执行全部 Alembic 迁移和
`tests/test_postgresql_integration.py`，验证 JSONB、成员/邀请/调查员关系及房间行动唯一约束。SQLite
测试不能替代这道发布门禁。

## 上线检查

- `curl http://127.0.0.1:8766/api/health` 与外部 HTTPS 健康检查均成功；
- 浏览器和 Electron 使用 WSS，Cookie 为 HttpOnly/Secure/SameSite=Lax 且与生产 Cookie 隔离；
- 两个独立账号完成创建/加入房间、不同调查员、准备、开局和一个完整回合；
- 非当前行动者、伪造调查员、重复 `action_id` 和无权限世界连接均被服务端拒绝；
- 私人事件不出现在另一账号的实时帧、ack 补发或 `room_full_state`；
- 回合中断开一端、刷新、服务重启后公开历史与当前行动者恢复且不重复调用模型；
- `systemctl restart`、当前 release 回滚、数据库备份恢复和 Nginx 回滚均演练成功。

## 备份与恢复

`deploy/backup-trpg-master.sh` 使用 `pg_dump --format=custom`，连同运行目录、校验和一起用 GPG AES256
加密。备份成功不等于可恢复：每次发布候选至少在隔离数据库执行一次以下演练：

1. 解密归档并验证 `SHA256SUMS`；
2. 创建空数据库，使用 `pg_restore --no-owner --no-acl` 导入；
3. 指向恢复数据库启动同一 release，运行 Alembic 并检查账号、成员、世界、调查员、回合和存档；
4. 用两个客户端重连一个已完成回合，确认没有重放 incomplete turn；
5. 删除演练数据库，不触碰生产数据库和 `/var/lib/trpg-master`。

回滚应用版本只切换 release symlink。数据库迁移若不向后兼容，必须在发布前提供经过演练的降级或
前滚修复方案，不能在生产上临时执行破坏性 SQL。
