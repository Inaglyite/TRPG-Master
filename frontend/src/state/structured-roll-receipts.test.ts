import { beforeEach, describe, expect, it } from "vitest";
import type { StructuredEventEnvelope } from "../protocol/structured";
import { initialStructuredState, useStructuredStore } from "./structured-store";

beforeEach(() => useStructuredStore.setState({ ...initialStructuredState }));

function result(type: string, cause: string, checkId = "check-1") {
  useStructuredStore.getState().applyEvent({
    protocol_version: 1,
    world_id: "world-1",
    event_id: 1,
    sequence: 1,
    revision: 1,
    cause_request_id: cause,
    type,
    payload: { check_request_id: checkId, outcome: "failure", roll: 98 },
  } as StructuredEventEnvelope);
}

function register(type: "check_response" | "free_roll_request") {
  useStructuredStore.getState().registerOutgoing({
    requestId: "response-1",
    kind: type === "check_response" ? "check_response" : "free_roll",
    label: "掷骰",
    payload: { type, check_request_id: "check-1" },
    digest: "original-digest",
  });
  useStructuredStore.getState().markSent("response-1");
  useStructuredStore.getState().markAwaitingAck("response-1");
}

describe("committed roll receipts", () => {
  it.each(["check_resolved", "check_cancelled"])(
    "%s acknowledges the matching response even when the check failed or was declined",
    (type) => {
      register("check_response");
      result(type, "response-1");
      expect(
        useStructuredStore.getState().requests["response-1"],
      ).toMatchObject({
        status: "completed",
        awaitingAck: false,
        digest: "original-digest",
        outcome: type === "check_cancelled" ? "not_executed" : "failure",
      });
    },
  );

  it("another player's result or a mismatched check does not acknowledge this response", () => {
    register("check_response");
    result("check_resolved", "other-player");
    result("check_resolved", "response-1", "other-check");
    expect(useStructuredStore.getState().requests["response-1"].status).toBe(
      "queued",
    );
    expect(
      useStructuredStore.getState().requests["response-1"].awaitingAck,
    ).toBe(true);
  });

  it("a committed free roll ends its receipt without completing an investigation", () => {
    register("free_roll_request");
    result("roll_resolved", "response-1");
    expect(useStructuredStore.getState().requests["response-1"]).toMatchObject({
      status: "completed",
      awaitingAck: false,
      outcome: null,
    });
  });
});
