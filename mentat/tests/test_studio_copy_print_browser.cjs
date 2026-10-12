/* Copy image and Print chart, in the only place they can honestly be tested:
   a secure http://localhost document. The clipboard needs a secure context and
   a user-facing failure when it is missing, and the print sheet has to lose the
   whole builder and keep the provenance footer. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const os=require('node:os');
const fs=require('node:fs');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');
const html=fs.readFileSync(path.join(app,'index.html'),'utf8');

const FOOTER=(html.match(/<div id="studio-print-footer"[\s\S]*?<\/div>/)||[])[0];
assert.ok(FOOTER,'index.html no longer contains the print-only footer');
const TOGGLE=(html.match(/<button id="studio-inspector-toggle"[\s\S]*?<\/button>/)||[])[0];
assert.ok(TOGGLE,'index.html no longer contains the Inspector toggle');

const CHART='<svg xmlns="http://www.w3.org/2000/svg" width="600" height="300" viewBox="0 0 600 300">'+
  '<rect width="600" height="300" fill="#ffffff"/><rect x="20" y="20" width="120" height="180" fill="#342649"/></svg>';

// The studio subtree the print stylesheet acts on, with the shipped footer.
const PAGE='<!doctype html><html><head><meta charset="utf-8">'+
  '<link rel="stylesheet" href="/acu-theme.css"><link rel="stylesheet" href="/chart-studio.css"></head><body>'+
  '<header class="app-navigation">MENTAT</header>'+
  '<div class="studio"><div class="studio-header">Chart Studio'+TOGGLE+'</div>'+
  '<div class="studio-grid"><div class="studio-side">Data</div>'+
  '<div class="studio-view"><div class="studio-toolbar"><button>Clear</button></div>'+
  '<div class="studio-shelves">Columns</div>'+
  '<div class="studio-canvas-wrap" id="studio-canvas-wrap"><h2 id="studio-title">Published included receipts by Recipient category</h2>'+
  '<p id="studio-note" class="studio-note">Published included receipts. All selected groups shown.</p>'+
  FOOTER+
  '<div id="studio-canvas" class="studio-canvas">'+CHART+'</div></div>'+
  '<div class="studio-sheets">Sheet 1</div></div>'+
  '<div class="studio-inspector">Marks</div></div>'+
  '<div class="studio-status">4 groups</div></div></body></html>';

async function localhost(t){
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const context=await browser.newContext();
  await context.grantPermissions(['clipboard-read','clipboard-write'],{origin:'http://localhost'});
  await context.route('http://localhost/**',async route=>{
    const name=new URL(route.request().url()).pathname.replace(/^\//,'')||'fixture.html';
    if(name==='fixture.html'){await route.fulfill({contentType:'text/html; charset=utf-8',body:PAGE});return;}
    const file=path.join(app,name);
    if(!fs.existsSync(file)){await route.fulfill({status:404,body:'missing'});return;}
    const type=name.endsWith('.css')?'text/css':'text/javascript';
    await route.fulfill({contentType:type+'; charset=utf-8',body:fs.readFileSync(file,'utf8')});
  });
  const page=await context.newPage();
  await page.goto('http://localhost/fixture.html');
  await page.addScriptTag({path:path.join(app,'chart-export.js')});
  return page;
}

test('Copy image puts a captioned PNG on the clipboard at localhost',{timeout:120000},async t=>{
  const page=await localhost(t);
  assert.equal(await page.evaluate(()=>window.isSecureContext),true,'localhost must be a secure context');
  await page.bringToFront();
  await page.click('#studio-title');
  const copied=await page.evaluate(async()=>{
    const svg=document.querySelector('#studio-canvas svg');
    const size=await MentatExport.copy(svg,'Published included receipts by Recipient category',
      'Banking · Cycle 2025-2026 · workbook SHA-256 842ba7a2');
    const read=await navigator.clipboard.read();
    const types=read.flatMap(entry=>entry.types);
    const blob=await read[0].getType('image/png');
    const url=URL.createObjectURL(blob),image=new Image();
    await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=reject;image.src=url;});
    URL.revokeObjectURL(url);
    return {size,types,bytes:blob.size,width:image.naturalWidth,height:image.naturalHeight};
  });
  assert.ok(copied.types.includes('image/png'),'the clipboard entry must be a PNG: got '+copied.types.join(', '));
  assert.ok(copied.bytes>1000);
  assert.equal(copied.size.width,600,'the copy is composed at the chart width');
  assert.ok(copied.size.height>300,'the caption band must be part of what was copied');
  assert.ok(copied.width>=600&&copied.height>=copied.width*copied.size.height/copied.size.width-2,
    'the clipboard image must be the whole composed drawing');
});

test('Copy image names the missing capability instead of failing silently',{timeout:120000},async t=>{
  const page=await localhost(t);
  const message=await page.evaluate(async()=>{
    Object.defineProperty(navigator,'clipboard',{value:undefined,configurable:true});
    try{await MentatExport.copy(document.querySelector('#studio-canvas svg'),'Chart','Scope');return '';}
    catch(error){return error.message;}
  });
  assert.equal(message,'Copy needs Chrome or Edge at localhost. Download the PNG instead.');
});

test('Print chart drops the whole builder and keeps the provenance footer',{timeout:120000},async t=>{
  const page=await localhost(t);
  const onScreen=await page.evaluate(()=>getComputedStyle(document.getElementById('studio-print-footer')).display);
  assert.equal(onScreen,'none','the footer is print-only');

  await page.evaluate(()=>document.body.classList.add('studio-printing'));
  await page.emulateMedia({media:'print'});
  const printed=await page.evaluate(()=>{
    const style=selector=>getComputedStyle(document.querySelector(selector));
    return {
      side:style('.studio-side').display,
      toolbar:style('.studio-toolbar').display,
      inspector:style('.studio-inspector').display,
      shelves:style('.studio-shelves').display,
      sheets:style('.studio-sheets').display,
      status:style('.studio-status').display,
      nav:style('.app-navigation').display,
      note:style('#studio-note').display,
      footer:style('#studio-print-footer').display,
      title:style('#studio-title').display,
      svgMaxHeight:style('#studio-canvas svg').maxHeight,
      wrapOverflow:style('.studio-canvas-wrap').overflowY
    };
  });
  for(const key of ['side','toolbar','inspector','shelves','sheets','status','nav','note'])
    assert.equal(printed[key],'none','the print sheet must not carry '+key);
  assert.equal(printed.footer,'block','the provenance footer must print');
  assert.notEqual(printed.title,'none','the chart keeps its title on paper');
  assert.equal(printed.svgMaxHeight,'none','a capped chart height would crop the printed chart');
  assert.equal(printed.wrapOverflow,'visible','a scrolling canvas prints only its first screen');

  let pdf=null;
  try{pdf=await page.pdf({landscape:true});}catch(error){pdf=error;}
  if(Buffer.isBuffer(pdf))assert.ok(pdf.length>1000,'the printed sheet is not an empty PDF');
  else assert.ok(/pdf/i.test(String(pdf&&pdf.message)),'page.pdf failed for an unexpected reason: '+pdf);
  await page.emulateMedia({media:null});
});

test('Canvas only keeps the Inspector button and its filter count at desktop width',{timeout:120000},async t=>{
  const page=await localhost(t);
  await page.setViewportSize({width:1440,height:900});
  const builder=await page.evaluate(()=>{
    const toggle=document.getElementById('studio-inspector-toggle');
    return {toggle:getComputedStyle(toggle).display,inspector:getComputedStyle(document.querySelector('.studio-inspector')).display};
  });
  assert.equal(builder.toggle,'none','a wide builder shows the Inspector itself, not a button for it');
  assert.notEqual(builder.inspector,'none');

  const canvasOnly=await page.evaluate(()=>{
    document.querySelector('.studio').classList.add('studio-options-hidden');
    const toggle=document.getElementById('studio-inspector-toggle');
    const badge=document.getElementById('studio-inspector-count');
    badge.hidden=false;badge.textContent='2';
    toggle.setAttribute('aria-expanded','false');
    const inspector=document.querySelector('.studio-inspector');
    inspector.hidden=true;
    const closed=getComputedStyle(inspector).display;
    inspector.hidden=false;
    const opened=getComputedStyle(inspector);
    return {toggle:getComputedStyle(toggle).display,border:getComputedStyle(toggle).borderTopColor,
      badge:getComputedStyle(badge).display,closed,opened:opened.display,position:opened.position,
      side:getComputedStyle(document.querySelector('.studio-side')).display,
      shelves:getComputedStyle(document.querySelector('.studio-shelves')).display};
  });
  assert.notEqual(canvasOnly.toggle,'none','render R2 puts the Inspector button in the Canvas only header');
  assert.notEqual(canvasOnly.badge,'none','the filter count rides with it');
  assert.equal(canvasOnly.border,'rgb(236, 169, 28)','a closed Inspector with filters behind it wears the gold border');
  assert.equal(canvasOnly.closed,'none','Canvas only starts with the Inspector closed');
  assert.equal(canvasOnly.opened,'flex','the button has to be able to open it');
  assert.equal(canvasOnly.position,'fixed','it opens as a drawer over the chart, not as a column');
  assert.equal(canvasOnly.side,'none','Canvas only still puts the builder away');
  assert.equal(canvasOnly.shelves,'none');
});
