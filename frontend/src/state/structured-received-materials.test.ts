import { beforeEach, describe, expect, it } from "vitest";
import { useStructuredStore } from "./structured-store";
import type { StructuredEventEnvelope } from "../protocol/structured";

const assets = [{ id: "photo", label: "仅给本人的照片" }];
function snapshot(received: unknown = assets) {
  useStructuredStore.getState().applySnapshot(
    {
      revision: 1,
      investigator_id: "alice",
      received_assets: received,
      clues: [],
      items: [],
      requests: [],
      pending_checks: [],
    },
    "world-a",
  );
}
function event(investigatorId = "alice"): StructuredEventEnvelope {
  return {
    protocol_version: 1,
    type: "handout_presented",
    world_id: "world-a",
    event_id: 2,
    sequence: 2,
    revision: 2,
    cause_request_id: null,
    payload: {
      asset_id: "new-photo",
      investigator_id: investigatorId,
      caption: "当场给你的图片",
    },
  };
}
describe("recipient material projection", () => {
  beforeEach(() => useStructuredStore.getState().reset());
  it("restores the server catalog, not the keeper author's catalog", () => {
    snapshot();
    expect(useStructuredStore.getState().receivedAssets).toEqual(assets);
    expect(useStructuredStore.getState().keeperAssets).toEqual([]);
  });
  it("replaces on recovery including missing/empty fields, and clears on world change", () => {
    snapshot();
    snapshot(undefined);
    // Omitted field is a legacy server, not permission to retain old entries.
    useStructuredStore
      .getState()
      .applySnapshot({ revision: 2, investigator_id: "alice" }, "world-a");
    expect(useStructuredStore.getState().receivedAssets).toEqual([]);
    snapshot();
    useStructuredStore.getState().bindWorld("world-b");
    expect(useStructuredStore.getState().receivedAssets).toEqual([]);
  });
  it("committed private delivery appears immediately, idempotently, only for its recipient", () => {
    snapshot([]);
    useStructuredStore.getState().applyEvent(event("bob"));
    expect(useStructuredStore.getState().receivedAssets).toEqual([]);
    useStructuredStore.getState().applyEvent(event());
    useStructuredStore.getState().applyEvent(event());
    expect(useStructuredStore.getState().receivedAssets).toEqual([
      { id: "new-photo", label: "当场给你的图片" },
    ]);
  });
  it("rejects malformed catalog entries and deduplicates stable IDs", () => {
    snapshot([null, {}, { id: "", label: "wrong" }, ...assets, ...assets]);
    expect(useStructuredStore.getState().receivedAssets).toEqual(assets);
  });
});
