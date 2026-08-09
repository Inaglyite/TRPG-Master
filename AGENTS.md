# 项目协作约定

## 环境保护（2026-08-10 起生效）

- **https://trpggame.xyz 是正式发布环境**：不得在上面做测试、冒烟、试改或
  "顺手验证"。对它的任何变更只允许是经过确认的正式发布动作。
- **日常测试在本地**：单元/集成/E2E 一律本地跑
  （`pytest`、`frontend: npm test / test:e2e`）。
- 默认预发布验证用局域网 Raspberry Pi staging（内部 8766 / HTTPS 8443）；
  staging 上允许冒烟账号与测试世界。Azure 上的旧 staging 与 production 同机，
  只在明确需要外网预发布验证时使用，不得用它代替 Pi/本地日常测试。
- 生产发布路径：`master` push → quality 绿 → deploy-azure（目前 CI 的
  SSH 用户无免密 sudo，最后一步需手动或先配好受限 NOPASSWD）。
  发布前先在本地与 staging 验证，再动生产。
