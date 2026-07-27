const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld(
  "trpgSetup",
  Object.freeze({
    saveConfig: (config) => ipcRenderer.invoke("trpg:save-local-config", config),
  }),
);
