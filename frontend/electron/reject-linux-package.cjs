const { unsupportedPlatformMessage } = require("./packaged-backend.cjs");

function rejectionMessage() {
  return [
    unsupportedPlatformMessage("linux"),
    "此命令已停止生成会误带 Windows 后端的损坏 AppImage。",
    "Windows 安装包请在 Windows 上运行 packaging/build_windows.ps1。",
  ].join("\n");
}

if (require.main === module) {
  console.error(rejectionMessage());
  process.exitCode = 1;
}

module.exports = { rejectionMessage };
