# 服务器连接 UI：效果图与实际素材

使用内置 imagegen，不使用 CLI、叙事模型 Key 或正式服务器。均为新生成图片，没有覆盖既有素材。图片只负责材质与构图，地址、登录状态、警告和按钮全为真实 DOM。

## 效果图

工作区：`server-connection-concept-v1.png`。

生成原图：`/home/inaglyite/.codex/generated_images/01a06c3d-3b8c-7263-82c5-de1bee5f9328/exec-9bd25e2c-f7c6-45bb-bb4a-a398771871bd.png`。

提示词：

```text
Use case: ui-mockup. Asset type: high fidelity implementable Chinese browser-game authentication connection settings board. Primary request: three connected interface studies in a landscape canvas for a Call of Cthulhu tabletop platform with human or AI Keeper modes. Left: a quiet server identity card under a login form, title 连接服务器, a folder tab 当前连接, full wrapping address https://table.example.com, small honest note 账号、存档和权限属于此服务器, a restrained 修改服务器 button. Center: expanded editor titled 修改服务器 with practical large address input containing https://table.example.com, small note 留空使用默认服务器, clear caution 更换服务器会清除本机旧会话视图；不会删除服务器上的存档, equally accessible well-padded 保存并重新检查 and 取消 buttons, and secondary readable HTTP未加密 warning. Right: recovery state card title 无法连接服务器, short direct explanation 请检查网络或服务器地址, primary 重新检查 and secondary 修改服务器 / 返回模式选择, no fake green connected state. Show only these actual available actions, no social login, no password reset, no certificate verified badge, no realtime dashboard, no API key field, no account syncing promise. Style: screenshot-realistic straight-on DOM design reference with clearly readable Chinese text and 44px practical buttons, not a device mockup. Specific visual identity: 1920s investigation archive desk, subtle dossier folder edge as signature, matte dark walnut #181511 backdrop, charcoal-brown #24201b card panels, restrained aged brass #baa16a accents, warm ivory #e4dccd body text, muted grey #b3a998 help text, soft desaturated ochre warning border without red alarm glow. Chinese serif restrained headings and Chinese sans-serif inputs and help. Generous practical whitespace, calm for night reading; subtle closed file folders in background subordinate to interface. No glowing gold, no bright parchment, no charts, no portraits, no watermarks. This is a layout/material reference, not proof of a current server or live state.
```

实现区别：连接地址不是已认证或证书已验证标记；HTTP 警告只在实际 HTTP 地址时显示，不照抄生成图的混合示意。返回入口放在卡片顶部，等待或低高度时不用先找底部按钮。

## 未采用的写实档案夹／实体指南针草稿

用户明确指出这张图过于写实，且需要的是文件夹组件和纸上指南针图案，而不是桌上实体指南针。保留以下提示词与原图作为迭代记录；当前界面不再引用 `connection-dossier-v1.webp`。最终选择是[手绘文件夹组件提示词集](folder-components-prompt-v1.md)中的横版／竖版皮肤与独立纸质指南针插画。

工作区原图：`connection-dossier-source-v1.png`。

草稿压缩资源：`frontend/src/assets/ui/connection-dossier-v1.webp`，由生成原图机械缩放、WebP 压缩。留存但不接入最终界面；不得将这张草稿写成当前正式皮肤。

生成原图：`/home/inaglyite/.codex/generated_images/01a06c3d-3b8c-7263-82c5-de1bee5f9328/exec-95b6bce4-1d71-4909-af03-2d4a091bc69a.png`。

提示词：

```text
Use case: stylized-concept. Asset type: FINAL production bitmap skin for the header of a real Chinese Call of Cthulhu tabletop application's connection dossier card, not a UI screenshot. Primary request: an elegant matte dark archival folder lying flat on an old investigator's desk with a small aged-brass compass, combining the user's favorite file-folder component motif and compass background motif. Composition/framing: wide landscape 1536x1024, straight overhead, the left 72 percent is smooth deep charcoal-brown blank folder cover with almost no marks, reserved for REAL DOM connection address and help text; a subdued folder tab protrudes at the top edge, a little binder clip and closed case-file edge in the far upper right, a small antique compass occupies ONLY the far right third, no bright focal points. Most decoration stays at the right edge and upper corner so the asset can be cover-cropped into a 440x155 or 680x150 header. Style/medium: sophisticated hand-painted environmental realism, believable understated 1920s archival materials, not a cartoon or metallic fantasy game frame. Materials: worn matte walnut, unprinted charcoal-brown file cover, softly scuffed brass compass, very sparse leather edge and paper layering. Lighting/mood: quiet indirect shaded lamplight, comfortable for long nighttime reading, exceptionally low contrast behind text area. Color palette: background #181511, folder #24201b, left blank area #201c17, aged brass #baa16a desaturated and dark, absolutely no white parchment or luminous gold. Text: NONE; no numbers, cardinal letters, handwriting, labels, or invented interface controls. Compass face may have tiny unlabelled tick marks only. Constraints: this is a reusable production decorative material; no UI, no buttons, no logos, no watermark, no people, no flames, no flares, no glows, no high-frequency grain, no large border, no bright compass face. Keep all imagery subordinate to readable DOM text, and do not imply a verified network, authentication, permissions, or a game-state change.
```
