import type { AdventureEntry } from "../state/app-store";
import {
  fetchSoloTimelines,
  getRoomInfo,
  listWorlds,
  type WorldSummary,
} from "./worlds";

export function mayHaveSoloSave(world: WorldSummary, module: string) {
  return (
    world.module === module &&
    world.metadata?.play_mode === "solo" &&
    world.role === "owner" &&
    (world.metadata.room_status === "playing" ||
      Boolean(
        world.resume_world_id && world.resume_world_id !== world.world_id,
      ))
  );
}

/** 初始自动存档可能在选角前产生。可读档的目标须是已经开局的当前时间线。 */
export async function listSoloSaves(
  module: string,
  title: string,
): Promise<AdventureEntry[]> {
  const worlds = (await listWorlds()).filter((world) =>
    mayHaveSoloSave(world, module),
  );
  const slots = await Promise.all(
    worlds.map(async (world): Promise<AdventureEntry | null> => {
      const tree = await fetchSoloTimelines(world.world_id);
      const current = tree.worlds.find(
        (item) => item.world_id === tree.active_world_id,
      );
      if (!current) return null;
      const metadata =
        current.world_id === world.world_id
          ? world.metadata
          : (await getRoomInfo(current.world_id)).metadata;
      if (metadata?.play_mode !== "solo" || metadata.room_status !== "playing")
        return null;
      return {
        root_world_id: world.world_id,
        resume_world_id: current.world_id,
        module_name: module,
        module_title: title,
        slot_name: world.metadata?.name,
        scene_name: current.scene_name,
        character_name: current.character_name,
        updated_at: current.updated_at || world.updated_at,
        timeline_count: tree.worlds.length,
      };
    }),
  );
  return slots
    .filter((item): item is AdventureEntry => item !== null)
    .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
}
