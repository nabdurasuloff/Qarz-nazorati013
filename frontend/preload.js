const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('qarzNazorat', {
    getBackendPort: () => ipcRenderer.invoke('backend-port'),
});
