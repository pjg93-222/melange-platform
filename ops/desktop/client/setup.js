'use strict';
const byId=id=>document.getElementById(id);
const status=message=>{byId('status').textContent=message;};
async function action(fn){try{await fn();}catch(error){status(error.message);}}
async function refresh(){const info=await window.desktop.info();document.title=info.name+' settings';byId('name').textContent=info.name;if(['melange','terrier','mentat'].includes(info.appId)){byId('app-logo').src='icons/'+info.appId+'.ico';byId('app-logo').hidden=false;document.body.dataset.app=info.appId;}byId('pairing').hidden=info.updateProvider==='github';byId('pair').hidden=info.updateProvider==='github';byId('paired').textContent=info.updateProvider==='github'?(info.paired?'GitHub update receiver is ready for approved releases.':'GitHub update receiver is being initialized.'):(info.paired?'Paired with administrator.':'Not paired.');status(info.status);}
byId('launch').addEventListener('click',()=>action(()=>window.desktop.launch()));
byId('pair').addEventListener('click',()=>action(async()=>{await window.desktop.pair(byId('pairing').value);byId('pairing').value='';await refresh();}));
window.desktop.onStatus(status);action(refresh);
