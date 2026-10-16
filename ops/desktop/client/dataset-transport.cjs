'use strict';
const crypto=require('node:crypto'),https=require('node:https'),tls=require('node:tls');
const {descriptor}=require('./access-client.cjs');const {strictJson,CHUNK}=require('./dataset-protocol.cjs');
function route(relative,method){
 const uuid='[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',number='(?:0|[1-9][0-9]{0,15})';
 if(method==='GET'&&new RegExp('^/v1/data/catalog\\?after='+number+'&limit=(?:[1-9]|[1-9][0-9]|100)(?:&districtId=(?:co-05|ct-01|or-05))?$').test(relative))return {limit:9*1024*1024,json:true};
 if(method==='GET'&&new RegExp('^/v1/data/objects/[a-f0-9]{64}\\?offset='+number+'&length=([1-9][0-9]{0,6})&revisionId='+uuid+'$').test(relative)){const length=Number(new URL(relative,'https://127.0.0.1').searchParams.get('length'));if(length>CHUNK)throw new Error('Dataset range too large');return {limit:length,binary:true};}
 if(method==='GET'&&new RegExp('^/v1/data/revisions/'+uuid+'$').test(relative)||method==='POST'&&relative==='/v1/data/revisions')return {limit:100000,json:true};
 if(method==='POST'&&relative==='/v1/data/uploads'||method==='GET'&&new RegExp('^/v1/data/uploads/'+uuid+'$').test(relative)||method==='PUT'&&new RegExp('^/v1/data/uploads/'+uuid+'\\?offset='+number+'$').test(relative)||method==='POST'&&new RegExp('^/v1/data/uploads/'+uuid+'/complete$').test(relative))return {limit:16384,json:true,put:method==='PUT',complete:relative.endsWith('/complete')};
 throw new Error('Unsupported dataset operation');
}
function datasetRequest(trustValue,relative,{method='GET',token,body,bytes,signal,deadlineMs=15000}={}){
 const trusted=descriptor(trustValue),limits=route(relative,method),origin=new URL(trusted.url);
 if(typeof token!=='string'||!/^[A-Za-z0-9_-]{64}$/.test(token)||Buffer.from(token,'base64url').length!==48)throw new Error('Dataset access credential required');
 if(!Number.isInteger(deadlineMs)||deadlineMs<50||deadlineMs>15000)throw new Error('Invalid dataset deadline');
 if(signal?.aborted)return Promise.reject(new Error('Dataset transfer canceled'));
 const data=limits.put?bytes:body===undefined?null:Buffer.from(JSON.stringify(body));
 if(limits.put&&(!Buffer.isBuffer(data)||data.length<1||data.length>CHUNK)||!limits.put&&(bytes!==undefined||data&&data.length>65536)||method==='GET'&&data!==null)throw new Error('Invalid dataset request size');
 return new Promise((resolve,reject)=>{
  const agent=new https.Agent({keepAlive:false});let request,response,socket,settled=false;
  const abort=()=>finish(new Error('Dataset transfer canceled'));
  const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);signal?.removeEventListener('abort',abort);response?.destroy();request?.destroy();socket?.destroy();agent.destroy();error?reject(error):resolve(value);};
  const timer=setTimeout(()=>finish(new Error('Dataset request deadline exceeded')),deadlineMs);signal?.addEventListener('abort',abort,{once:true});
  agent.createConnection=(_options,callback)=>{let completed=false;const done=(error,connection)=>{if(!completed){completed=true;callback(error,connection);}};
   socket=tls.connect({host:origin.hostname,port:Number(origin.port)||443,rejectUnauthorized:false});socket.once('error',()=>{done(new Error('Dataset connection unavailable'));finish(new Error('Dataset connection unavailable'));});
   socket.once('secureConnect',()=>{try{const cert=new crypto.X509Certificate(socket.getPeerCertificate(true).raw),pin=crypto.createHash('sha256').update(cert.publicKey.export({format:'der',type:'spki'})).digest();if(Date.now()<Date.parse(cert.validFrom)||Date.now()>Date.parse(cert.validTo)||!crypto.timingSafeEqual(pin,Buffer.from(trusted.tlsSpkiSha256,'hex')))throw new Error();done(null,socket);}catch{done(new Error('Dataset server identity mismatch'));finish(new Error('Dataset server identity mismatch'));}});
  };
  const headers={Authorization:'Bearer '+token,Accept:limits.binary?'application/octet-stream':'application/json',...(data?{'Content-Type':limits.put?'application/octet-stream':'application/json','Content-Length':data.length}:{})};
  request=https.request(new URL(relative,origin),{method,headers,agent},incoming=>{
   response=incoming;
   if(![200,201,...(limits.complete?[202]:[])].includes(response.statusCode)){const error=new Error('Dataset service refused request');error.statusCode=response.statusCode;finish(error);return;}
   const lengthText=response.headers['content-length'],length=Number(lengthText),type=response.headers['content-type'];
   if(typeof lengthText!=='string'||!/^(0|[1-9][0-9]*)$/.test(lengthText)||!Number.isSafeInteger(length)||length>limits.limit||response.headers['transfer-encoding']||limits.binary&&(length!==limits.limit||type!=='application/octet-stream')||!limits.binary&&!(typeof type==='string'&&/^application\/json(?:;\s*charset=utf-8)?$/i.test(type))){finish(new Error('Invalid dataset response framing'));return;}
   let received=0,ended=false;const chunks=[];
   response.on('data',part=>{received+=part.length;if(received>length)finish(new Error('Dataset response too large'));else chunks.push(part);});
   response.once('aborted',()=>finish(new Error('Dataset response interrupted')));response.once('error',()=>finish(new Error('Dataset response unavailable')));response.once('close',()=>{if(!ended)finish(new Error('Dataset response interrupted'));});
   response.once('end',()=>{ended=true;try{if(received!==length)throw new Error();const all=Buffer.concat(chunks);finish(null,limits.binary?all:strictJson(all));}catch{finish(new Error('Invalid dataset response'));}});
  });
  request.once('error',()=>finish(new Error('Dataset connection unavailable')));request.end(data);
 });
}
module.exports={datasetRequest,route};
