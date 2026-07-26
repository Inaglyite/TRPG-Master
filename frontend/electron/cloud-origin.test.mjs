import { describe, expect, it } from "vitest";

import { validateCloudOrigin } from "./cloud-origin.cjs";

describe("validateCloudOrigin", () => {
  it("接受裸 https origin 并规范化", () => {
    expect(validateCloudOrigin("https://trpg.example.com")).toBe("https://trpg.example.com");
    expect(validateCloudOrigin("https://trpg.example.com/")).toBe("https://trpg.example.com");
    expect(validateCloudOrigin("  https://trpg.example.com:8443 ")).toBe(
      "https://trpg.example.com:8443",
    );
  });

  it("拒绝非 https 协议", () => {
    expect(validateCloudOrigin("http://trpg.example.com")).toBeNull();
    expect(validateCloudOrigin("ws://trpg.example.com")).toBeNull();
    expect(validateCloudOrigin("file:///etc/passwd")).toBeNull();
  });

  it("拒绝用户信息、路径、查询与哈希", () => {
    expect(validateCloudOrigin("https://user:pass@trpg.example.com")).toBeNull();
    expect(validateCloudOrigin("https://trpg.example.com/app")).toBeNull();
    expect(validateCloudOrigin("https://trpg.example.com/?mode=online")).toBeNull();
    expect(validateCloudOrigin("https://trpg.example.com/#frag")).toBeNull();
  });

  it("拒绝非字符串与非法 URL", () => {
    expect(validateCloudOrigin("")).toBeNull();
    expect(validateCloudOrigin("not a url")).toBeNull();
    expect(validateCloudOrigin(null)).toBeNull();
    expect(validateCloudOrigin(undefined)).toBeNull();
    expect(validateCloudOrigin(42)).toBeNull();
  });
});
