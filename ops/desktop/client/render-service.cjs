'use strict';
// Private backend capability: HTML in, PNG out. Never expose this token through renderer IPC.
const http=require('node:http');
const crypto=require('node:crypto');

const LIMITS=Object.freeze({bodyBytes:40*1024*1024,minDimension:100,maxDimension:19000,maxPixels:32000000,timeoutMs:60000});
const CSP="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; frame-src 'none'; child-src 'none'; object-src 'none'; media-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'none'";
const PNG_SIGNATURE=Buffer.from([137,80,78,71,13,10,26,10]);
class RenderError extends Error{constructor(status,message){super(message);this.status=status;}}

function validate(value){
  if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join(',')!=='document,height,scale,width')throw new RenderError(400,'Invalid render request');
  const {document,width,height,scale}=value;
  if(typeof document!=='string'||!document.trim()||Buffer.byteLength(document,'utf8')>LIMITS.bodyBytes)throw new RenderError(400,'Invalid document');
  if(!Number.isInteger(width)||!Number.isInteger(height)||width<LIMITS.minDimension||height<LIMITS.minDimension||width>LIMITS.maxDimension||height>LIMITS.maxDimension||(scale!==1&&scale!==2))throw new RenderError(400,'Invalid dimensions');
  if(width*scale>LIMITS.maxDimension||height*scale>LIMITS.maxDimension||width*height*scale*scale>LIMITS.maxPixels)throw new RenderError(400,'Image exceeds render limits');
  return {document,width,height,scale};
}
function abortable(promise,signal){
  if(signal.aborted)return Promise.reject(signal.reason);
  return new Promise((resolve,reject)=>{
    const abort=()=>reject(signal.reason);
    signal.addEventListener('abort',abort,{once:true});
    Promise.resolve(promise).then(resolve,reject).finally(()=>signal.removeEventListener('abort',abort));
  });
}
function readBody(request,signal){
  return new Promise((resolve,reject)=>{
    let size=0;const chunks=[];
    const cleanup=()=>{request.off('data',data);request.off('end',end);request.off('error',error);signal.removeEventListener('abort',abort);};
    const fail=reason=>{cleanup();reject(reason);};
    const error=()=>fail(new RenderError(400,'Invalid request body'));
    const abort=()=>fail(signal.reason);
    const data=chunk=>{size+=chunk.length;if(size>LIMITS.bodyBytes)return fail(new RenderError(413,'Request too large'));chunks.push(chunk);};
    const end=()=>{cleanup();try{resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));}catch{reject(new RenderError(400,'Invalid JSON'));}};
    request.on('data',data);request.once('end',end);request.once('error',error);signal.addEventListener('abort',abort,{once:true});
    if(signal.aborted)abort();
  });
}
function deny(event){event.preventDefault();}

async function startRenderService({BrowserWindow,session,timeoutMs=LIMITS.timeoutMs}={}){
  if(!BrowserWindow||!session||!Number.isInteger(timeoutMs)||timeoutMs<1||timeoutMs>LIMITS.timeoutMs)throw new Error('Invalid render service dependencies');
  const token=crypto.randomBytes(32).toString('base64url'),credential=Buffer.from('Bearer '+token);
  const isolated=session.fromPartition('mentat-render-'+crypto.randomUUID(),{cache:false});
  isolated.setPermissionRequestHandler((_contents,_permission,callback)=>callback(false));
  isolated.setPermissionCheckHandler(()=>false);
  isolated.setDevicePermissionHandler(()=>false);
  isolated.on('will-download',deny);
  // CSP is a second boundary; the session rejects every non-data network/file request.
  isolated.webRequest.onBeforeRequest((details,callback)=>callback({cancel:!details.url.startsWith('data:')}));
  let active=null,closed=false,authority;
  async function capture(payload,signal){
    const {document,width,height,scale}=payload,physicalWidth=width*scale,physicalHeight=height*scale;
    let window;
    const destroy=()=>{if(window&&!window.isDestroyed())window.destroy();};
    signal.addEventListener('abort',destroy,{once:true});
    try{
      if(signal.aborted)throw signal.reason;
      window=new BrowserWindow({width:physicalWidth,height:physicalHeight,useContentSize:true,frame:false,show:false,focusable:false,skipTaskbar:true,paintWhenInitiallyHidden:true,backgroundColor:'#ffffff',
        webPreferences:{session:isolated,sandbox:true,contextIsolation:true,nodeIntegration:false,nodeIntegrationInWorker:false,nodeIntegrationInSubFrames:false,javascript:false,webSecurity:true,allowRunningInsecureContent:false,webviewTag:false,plugins:false,webgl:false,devTools:false,spellcheck:false,disableDialogs:true,navigateOnDragDrop:false,backgroundThrottling:false,offscreen:true,zoomFactor:scale,images:true,imageAnimationPolicy:'noAnimation',autoplayPolicy:'document-user-activation-required'}});
      const contents=window.webContents;
      for(const event of ['will-navigate','will-frame-navigate','will-redirect','will-attach-webview'])contents.on(event,deny);
      contents.setWindowOpenHandler(()=>({action:'deny'}));
      const protectedDocument='<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="'+CSP+'"><meta name="referrer" content="no-referrer"></head><body>'+document+'</body></html>';
      // loadURL resolves after did-finish-load. capturePage requests compositor pixels with
      // the hidden capturer active; page scripts remain disabled, including SVG scripts.
      await abortable(window.loadURL('data:text/html;charset=utf-8;base64,'+Buffer.from(protectedDocument).toString('base64')),signal);
      const captured=await abortable(contents.capturePage({x:0,y:0,width:physicalWidth,height:physicalHeight},{stayHidden:true,stayAwake:true}),signal);
      if(captured.isEmpty())throw new Error('Empty capture');
      // Normalize host display DPI without changing the requested output dimensions.
      const image=captured.resize({width:physicalWidth,height:physicalHeight,quality:'best'});
      const png=image.toPNG();
      if(png.length<24||png.length>LIMITS.maxPixels*4+1024*1024||!png.subarray(0,8).equals(PNG_SIGNATURE)||png.readUInt32BE(16)!==physicalWidth||png.readUInt32BE(20)!==physicalHeight)throw new Error('Invalid capture');
      return png;
    }finally{signal.removeEventListener('abort',destroy);destroy();}
  }
  const server=http.createServer({maxHeaderSize:8192,requestTimeout:timeoutMs,headersTimeout:Math.min(10000,timeoutMs)},async(request,response)=>{
    function reply(status,message,type='text/plain; charset=utf-8'){
      if(response.destroyed||response.writableEnded)return;
      response.writeHead(status,{'Content-Type':type,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Connection':'close'});response.end(message);
    }
    let controller,timer;
    const disconnected=()=>{if(!response.writableEnded)controller?.abort(new RenderError(499,'Client disconnected'));};
    try{
      if(closed)throw new RenderError(503,'Render service unavailable');
      if(request.method!=='POST'||request.url!=='/render')throw new RenderError(404,'Not found');
      const counts=new Map();for(let i=0;i<request.rawHeaders.length;i+=2){const name=request.rawHeaders[i].toLowerCase();counts.set(name,(counts.get(name)||0)+1);}
      if(request.socket.remoteAddress!=='127.0.0.1'||request.headers.host!==authority||counts.get('host')!==1||'origin' in request.headers||Object.keys(request.headers).some(name=>name.startsWith('sec-fetch-')))throw new RenderError(403,'Request refused');
      const supplied=Buffer.from(request.headers.authorization||'');
      if(counts.get('authorization')!==1||supplied.length!==credential.length||!crypto.timingSafeEqual(supplied,credential))throw new RenderError(401,'Unauthorized');
      if(counts.get('content-type')!==1||!/^application\/json(?:;\s*charset=utf-8)?$/i.test(request.headers['content-type']||'')||request.headers['content-encoding'])throw new RenderError(415,'JSON required');
      if(request.headers['content-length']&&Number(request.headers['content-length'])>LIMITS.bodyBytes)throw new RenderError(413,'Request too large');
      if(active)throw new RenderError(429,'Renderer busy');
      controller=new AbortController();active=controller;
      timer=setTimeout(()=>controller.abort(new RenderError(504,'Render timed out')),timeoutMs);timer.unref();
      request.once('aborted',disconnected);response.once('close',disconnected);
      const payload=validate(await readBody(request,controller.signal));
      const png=await capture(payload,controller.signal);
      if(controller.signal.aborted)throw controller.signal.reason;
      reply(200,png,'image/png');
    }catch(error){reply(error instanceof RenderError?error.status:500,error instanceof RenderError?error.message:'Render failed');}
    finally{
      clearTimeout(timer);request.off('aborted',disconnected);response.off('close',disconnected);
      if(controller&&active===controller)active=null;
      // No persisted partition or script storage exists. Destroyed windows release their
      // documents; clear cache opportunistically without keeping a failed request busy.
      if(controller){isolated.clearCache().catch(()=>{});isolated.clearStorageData().catch(()=>{});}
    }
  });
  server.maxConnections=4;server.maxRequestsPerSocket=1;server.keepAliveTimeout=1000;server.timeout=timeoutMs;
  server.on('timeout',socket=>socket.destroy());
  server.on('clientError',(_error,socket)=>socket.destroy());
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',()=>{server.off('error',reject);resolve();});});
  authority='127.0.0.1:'+server.address().port;
  return {url:'http://'+authority+'/render',token,close:async()=>{
    if(closed)return;closed=true;active?.abort(new RenderError(503,'Render service unavailable'));
    await new Promise(resolve=>{server.close(resolve);server.closeAllConnections();});
    await Promise.allSettled([isolated.clearCache(),isolated.clearStorageData()]);
  }};
}
module.exports={startRenderService,LIMITS};
