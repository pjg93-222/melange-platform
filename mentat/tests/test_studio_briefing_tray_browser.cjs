/* The briefing tray in a real browser: the drawer markup index.html ships,
   the order the buttons produce, and the two bundles that leave the console.
   The PowerPoint case opens the written .pptx as a ZIP and reads the slide XML,
   because a deck that downloads but carries no provenance is the failure this
   feature exists to prevent. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const os=require('node:os');
const fs=require('node:fs');
const zlib=require('node:zlib');
const modules=process.env.MENTAT_NODE_MODULES||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=require(require.resolve('playwright',{paths:[modules]}));
const app=path.join(__dirname,'../app');
const html=fs.readFileSync(path.join(app,'index.html'),'utf8');

// The fixture is the shipped markup, not a copy of it: a label that changes in
// index.html has to change here too.
const cut=(pattern,what)=>{const found=html.match(pattern);assert.ok(found,'index.html no longer contains '+what);return found[0];};
const DRAWER=cut(/<aside class="studio-briefing"[\s\S]*?<\/aside>/,'the briefing drawer');
const DIALOG=cut(/<dialog id="studio-share"[\s\S]*?<\/dialog>/,'the share dialog');
const ADD=cut(/<button id="studio-briefing-add"[\s\S]*?<\/button>/,'the Add to briefing button');
const SHA='842ba7a2c1d34f5e6789ab0cdef12345842ba7a2c1d34f5e6789ab0cdef12345';

const FIXTURE='<main id="fixture"></main>'+ADD+DRAWER+DIALOG;

function unzip(buffer){
  let eocd=-1;
  for(let i=buffer.length-22;i>=0;i--)if(buffer.readUInt32LE(i)===0x06054b50){eocd=i;break;}
  assert.ok(eocd>=0,'the .pptx has no end-of-central-directory record');
  const count=buffer.readUInt16LE(eocd+10);
  let at=buffer.readUInt32LE(eocd+16);
  const files=new Map();
  for(let n=0;n<count;n++){
    assert.equal(buffer.readUInt32LE(at),0x02014b50,'central directory entry '+n+' is malformed');
    const method=buffer.readUInt16LE(at+10),compressed=buffer.readUInt32LE(at+20);
    const nameLen=buffer.readUInt16LE(at+28),extraLen=buffer.readUInt16LE(at+30),commentLen=buffer.readUInt16LE(at+32);
    const offset=buffer.readUInt32LE(at+42);
    const name=buffer.toString('utf8',at+46,at+46+nameLen);
    const start=offset+30+buffer.readUInt16LE(offset+26)+buffer.readUInt16LE(offset+28);
    const raw=buffer.subarray(start,start+compressed);
    files.set(name,method===8?zlib.inflateRawSync(raw):Buffer.from(raw));
    at+=46+nameLen+extraLen+commentLen;
  }
  return files;
}

async function tray(t){
  const browser=await chromium.launch({headless:true,channel:process.env.MENTAT_BROWSER_CHANNEL||'chrome'});
  t.after(()=>browser.close());
  const page=await browser.newPage();
  await page.setContent(FIXTURE);
  await page.evaluate(()=>{
    window.exportBlobs=[];window.exportNames=[];
    const create=URL.createObjectURL.bind(URL);
    URL.createObjectURL=blob=>{if(blob&&blob.type!=='image/svg+xml')exportBlobs.push(blob);return create(blob);};
    HTMLAnchorElement.prototype.click=function(){exportNames.push(this.download);};
  });
  for(const script of ['chart-export.js','chart-briefing.js'])await page.addScriptTag({path:path.join(app,script)});
  await page.evaluate(sha=>{
    // chart-briefing.js:111 isoDate() stamps the filename with the LOCAL
    // calendar day, the day dateLabel() also prints in the bundle. A node-side
    // toISOString() is UTC and names tomorrow every evening, so the expectation
    // is computed here, the app's way, at the instant of the export.
    window.localDay=()=>{const p=n=>String(n).padStart(2,'0'),d=new Date();
      return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate());};
    // One drawn chart per capture, so the tray stores real SVG bytes and the
    // deck rasterizes something a reader could actually recognise.
    window.put=async (title,colour)=>{
      fixture.innerHTML='<svg xmlns="http://www.w3.org/2000/svg" width="600" height="300" viewBox="0 0 600 300">'+
        '<rect width="600" height="300" fill="#ffffff"/><rect x="20" y="20" width="120" height="180" fill="'+colour+'"/></svg>';
      await MentatBriefing.capture({svg:fixture.firstChild,title,
        scope:'Banking · Cycle 2025-2026 · Bank of America NA · workbook SHA-256 '+sha,
        chip:'Banking · Cycle 2025-2026 · Bank of America NA',
        note:'Published included receipts. All selected groups shown.',
        basis:'Published included receipts',sheetId:'s1',sheetName:'Sheet 1',
        source:'banking_2025_2026.xlsx',provenance:sha,origin:'studio'});
    };
  },SHA);
  return page;
}

const titles=page=>page.$$eval('#studio-briefing-items h4',nodes=>nodes.map(n=>n.textContent));

test('the tray orders, reorders and drops charts, and says how many it holds',{timeout:120000},async t=>{
  const page=await tray(t);
  assert.equal(await page.$eval('.studio-briefing-title',n=>n.textContent),'Briefing');
  assert.equal(await page.$eval('#studio-briefing-pptx',n=>n.textContent),'Export PowerPoint');
  assert.equal(await page.$eval('#studio-briefing-print',n=>n.textContent),'Print briefing');
  assert.equal(await page.$eval('#studio-briefing-share',n=>n.textContent),'Share bundle');
  assert.equal(await page.$eval('.studio-briefing-note',n=>n.textContent),
    'Each slide shows its scope, basis and source.');

  await page.evaluate(()=>window.put('Published included receipts by Recipient category','#342649'));
  await page.evaluate(()=>window.put('Giving by election cycle','#57447b'));
  assert.deepEqual(await titles(page),
    ['Published included receipts by Recipient category','Giving by election cycle']);
  assert.equal(await page.$eval('#studio-briefing-count',n=>n.textContent),'2 items');
  assert.equal(await page.$eval('#studio-briefing-badge',n=>n.textContent),'2');
  assert.equal(await page.$eval('#studio-briefing',n=>n.hidden),false,'adding a chart opens the drawer');
  // No server behind this fixture, so the tray has to say the charts are not
  // stored rather than implying they are.
  assert.match(await page.$eval('#studio-briefing-notice',n=>n.textContent),/stays in this tab/);

  await page.evaluate(()=>window.put('Top recipients','#327a7a'));
  const action=(index,label)=>page.locator('#studio-briefing-items .studio-briefing-card')
    .nth(index).getByRole('button',{name: label, exact: true});
  assert.deepEqual(await page.$$eval('#studio-briefing-items .studio-briefing-card-actions button',
    nodes=>nodes.slice(0,3).map(n=>n.textContent)),['Move up','Move down','Remove'],
    'the card actions are the three R4 names');
  assert.equal(await action(0,'Move up').isDisabled(),true,'the first card cannot move up');
  assert.equal(await action(2,'Move down').isDisabled(),true,'the last card cannot move down');
  await action(2,'Move up').click();
  assert.deepEqual(await titles(page),
    ['Published included receipts by Recipient category','Top recipients','Giving by election cycle'],
    'Move up walks a card exactly one place');

  await action(0,'Remove').click();
  assert.deepEqual(await titles(page),['Top recipients','Giving by election cycle']);
  assert.equal(await page.$eval('#studio-briefing-count',n=>n.textContent),'2 items');

  const bundle=await page.evaluate(async()=>{
    const text=await MentatBriefing.exportHtml();
    return {text,name:exportNames.at(-1),blob:await exportBlobs.at(-1).text(),today:localDay()};
  });
  assert.equal(bundle.name,'mentat-briefing-'+bundle.today+'.html');
  assert.equal(bundle.blob,bundle.text,'the downloaded bytes must be the bundle that was built');
  // compose() nests the chart's own viewport inside the drawing it builds, so
  // the count that matters is one root drawing per section, not one <svg> tag.
  assert.equal((bundle.text.match(/<section>/g)||[]).length,2,'one section per chart');
  assert.equal((bundle.text.match(/<\/h2><svg[\s>]/g)||[]).length,2,'one inline drawing per chart');
  assert.match(bundle.text,/Top recipients/);
  assert.match(bundle.text,/Giving by election cycle/);
  assert.ok(!bundle.text.includes('Published included receipts by Recipient category'),
    'a removed chart must not survive in the bundle');
  assert.match(bundle.text,new RegExp(SHA),'the provenance table carries the workbook hash');
  assert.match(bundle.text,/Each slide shows its scope, basis and source\./);
  assert.ok(!/<script/i.test(bundle.text),'the bundle is a document, not a program');
});

test('the PowerPoint deck carries one slide per chart with its provenance and notes',{timeout:180000},async t=>{
  const page=await tray(t);
  const candidates=[process.env.MENTAT_PPTX_BUNDLE,path.join(app,'vendor/pptxgen.bundle.js')].filter(Boolean);
  const staged=candidates.find(file=>fs.existsSync(file));
  if(!staged){t.skip('pptxgen.bundle.js is not on disk yet: looked in '+candidates.join(', '));return;}
  await page.addScriptTag({path:staged});
  assert.equal(await page.evaluate(()=>typeof window.PptxGenJS),'function',
    'the vendored bundle must register window.PptxGenJS');

  await page.evaluate(()=>window.put('Published included receipts by Recipient category','#342649'));
  await page.evaluate(()=>window.put('Giving by election cycle','#57447b'));
  const deck=await page.evaluate(async()=>{
    await MentatBriefing.exportPptx();
    const blob=exportBlobs.at(-1);
    const bytes=new Uint8Array(await blob.arrayBuffer());
    let binary='';
    for(let i=0;i<bytes.length;i+=0x8000)binary+=String.fromCharCode.apply(null,bytes.subarray(i,i+0x8000));
    return {name:exportNames.at(-1),type:blob.type,size:blob.size,base64:btoa(binary),today:localDay()};
  });
  assert.equal(deck.name,'mentat-briefing-'+deck.today+'.pptx');
  assert.ok(deck.size>10000,'a two-slide deck with two images is not a few hundred bytes');
  const files=unzip(Buffer.from(deck.base64,'base64'));
  for(const name of ['ppt/slides/slide1.xml','ppt/slides/slide2.xml','ppt/notesSlides/notesSlide1.xml'])
    assert.ok(files.has(name),'the deck is missing '+name);
  const slide1=files.get('ppt/slides/slide1.xml').toString('utf8');
  const slide2=files.get('ppt/slides/slide2.xml').toString('utf8');
  const notes1=files.get('ppt/notesSlides/notesSlide1.xml').toString('utf8');
  assert.match(slide1,/Published included receipts by Recipient category/);
  assert.match(slide2,/Giving by election cycle/);
  assert.match(slide1,new RegExp(SHA.slice(0,12)),'slide 1 must name the workbook hash');
  assert.match(slide2,new RegExp(SHA.slice(0,12)),'slide 2 must name the workbook hash');
  assert.match(slide1,/MENTAT/);
  assert.match(slide1,/Published included receipts/,'the accounting basis belongs on the slide');
  assert.match(notes1,/Published included receipts by Recipient category/);
  assert.match(notes1,new RegExp(SHA.slice(0,12)),'the speaker notes must carry the provenance');
  assert.ok(files.has('ppt/media/image1.png')||[...files.keys()].some(k=>/^ppt\/media\//.test(k)),
    'the deck must embed the rasterized charts');
});
