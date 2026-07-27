import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  localConfigPath,
  migrateLegacyLocalConfig,
  normalizeLocalConfig,
  writeLocalConfig,
} from "./local-config.cjs";

const temporaryDirectories = [];

function temporaryUserData() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "trpg-config-"));
  temporaryDirectories.push(directory);
  return directory;
}

afterEach(() => {
  while (temporaryDirectories.length) {
    fs.rmSync(temporaryDirectories.pop(), { recursive: true, force: true });
  }
});

describe("Electron 本地模型配置", () => {
  it("只接受 HTTP(S) endpoint 和受限长度的密钥", () => {
    expect(
      normalizeLocalConfig({
        api_key: "  sk-test  ",
        base_url: "http://127.0.0.1:11434/v1/",
        glm_api_key: " glm-test ",
        ignored: "不会写入",
      }),
    ).toEqual({
      api_key: "sk-test",
      base_url: "http://127.0.0.1:11434/v1",
      glm_api_key: "glm-test",
    });
    expect(() =>
      normalizeLocalConfig({ api_key: "", base_url: "https://api.example" }),
    ).toThrow("API Key");
    expect(() =>
      normalizeLocalConfig({
        api_key: "secret",
        base_url: "file:///tmp/config",
      }),
    ).toThrow("HTTP");
    expect(() =>
      normalizeLocalConfig({
        api_key: "secret",
        base_url: "https://user:password@example.test",
      }),
    ).toThrow("格式无效");
  });

  it("把配置原子写进 userData/runtime，而不是只读安装资源", () => {
    const userData = temporaryUserData();
    const target = localConfigPath(userData);
    expect(writeLocalConfig(target, { api_key: "secret" })).toEqual({
      ok: true,
    });
    expect(target).toBe(path.join(userData, "runtime", ".env.json"));
    expect(JSON.parse(fs.readFileSync(target, "utf8"))).toEqual({
      api_key: "secret",
      base_url: "https://api.deepseek.com",
    });
    if (process.platform !== "win32") {
      expect(fs.statSync(target).mode & 0o777).toBe(0o600);
    }
  });

  it("可把旧安装目录中的有效配置迁到 userData，非法配置不迁移", () => {
    const userData = temporaryUserData();
    const legacy = path.join(userData, "old", ".env.json");
    const target = localConfigPath(userData);
    fs.mkdirSync(path.dirname(legacy), { recursive: true });
    fs.writeFileSync(
      legacy,
      JSON.stringify({ api_key: "old-secret", base_url: "https://api.test" }),
      "utf8",
    );
    expect(migrateLegacyLocalConfig(legacy, target)).toBe(true);
    expect(JSON.parse(fs.readFileSync(target, "utf8")).api_key).toBe(
      "old-secret",
    );

    const invalidTarget = path.join(userData, "invalid", ".env.json");
    fs.writeFileSync(legacy, JSON.stringify({ api_key: "" }), "utf8");
    expect(migrateLegacyLocalConfig(legacy, invalidTarget)).toBe(false);
    expect(fs.existsSync(invalidTarget)).toBe(false);
  });
});
