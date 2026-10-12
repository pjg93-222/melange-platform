/* The briefing tray: the charts a reader has decided to keep, in the order
   they will be told in, and the three ways they leave - a printed sheet, a
   self-contained HTML bundle, a PowerPoint deck. Every one of them carries the
   chart's scope, accounting basis and workbook SHA-256, because a chart that
   travels without them is a number nobody can check.

   The tray is stored on the console so a second console sees the same one. A
   console that cannot store it (a read-only preview, or a build whose server
   has no briefing endpoint) keeps it in this tab and says so, rather than
   pretending the charts were saved. */
(function (root) {
  'use strict';

  /* ------------------------------------------------------------- the model */

  // Forty charts is already a long meeting; past that the tray stops taking
  // them rather than silently dropping the oldest.
  const CAP = 40;

  function addItem(items, item) {
    if (items.length >= CAP) {
      throw new Error('Briefing is full (' + CAP + ' charts). Remove one first.');
    }
    return items.concat([item]);
  }
  function moveItem(items, id, delta) {
    const at = items.findIndex(item => item.id === id);
    const to = at + Number(delta);
    if (at < 0 || to < 0 || to >= items.length) return items;
    const next = items.slice();
    next.splice(to, 0, next.splice(at, 1)[0]);
    return next;
  }
  function removeItem(items, id) {
    return items.filter(item => item.id !== id);
  }
  // A briefing item's provenance and source are objects on the server, which
  // replaces anything else with an empty one. Older items, and items added by
  // a console that could not reach the server, may still carry a bare string.
  // One reader for both shapes, and neither of them ever stringifies an
  // object: a slide that prints "[object Obje" where the workbook digest
  // belongs is worse than one that admits the digest is unstated.
  function provenanceSha(item) {
    const value = item && item.provenance;
    if (typeof value === 'string') return value;
    if (!value || typeof value !== 'object') return '';
    for (const key of ['sha', 'sha256', 'workbook_sha256', 'digest']) {
      if (typeof value[key] === 'string' && value[key]) return value[key];
    }
    return '';
  }
  function sourceText(item) {
    const value = item && item.source;
    if (typeof value === 'string') return value;
    if (!value || typeof value !== 'object') return '';
    for (const key of ['label', 'datasetLabel', 'reportName']) {
      if (typeof value[key] === 'string' && value[key]) return value[key];
    }
    return '';
  }
  // Going the other way: what this console sends the server has to be an
  // object, so a caption is wrapped rather than dropped on the floor.
  const sourceRecord = value => typeof value === 'string'
    ? (value ? {label: value} : {})
    : (value && typeof value === 'object' ? value : {});
  const provenanceRecord = value => typeof value === 'string'
    ? (value ? {sha: value} : {})
    : (value && typeof value === 'object' ? value : {});

  // The one line every export carries - printed sheet, HTML bundle and slide
  // notes alike - saying what the chart was drawn from, its workbook digest,
  // its accounting basis and when it left. Pure, and up here with the model,
  // so a test can read the exact words without standing up a document.
  const sourceNoteText = (item, exportedOn) => 'Source: ' + (sourceText(item) || 'workbook') +
    ' · SHA-256 ' + (provenanceSha(item).slice(0, 12) || 'unstated') +
    ' · basis ' + ((item && item.basis) || 'unstated') +
    ' · exported ' + (exportedOn || '') + ' from Mentat Chart Studio';

  const pad = n => String(n).padStart(2, '0');
  // added_at (like saved_at in chart-studio.js) comes back from the server
  // in seconds; a bare Date() reads it as milliseconds and prints a 1970
  // clock. Pure, and up here with the model, so the Node test below can
  // catch a regression without standing up a document.
  const timeLabel = value => {
    const date = value
      ? new Date(typeof value === 'number' ? value * 1000 : value)
      : new Date();
    return Number.isNaN(date.getTime()) ? '' : pad(date.getHours()) + ':' + pad(date.getMinutes());
  };

  // Slide geometry (inches) around the scope line. One line, as before, leaves the picture its 5.0; a long
  // line (a Coverage gap caption names every short cell in scope, so it can run to a dozen) takes the extra
  // room from the picture and never from the source note below it.
  const SCOPE_LINE = 165;
  const slideLayout = text => {
    const lines = Math.max(1, Math.ceil(String(text || '').length / SCOPE_LINE));
    const extra = Math.min(0.7, Math.max(0, 0.15 * lines - 0.35));
    return {pictureH: 5.0 - extra, ruleY: 6.15 - extra, scopeY: 6.3 - extra, scopeH: 0.35 + extra};
  };

  // The box above is sized for 9 pt text at SCOPE_LINE characters a line, and it stops growing at 0.7 in. A
  // caption that still does not fit it (the Coverage gap caption of an all-company view runs past a thousand
  // characters) steps the face down to a 7 pt floor and, past that, is cut on the slide with a pointer to the
  // speaker notes, which carry the whole caption (speakerNotes). So it never runs on into the source note.
  const SCOPE_PT = 9, SCOPE_MIN_PT = 7, SCOPE_CUT = '… (full coverage note in the speaker notes)';
  const slideScope = (text, layout) => {
    const flat = String(text || '').replace(/\s*\n\s*/g, ' ').trim();
    // A line of the box holds proportionally more characters in a smaller face; a line is 1.2 times the face tall.
    const capacity = pt => Math.floor(SCOPE_LINE * SCOPE_PT / pt) * Math.max(1, Math.floor(layout.scopeH / (pt * 1.2 / 72) + 1e-9));
    for (let pt = SCOPE_PT; pt >= SCOPE_MIN_PT; pt -= 0.5) {
      if (flat.length <= capacity(pt)) return {fontSize: pt, text: flat, truncated: false};
    }
    const room = capacity(SCOPE_MIN_PT) - SCOPE_CUT.length, head = flat.slice(0, room), space = head.lastIndexOf(' ');
    return {fontSize: SCOPE_MIN_PT, text: (space > room / 2 ? head.slice(0, space) : head).trimEnd() + SCOPE_CUT, truncated: true};
  };

  const model = {CAP, add: addItem, move: moveItem, remove: removeItem, slideLayout, slideScope, SCOPE_CUT,
    provenanceSha, sourceText, sourceRecord, provenanceRecord, sourceNoteText,
    shortSha: item => provenanceSha(item).slice(0, 12), pad, timeLabel};

  // Node requires this file for the model alone; everything below needs a
  // document and must not run there.
  if (typeof module !== 'undefined' && module.exports) { module.exports = model; return; }

  /* ------------------------------------------------------------ the console */

  const el = id => document.getElementById('studio-' + id);
  const PPTX_TYPE = 'application/vnd.openxmlformats-officedocument.presentationml.presentation';
  const previewReadOnly = () => { try { return !!readOnly; } catch { return false; } };
  const studio = () => root.MentatStudioPane || null;

  let items = [];
  let notice = '';
  let busy = false;
  const svgCache = new Map();

  const isoDate = (date = new Date()) =>
    date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate());
  const dateLabel = (date = new Date()) =>
    date.toLocaleDateString('en-US', {year: 'numeric', month: 'long', day: 'numeric'});
  const escapeHtml = value => String(value == null ? '' : value)
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');

  // What to say when the console will not keep something. The reason matters:
  // a read-only preview is working as designed, a missing endpoint is a console
  // that has not been restarted, and neither is a lost chart.
  function problem(error) {
    if (previewReadOnly()) return 'This preview console is read-only.';
    const text = String((error && error.message) || '');
    if (!text || /JSON|Unexpected token|Failed to fetch|NetworkError|<!DOCTYPE|Server unavailable/i.test(text)) {
      return 'Server unavailable.';
    }
    return text;
  }
  // The tray keeps working when the console will not store it, so every refusal
  // has to end by saying where the charts actually are.
  function refusal(error) {
    if (previewReadOnly()) return 'Read-only preview. This briefing stays in this tab.';
    const text = problem(error);
    return text === 'Server unavailable.'
      ? 'Server unavailable. This briefing stays in this tab.'
      : text + ' This briefing stays in this tab.';
  }
  async function getJSON(path) {
    const response = await fetch(path, {credentials: 'same-origin'});
    let body = null;
    try { body = await response.json(); } catch { body = null; }
    if (!response.ok) throw new Error((body && body.error) || 'Server unavailable (' + response.status + ').');
    return body || {};
  }
  function post(path, body) {
    if (typeof api !== 'function') return Promise.reject(new Error('Server unavailable.'));
    return api(path, body);
  }
  function setNotice(text) {
    notice = text || '';
    const node = el('briefing-notice');
    if (!node) return;
    node.textContent = notice;
    node.hidden = !notice;
  }

  /* ------------------------------------------------------------- drawings */

  // A stored drawing is a picture, never a script. The tray re-reads whatever
  // is sitting in the data root, so anything executable comes out before the
  // node ever reaches the page.
  function svgNode(text) {
    const parsed = new DOMParser().parseFromString(String(text || ''), 'image/svg+xml');
    const source = parsed.documentElement;
    if (!source || source.nodeName.toLowerCase() !== 'svg' || source.querySelector('parsererror')) return null;
    const node = document.importNode(source, true);
    for (const child of node.querySelectorAll('script, foreignObject, iframe, image')) child.remove();
    for (const element of [node, ...node.querySelectorAll('*')]) {
      for (const attribute of [...element.attributes]) {
        const name = attribute.name.toLowerCase();
        const value = String(attribute.value || '');
        if (name.startsWith('on')) element.removeAttribute(attribute.name);
        else if (/(^|:)href$/.test(name) && /^\s*(javascript|data):/i.test(value)) element.removeAttribute(attribute.name);
      }
    }
    return node;
  }
  async function svgFor(item) {
    if (item.svg) return item.svg;
    if (svgCache.has(item.id)) return svgCache.get(item.id);
    const response = await fetch('/api/studio/briefing/item?id=' + encodeURIComponent(item.id),
      {credentials: 'same-origin'});
    if (!response.ok) throw new Error('Chart no longer on this console.');
    const text = await response.text();
    svgCache.set(item.id, text);
    return text;
  }
  async function nodeFor(item) {
    const node = svgNode(await svgFor(item));
    if (!node) throw new Error('Chart could not be read.');
    return node;
  }

  /* --------------------------------------------------------------- capture */

  // One chart, described once, in the shape every export reads. The caption is
  // left off the drawing on purpose: a slide and a bundle print the title,
  // scope and source themselves, and a baked-in caption would say it twice.
  function describe(chart) {
    if (!chart || !chart.svg) throw new Error('Build a chart before adding it to the briefing.');
    if (!root.MentatExport) throw new Error('Export is not available. Reload the page.');
    const composed = root.MentatExport.compose(chart.svg, chart.title, chart.scope, {caption: false});
    const text = new XMLSerializer().serializeToString(composed.svg);
    if (text.length > 6 * 1024 * 1024) throw new Error('Narrow the view before adding it');
    return {
      title: String(chart.title || 'Chart').slice(0, 300),
      scope: String(chart.scope || ''),
      chip: String(chart.chip || ''),
      note: String(chart.note || ''),
      basis: String(chart.basis || ''),
      sheetId: String(chart.sheetId || ''),
      sheetName: String(chart.sheetName || ''),
      source: sourceRecord(chart.source),
      provenance: provenanceRecord(chart.provenance),
      width: composed.width,
      height: composed.height,
      origin: String(chart.origin || 'studio'),
      svg: text
    };
  }
  async function capture(chart) {
    try { return await add(describe(chart)); }
    catch (error) { setNotice(error.message); openDrawer(); return null; }
  }
  async function add(record) {
    if (items.length >= CAP) {
      setNotice('Briefing is full (' + CAP + ' charts). Remove one first.');
      openDrawer();
      return null;
    }
    const item = Object.assign({}, record);
    try {
      const body = await post('/api/studio/briefing/add', item);
      item.id = body.id;
      item.added_at = body.added_at || new Date().toISOString();
      setNotice('');
    } catch (error) {
      item.id = 'tab-' + Date.now() + '-' + Math.random().toString(16).slice(2, 8);
      item.added_at = new Date().toISOString();
      setNotice(refusal(error));
    }
    svgCache.set(item.id, item.svg);
    items = addItem(items, item);
    render();
    openDrawer();
    return item;
  }
  async function move(id, delta) {
    const next = moveItem(items, id, delta);
    if (next === items) return;
    items = next;
    render();
    try { await post('/api/studio/briefing/order', {ids: items.map(item => item.id)}); setNotice(''); }
    catch (error) { setNotice(refusal(error)); }
  }
  async function remove(id) {
    items = removeItem(items, id);
    svgCache.delete(id);
    render();
    try { await post('/api/studio/briefing/remove', {id}); setNotice(''); }
    catch (error) { setNotice(refusal(error)); }
  }
  const list = () => items.map(item => Object.assign({}, item));

  async function load() {
    try {
      const body = await getJSON('/api/studio/briefing');
      items = (body.items || []).slice(0, CAP);
      setNotice('');
    } catch (error) {
      setNotice(refusal(error));
    }
    render();
  }

  /* ------------------------------------------------------------ the drawer */

  // The drawer is fixed over the right edge of the window. The page has to be
  // told it is there, or it sits on top of the studio toolbar's right-hand
  // controls and swallows their clicks; the class is what the stylesheet reads
  // to reserve the gutter.
  const markOpen = open => {
    if (document.body) document.body.classList.toggle('studio-briefing-open', !!open);
  };
  const openDrawer = () => {
    const drawer = el('briefing');
    if (drawer) drawer.hidden = false;
    markOpen(true);
    const button = el('briefing-add');
    if (button) button.setAttribute('aria-expanded', 'true');
  };
  const closeDrawer = () => {
    const drawer = el('briefing');
    if (drawer) drawer.hidden = true;
    markOpen(false);
    const button = el('briefing-add');
    if (button) { button.setAttribute('aria-expanded', 'false'); button.focus(); }
  };

  function paintThumb(host, item) {
    nodeFor(item).then(node => {
      node.removeAttribute('width');
      node.removeAttribute('height');
      node.setAttribute('preserveAspectRatio', 'xMidYMid meet');
      host.replaceChildren(node);
    }).catch(() => { host.textContent = 'No image'; });
  }

  function card(item, index) {
    const node = document.createElement('article');
    node.className = 'studio-briefing-card';
    node.dataset.id = item.id;

    const thumb = document.createElement('div');
    thumb.className = 'studio-briefing-thumb';
    thumb.setAttribute('aria-hidden', 'true');
    paintThumb(thumb, item);

    const body = document.createElement('div');
    const title = document.createElement('h4');
    title.textContent = item.title || 'Chart';
    const scope = document.createElement('p');
    scope.className = 'studio-briefing-line';
    scope.textContent = item.chip || item.scope || '';
    const meta = document.createElement('p');
    meta.className = 'studio-briefing-line';
    meta.textContent = [item.basis, item.added_at ? 'added ' + timeLabel(item.added_at) : '']
      .filter(Boolean).join(' · ');

    const actions = document.createElement('div');
    actions.className = 'studio-briefing-card-actions';
    for (const [label, run, disabled] of [
      ['Move up', () => move(item.id, -1), index === 0],
      ['Move down', () => move(item.id, 1), index === items.length - 1],
      ['Remove', () => remove(item.id), false]
    ]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.disabled = !!disabled;
      button.onclick = run;
      actions.append(button);
    }
    body.append(title, scope, meta, actions);
    node.append(thumb, body);
    return node;
  }

  function render() {
    const count = items.length;
    const chip = el('briefing-count');
    if (chip) chip.textContent = count + ' item' + (count === 1 ? '' : 's');
    const badge = el('briefing-badge');
    if (badge) { badge.textContent = count ? String(count) : ''; badge.hidden = !count; }
    const host = el('briefing-items');
    if (host) {
      if (count) host.replaceChildren(...items.map(card));
      else {
        const empty = document.createElement('p');
        empty.className = 'studio-briefing-empty';
        empty.textContent = 'Nothing in the briefing yet. Use "Add to briefing" on a chart.';
        host.replaceChildren(empty);
      }
    }
    for (const id of ['briefing-pptx', 'briefing-print', 'briefing-share']) {
      const button = el(id);
      if (button) button.disabled = !count || busy;
    }
    const bundle = el('briefing-html');
    if (bundle) bundle.disabled = !count || busy;
  }

  async function guard(label, run) {
    if (busy) return;
    busy = true;
    render();
    try { await run(); }
    catch (error) { setNotice(label + ': ' + ((error && error.message) || 'that did not finish.')); }
    finally { busy = false; render(); }
  }

  /* --------------------------------------------------------------- exports */

  const sourceNote = item => sourceNoteText(item, dateLabel());
  const consoleUrl = () => location.origin + location.pathname;
  const speakerNotes = item => [
    item.title || 'Chart',
    item.chip || '',
    item.scope || '',
    item.note || '',
    sourceNote(item),
    'Console ' + consoleUrl()
  ].filter(Boolean).join('\n');

  // Print briefing: one page per chart on the same sheet E2 prints, so a
  // printed briefing and a printed chart say the same things in the same order.
  async function exportPrint() {
    const host = document.createElement('div');
    host.className = 'studio-briefing-print-host';
    host.id = 'studio-briefing-print-host';
    for (const item of items) {
      const sheet = document.createElement('section');
      sheet.className = 'studio-briefing-sheet';
      const heading = document.createElement('h2');
      heading.textContent = item.title || 'Chart';
      sheet.append(heading);
      try {
        const node = await nodeFor(item);
        node.removeAttribute('width');
        node.removeAttribute('height');
        sheet.append(node);
      } catch {
        const missing = document.createElement('p');
        missing.textContent = 'Chart no longer on this console.';
        sheet.append(missing);
      }
      const rule = document.createElement('hr');
      rule.className = 'studio-briefing-rule';
      sheet.append(rule);
      for (const line of [item.chip || item.scope, item.note, sourceNote(item)].filter(Boolean)) {
        const text = document.createElement('p');
        text.textContent = line;
        sheet.append(text);
      }
      const mark = document.createElement('p');
      mark.className = 'studio-briefing-sheet-mark';
      mark.textContent = 'MENTAT';
      sheet.append(mark);
      host.append(sheet);
    }
    document.body.append(host);
    document.body.classList.add('studio-briefing-printing');
    const done = () => {
      document.body.classList.remove('studio-briefing-printing');
      host.remove();
      window.removeEventListener('afterprint', done);
      clearTimeout(timer);
    };
    const timer = setTimeout(done, 60000);
    window.addEventListener('afterprint', done);
    window.print();
  }

  const BUNDLE_CSS = [
    'body{margin:0;padding:28px 32px 40px;background:#fcf7f4;color:#342649;',
    'font:14px/1.5 "Articulat CF",Arial,sans-serif}',
    'h1,h2,h3{font-family:"Freight Text Pro",Georgia,serif;font-weight:400;color:#342649}',
    'h1{font-size:30px;margin:0 0 4px}h2{font-size:21px;margin:0 0 6px}',
    '.lead{color:#716a64;font-size:12px;margin:0 0 24px}',
    'section{background:#fff;border:1px solid #e4d9d0;border-radius:6px;padding:18px 20px;margin:0 0 18px}',
    'section svg{width:100%;height:auto;display:block;margin:6px 0 10px}',
    'section p{font-size:11px;line-height:1.5;color:#716a64;margin:0 0 3px}',
    'hr{border:0;border-top:1px solid #eca91c;margin:10px 0 8px}',
    '.mark{font-size:10px;font-weight:700;letter-spacing:.09em;color:#342649;margin-top:6px}',
    'table{border-collapse:collapse;width:100%;font-size:11px;background:#fff}',
    'th,td{border:1px solid #e4d9d0;padding:6px 8px;text-align:left;vertical-align:top;word-break:break-word}',
    'th{background:#f7ede4;font-weight:600}',
    '@media print{body{background:#fff;padding:0}section{page-break-after:always;border:0;padding:0}',
    'section:last-of-type{page-break-after:auto}@page{size:landscape;margin:14mm}}'
  ].join('');

  // HTML bundle: one file, no network, every drawing inline and a provenance
  // table under them, so the bundle can be read on a machine that has never
  // heard of this console.
  async function exportHtml() {
    const drawn = [];
    for (const item of items) {
      let markup = '<p>Chart no longer on this console.</p>';
      try { markup = new XMLSerializer().serializeToString(await nodeFor(item)); } catch { /* keep the note */ }
      drawn.push(
        '<section>' +
        '<h2>' + escapeHtml(item.title || 'Chart') + '</h2>' +
        markup +
        '<hr>' +
        [item.chip || item.scope, item.note, sourceNote(item)].filter(Boolean)
          .map(line => '<p>' + escapeHtml(line) + '</p>').join('') +
        '<p class="mark">MENTAT</p>' +
        '</section>');
    }
    const rows = items.map((item, index) =>
      '<tr><td>' + (index + 1) + '</td><td>' + escapeHtml(item.title || 'Chart') + '</td>' +
      '<td>' + escapeHtml(item.chip || item.scope || '') + '</td>' +
      '<td>' + escapeHtml(item.basis || '') + '</td>' +
      '<td>' + escapeHtml(sourceText(item)) + '</td>' +
      '<td>' + escapeHtml(provenanceSha(item)) + '</td></tr>').join('');
    const html = [
      '<!doctype html>',
      '<html lang="en"><head><meta charset="utf-8">',
      '<meta name="viewport" content="width=device-width, initial-scale=1">',
      '<title>Mentat briefing · ' + escapeHtml(dateLabel()) + '</title>',
      '<style>' + BUNDLE_CSS + '</style></head><body>',
      '<h1>Briefing</h1>',
      '<p class="lead">' + items.length + ' chart' + (items.length === 1 ? '' : 's') +
        ' · exported ' + escapeHtml(dateLabel()) + ' from Mentat Chart Studio · ' +
        escapeHtml(consoleUrl()) + '</p>',
      drawn.join(''),
      '<h2>Provenance</h2>',
      '<table><thead><tr><th>#</th><th>Chart</th><th>Scope</th><th>Basis</th>' +
        '<th>Workbook</th><th>Workbook SHA-256</th></tr></thead><tbody>' + rows + '</tbody></table>',
      '<p class="lead">Each slide shows its scope, basis and source.</p>',
      '</body></html>'
    ].join('\n');
    root.MentatExport.download(html, 'text/html;charset=utf-8', 'mentat-briefing-' + isoDate() + '.html');
    return html;
  }

  /* ------------------------------------------------------------ PowerPoint */

  let pptxLoad = null;
  // Same-origin script tag, loaded the first time a deck is asked for: the
  // bundle is 450 KB and most sessions never export one.
  function loadPptx() {
    if (root.PptxGenJS) return Promise.resolve(root.PptxGenJS);
    if (!pptxLoad) {
      pptxLoad = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = '/vendor/pptxgen.bundle.js';
        script.onload = () => root.PptxGenJS
          ? resolve(root.PptxGenJS)
          : reject(new Error('PowerPoint export failed to load. Reload and try again.'));
        script.onerror = () => {
          pptxLoad = null;
          reject(new Error('PowerPoint export needs a console restart.'));
        };
        document.head.append(script);
      });
    }
    return pptxLoad;
  }
  async function pngFor(item) {
    const node = await nodeFor(item);
    const blob = await root.MentatExport.rasterize(
      {svg: node, width: item.width || 1000, height: item.height || 600}, {scale: 1.5});
    return await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(new Error('Chart image could not be read.'));
      reader.readAsDataURL(blob);
    });
  }
  async function exportPptx() {
    const Deck = await loadPptx();
    const deck = new Deck();
    deck.layout = 'LAYOUT_WIDE';
    for (const item of items) {
      const slide = deck.addSlide();
      const scopeLine = item.chip || item.scope || '', fit = slideLayout(scopeLine);
      const scope = slideScope(scopeLine, fit);
      slide.addText(item.title || 'Chart',
        {x: 0.5, y: 0.35, w: 12.3, h: 0.7, fontSize: 22, fontFace: 'Georgia', color: '342649', valign: 'top'});
      try {
        const png = await pngFor(item);
        slide.addImage({data: png, x: 0.5, y: 1.15, w: 12.3, h: fit.pictureH,
          sizing: {type: 'contain', w: 12.3, h: fit.pictureH}});
      } catch {
        slide.addText('Chart no longer on this console.',
          {x: 0.5, y: 1.15, w: 12.3, h: 0.5, fontSize: 12, fontFace: 'Arial', color: 'BF3F36'});
      }
      const line = (deck.ShapeType && deck.ShapeType.line) || 'line';
      slide.addShape(line, {x: 0.5, y: fit.ruleY, w: 12.3, h: 0, line: {color: 'ECA91C', width: 1}});
      slide.addText(scope.text,
        {x: 0.5, y: fit.scopeY, w: 12.3, h: fit.scopeH, fontSize: scope.fontSize, fontFace: 'Arial', color: '716A64', valign: 'top'});
      slide.addText(sourceNote(item),
        {x: 0.5, y: 6.8, w: 9.0, h: 0.35, fontSize: 8, fontFace: 'Arial', color: '716A64'});
      slide.addText('MENTAT',
        {x: 9.6, y: 6.8, w: 3.2, h: 0.35, align: 'right', fontSize: 12, fontFace: 'Georgia', color: '342649'});
      slide.addNotes(speakerNotes(item));
    }
    const blob = await deck.write({outputType: 'blob'});
    root.MentatExport.download(blob, PPTX_TYPE, 'mentat-briefing-' + isoDate() + '.pptx');
    return blob;
  }

  /* ----------------------------------------------------------- share bundle */

  let pocketTimer = null;
  const shareProgress = text => { const node = el('share-progress'); if (node) node.textContent = text || ''; };
  function shareOutput(text) {
    const node = el('share-output');
    if (!node) return;
    node.textContent = text || '';
    node.hidden = !text;
  }
  async function openShare() {
    const dialog = el('share');
    if (!dialog) return;
    shareProgress('Loading reports…');
    shareOutput('');
    const host = el('share-datasets');
    if (host) host.replaceChildren();
    if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', '');
    try {
      const catalog = await getJSON('/api/contributions/catalog');
      const datasets = catalog.datasets || [];
      if (host) {
        host.replaceChildren(...datasets.map(dataset => {
          const label = document.createElement('label');
          const box = document.createElement('input');
          box.type = 'checkbox';
          box.value = dataset.id;
          box.disabled = !dataset.available;
          const text = document.createElement('span');
          text.textContent = (dataset.label || dataset.id) +
            (dataset.available ? '' : ' — ' + (dataset.message || 'unavailable'));
          label.append(box, text);
          return label;
        }));
      }
      shareProgress(datasets.length ? 'Choose reports for the phone snapshot.'
        : 'No verified reports to publish.');
    } catch (error) {
      shareProgress(problem(error));
    }
  }
  function closeShare() {
    clearTimeout(pocketTimer);
    const dialog = el('share');
    if (!dialog) return;
    if (typeof dialog.close === 'function' && dialog.open) dialog.close(); else dialog.removeAttribute('open');
  }
  async function publishPocket() {
    const host = el('share-datasets');
    const ids = host ? [...host.querySelectorAll('input:checked')].map(box => box.value) : [];
    if (!ids.length) { shareProgress('Choose at least one report first.'); return; }
    shareOutput('');
    const button = el('share-publish');
    if (button) button.disabled = true;
    try {
      const started = await post('/api/publish/pocket', {datasets: ids});
      const job = typeof started.job === 'string' ? started.job
        : (started.job && (started.job.id || started.job.job_id)) || '';
      if (!job) throw new Error('Publish did not start. Try again.');
      shareProgress('Building the phone snapshot…');
      pollPocket(job, Date.now());
    } catch (error) {
      shareProgress(problem(error));
      if (button) button.disabled = false;
    }
  }
  function pollPocket(job, startedAt) {
    clearTimeout(pocketTimer);
    pocketTimer = setTimeout(async () => {
      const button = el('share-publish');
      try {
        const body = await getJSON('/api/publish/pocket?id=' + encodeURIComponent(job));
        const status = String(body.status || '').toLowerCase();
        const elapsed = Math.round((Date.now() - startedAt) / 1000);
        if (['succeeded', 'success', 'done', 'complete', 'completed'].includes(status)) {
          shareProgress(body.message || 'Phone snapshot built.');
          shareOutput(body.output_path
            ? 'Written to ' + body.output_path
            : 'Written on the console.');
          if (button) button.disabled = false;
          return;
        }
        if (['failed', 'error', 'cancelled'].includes(status)) {
          shareProgress(body.message || 'Phone snapshot failed.');
          if (button) button.disabled = false;
          return;
        }
        shareProgress((body.message || 'Building the phone snapshot…') + ' · ' + elapsed + 's elapsed');
        if (elapsed > 900) {
          shareProgress('Still building after 15 minutes. Check the console window.');
          if (button) button.disabled = false;
          return;
        }
        pollPocket(job, startedAt);
      } catch (error) {
        shareProgress(problem(error));
        if (button) button.disabled = false;
      }
    }, 1500);
  }

  /* ----------------------------------------------------------------- wiring */

  function wire() {
    const drawer = el('briefing');
    if (!drawer) return;
    const on = (id, run) => { const node = el(id); if (node) node.onclick = run; };
    on('briefing-close', closeDrawer);
    on('briefing-pptx', () => guard('Export PowerPoint', exportPptx));
    on('briefing-print', () => guard('Print briefing', exportPrint));
    on('briefing-html', () => guard('HTML bundle', exportHtml));
    on('briefing-share', () => { openDrawer(); openShare(); });
    on('briefing-save-view', () => { const pane = studio(); if (pane && pane.saveView) pane.saveView(); });
    on('share-close', closeShare);
    on('share-publish', publishPocket);
    const dialog = el('share');
    if (dialog) dialog.addEventListener('close', () => clearTimeout(pocketTimer));
    render();
    load();
  }

  root.MentatBriefing = {capture, add, move, remove, list, exportPrint, exportHtml, exportPptx,
    open: openDrawer, close: closeDrawer, model};

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire);
  else wire();
})(globalThis);
