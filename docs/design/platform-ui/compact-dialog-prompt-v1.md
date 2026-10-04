# 紧凑操作面板参考图

用途：`前往…` 与 `普通掷骰` 的低高度布局参考，不是实际游戏截图，也不作为状态证明。复用已生成的宽版文件夹九宫格资源，不把参考图整张塞进界面；标题、当前场景、地点与按钮都是 DOM。

- 工作区成品：`compact-dialog-concept-v1.png`（1430×1100，RGB PNG）
- 原始生成文件：`/home/inaglyite/.codex/generated_images/01a06c3d-3b8c-7263-82c5-de1bee5f9328/exec-9e4f12ff-c2b2-40c2-ac33-ac2f5ebb9592.png`
- 输入参考：`folder-recovery-implemented-v1-1280.png`（已实现界面，仅取配色和文件夹皮肤，不复制网络错误文案／指南针）。
- 图片生成工具：内置 imagegen；本轮未重新生成或覆盖既有文件夹生产素材。

完整生成提示词：

```text
Use case: ui-mockup.
Asset type: implementable high fidelity GAME UI component design board for a Chinese occult tabletop platform with human or AI Keeper, not a photograph or a dashboard.
Input image: the supplied server recovery screenshot is ONLY a palette and painted file-folder component style reference. Preserve its subdued ochre protruding upper-left paper tab, thin worn brass outline, matte charcoal-brown face and warm readable Chinese text. DO NOT repeat the network error copy, the physical room, or the central compass artwork.
Primary request: design three compact operational dialog examples on one landscape board. First: a broad low-height browser window study for a destination selector titled "前往…", with small honest location label "当前场景 · 密斯卡托尼克大学", a short note "提出出发请求，由守秘人回应。", a central scrollable list of three publicly known destinations "医学院", "莱特的办公室", "莱特的小屋", and a visible stable bottom action "取消". Second: the SAME destination chooser in a narrow 390px by 480px window, complete heading and close button at top, list scrolls while cancel stays visible. Third: a companion compact "普通掷骰" dialog with input "骰子表达式", value "1d100", small note "只产生骰点，不自动改变剧情。", and equally accessible bottom "取消" / "掷骰" buttons.
Composition: straight-on flat UI studies, not physical cards on a desk; all dialog silhouettes visible, with hand-painted archival file-folder skins and restrained upper-left paper tabs. Controlled content density, clear title/current place/action hierarchy. Keep the parchment illustration OUT of these utilitarian dialogs so it does not take height from live controls. A modest hidden list scrollbar and faint focus ring may explain scroll and keyboard focus. Close controls must have at least 44px touch boxes, bottom actions at least 44px tall. Live game chat background should be only a very quiet dark abstract backing, no extra portraits, maps or invented destinations.
Palette: charcoal #201c17, matte umber #30281f, dusty ochre #8d7954, warm ivory #e4dccd, readable subdued grey #b3a998. Chinese serif restrained headings, Chinese sans-serif forms and help. No luminous gold, no bright white parchment, no high-contrast grain, no SaaS gradients, no perspective, no physical props, no real compass instrument, no huge ornate borders, no logos or watermark.
Constraints: no secret map, no requirements invented from skill scores, no automatic scene-change success message, no raw database IDs, no extra confirmation dialog. This is a component layout/material reference only, not evidence of live game state.
```

实现约束：标题与底部操作常驻，中间独立滚动；按钮至少 44px、键盘焦点约束、Escape 取消且不提交，关闭恢复入口焦点。地点来自服务端公开投影，不把内部 ID 显示为正文。点击地点提交原始 ID，由主持回应；只有已提交的服务端事件可改变当前位置。
