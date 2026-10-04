# 角色库：生成参考与实现边界

2026-10-04 使用内置 imagegen 生成，无 CLI、未使用游戏模型 Key。

参考图：[character-library-concept-v1.png](character-library-concept-v1.png)。原图保留在 `/home/inaglyite/.codex/generated_images/01a06c3d-3b8c-7263-82c5-de1bee5f9328/exec-5b267907-385f-4f4b-84c4-86cd21d69081.png`。

这是角色库改造的**imagegen 设计参考，不是浏览器实现截图**。第十四阶段已按它的布局方向实施：档案夹顶边、可搜索列表／详情分层、常驻标题与关闭、底部管理操作；窄窗口用单列滚动。实际组件复用已确认的九宫格文件夹素材和 DOM 正文，不把整张图当背景。实现及验收记录见 [README](README.md)。

生成器自行添加的示例“阵营／核心特质／出生地”和人像不构成产品需求，不引入新数据字段，也不代替玩家真实卡面。实现只显示已有卡面字段，编辑保留未呈现的扩展资料；正文采用低纹理的哑光暖灰，不照搬效果图的大面积亮纸。没有为了模拟概念图而新建人物数据、假履历或模型请求。

完整提示词：

```text
Use case: ui-mockup.
Asset type: high-fidelity implementable visual reference board for the character-library modal in an existing Chinese-language Call of Cthulhu browser tabletop platform.
Primary request: create a polished desktop character-library interface and a smaller narrow-window adaptation side by side on a landscape board. A real flat screenshot-style UI, not a perspective photograph of physical objects. The platform is an investigator's archive; players browse their own character cards, import a JSON card, create or edit a character, and close back to their current lobby without losing the game.
Layout: desktop around 939px wide, left searchable character list, right selected character dossier. Fixed top header labeled “角色库”, a subtle identity line “当前账号的角色档案”, and a quiet readable “关闭” button. Header actions “导入角色卡” and “新建角色”. List contains two sample investigators “爱丽丝 · 记者” and “黄子陆 · 调查员”. Selected dossier has tabs “概览”“属性与技能”“背景与物品”, occupation, HP/SAN values, a restrained small portrait silhouette, and readable section labels. Bottom actions “编辑”“复制”“导出”; deletion only a subdued secondary control. Narrow-window adaptation around 390px wide is a single column with a persistent header/close and scrollable middle, not squeezed two columns. Use sample labels only, no dense pseudo-text. Real DOM will carry all final text.
Visual identity: stylized painted game-UI archive folder with a modest kraft tab, muted brass rules, matte dark charcoal-brown #24201b, background #181511, comfortably readable warm ivory #e4dccd and utility text #b3a998. Subtle texture ONLY at edges, almost flat behind content. Title uses a restrained Chinese serif; body uses clean Chinese sans serif. An understated printed-paper dossier, not a glossy realistic leather case or ornate golden frame. No bright white panels, no neon, no light flares, no photorealistic desk, no 3D mockup, no charts, no watermark. Preserve generous click targets and readable padding. Mood calm and mysterious, suitable for long nighttime reading.
```
