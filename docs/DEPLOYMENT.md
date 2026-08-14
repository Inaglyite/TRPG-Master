# 部署与恢复

本文记录当前单进程权威房间服务的部署约束。多人版本必须先进入隔离 staging；生产只从通过质量
门禁的 `master` 发布：`master` push 经 quality 全部通过（绿）后，由维护者人工在 GitHub Actions
页面触发 `deploy-azure`（workflow_dispatch），不得由其他工作流自动级联；正式发布前仍需在
staging 完成验收并获得明确确认。

正式机的 GitHub Actions 只允许免密调用 root-owned、参数校验严格的固定入口
`/usr/local/sbin/trpg-activate-release`。入口只接受完整 release SHA，并从 root
控制的 incoming 目录派生归档路径；它会校验目录、文件所有者、权限、硬链接和归档
复制过程，再调用当前的 release installer。不得为 `/tmp` 下的临时脚本授予 NOPASSWD，
也不得使用 `sudo -S`、保存密码或 expect 脚本绕过。首次安装或重建入口必须由授权维护者
交互式执行 `deploy/install-release-activation-entrypoint.sh`，并通过 `visudo -cf`
校验生成的最小 sudoers 规则。入口安装前，正式 workflow 仍会在最后激活步骤失败。

首次在正式机安装入口（只执行一次，或入口文件变更后由授权维护者执行）：

```bash
scp deploy/trpg-activate-release deploy/install-release-activation-entrypoint.sh <azure-host>:/tmp/
ssh -tt <azure-host> \
  "sudo bash /tmp/install-release-activation-entrypoint.sh /tmp <deploy-user>"
```

`<deploy-user>` 必须与 GitHub Environment 中的 `AZURE_VM_USER` 相同。安装器会创建
root 控制的 `/var/lib/trpg-master-release/incoming`，仅允许该用户写入，并把入口安装为
`root:root 0755`；sudoers 只放行 `/usr/local/sbin/trpg-activate-release`。确认安装输出和
`sudo -l -U <deploy-user>` 后，删除 `/tmp` 中的两个安装文件。之后 workflow 只上传归档、
执行 `chmod 600` 和 `sudo -n` 固定入口，不再上传或执行 `/tmp` 脚本。

## 正式域名与 TLS

正式浏览器与 Electron origin 固定为 `https://trpggame.xyz`。DNS 的 `@` 与 `www` A 记录均指向
Azure VM `20.249.11.57`；Nginx 将 HTTP 和 `www.trpggame.xyz` 永久跳转到裸域名，并继续代理
WebSocket Upgrade。Azure NSG 必须允许公网 TCP 80（Let's Encrypt HTTP-01 与跳转）和 443，应用
端口 8765/8766 只监听回环地址，不向公网开放。

生产证书由 Certbot 管理，路径固定为：

```text
/etc/letsencrypt/live/trpggame.xyz/fullchain.pem
/etc/letsencrypt/live/trpggame.xyz/privkey.pem
```

首次安装或重建证书：

```bash
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d trpggame.xyz -d www.trpggame.xyz
sudo certbot renew --dry-run --no-random-sleep-on-renew
```

`certbot.timer` 必须保持 enabled。生产环境使用
`TRPG_ALLOWED_ORIGINS=https://trpggame.xyz`；不要把 `www` 或 IP 地址同时作为独立游戏 origin，
以免产生两套 Cookie 与 WebSocket Origin。执行发布安装器前必须确认正式证书已经存在，因为仓库的
生产 Nginx 模板直接引用上述路径。

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

### 局域网 Raspberry Pi 测试环境

开发期另有一套与 Azure 生产完全无关的 ARM64 staging，当前通过
`https://192.168.5.22:8443`（或局域网 mDNS 可用时的
`https://raspberrypi.local:8443`）访问。它复用上表 staging 的目录、服务、端口和 Cookie 命名，
但数据库、运行目录、账号、世界和备份都只存在于树莓派。生产域名不得指向或代理到这台设备。

- 当前实例把 Nginx 明确绑定到 `192.168.5.22:8443`，不监听其他网卡或 IPv6；应用 8766 与
  PostgreSQL 5432 只监听 `127.0.0.1`。把主机级 vhost 写到
  `/etc/trpg-master/staging-nginx.conf`（root 所有、不可 group/world 写）后，staging 安装器
  每次发布都会校验并保留该主机绑定，不再用 release 中的通用 vhost 覆盖。
- TLS 使用覆盖当前 IP 与 `raspberrypi.local` 的自签名证书。浏览器可在明确确认后接受；Electron
  测试前应把证书导入客户端系统信任库。若 DHCP 地址变化，先更新地址保留并重新签发证书与
  `TRPG_ALLOWED_ORIGINS`，不要关闭 Origin 校验或 TLS 校验来绕过。
- `/etc/trpg-master/staging.env` 为 `0600 root:root`，备份口令为
  `0600 trpgdeploy:trpgdeploy`；模型配置只写入该 env，不进入 release、仓库或日志。
- Pi 专用 systemd drop-in 把 `TRPG_LLM_MAX_CONCURRENCY` 限为 1；每日加密备份写入
  `/var/backups/trpg-master-staging`。
- 发布仍使用内容哈希 release 和 `deploy/install-staging-release.sh`，先在本机通过质量门禁并构建
  `frontend/dist`，再上传到 Pi。不得把 Pi 验收替代本地自动化测试，也不得让该流程触碰 Azure
  production。

常用只读检查：

```bash
sudo systemctl status trpg-master-staging.service
sudo systemctl status trpg-master-staging-backup.timer
curl --fail http://127.0.0.1:8766/api/ready
sudo journalctl -u trpg-master-staging.service --no-pager -n 100
```

#### 断电后的恢复

树莓派上的 PostgreSQL、Nginx、`trpg-master-staging.service` 与备份 timer 都设为开机启动；正常情况下
恢复供电后无需手动操作。若需要从开发机显式启动并同时做 readiness 验证，运行：

```bash
bash tools/start-pi-staging.sh
```

它通过 SSH 调用树莓派上 root 所有、无参数的
`/usr/local/sbin/trpg-start-staging`，依次启动所需 unit，并检查
`http://127.0.0.1:8766/api/ready`。树莓派的 LAN 地址变化时，可临时覆盖目标：

```bash
TRPG_PI_SSH_TARGET=inaglyite@新地址 bash tools/start-pi-staging.sh
```

若已经在任意终端中，只需直接执行等价的单条命令：

```bash
ssh inaglyite@192.168.5.22 'sudo -n /usr/local/sbin/trpg-start-staging'
```

一次性安装该固定入口（树莓派上执行；不应放入 release 目录）：

```bash
scp deploy/trpg-start-staging inaglyite@192.168.5.22:/tmp/
ssh inaglyite@192.168.5.22 \
  'sudo install -o root -g root -m 0755 /tmp/trpg-start-staging /usr/local/sbin/trpg-start-staging && rm -f /tmp/trpg-start-staging'
```

入口不接受任何参数，避免把 SSH 启动快捷方式变成可注入的特权命令；本机 helper 也使用
`BatchMode=yes` 和 `sudo -n`，凭据或 sudo 授权失效时会直接失败而不会在后台卡住。

#### 最近一次 Pi 候选验收（2026-08-10）

候选 release `fd46bbf7be06…` 已在上述 Pi staging 完成以下验收；这不是 Azure production 发布记录：

- 浏览器+浏览器、Electron+浏览器分别完成注册、建房、邀请、选角、准备、开局、行动和存档纵切；
- 服务重启后，房间历史、当前行动者、输入权限和存档面板恢复正确；
- 最新加密备份成功恢复到前缀受限的独立 PostgreSQL 演练库，关键表检查通过，
  `users/worlds/turns/save_slots` 与源库计数一致；演练库和临时密文副本随后按精确名称删除；
- E2E 账号、审计记录和世界按精确 ID 清理，保留 `local-mansion_of_madness`；清理后重新生成的
  加密备份再次通过独立恢复，计数为 `0/1/0/0`；
- readiness、服务、备份新鲜度、证书和磁盘的 staging 参数化监控检查全部通过；应用与 PostgreSQL
  仍只监听回环地址，对局域网只暴露 Nginx 的 `192.168.5.22:8443`。

仍未完成：真实玩家人工验收、连续 24 小时浸泡、Azure staging 验收和 production 发布。不得用本节
替代这些门槛。

## 数据库与账号基线

生产使用 PostgreSQL，数据库只监听 `127.0.0.1`，应用使用独立最小权限账号；不要让 5432 进入
Azure 公网 NSG。把 `deploy/postgresql-trpg-master.conf` 纳入 `postgresql.conf`，并把
`deploy/pg_hba-trpg-master.conf` 放在更宽泛的 `pg_hba.conf` 规则之前。应用通过 SQLAlchemy 2 访问
`world_states` JSONB、成员、回合、快照和存档等关系表，不从旧 JSON 目录读取运行事实。

生产环境至少配置：

```bash
TRPG_DATABASE_URL=postgresql+psycopg://trpg_app:...@127.0.0.1/trpg_master
TRPG_REQUIRE_AUTH=1
TRPG_ALLOW_REGISTRATION=0
TRPG_ALLOWED_ORIGINS=https://trpggame.xyz
TRPG_SESSION_COOKIE=trpg_session
TRPG_BACKUP_PASSPHRASE_FILE=/etc/trpg-master/backup-passphrase
```

云端单人/多人共用服务端全局模型配置（API Key 只在服务器，绝不下发客户端）。花费护栏随
service 下发，小内存 VM 初期保持保守值，按实测成本再调：

```bash
TRPG_LLM_MAX_CONCURRENCY=2      # 全局模型调用并发上限，超出排队（60s 超时）
TRPG_ACTION_RATE_PER_MINUTE=10  # 每账号每分钟行动上限
TRPG_DAILY_TURN_QUOTA=200       # 每账号每日回合额度（进程内存计数，重启清零）
```

模型调用与拒绝事件会带 user_id/world_id 写入应用日志，用于核算成本与封禁滥用账号。
`TRPG_ALLOW_REGISTRATION=0` 时注册接口关闭，用 `tools/manage_users.py` 人工开户（白名单）。

紧急停用账号时必须区分两层语义：把 `users.status` 改为 `inactive` 后，新登录会被拒绝，
已发 Session token 也会在下一次查库解析时被拒绝；但已完成握手的 WebSocket 在连接存活期间
不会每帧查库。在管理员踢人通道完成前，需要“立即断开”的现场封禁应同时撤销该用户的
服务端 Session，并在确认当前进行中行动的恢复影响后重启应用进程，使存量 WebSocket 统一断开。
不得把仅修改 `users.status` 记录为“已立即踢下线”。

每个 release 的 systemd `ExecStartPre` 执行 `alembic upgrade head`，失败时不得启动应用。人工核对迁移：

```bash
cd /opt/trpg-master/current
.venv/bin/python -m alembic -c alembic.ini current
.venv/bin/python -m alembic -c alembic.ini upgrade head
```

首次从旧服务器导入世界前，先创建明确的 owner；导入器会检查冲突并以数据库审计事件保证幂等：

```bash
.venv/bin/python tools/manage_users.py create account_name
.venv/bin/python tools/import_worlds_to_database.py \
  --runtime-root /var/lib/trpg-master \
  --owner account_name --once --replace
```

确认数据库、回合、存档和加密备份均可恢复前保留旧目录；它只是离线导入来源，不再是运行时事实。

### 受控回合恢复

自动恢复只会中断本机且 owner PID 已确认退出的活动回合；旧格式、跨主机或未知 owner 会保留为
`active`，避免第二个服务错误覆盖仍在生成的回合。它们若确认已经遗留，可由**能读取同一受管
运行环境和数据库的本机维护者**执行 `tools/recover_active_turn.py`，没有 HTTP 或前端入口。

先停止并核实原服务/worker 不会再提交该世界，且不要把 `TRPG_DATABASE_URL` 贴进命令行或工单。
在已注入该受限环境变量的维护 shell 中先只读检查：

```bash
cd /opt/trpg-master/current
TRPG_RUNTIME_ROOT=/var/lib/trpg-master \
  .venv/bin/python tools/recover_active_turn.py --world-id '<world-id>'
```

命令会打印活动回合的 `turn_id` 与当前 `owner_token`。复制其对应的一行，并在再次核实后追加
`--force --yes`；这两个值是 compare-and-swap 栅栏，检查后若回合已结束、被替换或 owner token
变化，工具会拒绝写入。强制恢复只把该回合标记为 `interrupted`，不会把未提交叙述或状态当作完成
回合，并在同一数据库事务写入 `audit_events.turn_force_recovered`。绝不可对仍在运行的游戏执行。

## 首次安装 staging

1. 创建独立数据库和只拥有该数据库对象权限的应用用户。
2. 写入 `/etc/trpg-master/staging.env`，至少包含：

   ```bash
   TRPG_DATABASE_URL=postgresql+psycopg://trpg_staging:...@127.0.0.1/trpg_master_staging
   TRPG_ALLOWED_ORIGINS=https://服务器地址:8443
   TRPG_BACKUP_PASSPHRASE_FILE=/etc/trpg-master/staging-backup-passphrase
   ```

   env 文件与备份口令文件都属于敏感凭据，权限要求如下：

   - `/etc/trpg-master/staging.env` 必须为 `0600 root:root`；systemd 以 root 读取后把变量注入
     服务进程，应用和备份脚本不需要直接读取该文件。
   - 备份口令文件 `/etc/trpg-master/staging-backup-passphrase` 由 `trpgdeploy` 运行的备份服务
     读取，必须为 `0600 trpgdeploy:trpgdeploy`（或 `0640 root:trpgdeploy` 且 `trpgdeploy` 属于
     对应组），否则 `backup-trpg-master.sh` 的 `--passphrase-file` 会失败。
   - 生产对应文件为 `/etc/trpg-master/trpg-master.env` 与
     `/etc/trpg-master/backup-passphrase`，权限要求相同。

3. 安装 `deploy/trpg-master-staging.service`、`deploy/trpg-master-staging-backup.service`、
   `deploy/trpg-master-staging-backup.timer`、`deploy/nginx-trpg-master-staging.conf` 和
   `deploy/install-staging-release.sh`，后者固定安装为
   `/usr/local/sbin/trpg-install-staging-release`。
4. 启用 `trpg-master-staging-backup.timer`；staging 备份只写入
   `/var/backups/trpg-master-staging`，不得与生产备份或运行目录混用。
5. 执行 `nginx -t` 后才 reload；确认 Azure NSG 仅向测试来源开放 8443。
6. 从 GitHub 手动运行 `deploy-multiplayer-staging`。它不会由分支 push 自动触发，也不会修改生产
   symlink。

staging 的 8443 vhost 不复用旧生产站点的共享 Nginx Basic Auth；它使用应用自己的 HttpOnly
Session、注册/登录限流和世界成员权限，确保浏览器与 Electron 能完成标准账号流程。若需要额外限制
测试入口，应在 Azure NSG 或独立 staging 主机名上做来源限制，不能再把共享 Basic Auth 当游戏账号。

发布包必须包含 `alembic.ini`、`migrations/`、运行时使用的 `tools/` 和备份所需的 `deploy/`。
systemd 在每次启动前运行
`alembic upgrade head`；迁移失败会阻止新版本启动。

安装器不会直接把 tar 交给 root 解压：它先以 Python 3.12 流式读取 gzip/tar header，限制压缩包、成员数、
单文件和总展开大小，拒绝绝对路径、`..`、反斜杠、重复项、链接及特殊文件，再用 `tarfile` 流式校验并
以 `filter=data` 提取到空目录。候选源码和 systemd/Nginx 配置保持 root-owned；只有候选 `.venv` 临时
由 `trpgdeploy` 写入依赖，完成后重新收回所有权并复核。失败产生的 `.install-*`、`.incomplete-*`
目录按严格名称在 24 小时后清理。

手动 staging 工作流在上传前会启动一次性 PostgreSQL 17 service，实际执行全部 Alembic 迁移和
`tests/test_postgresql_integration.py`，验证 JSONB、成员/邀请/调查员关系及房间行动唯一约束。SQLite
测试不能替代这道发布门禁。

## 上线检查

- `curl http://127.0.0.1:8766/api/health` 能确认进程存活，但不访问数据库；
- 内部 `curl http://127.0.0.1:8766/api/ready` 与外部 HTTPS `/api/ready` 均成功；数据库不可用时
  readiness 必须返回 HTTP 503；
- 浏览器和 Electron 使用 WSS，Cookie 为 HttpOnly/Secure/SameSite=Lax 且与生产 Cookie 隔离；
- 两个独立账号完成创建/加入房间、不同调查员、准备、开局和一个完整回合；
- 非当前行动者、伪造调查员、重复 `action_id` 和无权限世界连接均被服务端拒绝；
- 私人事件不出现在另一账号的实时帧、ack 补发或 `room_full_state`；
- 回合中断开一端、刷新、服务重启后公开历史与当前行动者恢复且不重复调用模型；
- `systemctl restart`、当前 release 回滚、数据库备份恢复和 Nginx 回滚均演练成功。

## 备份与恢复

`deploy/backup-trpg-master.sh` 使用 `pg_dump --format=custom`，连同运行目录、校验和一起用 GPG AES256
加密。脚本按备份根目录串行化任务，在同一文件系统写入唯一隐藏临时文件，解密并完整遍历 tar 验证
后才原子改名；加密、验证或信号中断时不会发布最终文件，同秒运行也不会覆盖已有备份。脚本级验证
仍不能替代真实恢复：每次发布候选至少在隔离数据库执行一次以下演练：

备份同时点-in-time 地保证 PostgreSQL dump 和运行目录 tar 各自完整、可解密、可校验；它不会冻结应用
写入，因此不能声称两者是同一个跨系统事务快照。若要做 restore-grade 演练，应先让应用进入维护/静默
写入窗口，再执行备份并在隔离数据库验证；不要把脚本级原子发布误认为世界状态一致性屏障。

同一脚本通过 `TRPG_BACKUP_ROOT`、`TRPG_BACKUP_RUNTIME_ROOT` 和 `TRPG_BACKUP_PREFIX` 隔离环境；脚本
只接受 `/var/backups/trpg-master[-*]` 与 `/var/lib/trpg-master[-*]` 范围内的目标。生产使用默认值，
staging systemd 单元显式指向 staging 目录。

1. 解密归档并验证 `SHA256SUMS`；
2. 创建空数据库，使用 `pg_restore --no-owner --no-acl` 导入；
3. 指向恢复数据库启动同一 release，运行 Alembic 并检查账号、成员、世界、调查员、回合和存档；
4. 用两个客户端重连一个已完成回合，确认没有重放 incomplete turn；
5. 删除演练数据库，不触碰生产数据库和 `/var/lib/trpg-master`。

回滚应用版本只切换 release symlink。数据库迁移若不向后兼容，必须在发布前提供经过演练的降级或
前滚修复方案，不能在生产上临时执行破坏性 SQL。

## 监控与告警

`deploy/monitor-trpg-master.sh` 是只读健康监控脚本，退出码约定：`0` 全部检查通过；`1` 至少一项
检查失败；`2` 用法或配置错误（不触发 webhook）。默认检查五项：

- **ready**：`curl --fail` 访问 `TRPG_MONITOR_HEALTH_URL`（默认 `http://127.0.0.1:8765/api/ready`）；
- **service**：`systemctl is-active --quiet` 检查 `TRPG_MONITOR_SERVICE`（默认 `trpg-master.service`）；
- **backup**：备份根目录下 `TRPG_MONITOR_BACKUP_PREFIX-*.tar.gpg` 的最新文件
  `TRPG_MONITOR_BACKUP_MAX_AGE_HOURS`（默认 26）小时内生成；
- **certificate**：`TRPG_MONITOR_CERT_PATH`（默认生产 Let's Encrypt fullchain.pem）剩余天数小于
  `TRPG_MONITOR_CERT_MIN_DAYS`（默认 14）即失败；
- **disk**：`TRPG_MONITOR_DISK_PATHS`（默认 `/ /var/backups/trpg-master`）逐路径检查，使用率超过
  `TRPG_MONITOR_DISK_MAX_PERCENT`（默认 90）或可用空间小于 `TRPG_MONITOR_DISK_MIN_FREE_MB`
  （默认 1024MB）即失败。

其余参数 `TRPG_MONITOR_ENV`（告警标签）、`TRPG_MONITOR_BACKUP_ROOT`、`TRPG_MONITOR_BACKUP_PREFIX`、
`TRPG_MONITOR_WEBHOOK_URL`（可选，仅失败时 POST JSON）都经环境变量覆盖，便于 staging/生产注入不同值。
监控脚本只读备份根，不硬性限制 `/var/backups` 前缀，但要求绝对路径、真实目录且不是符号链接；生产
systemd 单元仍显式指向受管备份根。

失败时向 webhook POST 的 JSON 摘要形如：

```json
{"env":"production","ok":false,"failed":2,
 "checks":[{"name":"ready","ok":false,"message":"readiness failed: http://127.0.0.1:8765/api/ready"},
           {"name":"certificate","ok":false,"message":"certificate expires in 3d (minimum 14d)"}]}
```

webhook 投递失败只打印 warning，不改变检查退出码。

`deploy/trpg-master-monitor.service` 与 `deploy/trpg-master-monitor.timer` 是发布安装器安装的
oneshot 单元：monitor service 以 root 运行（需要读取 `/etc/letsencrypt`），启用
`ProtectSystem=strict`、`NoNewPrivileges` 等加固，并把上表默认值显式写成 `Environment=`，便于按
环境覆盖；timer 每日 `04:20` 触发（备份在 `03:15` 之后），`Persistent=true`。

- 手动运行一次：`systemctl start trpg-master-monitor.service`，结果看
  `journalctl -u trpg-master-monitor.service`；
- 手动演练（不上生产也可以）：`TRPG_MONITOR_WEBHOOK_URL=... ./deploy/monitor-trpg-master.sh`；
- 阈值调整只改 `/etc/systemd/system/trpg-master-monitor.service` 的 `Environment=` 行后
  `systemctl daemon-reload`；下次发布会被模板覆盖，因此自定义阈值应记录到发布流程。

monitor 单元与 backup 单元一样属于**受管发布资产**：`install-release.sh` 激活新 release 时原子替换
两个单元并 `systemctl enable --now` monitor timer；激活失败回滚时恢复安装前的单元文件与
enabled/active 状态。证书续期、数据库不可用、磁盘不足、备份陈旧与模型供应商故障的处置参见各
runbook；`ready` 或 `certificate` 告警需在 24 小时内响应（阶段 3 门槛：备份失败一个工作日内发现）。

## 恢复演练（restore-drill）

`deploy/restore-drill.sh` 把"真实恢复演练"固化成安全脚本，默认 `--dry-run`，绝不误碰生产数据库：

- **dry-run（默认）**：解密备份（GPG + `TRPG_BACKUP_PASSPHRASE_FILE`）、`sha256sum -c` 校验、
  `pg_restore --list` 列出 dump 内容；全程不连接任何数据库、不设置任何 libpq 连接参数；
- **`--restore URL`**：把备份导入显式指定的隔离数据库并执行只读验证（15 张关键表存在性 +
  `users`/`worlds`/`turns` 计数）。

安全不变量：

- 脚本从不执行 `DROP DATABASE` / `DROP TABLE` / `TRUNCATE`，`pg_restore` 不使用
  `--clean`/`--if-exists`，因此演练库已存在时脚本会失败并要求人工处理，而不是覆盖或删除任何库；
- `--restore` 目标库名必须以 `TRPG_RESTORE_DB_PREFIX`（默认 `trpg_drill_`）开头，且不得与
  `TRPG_PRODUCTION_DATABASE_URL` 中的数据库同名，否则立即以退出码 2 拒绝；
- 备份归档必须是备份根下的常规文件（拒绝符号链接与越界路径）。

依赖与故障提示：`pg_restore` 缺失时脚本立即以退出码 2 失败并提示安装 `postgresql-client` 或用
`TRPG_PG_RESTORE` 指向实际二进制（PostgreSQL client bin 常不在 PATH，例如
`/usr/lib/postgresql/17/bin/pg_restore`）；`--restore` 还需要 `psql` 与 `createdb`，密码经临时
`.pgpass` 传递，不落入进程参数。

典型流程（每次发布候选在隔离数据库执行一次，对应"备份与恢复"一节的手动步骤）：

```bash
# 1) 只读演练：解密、校验、列出内容
TRPG_BACKUP_PASSPHRASE_FILE=/etc/trpg-master/backup-passphrase \
  ./deploy/restore-drill.sh --latest

# 2) 真实恢复演练：导入到隔离库并验证
TRPG_BACKUP_PASSPHRASE_FILE=/etc/trpg-master/backup-passphrase \
TRPG_PRODUCTION_DATABASE_URL="$TRPG_DATABASE_URL" \
  ./deploy/restore-drill.sh \
  --restore 'postgresql+psycopg://trpg_drill:...@127.0.0.1/trpg_drill_20260801'
```

导入完成后脚本保留演练库供检查；清理必须人工执行（脚本永不删库）：

```bash
PGDATABASE=postgres psql -c 'DROP DATABASE trpg_drill_20260801'
```

演练库不应指向生产或 staging 数据库；`TRPG_RESTORE_DB_PREFIX` 不得设置为空或生产库前缀。
