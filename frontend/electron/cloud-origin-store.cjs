const fs = require("node:fs");
const path = require("node:path");

const { validateCloudOrigin } = require("./cloud-origin.cjs");

const STORE_FILE = "cloud-origin.json";

function storePath(userDataPath) {
  return path.join(userDataPath, STORE_FILE);
}

function readStoredCloudOrigin(userDataPath) {
  try {
    const raw = fs.readFileSync(storePath(userDataPath), "utf8");
    const data = JSON.parse(raw);
    return validateCloudOrigin(data?.origin);
  } catch {
    return null;
  }
}

/**
 * 原子保存已校验的云端 origin。null 表示清除；写入失败返回 false，
 * 调用方不得在持久化失败时继续导航。
 */
function writeStoredCloudOrigin(userDataPath, rawOrigin) {
  const origin = rawOrigin === null ? null : validateCloudOrigin(rawOrigin);
  if (rawOrigin !== null && !origin) return false;
  const target = storePath(userDataPath);
  try {
    fs.mkdirSync(userDataPath, { recursive: true });
    if (origin === null) {
      if (fs.existsSync(target)) fs.unlinkSync(target);
      return true;
    }
    const temporary = `${target}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify({ origin }, null, 2), {
      encoding: "utf8",
      mode: 0o600,
    });
    try {
      fs.renameSync(temporary, target);
    } catch {
      // Windows 上 rename 可能拒绝覆盖既有目标；copyFileSync 可安全更新。
      fs.copyFileSync(temporary, target);
      fs.unlinkSync(temporary);
    }
    return true;
  } catch {
    return false;
  }
}

module.exports = {
  readStoredCloudOrigin,
  writeStoredCloudOrigin,
};
