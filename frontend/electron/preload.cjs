const { contextBridge, ipcRenderer } = require("electron");

// 渲染进程唯一可见的桌面能力白名单。mode 选择经 IPC 由主进程执行：
// 单机 = 按需配置并启动本地后端；联机 = 主进程校验后同源加载云端页面。
contextBridge.exposeInMainWorld("trpgDesktop", {
  getOnlineOrigin: () => ipcRenderer.invoke("trpg:get-online-origin"),
  selectLocalMode: () => ipcRenderer.invoke("trpg:select-local"),
  selectOnlineMode: (origin, intent) =>
    ipcRenderer.invoke("trpg:select-online", origin, intent),
  returnToLauncher: () => ipcRenderer.invoke("trpg:return-launcher"),
  openEditor: () => ipcRenderer.invoke("trpg:open-editor"),
});
