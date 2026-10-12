/* Local chart downloads. No external renderer or upload. */
(() => {
  'use strict';
  function download(content, type, name) {
    const url = URL.createObjectURL(content instanceof Blob ? content : new Blob([content], {type}));
    const link = document.createElement('a'); link.href = url; link.download = name; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  // A chart leaves this console in more than one shape - a downloaded file, a
  // clipboard image, a slide - and every shape needs the same drawing. compose()
  // builds that drawing, rasterize() turns it into PNG bytes, and image() stays
  // the download wrapper charts.js, graph.js and the studio already call.
  function compose(source, title, context, options) {
    const settings = options || {};
    if (!source) throw new Error('Create a chart before exporting.');
    // A drawing fitted to the screen (a Chart Studio pie, 2026-09-24) leaves
    // the console as it always has: its __mentatExport draws today's picture,
    // title, subtitle and geometry included. The on-screen node still answers
    // for the background colour, as before.
    const drawing = typeof source.__mentatExport === 'function' ? source.__mentatExport() : source;
    const ns = 'http://www.w3.org/2000/svg', box = drawing.viewBox.baseVal;
    if (![box.x,box.y,box.width,box.height].every(Number.isFinite) || box.width <= 0 || box.height <= 0) throw new Error('Nothing to export.');
    // A map's viewBox moves and shrinks while zooming. Keep that viewBox on a
    // nested, clipped viewport, and add the caption outside it. Resetting x/y to
    // zero without moving the marks exports a different geographic area.
    const absoluteWidth = /^\d+(?:\.\d+)?$/.test(drawing.getAttribute('width') || '') ? Number(drawing.getAttribute('width')) : 1000;
    const width = Math.max(320, absoluteWidth), height = width * box.height / box.width;
    const wrap = (value, size) => String(value ?? '').split(/\r?\n/).flatMap(line => line.match(new RegExp('.{1,' + size + '}(?:\\s|$)|.{1,' + size + '}', 'g')) || ['']);
    const lineWidth = Math.max(20, Math.floor((width - 32) / 7));
    // A slide carries the title, the scope and the source note in its own text
    // boxes, so a baked-in caption would print every one of them twice.
    // caption:false draws the chart alone; every existing caller keeps it.
    const caption = settings.caption !== false;
    const titles = caption ? wrap(title, lineWidth) : [], lines = caption ? wrap(context, lineWidth) : [];
    const fullHeight = caption ? Math.ceil(height + 35 + (titles.length + lines.length) * 17) : Math.ceil(height);
    const svg = document.createElementNS(ns,'svg'), viewport = drawing.cloneNode(true);
    for (const [key,value] of Object.entries({xmlns:ns,viewBox:`0 0 ${width} ${fullHeight}`,width,height:fullHeight})) svg.setAttribute(key,value);
    viewport.removeAttribute('class'); viewport.removeAttribute('style');
    for (const [key,value] of Object.entries({x:0,y:0,width,height,overflow:'hidden',viewBox:`${box.x} ${box.y} ${box.width} ${box.height}`})) viewport.setAttribute(key,value);
    const background = document.createElementNS(ns, 'rect');
    for (const [k,v] of Object.entries({width, height:fullHeight, fill:'#ffffff'})) background.setAttribute(k,v);
    svg.append(background);
    const canvasBackground = document.createElementNS(ns,'rect');
    const sourceBackground = getComputedStyle(source).backgroundColor;
    for (const [key,value] of Object.entries({width,height,fill:sourceBackground && sourceBackground !== 'rgba(0, 0, 0, 0)' ? sourceBackground : '#ffffff'})) canvasBackground.setAttribute(key,value);
    svg.append(canvasBackground,viewport);
    const theme=globalThis.MentatTheme||{ink:'#202522',sans:'Arial, sans-serif'};
    const text = (value,y,bold=false) => {
      const node = document.createElementNS(ns,'text'); node.textContent=value;
      for (const [k,v] of Object.entries({x:16,y,fill:theme.ink,'font-size':12,'font-family':theme.sans,'font-weight':bold?600:400})) node.setAttribute(k,v);
      svg.append(node);
    };
    titles.forEach((line,i)=>text(line.trim(),height+24+i*17,true));
    lines.forEach((line,i)=>text(line.trim(),height+30+(titles.length+i)*17));
    return {svg, width, height: fullHeight};
  }
  const serialize = composed => new XMLSerializer().serializeToString(composed.svg);
  async function rasterize(composed, options) {
    const settings = options || {};
    const width = composed.width, fullHeight = composed.height;
    const url=URL.createObjectURL(new Blob([serialize(composed)],{type:'image/svg+xml'}));
    try {
      const img=new Image();
      await new Promise((resolve,reject)=>{img.onload=resolve;img.onerror=()=>reject(new Error('Could not render PNG. Try SVG export.'));img.src=url;});
      // Dense multi-measure bar pages can exceed browser canvas limits. Reduce
      // raster resolution uniformly while retaining the entire chart and caption.
      const ceiling=Math.min(2,16384/width,16384/fullHeight,Math.sqrt(32000000/(width*fullHeight)));
      const asked=Number(settings.scale);
      const scale=Math.min(asked > 0 ? asked : 2, ceiling);
      const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.floor(width*scale));canvas.height=Math.max(1,Math.floor(fullHeight*scale));
      canvas.getContext('2d').drawImage(img,0,0,canvas.width,canvas.height);
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
      if(!blob)throw new Error('PNG export unavailable. Try SVG export.');
      return blob;
    } finally {URL.revokeObjectURL(url);}
  }
  async function image(source, format, title, context, name='mentat-chart') {
    const composed = compose(source, title, context);
    if (format === 'svg') {download(serialize(composed),'image/svg+xml',name+'.svg'); return;}
    if (format !== 'png') throw new Error('Choose SVG or PNG.');
    download(await rasterize(composed),'image/png',name+'.png');
  }
  // A clipboard image needs a secure context and ClipboardItem; localhost is a
  // secure context, a plain http:// host is not. Say which one is missing
  // instead of failing where nobody can see it.
  async function copy(source, title, context) {
    if (!(globalThis.isSecureContext && navigator.clipboard && typeof navigator.clipboard.write === 'function' && typeof globalThis.ClipboardItem === 'function'))
      throw new Error('Copy needs Chrome or Edge at localhost. Download the PNG instead.');
    const composed = compose(source, title, context);
    try {
      // The Promise goes to ClipboardItem unawaited: the user activation that
      // permitted this write expires while the PNG is being drawn.
      await navigator.clipboard.write([new ClipboardItem({'image/png': rasterize(composed)})]);
    } catch (error) {
      if (error && error.name === 'NotAllowedError') throw new Error('Click the chart, then copy again, or download the PNG.');
      throw error;
    }
    return {width: composed.width, height: composed.height};
  }
  globalThis.MentatExport={download,image,compose,rasterize,copy};
})();
