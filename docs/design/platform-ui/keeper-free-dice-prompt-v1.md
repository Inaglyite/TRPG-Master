# 主持普通骰：生成参考与实现边界

状态：独立主持通道与真实UI已接线，本地无模型浏览器组合5/5；不是全套/发布声明。
内置 imagegen（非CLI/BYOK），项目参考 `keeper-free-dice-concept-v1.png`；原件
`/home/inaglyite/.codex/generated_images/01a06c3d-3b8c-7263-82c5-de1bee5f9328/exec-d71a2e04-d2dc-4782-a033-96ff31d03cf3.png`。

已查看生成输出。采用手绘档案夹/小暖纸收据/暗色字段与固定操作区，实际标题
会缩小，不复制大标题/背景塔楼；字段、按钮和结果均为DOM。27是示例，不是
真实骰点，不作为默认值、成功判断或伪造回执。沿用已有文件夹九宫格素材，不
把整张效果图变成运行时背景。

通道要求：当前主持权限不等于调查员控制权；普通骰只产生一次已提交随机数，
不结算技能、HP/SAN/弹药/伤害/时间/剧情，不触发Agent。公开/仅主持投递必须
在服务端隔离，重放不得重掷；断线不排队自动掷骰。不能借此代掷玩家待检定。
既有玩家普通骰及本人响应权限不放宽。

实现：`keeper_roll`人类命令，私有默认、公开可选；严格schema/RNG限制，当前
授权先于查重，同一主持可重放，角色认领变化不误拒。最近20条可见收据按
当前世界outbox恢复，读档删除未来同revision记录，分支不继承普通骰历史。
新弹窗接入有界高度与44px控件；短窗字段并排，整组标签可见、固定操作区，
正文仍可滚动。不用随机数推断技能成败。最终前端1303、后端1918/8跳过，
本地/云端单人/三客户端组合5/5（4.3分钟），代码/构建指纹一致；详情见
`CURRENT_ACCEPTANCE.md`。未调用付费模型、未发布。

2026-10-08 限频增量：主动普通骰默认同账号60秒30次，跨角色/世界共享，
已提交同ID重放不占额度；游戏计时/读档/分支不重置，不限制正式检定或战斗骰。
限频卡显示“稍后重试”，不产生新结果、不自动重掷，按钮沿用原ID。
新增13项后台对偶、4项前端反馈/运输断言；后端1931/8跳过、前端1307，
真实组合4/4（4.4分钟），源/dist一致。已看939及短窗；本次仅改DOM文案，
保留现有档案夹和纸收据的视觉方向，不为错误文案另生成位图。

完整提示词：

```text
Use case: ui-mockup.
Asset type: implementation reference for a compact human keeper dice panel in a Chinese 1920s mystery tabletop game.
Primary request: show an intentional, readable desktop panel and a narrow 390px version of the SAME tool, not a full dashboard. Title "主持普通骰", quiet badge "不需要认领调查员". A form has label "骰式" and value "1d100", label "接收范围" and selection "仅主持", then calm explanatory text "只产生随机数，不结算技能、伤害或剧情。". Fixed footer buttons "关闭" and "掷骰", at least 44px, compact not oversized. Beneath or alongside show one small committed-result example labelled "结果示例" and "1d100 → 27"; this is illustrative only, no success/failure classification. Also a small note "公开结果会发给所有参与者；仅主持结果不会发给玩家。". Clear field/label separation.
Style/medium: flat hand-illustrated archive-folder UI, NOT a photographed physical object. Thin worn paper tab at top, muted brass edge, matte dark-brown backing #211a14 / #292118, warm legible body #e5d8b8, restrained brass #c9b77b. Only the small read-only receipt strip can be parchment #d1bf9b with ink #312a21; keep most of panel dark and calm. Chinese serif title used sparingly, readable Chinese body and monospace dice expression. Low texture where text sits.
Composition: two parallel size examples without device shells, hierarchy title -> fields -> explanation -> fixed actions; short-window body scrolls without losing actions. This is a design reference; all labels, inputs, result and buttons will be DOM.
Constraints: no enormous dice ornament, no photorealism, no 3D metal, no leather, no neon/glow, no dramatic lighting, no animation, no avatars, no charts, no extra stats, no target difficulty, no HP or ammo, no automatically executed command, no numbering the parallel examples as steps, no phone frames, no watermark. Do not suggest the keeper can roll a player's pending skill/combat check.
```
