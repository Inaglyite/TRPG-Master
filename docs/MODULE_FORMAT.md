# 模组作者入口

完整字段、示例与编译诊断保留在[模组格式参考](reference/MODULE_FORMAT.md)。本入口只说明制作流程与运行边界；不要靠摘要猜可用字段。

## 1. 作者态与运行态

`.trpgmod` 是 ZIP 包，支持格式 v1/v2，包含结构化定义、Markdown 和素材。作者态定义场景、NPC、线索、秘密、规则与结局；安装编译生成 `module.md` 与 `world_state_initial.json`。

玩家的当前场景、物品、属性与进度保存在运行数据库，不写回模组包。模组结构支持某个字段，不代表新旧每条运行路径都已接入其效果，验收须写明 execution_profile。

## 2. 从模板开始

在仓库根目录，激活开发虚拟环境后：

```bash
cp -r examples/module-template my-module
python tools/module_packager.py compile my-module
python tools/module_packager.py pack my-module dist/my-module.trpgmod
python tools/module_packager.py validate dist/my-module.trpgmod
```

先修改 `manifest.json`、`module.json` 和 `keeper.md`，再编译、打包、预检导入，在独立世界试玩。模板见 [examples/module-template](../examples/module-template/manifest.json)，schema 见 [schemas/trpgmod](../schemas/trpgmod/)。

## 3. 应查哪份资料

| 内容 | 位置 |
|---|---|
| 包结构、稳定 ID、场景/NPC/线索、发现与失败保底 | [格式参考](reference/MODULE_FORMAT.md) |
| v2 主线安全、危机与结局契约、编译诊断 | 同上；实际校验 `src/modules/module_format.py`、`module_compiler.py` |
| 素材、主题、Lorebook、技能声明 | 同上；`schemas/trpgmod/` 与 `skills/catalog.json` |
| 包安装安全、版本并存 | `src/modules/module_registry.py` |
| 游戏命令与玩家可见性 | [协议](PROTOCOL.md) |
| 尚未实现的地图/工坊方向 | [状态与后续方向](STATUS.md) |

场景地图、Token、迷雾和自由分头行动不能通过手写未识别字段启用；它们不是当前格式已支持的承诺。

## 4. 过渡与旧存档兼容

legacy advisory 的 blocking 卡与非阻塞 transition_text 是不同用途。新模式正常叙事等待由主持/交互线程承载，不能只改一段 advisory 就宣布新过渡回合完成。

旧 advisory/entry_beat 的 `supersedes` 是精确旧载荷升级依据，保护范围不能夸大：条目级升级与模组版本变化时整体刷新场景目录是两条路径，后者仍可能覆盖世界内手改内容。发布模组更新前验证原样旧数据、用户改写、分支与新开局对偶。

剧情秘密不应直接送入玩家投影。提供了全量模组知识，也不等于每个 NPC 都知道；素材分发、调查检定和物品取得必须有实际授权与结算。

## 5. 验收与交付

格式校验、引用、素材路径、秘密可见性、失败出口、主线可达性、重复触发、时间/状态一致性与结局都要验证。主线测试允许真实玩家改变做法，但不能改断言掩盖机制缺陷。

按[开发验收](DEVELOPMENT.md)分开记录编译通过、确定性测试、真实人类主持、真实 Agent 与旧主线结果；涉及模型调用先获授权。第三方包及 custom skills 是不可信输入，必须经过安全预检和信任提示，不能绕过安装器直接覆盖运行目录。
