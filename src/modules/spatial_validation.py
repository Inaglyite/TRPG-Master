"""Cross-reference validation shared by compiler and editor schema consumers."""

from __future__ import annotations

import re

from src.modules.spatial_format import SpatialDefinition


def validate_spatial(module, manifest=None) -> None:
    spatial = getattr(module, "spatial", None)
    if spatial is None:
        return
    if manifest is not None:
        if "spatial_investigation" not in manifest.capabilities:
            raise ValueError("spatial 需要声明 spatial_investigation capability")
        if tuple(map(int, re.split(r"[-+]", manifest.min_engine_version)[0].split("."))) < (
            1,
            1,
            0,
        ):
            raise ValueError("spatial 需要 min_engine_version >= 1.1.0")
    scenes, clues = module.scenes, module.clues
    flags = module.initial_state.flags
    clocks = module.initial_state.case_clocks
    labels = [item.label for item in spatial.items.values()]
    if len(labels) != len(set(labels)):
        raise ValueError("空间物品 label 不得重复（避免旧背包绑定歧义）")
    for sid, node in spatial.nodes.items():
        if sid not in scenes:
            raise ValueError(f"spatial.nodes 引用不存在的场景 {sid}")
        seen, parent = {sid}, node.parent_scene_id
        while parent:
            if parent not in scenes or parent in seen or len(seen) >= 8:
                raise ValueError(f"场景父链非法、循环或超过 8 层: {sid}")
            seen.add(parent)
            ancestor = spatial.nodes.get(parent)
            if ancestor is None or ancestor.kind != "container":
                raise ValueError(f"父场景必须声明为 container: {parent}")
            parent = ancestor.parent_scene_id
        if node.kind == "container":
            entry = spatial.nodes.get(node.entry_scene_id)
            if entry is None or entry.kind != "location":
                raise ValueError(f"容器入口必须指向叶场景: {sid}")
            parents = set()
            current = entry.parent_scene_id
            while current and current not in parents:
                parents.add(current)
                ancestor = spatial.nodes.get(current)
                if ancestor is None:
                    raise ValueError(f"容器入口父链缺少节点声明: {current}")
                current = ancestor.parent_scene_id
            if sid not in parents:
                raise ValueError(f"容器入口不是其子孙: {sid}")
        elif node.entry_scene_id:
            raise ValueError(f"location 不可声明 entry_scene_id: {sid}")
    pairs = set()
    for cid, edge in spatial.connections.items():
        if edge.source_scene_id not in scenes or edge.target_scene_id not in scenes:
            raise ValueError(f"通路引用不存在的场景: {cid}")
        if edge.source_scene_id == edge.target_scene_id:
            raise ValueError(f"通路不能连接自身: {cid}")
        source = _leaf(spatial, edge.source_scene_id)
        target = _leaf(spatial, edge.target_scene_id)
        if source == target:
            raise ValueError(f"通路入口解析后不能连接自身: {cid}")
        directions = [(source, target)]
        if edge.bidirectional:
            directions.append((target, source))
        for pair in directions:
            if pair in pairs:
                raise ValueError(f"同一有向通路重复定义: {pair}")
            pairs.add(pair)
        _condition(edge.visible_when.model_dump(), spatial, flags, clocks, clues)
        _condition(edge.requires.model_dump(), spatial, flags, clocks, clues)
        for effect in edge.costs:
            _effect(effect.model_dump(), spatial, flags, clocks, clues, scenes)
            if effect.delta >= 0:
                raise ValueError("通路 costs 必须消耗物品状态（delta < 0）")
    for pid, point in spatial.points.items():
        if point.scene_id not in scenes or (
            spatial.nodes.get(point.scene_id) and spatial.nodes[point.scene_id].kind != "location"
        ):
            raise ValueError(f"调查点必须位于叶场景: {pid}")
        _condition(point.visible_when.model_dump(), spatial, flags, clocks, clues)
        for action in point.actions.values():
            _condition(action.requires.model_dump(), spatial, flags, clocks, clues)
            for effect in [*action.success, *action.failure]:
                _effect(effect.model_dump(), spatial, flags, clocks, clues, scenes)
                if effect.kind == "advance_clock":
                    definition = (
                        module.initial_state.case_clock_definitions.get(effect.clock_id) or {}
                    )
                    if type(definition.get("max")) is not int:
                        raise ValueError("调查代价时钟必须声明 max，确保实际可支付")
                    if effect.clock_id == "clue_clarity" or definition.get("time_advance"):
                        raise ValueError(
                            "自动线索/时间型时钟由引擎推进，不可再作为空间效果重复推进"
                        )


def _condition(value, spatial: SpatialDefinition, flags, clocks, clues, depth=0):
    if depth > 8:
        raise ValueError("条件树超过 8 层")
    op = value["op"]
    if op in {"all", "any"}:
        for child in value["conditions"]:
            _condition(child, spatial, flags, clocks, clues, depth + 1)
    elif op == "not":
        _condition(value["condition"], spatial, flags, clocks, clues, depth + 1)
    elif op == "flag":
        if value["flag_id"] not in flags:
            raise ValueError(f"条件旗标不存在: {value['flag_id']}")
        if type(flags[value["flag_id"]]) is not type(value["value"]):
            raise ValueError("旗标条件值必须与初始声明类型一致")
    elif op == "clue" and value["clue_id"] not in clues:
        raise ValueError(f"条件线索不存在: {value['clue_id']}")
    elif op == "clock" and value["clock_id"] not in clocks:
        raise ValueError(f"条件时钟不存在: {value['clock_id']}")
    elif op == "completed":
        point = spatial.points.get(value["point_id"])
        if point is None or value["action_id"] not in point.actions:
            raise ValueError("completed 引用了不存在的调查动作")
    elif op == "item":
        item = spatial.items.get(value["item_id"])
        if item is None or any(key not in item.states for key in value["minimum_states"]):
            raise ValueError("item 条件引用不存在的物品/状态")
        if any(
            not item.states[key].minimum <= minimum <= item.states[key].maximum
            for key, minimum in value["minimum_states"].items()
        ):
            raise ValueError("物品状态条件超出声明范围")


def _effect(value, spatial, flags, clocks, clues, scenes):
    kind = value["kind"]
    refs = {
        "grant_clue": ("clue_id", clues),
        "give_item": ("item_id", spatial.items),
        "set_flag": ("flag_id", flags),
        "reveal_scene": ("scene_id", scenes),
        "unlock_connection": ("connection_id", spatial.connections),
        "advance_clock": ("clock_id", clocks),
    }
    if kind == "adjust_item_state":
        item = spatial.items.get(value["item_id"])
        if item is None or value["state"] not in item.states:
            raise ValueError("效果引用不存在的物品状态")
    elif (
        kind == "give_item"
        and value["item_id"] in spatial.items
        and spatial.items[value["item_id"]].states
        and value["quantity"] != 1
    ):
        raise ValueError("带数值状态的物品每个实例数量必须为 1")
    elif kind in refs:
        field, targets = refs[kind]
        if value[field] not in targets:
            raise ValueError(f"效果引用不存在: {kind}.{value[field]}")
        if kind == "set_flag" and type(flags[value[field]]) is not type(value["value"]):
            raise ValueError("旗标效果值必须与初始声明类型一致")


def _leaf(spatial, sid):
    node = spatial.nodes.get(sid)
    return node.entry_scene_id if node and node.kind == "container" else sid


def reachable_targets(module, scene_id):
    """Static topology only: conditions are deliberately not solved as a SAT task."""
    spatial = module.spatial
    result = []
    node = spatial.nodes.get(scene_id)
    if node:
        if node.entry_scene_id:
            result.append(node.entry_scene_id)
        if node.parent_scene_id:
            result.append(node.parent_scene_id)
    for edge in spatial.connections.values():
        source, target = _leaf(spatial, edge.source_scene_id), _leaf(spatial, edge.target_scene_id)
        if source == scene_id:
            result.append(target)
        if edge.bidirectional and target == scene_id:
            result.append(source)
    return result


def validates_essential_point(module, clue_id):
    spatial = module.spatial
    if spatial is None:
        return False
    producers = []
    for point in spatial.points.values():
        for action in point.actions.values():
            success = any(e.kind == "grant_clue" and e.clue_id == clue_id for e in action.success)
            failure = any(e.kind == "grant_clue" and e.clue_id == clue_id for e in action.failure)
            if success or failure:
                producers.append(success and (action.check is None or failure))
    if not producers:
        return False
    if not any(producers):
        raise ValueError(
            f"主线线索 {clue_id} 的空间检定失败路径缺少 fallback；请配置失败保底或无检定替代动作"
        )
    return True
