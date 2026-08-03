# 多人功能执行状态与接续说明

更新日期：2026-08-03
开发分支：`feat/multiplayer`  
文档性质：当前工作树的工程台账；最终发布仍以 staging 和 CI 验收为准。

本文与 [`MULTIPLAYER_DELIVERY_BRIEF.md`](MULTIPLAYER_DELIVERY_BRIEF.md) 一起作为接续入口。产品范围不因
“页面已经存在”或“接口已经存在”而缩减；接口、架构、数据库、部署和玩家操作分别以专题文档为准。

## 1. 当前结论

多人第一阶段的核心代码已经落地，当前分支已经具备：

- Electron 启动后选择单机或多人；单机按需启动并由 Electron 回收本地后端，联机不启动本地后端；
- 账号、Argon2id 密码哈希、可撤销 Session、登录限流、世界所有权、成员角色和邀请；
- Azure 权威服务器模型，单进程 `RoomManager` 按 `world_id` 复用一个 `GameRoom`/`GameEngine`；
- 调查员服务端占用、当前行动者、准备/开局门槛、行动幂等、房主管理和跨调查员工具路由；
- 公共/玩家/房主/服务端事件过滤、房间事件游标、ACK、增量和完整恢复；
- 断线、刷新、进程重启、存档读档和数据库权威 roster 对账；
- PostgreSQL 控制平面与 JSONB 世界/快照数据、Alembic head `20260728_0006`；
- 受限发布归档提取、备份加密/解密验证、同目录原子发布、回滚状态恢复和安装器权限边界；
- 浏览器与 Electron 双客户端联机 E2E，以及 Linux 源码启动/退出进程组验收。

尚未能称为正式生产发布的事项只剩重新部署后的 Azure staging 验收，以及正式域名和受信任 TLS 证书。
当前 Azure IP 自签名证书只适合受控测试，不应要求普通玩家绕过证书警告。GitHub quality 与 Windows
安装包门禁已在提交 `fe67af5` 上通过。

## 2. 当前代码边界

```text
Browser / Electron
        │ HTTPS + WSS
        ▼
Azure Nginx → FastAPI（单 worker）
              ├─ Session / 世界成员权限
              ├─ RoomManager → 一个 world 一个 GameRoom / GameEngine
              ├─ 公共与私密事件过滤、ACK、恢复
              └─ PostgreSQL（控制面） + JSONB 世界/回合/快照

Electron 单机 → 按需启动本地 FastAPI → SQLite
```

`src/agent_graph.py` 中的 Story/Combat 是同一个 `GameEngine` 内的职责节点，不是多 Agent；它们共享
消息历史、模型会话和世界状态。当前世界运行事实由数据库 `world_states.state`（SQLite 桌面、PostgreSQL
云端）保存；旧 `worlds/` 文件只用于一次性导入和兼容测试。

## 3. 测试证据

在当前工作树、无外部 staging 凭据的本机环境中已执行：

| 门禁 | 结果 |
|---|---|
| `python -m pytest -q`（SQLite） | `444 passed, 3 skipped` |
| `TRPG_TEST_POSTGRES_URL=... python -m pytest -q`（PostgreSQL 17） | `446 passed` |
| PostgreSQL Alembic `downgrade base → upgrade head → check` | 通过，当前 `20260728_0006` |
| Ruff、架构门禁、compileall、`git diff --check` | 全部通过 |
| 部署/备份/归档安全测试 | `43 passed`，三个 Bash 脚本 `bash -n` 通过 |
| 前端 Vitest（Linux） | `32` 个文件、`274` 个用例通过 |
| Prettier、TypeScript、Vite production build | 全部通过 |
| `multiplayer.spec.ts` Playwright | `3 passed`：双浏览器、Electron 双端、Electron 单机生命周期 |
| Windows VM（`192.168.12.129`）前端构建/测试 | `npm run build`、`npm run format:check`、`npm test -- --run` 全部通过；`32` 个文件、`274` 个用例 |
| Windows VM Electron 无头启动 | 成功打开 `file:///.../frontend/dist/index.html` 并暴露 DevTools 页面 |
| GitHub `quality`（`fe67af5`） | 通过，run `30780225280` |
| GitHub Windows NSIS/portable/backend smoke（`fe67af5`） | 通过，run `30780230054`；安装/卸载和桌面探针均通过 |

当前架构行数仍在既定 ratchet 内：`server.py 1697/1699`、`src/multiplayer_ws.py 710/740`、
`src/multiplayer_http.py 419/420`、`src/engine.py 2125/2126`、`src/tools.py 1492/1503`、
`tools/state_manager.py 780/797`、`src/model_streamer.py 412/412`。

Playwright 的 staging 恢复用例和真实 Azure 用例需要显式提供环境变量才会运行；没有凭据时必须是
skip，不能写入测试账号密码，也不能把自签名证书全局放行。

## 4. 已处理的高风险边界

- 模型供应商异常只回传通用提示，原始 endpoint、tenant、key 等只进服务端日志；
- WebSocket 内部故障使用 `1011`/`1012` 并保留房间恢复记录，只有明确认证/成员拒绝使用 `4401`/`4403`；
  角色降级使用 `4409`，前端清除旧玩家私态后以新角色重连；
- 玩家笔记、完整恢复和工具错误不会把服务器路径、DSML、模型参数或秘密发给客户端；
- 游戏开始后不能用 player 邀请加入、viewer 升级或 viewer 直接接任房主；已有玩家之间仍可移交，玩家
  降为 viewer 会释放调查员控制权；
- 开局取得 room action lease 后会重新读取数据库 roster/准备/在线状态，避免 claim/release race 用旧
  roster 开场；save/load 后再次按数据库 roster 对账，不复活旧 snapshot controller；
- 发布包在提取前进行流式 tar header/数量/大小/类型/路径校验，拒绝软硬链接和特殊文件；candidate
  源码/config 保持 root-owned，只有 venv 临时由构建账号写入，随后重新收回并复核；
- 备份使用独占文件描述符锁、隔离 `GNUPGHOME`、同目录 hidden partial、实际解密 tar 校验和
  `mv --no-clobber` 原子发布；失败/信号不会留下可发布 partial。

## 5. 部署事实与剩余门禁

部署拓扑、systemd/Nginx、备份恢复流程见 [`DEPLOYMENT.md`](DEPLOYMENT.md)。当前仓库声明：

- production `8765` / staging `8766`，两个环境使用独立运行目录、Cookie、数据库和备份；
- 应用保持一个 Uvicorn worker，`/api/health` 只查进程，`/api/ready` 执行数据库往返；
- staging 必须先于生产部署，发布安装器会迁移数据库、健康检查并保留旧 release；
- 发布失败回滚会恢复旧 symlink、Nginx/systemd 配置以及服务和 timer 原先的 enabled/active 状态；
- 归档/备份脚本级测试不等于真实恢复。正式候选仍需在 Azure staging 做 pg_restore、重启、备份、
  回滚、双客户端权限/隐私和 WSS 验收；
- Windows 的 NSIS/portable 构建与打包后端 smoke 已加入 `.github/workflows/windows-package.yml`；Windows
  VM 已完成源码级 Electron 验收，最终安装包仍以 GitHub Windows runner 结果为准。Linux 不提供 AppImage。

## 6. 下一步固定顺序

1. 完成当前工作树的最终文档审校和一次完整门禁；
2. 用当前已通过门禁的提交部署 Azure staging，执行 `/api/ready`、TLS/WSS 双客户端、重启恢复、备份解密/pg_restore
   和 release/Nginx 回滚；
3. 记录真实 SHA、迁移号、测试证据和剩余 TLS 域名风险；未有受信任域名证书前不宣称面向普通玩家上线；
4. 只有交付约定第 6 节全部满足后，才讨论合入 `master`。

## 7. 接续规则

- 始终保持在 `feat/multiplayer`，不要 `reset --hard` 或覆盖用户/Kimi 的未提交改动；
- 先看 `git status`、本文和 `MULTIPLAYER_DELIVERY_BRIEF.md`，再按专题文档修改；
- 数据库变化必须有 Alembic、SQLite 和真实 PostgreSQL 证据；HTTP/WS 变化必须同步 `API.md`；
- 不把密码、Session、API Key、数据库 DSN、邀请明文或 Azure 凭据写进仓库、日志、测试输出或回复；
- 测试失败要区分代码失败、测试环境误配置和外部 staging 状态，不能用放宽门禁掩盖问题。

## 8. 相关文档

- 产品范围与完成定义：[`MULTIPLAYER_DELIVERY_BRIEF.md`](MULTIPLAYER_DELIVERY_BRIEF.md)
- 技术路线与扩展边界：[`MULTIPLAYER_PLAN.md`](MULTIPLAYER_PLAN.md)
- HTTP/WS 契约：[`API.md`](API.md)
- 实际架构：[`ARCHITECTURE.md`](ARCHITECTURE.md)
- 数据模型与迁移：[`DATABASE.md`](DATABASE.md)
- 部署、备份和恢复：[`DEPLOYMENT.md`](DEPLOYMENT.md)
- 玩家操作：[`MULTIPLAYER_USER_GUIDE.md`](MULTIPLAYER_USER_GUIDE.md)
