import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "./state/app-store";
import { handleServerPayload, setActiveTransport } from "./ws";

describe("save rename authoritative feedback", () => {
  beforeEach(() => {
    setActiveTransport({ send: vi.fn() });
    useAppStore.setState({ renameSlotId: "slot_001" });
  });
  afterEach(() => setActiveTransport(null));
  it("closes the editor after successful acknowledgement", () => {
    handleServerPayload({ type: "save_renamed", ok: true, label: "调查存档" });
    expect(useAppStore.getState().renameSlotId).toBeNull();
  });
  it("keeps the editor when the server rejects the rename", () => {
    handleServerPayload({ type: "save_renamed", ok: false });
    expect(useAppStore.getState().renameSlotId).toBe("slot_001");
  });
});
