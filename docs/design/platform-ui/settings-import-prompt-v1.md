# 模型设置／模组导入：生成参考

本图仅提供布局和材质参考，不是功能或安全契约。使用内置 imagegen，一次新图生成；未调用项目的游戏模型或读取真实密钥。

- 生成原图：`/home/inaglyite/.codex/generated_images/01a06c3d-3b8c-7263-82c5-de1bee5f9328/exec-5c0e8940-400a-4208-868f-8b7375b195b3.png`。
- 仓库参考：[settings-import-concept-v1.png](settings-import-concept-v1.png)。
- 实现采用既有横／竖档案夹的 1x/2x 九宫格皮肤，不拉伸整张效果图，也不从图里裁切按钮文字。

## 实现约束

采用双角色配置卡、安静哑光正文、固定标题和操作区。API Key 不回显，输入是 write-only 草稿；保留实际服务端配置作用域、数据发送确认、BYOK 门禁、成员只读与估算／提供商统计来源。不采用图中生成器自行添加的“Key 仅存在本地浏览器”“允许共享”等错误或不存在的机制。LLM 不掌握骰点与状态结算权威。

模组导入仍为本地入口，云端创作 API 的管理员限制没有修改。模组名称、作者、版本、简介和警告均来自真实检查响应，不采用图中虚构故事、封面或认证声明。

## 完整提示词

Use case: ui-mockup. Asset type: one high-fidelity product UI reference board for two modal dialogs in an existing Chinese Call of Cthulhu browser tabletop platform. Primary request: show two straight-on implementable DOM interfaces side by side: a wider model settings archive folder and a smaller module-package review folder, not a photograph of physical folders. Established visual identity: stylized hand-painted flat game UI kraft-tab folder, matte charcoal brown #24201b, quiet dark backdrop #181511, restrained old brass #baa16a, warm readable text #e4dccd and muted captions #b3a998. Subtle edge-only paper texture, nearly flat behind text, no glow, no pure white panels, no ornaments or realistic leather. Left interface: heading '模型设置', persistent '关闭', two tabs '模型配置' and '上下文'; scrollable body containing clearly separated '叙述模型' and '裁决模型' cards, concise host/scope information, form labels '服务地址', '模型名称', 'API Key', example model 'deepseek-flash', password represented only by generic dots; data-sharing confirmation checkbox; a modest context capacity row distinguishing estimated input from reserved output, no invented live values. Fixed bottom with '恢复默认', '取消', '保存配置'. Right interface: heading '导入模组', persistent '关闭'; package review labeled '模组包预览', readable author/version/rule/file-count row, short description, subdued '导入前请确认' section; fixed bottom '取消', '导入并切换'. Make body scrolling explicit but visually quiet, keep footer buttons comfortable 44px-high with ample padding. Do not show real credentials, no new product features, no fake success badge or fake security certification. Chinese serif headings and clean Chinese sans-serif body, responsive hierarchy that can become one column in narrow windows. Composition a landscape reference board with both complete dialogs at useful scale. This is design reference only: all actual text, state, forms, errors and actions will remain real HTML/CSS, never baked into a production image. Avoid dashboard charts, bright gold outlines, perspective, phone devices, watermark, tiny dense text.
