'use strict';
const {contextBridge,ipcRenderer}=require('electron');
const savedState=ipcRenderer.sendSync('desktop:state-load');
if(savedState&&savedState.enabled){
  let stateError=savedState.error;
  if(!stateError){try{for(const key of savedState.keys)localStorage.removeItem(key);for(const [key,value]of Object.entries(savedState.values))localStorage.setItem(key,value);}catch{stateError='Saved workspace preferences could not be restored.';}}
  contextBridge.exposeInMainWorld('desktopState',Object.freeze({
    keys:Object.freeze(savedState.keys||[]),
    ready:!stateError,
    commit:(operation,key,value)=>{if(stateError)throw new Error(stateError);const result=ipcRenderer.sendSync('desktop:state-write',operation,key,value);if(!result||!result.ok)throw new Error(result?.error||'Saved workspace change could not be written.');return true;}
  }));
}
contextBridge.exposeInMainWorld('desktop',Object.freeze({
  info:()=>ipcRenderer.invoke('desktop:info'),
  districts:()=>ipcRenderer.invoke('desktop:districts'),
  switchDistrict:value=>ipcRenderer.invoke('desktop:switch-district',value),
  districtData:()=>ipcRenderer.invoke('desktop:district-data'),
  refreshDistrictData:()=>ipcRenderer.invoke('desktop:district-data-refresh'),
  onDistrictData:callback=>{const listener=(_event,value)=>callback(value);ipcRenderer.on('desktop:district-data-status',listener);return()=>ipcRenderer.removeListener('desktop:district-data-status',listener);},
  rendererReady:()=>ipcRenderer.invoke('desktop:renderer-ready'),
  pair:value=>ipcRenderer.invoke('desktop:pair',value),
  launch:()=>ipcRenderer.invoke('desktop:launch'),
  onStatus:callback=>{const listener=(_event,value)=>callback(String(value));ipcRenderer.on('desktop:status',listener);return()=>ipcRenderer.removeListener('desktop:status',listener);}
}));
