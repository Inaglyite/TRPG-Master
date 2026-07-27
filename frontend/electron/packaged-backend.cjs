const fs = require("node:fs");
const path = require("node:path");

function unsupportedPlatformMessage(platform) {
  if (platform === "linux") {
    return "当前未提供包含 Linux 原生后端的 AppImage。请在 Linux 上运行 bash start_desktop.sh，或使用多人联机模式。";
  }
  return `当前发行包不支持 ${platform} 内置单机后端。`;
}

function packagedBackendExecutable(resourcesPath, platform = process.platform) {
  if (platform !== "win32") {
    const error = new Error(unsupportedPlatformMessage(platform));
    error.code = "unsupported-packaged-platform";
    throw error;
  }
  const executable = path.join(
    resourcesPath,
    "backend",
    "trpg-server.exe",
  );
  if (!fs.existsSync(executable)) {
    const error = new Error(
      "安装包缺少内置后端 trpg-server.exe，请重新安装完整的 Windows 发行包。",
    );
    error.code = "missing-packaged-backend";
    throw error;
  }
  return executable;
}

function isTrpgHealthResponse(statusCode, body) {
  if (statusCode !== 200 || typeof body !== "string" || body.length > 64 * 1024) {
    return false;
  }
  try {
    const value = JSON.parse(body);
    return (
      value?.ok === true &&
      typeof value.module === "string" &&
      value.module.length > 0 &&
      typeof value.world_id === "string" &&
      value.world_id.length > 0
    );
  } catch {
    return false;
  }
}

module.exports = {
  isTrpgHealthResponse,
  packagedBackendExecutable,
  unsupportedPlatformMessage,
};
