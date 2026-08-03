import { beforeEach, describe, expect, it } from "vitest";

import {
  getNarrationSpeed,
  narrationTickMs,
  setNarrationSpeed,
} from "./narration-speed";

beforeEach(() => {
  localStorage.clear();
});

describe("叙述速度偏好", () => {
  it("默认与无效存储值都回退标准档", () => {
    expect(getNarrationSpeed()).toBe("standard");
    localStorage.setItem("trpg-narration-speed", "ludicrous");
    expect(getNarrationSpeed()).toBe("standard");
    localStorage.setItem("trpg-narration-speed", "slow");
    expect(getNarrationSpeed()).toBe("slow");
  });

  it("持久化保存三档取值", () => {
    setNarrationSpeed("fast");
    expect(localStorage.getItem("trpg-narration-speed")).toBe("fast");
    expect(getNarrationSpeed()).toBe("fast");
    setNarrationSpeed("slow");
    expect(getNarrationSpeed()).toBe("slow");
  });

  it("拒绝非法档位", () => {
    // @ts-expect-error 故意传入非法值验证守卫
    setNarrationSpeed("ludicrous");
    expect(getNarrationSpeed()).toBe("standard");
  });

  it("节拍对应约 18/28/42 字每秒", () => {
    expect(narrationTickMs("slow")).toBeCloseTo(1000 / 18, 5);
    expect(narrationTickMs("standard")).toBeCloseTo(1000 / 28, 5);
    expect(narrationTickMs("fast")).toBeCloseTo(1000 / 42, 5);
  });
});
