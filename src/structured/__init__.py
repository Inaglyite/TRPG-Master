"""结构化操作平台（structured_v1）后端。

契约正本：`schemas/structured-play/v1/` + `docs/STRUCTURED_PLAY_PROTOCOL_V1.md`。
本包实现：请求受理与去重、主持命令服务（每命令短事务提交 + 提交后事件发布）、
稳定物品/线索 ID 注册表、检定生命周期。不经过 GameEngine 的整轮 turn_cache；
legacy 世界路径不受影响。
"""
