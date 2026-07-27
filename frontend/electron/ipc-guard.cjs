/**
 * IPC 调用方与导航的纯函数校验。
 * 打包模式只信任确切的内置页面 URL（dist/index.html 的 file:// 形式），
 * 其他 file:// 一律拒绝——防止同盘恶意文件或远程页借 file 协议调用 IPC；
 * 开发模式只信任 vite dev server origin；云端 origin 仅在主进程显式批准后放行。
 */
function isTrustedSenderUrl(
  senderUrl,
  { isDev, devServerUrl, trustedFileUrl },
) {
  if (typeof senderUrl !== "string" || !senderUrl) return false;
  if (isDev) {
    try {
      return new URL(senderUrl).origin === new URL(devServerUrl).origin;
    } catch {
      return false;
    }
  }
  return typeof trustedFileUrl === "string" && senderUrl === trustedFileUrl;
}

function isNavigationAllowed(
  rawUrl,
  { isDev, devServerUrl, trustedFileUrl, approvedCloudOrigin },
) {
  if (typeof rawUrl !== "string" || !rawUrl) return false;
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    return false;
  }
  if (url.protocol === "about:") return true;
  if (approvedCloudOrigin && url.origin === approvedCloudOrigin) return true;
  if (isDev) {
    try {
      return url.origin === new URL(devServerUrl).origin;
    } catch {
      return false;
    }
  }
  return rawUrl === trustedFileUrl;
}

function isApprovedCloudSenderUrl(senderUrl, approvedCloudOrigin) {
  if (
    typeof senderUrl !== "string" ||
    !senderUrl ||
    typeof approvedCloudOrigin !== "string" ||
    !approvedCloudOrigin
  ) {
    return false;
  }
  try {
    return new URL(senderUrl).origin === approvedCloudOrigin;
  } catch {
    return false;
  }
}

module.exports = {
  isTrustedSenderUrl,
  isNavigationAllowed,
  isApprovedCloudSenderUrl,
};
