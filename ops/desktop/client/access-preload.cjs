'use strict';
const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('desktopAccess',Object.freeze({
 info:()=>ipcRenderer.invoke('desktop-access:info'),
 request:value=>ipcRenderer.invoke('desktop-access:request',value),
 poll:()=>ipcRenderer.invoke('desktop-access:poll'),
 activate:value=>ipcRenderer.invoke('desktop-access:activate',value),
 setPassword:value=>ipcRenderer.invoke('desktop-access:setPassword',value),
 unlock:value=>ipcRenderer.invoke('desktop-access:unlock',value),
 open:()=>ipcRenderer.invoke('desktop-access:open'),
 lock:()=>ipcRenderer.invoke('desktop-access:lock'),
 onStatus:callback=>{if(typeof callback!=='function')throw new Error('Status callback required');const listener=(_event,value)=>callback(value);ipcRenderer.on('desktop-access:status',listener);return()=>ipcRenderer.removeListener('desktop-access:status',listener);}
}));
