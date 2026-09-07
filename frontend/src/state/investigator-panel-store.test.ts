import { beforeEach, describe, expect, it } from "vitest";

import { useInvestigatorPanelStore } from "./investigator-panel-store";

function resetStore() {
  useInvestigatorPanelStore.setState({
    worldId: null,
    prefsByWorld: {},
    editor: null,
  });
}

function prefsOf(worldId: string | null) {
  const key = worldId || "__session__";
  return useInvestigatorPanelStore.getState().prefsByWorld[key];
}

beforeEach(resetStore);

describe("世界作用域与折叠偏好", () => {
  it("偏好按世界隔离，切世界重置草稿并保留各自折叠态", () => {
    const store = useInvestigatorPanelStore.getState();
    store.syncWorld("world-a");
    useInvestigatorPanelStore.getState().toggleCard("clues");
    expect(prefsOf("world-a").collapsed.clues).toBe(true);

    useInvestigatorPanelStore.getState().openEditor({
      kind: "use",
      itemLabel: "手电筒",
      usage: "照亮",
      target: "",
    });
    useInvestigatorPanelStore.getState().syncWorld("world-b");
    // 切世界：编辑器草稿被丢弃
    expect(useInvestigatorPanelStore.getState().editor).toBeNull();
    // 新世界是独立偏好
    expect(prefsOf("world-b").collapsed.clues).toBe(false);

    useInvestigatorPanelStore.getState().syncWorld("world-a");
    expect(prefsOf("world-a").collapsed.clues).toBe(true);
  });

  it("世界 id 缺失时退回会话内状态，不拿角色名当标识", () => {
    useInvestigatorPanelStore.getState().syncWorld(null);
    useInvestigatorPanelStore.getState().setClueFilter("task");
    expect(prefsOf(null).clueFilter).toBe("task");
  });
});

describe("线索对齐", () => {
  it("首次见到线索集合建立基线，之后的增量标记为新增", () => {
    useInvestigatorPanelStore.getState().syncWorld("world-a");
    useInvestigatorPanelStore.getState().reconcileClues(["investigation:a"]);
    expect(prefsOf("world-a").seenInitialized).toBe(true);
    expect(prefsOf("world-a").seenClueKeys).toEqual(["investigation:a"]);
    // 新线索不在基线内
    useInvestigatorPanelStore
      .getState()
      .reconcileClues(["investigation:a", "event:b"]);
    expect(prefsOf("world-a").seenClueKeys).toEqual(["investigation:a"]);
  });

  it("裁剪已不存在条目的展开与已读状态", () => {
    useInvestigatorPanelStore.getState().syncWorld("world-a");
    useInvestigatorPanelStore.getState().reconcileClues(["investigation:a"]);
    useInvestigatorPanelStore.getState().toggleClueDetail("investigation:a");
    expect(prefsOf("world-a").expandedClues).toContain("investigation:a");
    useInvestigatorPanelStore.getState().reconcileClues([]);
    expect(prefsOf("world-a").expandedClues).toEqual([]);
    expect(prefsOf("world-a").seenClueKeys).toEqual([]);
  });

  it("查看详情即摘掉新增标记", () => {
    useInvestigatorPanelStore.getState().syncWorld("world-a");
    useInvestigatorPanelStore.getState().reconcileClues(["investigation:a"]);
    useInvestigatorPanelStore
      .getState()
      .reconcileClues(["investigation:a", "event:b"]);
    useInvestigatorPanelStore.getState().toggleClueDetail("event:b");
    expect(prefsOf("world-a").seenClueKeys).toContain("event:b");
    // 再点一次收起详情，已读状态不回退
    useInvestigatorPanelStore.getState().toggleClueDetail("event:b");
    expect(prefsOf("world-a").expandedClues).not.toContain("event:b");
    expect(prefsOf("world-a").seenClueKeys).toContain("event:b");
  });
});

describe("编辑器生命周期", () => {
  it("打开记录世界，更新草稿清错误，关闭清空", () => {
    useInvestigatorPanelStore.getState().syncWorld("world-a");
    useInvestigatorPanelStore.getState().openEditor({
      kind: "present",
      clueKey: "investigation:a",
      clueSummary: "日记",
      target: "",
      question: "",
      physicalItem: null,
    });
    useInvestigatorPanelStore.getState().setEditorError("x");
    useInvestigatorPanelStore.getState().updateEditorDraft({ target: "医生" });
    const editor = useInvestigatorPanelStore.getState().editor;
    expect(editor?.worldId).toBe("world-a");
    expect(editor?.error).toBeNull();
    expect(editor?.draft.kind === "present" && editor.draft.target).toBe(
      "医生",
    );
    useInvestigatorPanelStore.getState().closeEditor();
    expect(useInvestigatorPanelStore.getState().editor).toBeNull();
  });
});
