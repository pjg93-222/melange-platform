/* One-click PNG image of a single figure (PAC evidence, Graph, Chart Studio, Insights).
   Nothing is printed from this window. The figure is cloned with the classes of its
   ancestors, its canvases and blob images are frozen into data: images, and the markup
   goes to POST /api/export/pdf (the path keeps its old name), where headless Edge photographs it
   from the console's own stylesheets. The reply is a download. */
(() => {
  'use strict';
  const PAD = 16, MAX_SIDE = 19000, SLACK = 2;
  const VIEW_SLUG = {evidence: 'pac-evidence', graph: 'graph', studio: 'chart-studio', insights: 'insights'};
  const VIEW_LABEL = {evidence: 'PAC evidence', graph: 'Graph', studio: 'Chart Studio', insights: 'Insights'};

  const slug = text => String(text == null ? '' : text).normalize('NFKD').replace(/[̀-ͯ]/g, '')
    .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60).replace(/-+$/, '');
  const two = number => String(number).padStart(2, '0');
  const stamp = date => `${date.getFullYear()}${two(date.getMonth() + 1)}${two(date.getDate())}-${two(date.getHours())}${two(date.getMinutes())}`;
  const fileName = (view, title, date) => `${VIEW_SLUG[view] || slug(view) || 'figure'}-${slug(title) || 'figure'}-${stamp(date || new Date())}.png`;
  // The image is the figure, the header above it and one margin around both.
  const pageSize = (width, height, headerHeight) => ({
    width: width + 2 * PAD, height: Math.ceil(PAD + headerHeight + height + PAD + SLACK)});

  if (typeof document === 'undefined') {
    if (typeof module !== 'undefined') module.exports = {slug, stamp, fileName, pageSize, PAD};
    return;
  }

  const text = node => (node ? node.textContent : '').replace(/\s+/g, ' ').trim();
  const query = selector => text(document.querySelector(selector));
  const forecastScope = host => {
    const industry = document.getElementById('gf-industry'), date = document.getElementById('gf-date');
    // The chart card prints its own basis line ("2025 dollars"); the header says it only for figures that do not.
    return [industry && industry.selectedOptions[0] ? text(industry.selectedOptions[0]) : '', host && host.querySelector('#gf-basis') ? '' : query('#gf-basis'),
      date && date.value ? 'As of ' + date.value : ''].filter(Boolean).join(' · ');
  };

  // The map's own controls say what it shows; the image carries that as words instead of buttons.
  const mapScope = host => {
    const direction = host.querySelector('.map-direction.on'), cycle = host.querySelector('#evidence-map-cycle');
    return [text(host.querySelector('.map-overline')), direction && direction.firstChild ? text(direction.firstChild) : '',
      cycle && cycle.selectedOptions[0] ? text(cycle.selectedOptions[0]) : '', text(host.querySelector('.map-layer.on'))].filter(Boolean).join(' · ');
  };

  // What each kind of figure says about itself in the image header. host: where the button lives and
  // what is drawn. title: a selector inside the figure, or a function; else its first heading.
  // scope and source: functions of the figure.
  const STUDIO_CANVAS = {view: 'studio', host: '#studio-canvas', title: () => query('#studio-title'), scope: () => query('#studio-subtitle'),
    source: () => query('#studio-note')};
  const STUDIO_TILE = {view: 'studio', host: '.studio-board-tile:not(:has(.studio-board-tile-empty))', title: '.studio-board-tile-title',
    scope: host => text(host.querySelector('.studio-board-tile-subtitle')), source: host => text(host.querySelector('.studio-board-tile-note'))};
  const FIGURES = [
    {view: 'evidence', host: '#evidence-panes > .evidence-pane', scope: host => text(host.querySelector('.pane-tags'))},
    {view: 'evidence', host: '#evidence-compare'},
    {view: 'evidence', host: '#evidence-map', title: '.map-head h2', scope: host => mapScope(host)},
    {view: 'evidence', host: '#evidence-flow .flow-card', scope: host => text(host.querySelector('.flow-sub'))},
    {view: 'graph', scope: () => query('#graph-scope'), source: () => query('#graph-source-label'),
      host: '#graph-category-panel, #graph-organization-panel, #graph-candidate-panel, #graph-company-panel, #graph-level-panel, #graph-drawer-panel, #graph-compare-table'},
    {view: 'graph', host: '#graph-flow .flow-card', scope: host => text(host.querySelector('.flow-sub'))},
    {view: 'graph', host: '#flow-preview', title: () => query('#flow-panel > h3'), scope: () => query('#flow-status')},
    {view: 'graph', host: '#chart-image', title: () => query('#chart-title'), scope: () => query('#chart-context')},
    STUDIO_CANVAS, STUDIO_TILE,
    {view: 'studio', host: '.studio-overview-card', scope: () => query('#studio-overview-scope')},
    {view: 'studio', host: '.worksheet-comparison-canvas', title: () => 'Compare worksheets'},
    {view: 'insights', host: '.insights-card', title: '.insights-card-title', scope: host => text(host.querySelector('.insights-card-subtitle')),
      source: host => text(host.querySelector('.insights-card-source'))},
    {view: 'insights', host: '.gf-chart-col', title: '#gf-hero-title', scope: forecastScope},
    {view: 'insights', host: '#gf-party', title: '#gf-party-title', scope: forecastScope, source: () => query('#gf-footnote-text')}
  ];
  // titleNode: the title's own element when it sits inside the figure; build() drops it from the clone so the title prints once, in the header.
  const describe = (host, spec) => {
    let node = null, named = '';
    if (typeof spec.title === 'function') named = spec.title(host);
    else if (spec.title) { node = host.querySelector(spec.title); named = text(node || document.querySelector(spec.title)); }
    if (!named) { node = host.querySelector('h1, h2, h3, h4, figcaption'); named = text(node); }
    return {
      title: named || host.getAttribute('aria-label') || VIEW_LABEL[spec.view], titleNode: named ? node : null,
      scope: spec.scope ? spec.scope(host) : '', source: spec.source ? spec.source(host) : ''};
  };

  // Export-only controls and anything that is chrome rather than the figure.
  const CHROME = ['.figure-pdf-btn', 'details.graph-export-menu', '.graph-text-button[data-values]', '.pane-buttons', '.pane-about',
    '.map-head button', '.map-toolbar-card', '.ev-toolbar', '.map-enlarge', '.map-collapse',
    '.insights-card-tools', '#gf-dollars', '.studio-menu', '.studio-board-tile-sheet', 'input[type=file]', 'script', 'iframe'].join(',');

  /* ------------------------------------------------------------ freezing */

  // A canvas leaves the page as a picture. MapLibre clears its drawing buffer after each paint, so
  // the street map is asked to paint once more and read inside that very frame.
  function canvasUrl(canvas) {
    const holder = canvas.closest('.mm-streets-map');
    const map = holder && holder.__maplibre;
    if (map && typeof map.triggerRepaint === 'function' && map.getCanvas && map.getCanvas() === canvas) {
      return new Promise(resolve => {
        const timer = setTimeout(() => resolve(''), 3000);
        map.once('render', () => { clearTimeout(timer); try { resolve(canvas.toDataURL('image/png')); } catch (error) { resolve(''); } });
        map.triggerRepaint();
      });
    }
    try { return Promise.resolve(canvas.width > 0 && canvas.height > 0 ? canvas.toDataURL('image/png') : ''); } catch (error) { return Promise.resolve(''); }
  }
  // blob: and same-origin pictures become data: pictures. The page's policy allows img-src blob: but
  // not connect-src blob:, so the picture is drawn rather than fetched.
  function pictureUrl(url) {
    return new Promise(resolve => {
      const image = new Image();
      image.onload = () => {
        try {
          const canvas = document.createElement('canvas');
          canvas.width = image.naturalWidth || 1; canvas.height = image.naturalHeight || 1;
          canvas.getContext('2d').drawImage(image, 0, 0);
          resolve(canvas.toDataURL('image/png'));
        } catch (error) { resolve(''); }
      };
      image.onerror = () => resolve('');
      image.src = url;
    });
  }
  async function freeze(original, clone, doc) {
    const sources = [...original.querySelectorAll('canvas')], copies = [...clone.querySelectorAll('canvas')];
    const jobs = sources.map(async (canvas, index) => {
      const copy = copies[index], box = canvas.getBoundingClientRect();
      const url = box.width >= 1 && box.height >= 1 ? await canvasUrl(canvas) : '';
      if (!url) { copy.remove(); return; }
      const picture = doc.createElement('img');
      for (const name of ['id', 'class']) if (copy.hasAttribute(name)) picture.setAttribute(name, copy.getAttribute(name));
      picture.setAttribute('src', url); picture.setAttribute('alt', '');
      picture.style.setProperty('width', box.width + 'px'); picture.style.setProperty('height', box.height + 'px');
      copy.replaceWith(picture);
    });
    for (const node of clone.querySelectorAll('image, img')) {
      const attribute = ['href', 'xlink:href', 'src'].find(name => node.hasAttribute(name));
      const url = attribute ? node.getAttribute(attribute) : '';
      if (!url || /^(data:|#)/i.test(url)) continue;
      jobs.push(pictureUrl(url).then(data => { if (data) node.setAttribute(attribute, data); else node.remove(); }));
    }
    await Promise.all(jobs);
  }
  // The clone keeps attributes, not properties: carry what the reader chose into markup.
  function carryControls(original, clone) {
    const sources = original.querySelectorAll('input, select, textarea'), copies = clone.querySelectorAll('input, select, textarea');
    sources.forEach((source, index) => {
      const copy = copies[index];
      if (!copy) return;
      if (source.tagName === 'SELECT') [...source.options].forEach((option, at) => copy.options[at] && copy.options[at].toggleAttribute('selected', option.selected));
      else if (source.tagName === 'TEXTAREA') copy.textContent = source.value;
      else if (source.type === 'checkbox' || source.type === 'radio') copy.toggleAttribute('checked', source.checked);
      else copy.setAttribute('value', source.value);
    });
  }

  /* ------------------------------------------------------- the document */

  const customProperties = (from, to) => {
    for (let i = 0; i < from.style.length; i += 1) {
      const name = from.style[i];
      if (name.startsWith('--')) to.style.setProperty(name, from.style.getPropertyValue(name));
    }
  };
  const shellAttributes = ['id', 'class', 'role', 'open'];
  // The figure's ancestors, emptied: descendant selectors and inherited variables still find them.
  function wrap(clone, original, doc) {
    let node = clone;
    for (let up = original.parentElement; up && up !== document.body && up !== document.documentElement; up = up.parentElement) {
      const shell = doc.createElement(up.localName);
      for (const attribute of up.attributes) if (shellAttributes.includes(attribute.name) || attribute.name.startsWith('data-')) shell.setAttribute(attribute.name, attribute.value);
      customProperties(up, shell);
      shell.setAttribute('data-figure-pdf-skeleton', '');
      shell.append(node);
      node = shell;
    }
    return node;
  }
  function rootAttributes(element) {
    const out = {};
    for (const attribute of element.attributes) if (['class', 'lang', 'dir'].includes(attribute.name) || attribute.name.startsWith('data-')) out[attribute.name] = attribute.value;
    const holder = document.createElement('div');
    customProperties(element, holder);
    if (holder.style.cssText) out.style = holder.style.cssText;
    return out;
  }
  function pin(clone, width, height) {
    for (const [name, value] of [['box-sizing', 'border-box'], ['width', width + 'px'], ['height', height + 'px'], ['min-width', '0'],
      ['max-width', 'none'], ['min-height', '0'], ['max-height', 'none'], ['margin', '0'], ['flex', 'none']]) clone.style.setProperty(name, value, 'important');
  }
  function header(doc, meta, now) {
    const block = doc.createElement('div');
    block.className = 'figure-pdf-head';
    const line = (className, value) => {
      if (!value) return;
      const node = doc.createElement('div'); node.className = className; node.textContent = value; block.append(node);
    };
    line('figure-pdf-title', meta.title);
    line('figure-pdf-line figure-pdf-scope', meta.scope);
    line('figure-pdf-line figure-pdf-source', meta.source);
    line('figure-pdf-line figure-pdf-stamp', 'Generated ' + now.toLocaleString('en-US', {dateStyle: 'medium', timeStyle: 'short'}) + ' · MENTAT');
    return block;
  }
  // The header's height is whatever its text needs at the figure's width; ask the page, which has the same styles.
  function measure(block, width) {
    const box = document.createElement('div');
    box.className = 'figure-pdf-measure';
    box.style.setProperty('width', width + 'px');
    box.append(document.importNode(block, true));
    document.body.append(box);
    const height = box.getBoundingClientRect().height;
    box.remove();
    return Math.ceil(height);
  }
  const stylesheetPaths = () => [...document.querySelectorAll('link[rel~="stylesheet"][href]')]
    .map(link => new URL(link.getAttribute('href'), location.href)).filter(url => url.origin === location.origin).map(url => url.pathname);

  // The strip the button sits in is screen furniture, like the button: the export leaves it out.
  function stripOf(element) {
    if (!element.classList.contains('figure-pdf-strip')) return 0;
    const on = parseFloat(getComputedStyle(element).paddingTop) || 0;
    element.classList.remove('figure-pdf-strip');
    const off = parseFloat(getComputedStyle(element).paddingTop) || 0;
    element.classList.add('figure-pdf-strip');
    return Math.max(0, on - off);
  }
  async function build(element, options) {
    const box = element.getBoundingClientRect();
    const width = Math.ceil(box.width), height = Math.ceil(Math.max(box.height, element.scrollHeight || 0));
    const lift = stripOf(element);
    if (width < 24 || height < 24) throw new Error('This figure is not on screen, so there is nothing to draw.');
    const doc = document.implementation.createHTMLDocument('');
    const clone = doc.importNode(element, true);
    const twin = options.titleNode && clone.querySelectorAll('*')[[...element.querySelectorAll('*')].indexOf(options.titleNode)];
    await freeze(element, clone, doc);
    carryControls(element, clone);
    for (const node of clone.querySelectorAll(CHROME + ',[hidden]')) node.remove();
    if (twin) twin.remove();
    clone.classList.remove('figure-pdf-strip');
    pin(clone, width, height - lift);
    const now = new Date(), meta = {title: options.title || '', scope: options.scope || '', source: options.source || ''};
    const head = header(doc, meta, now), headHeight = measure(head, width);
    const page = pageSize(width, height - lift, headHeight);
    if (page.height > MAX_SIDE || page.width > MAX_SIDE) throw new Error('This figure is too large for one image.');
    const sheet = doc.createElement('div');
    sheet.className = 'figure-pdf-sheet';
    sheet.append(head, wrap(clone, element, doc));
    return {
      body: sheet.outerHTML, html_attrs: rootAttributes(document.documentElement), body_attrs: rootAttributes(document.body),
      stylesheets: stylesheetPaths(), width: page.width, height: page.height, viewport: {width: innerWidth, height: innerHeight},
      filename: options.filename || fileName(options.view, meta.title, now)};
  }

  /* ---------------------------------------------------------- the request */

  let sessionToken = '';
  async function token(fresh) {
    if (sessionToken && !fresh) return sessionToken;
    const response = await fetch('/api/session?light=1', {cache: 'no-store'});
    const body = await response.json();
    if (!response.ok || !body.token) throw new Error('The local session did not provide a token. Refresh the page.');
    sessionToken = body.token;
    return sessionToken;
  }
  async function render(payload) {
    for (let attempt = 0; ; attempt += 1) {
      const response = await fetch('/api/export/pdf', {method: 'POST', body: JSON.stringify(payload),
        headers: {'Content-Type': 'application/json', 'X-Mentat-Token': await token(attempt > 0)}});
      if (response.status === 403 && attempt === 0) continue;
      if (!response.ok) {
        let message = '';
        try { message = (await response.json()).error; } catch (error) { message = ''; }
        throw new Error(message || `The image could not be drawn (${response.status}).`);
      }
      return response.blob();
    }
  }

  // element: the figure. options: {title, scope, source, filename, view}.
  async function exportElement(element, options) {
    const settings = options || {};
    if (!(element instanceof Element)) throw new Error('Choose a figure to export.');
    const payload = await build(element, settings);
    const blob = await render(payload);
    if (!globalThis.MentatExport || typeof globalThis.MentatExport.download !== 'function') throw new Error('Downloads are unavailable until the page finishes loading.');
    globalThis.MentatExport.download(blob, 'image/png', payload.filename);
    return {filename: payload.filename, bytes: blob.size, width: payload.width, height: payload.height};
  }

  /* -------------------------------------------------------------- buttons */

  const busy = new WeakSet(), checked = new WeakSet(), needsAnchor = new WeakSet();
  const report = message => {
    if (typeof globalThis.showError === 'function') globalThis.showError(message);
    else if (message) console.error(message);
  };
  async function run(control, host, spec) {
    if (busy.has(control)) return;
    busy.add(control);
    control.setAttribute('aria-busy', 'true');
    report('');
    try {
      await exportElement(host, {...describe(host, spec), view: spec.view});
    } catch (error) {
      report('Image export: ' + (error && error.message ? error.message : 'the figure could not be drawn.'));
    } finally {
      busy.delete(control);
      control.removeAttribute('aria-busy');
    }
  }
  function control(host, spec) {
    const node = document.createElement('button');
    node.type = 'button'; node.className = 'figure-pdf-btn'; node.textContent = 'Image';
    node.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); run(node, host, spec); });
    return node;
  }
  function attach() {
    for (const spec of FIGURES) {
      for (const host of document.querySelectorAll(spec.host)) {
        if (!host.querySelector(':scope > .figure-pdf-btn')) {
          const button = control(host, spec);
          button.setAttribute('aria-label', 'Download image of this figure');
          button.title = 'Download an image of this figure';
          host.append(button);
        }
        // The button has a strip of its own across the top of the figure, so it never sits on the figure.
        if (!host.classList.contains('figure-pdf-strip')) host.classList.add('figure-pdf-strip');
        // The button is absolutely placed; a figure that is not positioned yet becomes the anchor.
        if (!checked.has(host)) {
          checked.add(host);
          if (getComputedStyle(host).position === 'static') needsAnchor.add(host);
        }
        if (needsAnchor.has(host) && !host.classList.contains('figure-pdf-host')) host.classList.add('figure-pdf-host');
      }
    }
  }
  let queued = false;
  const schedule = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; attach(); });
  };

  globalThis.MentatPdf = {exportElement, attach, slug, fileName, pageSize};
  attach();
  new MutationObserver(schedule).observe(document.body, {childList: true, subtree: true});
})();
