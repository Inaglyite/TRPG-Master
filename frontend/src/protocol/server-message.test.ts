import { describe, expect, it } from "vitest";

import { parseServerMessage } from "./server-message";

describe("parseServerMessage", () => {
  it("retains payload fields for a known protocol message", () => {
    expect(
      parseServerMessage('{"type":"narrative_chunk","text":"雨声"}'),
    ).toEqual({
      type: "narrative_chunk",
      text: "雨声",
    });
  });

  it("rejects malformed and unknown messages without throwing", () => {
    expect(parseServerMessage("{")).toBeNull();
    expect(parseServerMessage({ type: "future_message" })).toBeNull();
    expect(parseServerMessage(null)).toBeNull();
  });

  it("accepts turn performance diagnostics", () => {
    expect(
      parseServerMessage({
        type: "turn_performance",
        metrics: { first_visible_ms: 120 },
      }),
    ).toEqual({
      type: "turn_performance",
      metrics: { first_visible_ms: 120 },
    });
  });
});
