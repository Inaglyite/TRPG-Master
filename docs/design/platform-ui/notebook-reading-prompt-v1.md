# 笔记优先的调查笔记效果图

生成方式：内置 image_gen；不是 CLI，不使用项目模型 Key。

风格参考：`notes-confirmation-recovery-939.png`（已有界面截图，不作为编辑目标）。

输出：`notebook-reading-concept-v1.png`；原始生成文件 `/home/inaglyite/.codex/generated_images/01a06c3d-3b8c-7263-82c5-de1bee5f9328/exec-76c573e7-e6cf-40ef-ba4e-3d058ab5a86d.png`。

用途：布局与层级对照，不能作为整张运行背景。正式 UI 复用既有 1x/2x 文件夹九宫格，文本／按钮为 DOM。效果图不证明实际 390×360 的尺寸合格，仍须浏览器验收；图中文字若有生成偏差，以 DOM 文案为准。三个视口均保留笔记首屏与固定操作，快捷行动仅展开时显示。

## 最终提示词

Use case: ui-mockup.
Asset type: high-fidelity design board for the Chinese-language investigation notebook overlay of an existing browser tabletop roleplaying platform.
Input images: the referenced screenshot is a style reference only, not an edit target.
Primary request: show three straight-on implementable interface mockups side by side on a landscape canvas: a comfortable desktop 939x480 view, a narrow 390x480 view, and a short 390x360 view. Redesign the notebook content hierarchy: PRIVATE NOTES is always first and its editable text region is visibly reachable at initial scroll position, not hidden below four shortcut buttons. Below the notes is a quiet single accordion button labeled 快捷行动 with a plus sign, initially collapsed. Show one desktop example with the accordion expanded and four buttons 观察环境 / 检查物品 / 梳理线索 / 与人物交谈, arranged two by two below the notebook; expansion is optional and can scroll inside the panel, never move the footer.
Visual direction: same 1920s archive-folder idiom as the reference, but clearly 2D hand-painted game interface, NOT a photographed object, not a 3D leather binder. Matte near-black charcoal-brown panel #24201b, background #181511, muted worn brass #baa16a, warm ivory readable text #e4dccd, quiet muted secondary text #b3a998. Understated fixed folder tab and thin hand-drawn edges; no lavish gilt ornaments, flares, glow or bright parchment backgrounds. No new props, no character portraits, no photos.
Layout: compact header title 调查笔记 and 44px close control. Main labeled 私人笔记, a roomy editable area with just the example sentence 记下你观察到的事. A calm, readable status line near the notes, showing 尚未收到保存确认，草稿仍在. Fixed footer with buttons 重新读取 / 关闭 / 保存笔记. On the short 390x360 mockup omit the English eyebrow, use compact padding and a visibly editable textarea, retain readable status and reachable 44px controls; do not squeeze Chinese words vertically. Avoid having shortcuts compete with notes. Footer contained INSIDE the folder edges; no viewport clipping.
Typography: restrained Chinese serif for title, readable modern Chinese sans-serif body, no tiny error text. All quoted Chinese strings verbatim. No extra paragraphs or fake story facts. No device mockups, no perspective, no watermark.
This image is a design reference board, not a production sprite. Prioritize practical hierarchy and restrained accessibility over material realism.
