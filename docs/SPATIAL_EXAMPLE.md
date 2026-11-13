# 寓所调查：空间机制示例

这是参考《无声编钟》寓所调查组织方式的独立教学模组，不是原作完整改编。充能钥匙为新增教学机制；原作、安装版本及玩家存档均不修改。无自定义 Skill，空间规则来自 module.json，主持执行说明由编译器生成。

需要支持 Spatial v1 的本体分支、模组格式 2.0、模块兼容版本 1.1.0。编辑器支持由 Kimi 在另一分支实现，现行发布版编辑器不保证保留新字段。

| 地点 | 调查点/动作 | 实际效果 |
| --- | --- | --- |
| 旧寓所 / 门厅 | cabinet / take | 取得一把未充能的教学钥匙；只能取一次 |
| 旧寓所 / 门厅 | lamp / charge | 当前持有的钥匙 charge +1，上限 1；不能免费钳制或溢出 |
| 旧寓所 / 卧室 | fireplace / search | spot_hidden 普通检定；成功发现通路与残谱信息，失败须先支付 pressure +1 后保底发现 |
| 旧寓所 / 隐藏房间 | bell_box / examine | 获得编钟盒印记的信息，不自动拿走编钟 |

初始位置 house 是容器，实际落在 foyer。门厅旧出口通往卧室。暗门未发现时不可见；发现后仍须当前持有 charge ≥1 的钥匙，首次通过扣除 1，双向永久开锁。module.scenes 中特意保留卧室到暗室的旧出口，用于验证旧路径无法绕过显式门锁。

经典工具链：`spatial_inspect` 查看当前房间；用 `spatial_interact(point_id, action_id)` 选择动作。壁炉动作先实际执行 `skill_check(skill="spot_hidden", target_id="fireplace:search", required_success_level="regular", approach="仔细搜索壁炉边缘")`，再把返回的真实 check_id 交给 spatial_interact。结构化接口见[编辑器交接](SPATIAL_EDITOR_HANDOFF.md)。抵达房间只移动，不自动搜索或检定。

打包时把输出放源码仓库外，例如从仓库根运行：

```bash
python tools/module_packager.py validate examples/spatial-investigation
python tools/module_packager.py pack examples/spatial-investigation ../mid_product/trpg-master/work/spatial-investigation.trpgmod
```

夹具只验证局部机制；并不证明任意作者条件组合都可通关。若压力已满且本次仍失败，代价无法支付会整批拒绝，不能先给线索；正式模组应另设可行的无检定替代路径。
