'use strict';
// Shows the main process's progress text (district download, retries, a stopped download) under the loading animation.
const detail=document.getElementById('detail');
window.desktop?.onStatus(text=>{detail.textContent=text;});
