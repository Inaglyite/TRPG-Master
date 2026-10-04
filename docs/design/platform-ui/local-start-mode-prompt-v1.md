# 本地开局模式选择

生成方式：内置 imagegen；[设计参考](local-start-mode-concept-v1.png)。原图保留在 Codex generated_images 下，项目引用已复制入本目录。

## 完整提示词

Use case: ui-mockup. Asset type: shippable design reference for an existing Chinese Call of Cthulhu desktop/browser RPG tool. Primary request: show two straight-on polished screens side by side: a local investigator selection screen with restrained archive dossier cards, a clearly visible compact control labeled 游玩方式：人类主持 next to the character confirmation area, and the same screen with a named archive-folder modal open titled 游玩方式. The modal offers four selectable cards 经典 AI 叙事 / 人类主持 / AI 辅助主持 / AI 主持; 人类主持 is selected and explains 不调用模型，不需要 API Key. A quiet note clarifies 单人模式由你兼任守秘人与调查员，主持资料对你可见 and 当前结构化模式支持调查与社交，尚无完整战斗与结局结算. One clear 关闭 button and accessible close icon. The first screen's footer has 返回 and 以此调查员开始, with mode summary not overwhelming the character dossier. Visual identity: the existing calm hand-drawn archive-folder interface, matte walnut/charcoal background #181511, surface #24201b, readable warm ivory #e4dccd, muted brass #baa16a, secondary warm gray #b3a998. Paper folder edges and binder tab are thin understated illustrated graphics, not photoreal physical folder photography. Chinese serif display headings, legible sans serif body, labels large enough for long nighttime sessions. Desktop mockup plus a small 390px-width inset showing modal body scroll while close/actions stay reachable. No animation, no glare, no bright light sources, no neon, no extra portraits, no SaaS charts, no watermark. The controls must look implementable as real DOM, not text baked into a background. Preserve generous button padding and 44px interaction heights.

## 取舍与验收要求

主代理实际查看生成图：紧凑模式摘要、四种主持方式和短窗口独立滚动符合需求；生成的浅纸色过亮，正式实现继续复用已生成的深色档案夹九宫格与1x/2x素材，不把浅色参考图整张铺入界面。图中的人物与额外侧栏不作为新增需求。

关键缺口不只是模式选择控件：本地 `start` 目前固定进入旧引擎开场；已有本地结构化浏览器用例修改临时世界metadata后再刷新，不能证明真实用户能无Key新建。必须补真正的本地创建协议，服务端明确校验模式、本地权限、新世界与调查员绑定；human 不启动模型回合。旧世界不覆盖，失败不显示已开局，重试不重复创建。

验收必须从真实开局页选择human，不用测试脚本改数据库metadata，使用关闭的模型地址或计数服务证明零模型请求，并验证主持发言、玩家按钮、保存/刷新与返回后新建不污染旧世界。云端复用原协议入口，不在正式环境测试。
