# 前端架构与 React 迁移

前端目标栈为 Electron + Vite + React + TypeScript。FastAPI 世界状态仍是规则权威，
React 只管理客户端展示与交互；未来地图通过版本化协议接入 React Three Fiber，不允许
AI 直接生成可执行前端代码。

## 依赖方向

```text
React components
      ↓
Zustand client state ← validated WebSocket messages (Zod)
      ↓
typed transport / HTTP services
      ↓
FastAPI authoritative world state
```

`src/react/GameShell.tsx` 负责组合 React 页面与覆盖层。WebSocket 服务会在 React 首次
提交 DOM 后由 `App` 启动。业务状态写入 Zustand，视图只由 React 组件渲染；主题服务仅在
平台边界写入 CSS 变量和窗口标题。禁止新增直接 `document.getElementById` 渲染逻辑。

已迁移区域：

- React 应用入口和完整 JSX 元素树；
- 顶栏、主题标题和连接状态；
- 角色属性、HP/SAN、物品与分类线索册；
- 玩家输入、结构化行动选项、检定确认、多选决定与结局确认；
- 聊天历史、流式叙事、等待/判定状态、骰子动画和回合改写/分支操作；
- 调查笔记、revision 冲突状态、自动保存和快捷行动；
- Handout、线索提示、角色面板、快速存档、存档重命名与时间线切换；
- 模型路由预设、受控模型 ID 输入和完整回合/Lorebook 诊断；
- 开始菜单、模组选择、调查员档案、新游戏重试、读档入口与 `.trpgmod` 导入；
- WebSocket 消息类型白名单和畸形消息安全拒绝。

新地图、面板或业务交互必须直接实现为 React 组件和类型化状态，不得引入命令式 DOM
渲染 adapter。
