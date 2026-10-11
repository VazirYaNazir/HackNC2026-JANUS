// Narrow, explicit bridge between Studio's React app and the Electron main process.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('janusDesktop', {
  getLocalConnection: () => ipcRenderer.invoke('janus:local-connection'),
  agentStatus: () => ipcRenderer.invoke('janus:agent-status'),
  startAgent: () => ipcRenderer.invoke('janus:agent-start'),
});
