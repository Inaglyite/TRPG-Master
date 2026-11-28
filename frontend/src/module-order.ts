const LOCAL_MODULE_ORDER = "trpg-local-module-order";

export function orderModules<T extends { id: string }>(
  modules: T[],
  ids: string[],
): T[] {
  const remaining = new Map(modules.map((item) => [item.id, item]));
  const ordered: T[] = [];
  for (const id of ids) {
    const item = remaining.get(id);
    if (item) {
      ordered.push(item);
      remaining.delete(id);
    }
  }
  return [...ordered, ...remaining.values()];
}

export function readLocalModuleOrder(): string[] {
  try {
    const ids: unknown = JSON.parse(
      localStorage.getItem(LOCAL_MODULE_ORDER) ?? "[]",
    );
    return Array.isArray(ids)
      ? ids.filter((id): id is string => typeof id === "string")
      : [];
  } catch {
    return [];
  }
}

export function saveLocalModuleOrder(ids: string[]) {
  localStorage.setItem(LOCAL_MODULE_ORDER, JSON.stringify(ids));
}
