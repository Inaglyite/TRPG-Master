# 项目协作约定

## 环境保护（2026-08-10 起生效）

- **https://trpggame.xyz 是正式发布环境**：不得在上面做测试、冒烟、试改或
  "顺手验证"。对它的任何变更只允许是经过确认的正式发布动作。
- **日常测试在本地**：单元/集成/E2E 一律本地跑
  （`pytest`、`frontend: npm test / test:e2e`）。
- 默认预发布验证用局域网 Raspberry Pi staging（内部 8766 / HTTPS 8443）；
  staging 上允许冒烟账号与测试世界。Azure 上的旧 staging 与 production 同机，
  只在明确需要外网预发布验证时使用，不得用它代替 Pi/本地日常测试。
- 生产发布路径：`master` push → quality 绿 → 人工触发 deploy-azure
  （workflow_dispatch）；正式发布仍需明确确认，不得自动或由其他
  工作流级联触发。
  **已知限制**：当前 CI SSH 用户尚无非交互 sudo 权限，workflow 末尾
  `sudo bash /tmp/trpg-install-release-*.sh` 会在激活步骤失败。
  不得为 `/tmp` 下的临时脚本授予 NOPASSWD（部署用户可写），也不得使用
  `sudo -S`、保存密码或 expect 脚本绕过。未来必须先设计并安装
  root-owned、参数校验严格的固定部署入口（如
  `/usr/local/sbin/trpg-activate-release`），再对该固定入口授予最小
  NOPASSWD；在此之前由授权维护者登录主机交互式执行激活。
