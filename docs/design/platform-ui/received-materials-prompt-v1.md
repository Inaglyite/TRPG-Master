# 收到的图片：设计参考

生成方式：内置 image_gen；效果图 `received-materials-concept-v1.png`。

用途：玩家侧线索卡内部的可折叠收到素材目录，不是主持作者素材库；文字和按钮由 DOM 实现，不把生成图当整页截图铺上去。

提示词：

Use case: ui-mockup. Asset type: implementable UI design reference for an existing Chinese Call of Cthulhu tabletop roleplaying game, not a new website. Primary request: show two straight-on views side by side, a narrow desktop investigator sidebar and a 390px mobile drawer. Each view has exactly three vertically stacked collapsible archive sections 人物状态, 线索, 道具. The expanded 线索 section contains quiet category tabs 全部 探案 事件 人物, one short text clue with small 详情 and 出示 buttons, and a clearly distinct collapsible subsection 收到的图片 with two compact labeled material rows 医生提供的照片 and 手绘地图, each a small stylized illustration thumbnail and a 查看 button. Below the list a readable quiet note 查看图片不会获得新线索或执行行动. The focus is persistent player received materials, NOT a keeper author catalog. Frame the surfaces as stylized hand-drawn archive folder components, flat front view, matte weathered paper edges, restrained printed compass detail, NOT photorealistic physical objects or a desk photograph. Palette: charcoal walnut #181511, warm panel #24201b, muted brass #baa16a, warm readable ivory #e4dccd, muted warm gray #b8ac96. Body Chinese sans serif, restrained Chinese serif section headings. Generous readable spacing, 44px buttons, modest thumbnail rows, quiet thin separators. The scene reading area is only suggested behind the drawer, no large ornate frames, no portraits, no charts, no glowing gold, no hard light, no glitter, no motion, no busy texture behind text. Desktop sidebar is compact and mobile remains scrollable without squeezed controls. No watermark. The mockup is design reference only; all production text and buttons will be real DOM.

实现取舍：沿用三个主卡与当前九宫格档案夹；初版目录不预取缩略图，避免每次快照刷新发起图片读取，玩家主动点击后打开现有阅读器。材料标题仅来自接收者授权投影，不按叙事文字猜测。该效果图的图文只是设计参考，不是新游戏线索。
