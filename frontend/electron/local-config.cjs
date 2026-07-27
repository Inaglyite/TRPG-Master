const fs = require("node:fs");
const path = require("node:path");

const DEFAULT_BASE_URL = "https://api.deepseek.com";
const MAX_URL_LENGTH = 2048;
const MAX_SECRET_LENGTH = 8192;

function localConfigPath(userDataPath) {
  return path.join(userDataPath, "runtime", ".env.json");
}

function cleanSecret(value, fieldName, { optional = false } = {}) {
  if (value == null && optional) return "";
  if (typeof value !== "string") {
    throw new Error(`${fieldName} 格式无效`);
  }
  const cleaned = value.trim();
  if (!cleaned && optional) return "";
  if (!cleaned) throw new Error(`请填写${fieldName}`);
  if (cleaned.length > MAX_SECRET_LENGTH || /[\u0000-\u001f\u007f]/u.test(cleaned)) {
    throw new Error(`${fieldName} 格式无效`);
  }
  return cleaned;
}

function normalizeBaseUrl(value) {
  const raw = typeof value === "string" && value.trim() ? value.trim() : DEFAULT_BASE_URL;
  if (raw.length > MAX_URL_LENGTH) throw new Error("请求地址过长");
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error("请求地址不是有效 URL");
  }
  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new Error("请求地址只允许 HTTP 或 HTTPS");
  }
  if (!parsed.hostname || parsed.username || parsed.password) {
    throw new Error("请求地址格式无效");
  }
  return raw.replace(/\/+$/u, "");
}

function normalizeLocalConfig(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("配置格式无效");
  }
  const config = {
    api_key: cleanSecret(raw.api_key, "API Key"),
    base_url: normalizeBaseUrl(raw.base_url),
  };
  const glmApiKey = cleanSecret(raw.glm_api_key, "GLM API Key", {
    optional: true,
  });
  if (glmApiKey) config.glm_api_key = glmApiKey;
  return config;
}

function atomicWriteJson(target, value) {
  const directory = path.dirname(target);
  const temporary = `${target}.tmp-${process.pid}`;
  fs.mkdirSync(directory, { recursive: true });
  try {
    fs.writeFileSync(temporary, JSON.stringify(value, null, 2), {
      encoding: "utf8",
      mode: 0o600,
    });
    try {
      fs.renameSync(temporary, target);
    } catch {
      // Windows 不允许 rename 覆盖既有文件；复制后删除临时文件仍避免半写入 JSON。
      fs.copyFileSync(temporary, target);
      fs.unlinkSync(temporary);
    }
    try {
      fs.chmodSync(target, 0o600);
    } catch {
      // Windows ACL 不使用 POSIX mode；安装仍可继续。
    }
  } finally {
    try {
      if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
    } catch {
      // 最佳努力清理，不覆盖原始写入错误。
    }
  }
}

function writeLocalConfig(target, raw) {
  let config;
  try {
    config = normalizeLocalConfig(raw);
  } catch (error) {
    return { ok: false, error: error.message || "配置格式无效" };
  }
  try {
    atomicWriteJson(target, config);
    return { ok: true };
  } catch {
    return { ok: false, error: "无法写入本地配置文件" };
  }
}

function migrateLegacyLocalConfig(legacyPath, target) {
  if (fs.existsSync(target) || !fs.existsSync(legacyPath)) return false;
  try {
    const raw = JSON.parse(fs.readFileSync(legacyPath, "utf8"));
    return writeLocalConfig(target, raw).ok;
  } catch {
    return false;
  }
}

module.exports = {
  DEFAULT_BASE_URL,
  localConfigPath,
  migrateLegacyLocalConfig,
  normalizeLocalConfig,
  writeLocalConfig,
};
