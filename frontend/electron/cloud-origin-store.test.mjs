import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  readStoredCloudOrigin,
  writeStoredCloudOrigin,
} from "./cloud-origin-store.cjs";

const temporaryDirectories = [];

function temporaryUserData() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "trpg-origin-"));
  temporaryDirectories.push(directory);
  return directory;
}

afterEach(() => {
  while (temporaryDirectories.length) {
    fs.rmSync(temporaryDirectories.pop(), { recursive: true, force: true });
  }
});

describe("cloud origin 主进程持久化", () => {
  it("保存规范化 https origin，并能跨 renderer 生命周期读取", () => {
    const userData = temporaryUserData();
    expect(writeStoredCloudOrigin(userData, "https://trpg.example.com/")).toBe(
      true,
    );
    expect(readStoredCloudOrigin(userData)).toBe("https://trpg.example.com");
    expect(writeStoredCloudOrigin(userData, "https://next.example.com")).toBe(
      true,
    );
    expect(readStoredCloudOrigin(userData)).toBe("https://next.example.com");
  });

  it("拒绝非法 origin，且不覆盖原值", () => {
    const userData = temporaryUserData();
    expect(writeStoredCloudOrigin(userData, "https://safe.example")).toBe(true);
    expect(writeStoredCloudOrigin(userData, "http://unsafe.example")).toBe(
      false,
    );
    expect(readStoredCloudOrigin(userData)).toBe("https://safe.example");
  });

  it("损坏文件按未配置处理，null 可清除已保存地址", () => {
    const userData = temporaryUserData();
    fs.writeFileSync(
      path.join(userData, "cloud-origin.json"),
      "{not-json",
      "utf8",
    );
    expect(readStoredCloudOrigin(userData)).toBeNull();
    expect(writeStoredCloudOrigin(userData, null)).toBe(true);
    expect(readStoredCloudOrigin(userData)).toBeNull();
  });
});
