/**
 * 严格校验云端服务器 origin：
 * - 仅允许 https:（生产 TLS；开发联调请改用本地后端或 dev server）；
 * - 不允许用户名/密码；
 * - 不允许路径、查询参数或哈希（只允许裸 origin）。
 * 返回规范化的 origin（无尾斜杠），非法输入返回 null。
 */
function validateCloudOrigin(input) {
  if (typeof input !== "string") return null;
  const trimmed = input.trim();
  if (!trimmed) return null;
  let url;
  try {
    url = new URL(trimmed);
  } catch {
    return null;
  }
  if (url.protocol !== "https:") return null;
  if (url.username || url.password) return null;
  if (url.pathname !== "" && url.pathname !== "/") return null;
  if (url.search || url.hash) return null;
  return url.origin;
}

module.exports = { validateCloudOrigin };
