# 手绘档案夹组件：最终素材与提示词

使用内置 imagegen。用户提供的“无法连接服务器”卡片是风格参考：手绘游戏 UI 文件夹、左上页签、低亮度棕黑面板、纸上印制指南针。不是写实桌面道具。原图均已保存到本目录，透明度保留；WebP 仅做机械尺寸转换和压缩。没有调用 CLI 或叙事模型，没有将状态、地址、标题、按钮烘焙进图。

## 资源与尺寸

| 用途 | 本目录源图 | 前端普通素材 | 前端 2x 素材 |
|---|---|---|---|
| 竖版提示／恢复卡 | `folder-panel-portrait-source-v1.png`，1024×1536 | `folder-panel-portrait-v1.webp`，512×768 | `folder-panel-portrait-v1@2x.webp`，1024×1536 |
| 宽版信息／地址面板 | `folder-panel-wide-source-v1.png`，1774×887 | `folder-panel-wide-v1.webp`，892×446 | `folder-panel-wide-v1@2x.webp`，1784×892 |
| 独立纸质指南针 | `printed-compass-source-v1.png`，1536×1024 | `printed-compass-v1.webp`，768×512 | `printed-compass-v1@2x.webp`，1536×1024 |

前端资源目录：`frontend/src/assets/ui/`。宽版转换时统一到偶数像素；2x 表示呈现密度，不意味着原图被放大后凭空增加细节。

`ArchiveFolderPanel` 用 CSS `border-image` 九宫格呈现：页签与四角固定，边线／低细节中心随内容伸展。不要把整张图作为 `background-size: 100% 100%` 硬拉；不要给卡片整体做非等比 transform。皮肤用 `image-set` 选择 1x/2x，百分比切片使两套密度共用布局。指南针用 `srcSet`、`object-fit: contain` 独立等比显示，不能跟着边框拉伸。

当前用于服务器信息面板和网络故障卡；不是把所有游戏卡片强行换成同一个文件夹。新增极宽／极矮组件时先验证构图和角区最小宽度，再决定是否需要新变体。

## 竖版皮肤提示词

原始生成文件：`/home/inaglyite/.codex/generated_images/01a06c3d-3b8c-7263-82c5-de1bee5f9328/exec-abdbca99-0c8a-4161-8a31-f5980d6a28b5.png`。

```text
Use case: stylized-concept. Asset type: FINAL reusable UI COMPONENT SKIN, transparent outer background, portrait archival file-folder panel frame for a Chinese occult tabletop browser game. Reference image: the attached Chinese error card screenshot is the exact visual direction, use its stylized painted UI card and folder tab design, NOT a photograph of a desk. Primary request: reproduce the COMPONENT aesthetic as a NEW EMPTY card skin with no text, no buttons and no center illustration. A single dark-brown archival folder panel, straight-on flat game UI, gently worn rounded rectangular outline, a subtle thin aged-brass rim, one thick muted-ochre PAPER FILE TAB protruding behind the TOP LEFT edge and a sliver of layered paper behind the top edge. The top-left protruding folder tab is essential and must be clearly visible like the reference. Panel face is smooth matte black-brown with only very quiet painted mottling and a large empty central area reserved for live DOM headings, illustrations, text and buttons. Style: hand-painted 2D / restrained 2.5D fantasy archive GAME INTERFACE skin, illustrated and stylized exactly like the screenshot, not photorealistic, no physical environment, no perspective, no cast shadow across a tabletop. Composition: only ONE complete portrait card centered, approximately 2:3 aspect, clean transparent space outside the folder silhouette. Keep generous interior negative space and even thin edges so the bitmap can be reused on variable-height interface cards; no decorative flourishes at the bottom. Palette: matte charcoal #201c17, dark umber #30281f, muted aged brass #8d7954, dusty ochre tab #746344; no white paper, no bright yellow, no luminous gold. Constraints: no letters, no writing, no labels, no title, no numbers, no icons, no compass, no buttons, no watermark, no furniture, no photographs, no rendering of an entire application screen, no big ornate frame. Genuinely transparent outer background, preserve full folder outline without cropping. This is a UI panel material asset, not a completed screenshot.
```

## 横版皮肤提示词

参考：已生成的空白竖版皮肤，按同风格重新构图，不是将竖版非等比拉宽。

原始生成文件：`/home/inaglyite/.codex/generated_images/01a06c3d-3b8c-7263-82c5-de1bee5f9328/exec-058ad7be-0851-4e47-a786-9dee045c97ea.png`。

```text
Use case: stylized-concept. Asset type: FINAL HORIZONTAL variant of the supplied EMPTY archival file-folder GAME UI panel skin, transparent exterior. Input image role: supplied portrait empty folder skin is the edit/style target; do NOT add the compass illustration or any UI content. Primary request: recompose the SAME stylized painted folder component into a wide short card approximately 2:1 width-to-height, retaining the exact quiet charcoal-brown matte cover, subtly worn thin aged-brass border, rounded corners, dusty ochre paper tab protruding behind the TOP LEFT edge and layered upper paper sliver. Design it as a matching sibling component for compact server identity cards, horizontal notification panels and short archive entries, not a physically widened photograph. Composition: one single complete HORIZONTAL folder skin centered, full outline visible, true transparent space outside. Top-left tab must stay modest and naturally proportioned, no giant stretched label area; flat straight-on painted game UI material, no perspective. Inner panel completely blank with large smooth quiet negative space. Maintain the original corner radius and restrained edge detail, only alter the overall card aspect ratio. NO TEXT, no title, no labels, no handwriting, no numbers, no icons, no compass, no buttons, no people, no furniture, no room, no logos, no watermark, no photo lighting. No shiny gold or bright parchment. This should match the user's stylized archived folder error-card reference, NOT realistic props on a desk. Genuine alpha transparency outside the card, subtle alpha shadow only. Suggested landscape raster 1536x768 or comparable wide composition.
```

## 纸上指南针插画提示词

参考：用户原卡片是构图／风格参考，空白文件夹皮肤仅提供色板。不要生成实体金属指南针。

原始生成文件：`/home/inaglyite/.codex/generated_images/01a06c3d-3b8c-7263-82c5-de1bee5f9328/exec-05bb371a-51f7-4a7d-9790-560cf9965e5a.png`。

```text
Use case: stylized-concept. Asset type: FINAL standalone decorative illustration for the center of a GAME UI archival folder card, transparent outer background. Reference images: the user's Chinese 无法连接服务器 screenshot is the style and composition reference; the newly generated blank folder skin is only a palette reference. Primary request: recreate the reference's PAPER PRINTED COMPASS / occult navigation diagram vignette. This is NOT a physical compass instrument, NOT a photograph, NOT a realistic brass compass on a desk. A small stack of aged, muted brown investigative sheets and envelopes, the front paper bearing a hand-drawn circular compass-rose / navigation chart printed in dark sepia ink, several overlapping corners, a modest dark closed folder edge behind, with a soft edge vignette that blends into a dark UI card. Flat illustrated game-interface art, hand-painted 2D / subtle 2.5D, the very same softened stylized archival illustration as the screenshot's central artwork. Composition: wide 3:2 landscape vignette, paper stack centered in lower half, loosely layered sheets angled by a few degrees, no table, no room, no photo lighting, no depth-of-field. All outer space genuinely transparent; preserve irregular paper silhouette and subtle shadow alpha. Front compass chart is a drawing ON THE PAPER: concentric circular rings, unlabelled ticks, eight-direction ink rose, imperfect pen strokes, no three-dimensional metal casing or glass face. Palette: matte dusty umber and tobacco paper #746344, charcoal sepia ink #28231c, warm restrained grey #b3a998 highlights; paper darker and low contrast, no white parchment, no shiny gold. Text: NONE, no letters, no invented writing, no title, no UI labels, no buttons, no logos or watermark. Keep illustration readable as a quiet archival motif without demanding attention from live DOM error text above and actions below. Produce only this illustration, not an app screenshot or the surrounding card frame.
```
