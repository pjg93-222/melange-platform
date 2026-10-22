/* Passive source-table browser. No contribution queries, filters or collectors. */
(function (global) {
  'use strict';
  const ROLE = /^mentat\.fec\.(cm|cn|ccl|pas2)\.(2022|2024|2026)$/;
  function endpoint(action, role, page) {
    if (!['catalogue', 'rows', 'download', 'provenance'].includes(action)) throw new Error('Unknown public data action.');
    const query = new URLSearchParams();
    if (action !== 'catalogue') {
      if (typeof role !== 'string' || !ROLE.test(role)) throw new Error('Choose a fixed public table.');
      query.set('table', role);
    }
    if (action === 'rows') {
      if (!Number.isSafeInteger(page) || page < 0 || page > 99999999) throw new Error('Invalid page.');
      query.set('page', String(page));
    }
    return '/api/public-data/' + action + (query.size ? '?' + query : '');
  }
  function officialUrl(role) {
    const match = typeof role === 'string' && role.match(ROLE);
    if (!match) throw new Error('Unknown public table.');
    return 'https://www.fec.gov/files/bulk-downloads/' + match[2] + '/' + match[1] + match[2].slice(-2) + '.zip';
  }
  function sameSource(first, second) {
    return !!first && !!second && /^[a-f0-9]{64}$/.test(first.sourceDigest || '') &&
      /^[a-f0-9]{64}$/.test(first.sourceFingerprint || '') && first.sourceDigest === second.sourceDigest &&
      first.sourceFingerprint === second.sourceFingerprint;
  }
  function renderTable(doc, table, value) {
    table.replaceChildren();
    if (!Array.isArray(value.columns) || value.columns.length > 22 || !Array.isArray(value.rows) || value.rows.length > 100) throw new Error('Invalid public table response.');
    const head = doc.createElement('thead'), labels = doc.createElement('tr');
    for (const label of value.columns) {
      const cell = doc.createElement('th'); cell.scope = 'col'; cell.textContent = String(label); labels.append(cell);
    }
    head.append(labels); const body = doc.createElement('tbody');
    for (const values of value.rows) {
      if (!Array.isArray(values) || values.length !== value.columns.length) throw new Error('Invalid public table row.');
      const row = doc.createElement('tr');
      for (const value of values) { const cell = doc.createElement('td'); cell.textContent = String(value); row.append(cell); }
      body.append(row);
    }
    table.append(head, body);
  }
  function mount(doc, fetcher) {
    const get = id => doc.getElementById(id), select = get('public-data-table');
    if (!select) return null;
    let catalogue = null, page = 0, revision = 0, loaded = false;
    const status = get('public-data-status'), table = get('public-data-rows');
    const previous = get('public-data-previous'), next = get('public-data-next');
    const download = get('public-data-download'), source = get('public-data-source');
    function clear() { table.replaceChildren(); get('public-data-provenance').textContent = ''; download.hidden = true; source.hidden = true; previous.disabled = true; next.disabled = true; }
    async function json(path) {
      const response = await fetcher(path, {cache: 'no-store'}), value = await response.json();
      if (!response.ok) throw new Error(value.error || 'Public data is unavailable.');
      return value;
    }
    async function rows(requestedPage) {
      const own = ++revision, role = select.value; clear(); status.textContent = 'Reading public source…';
      try {
        const [value, proof] = await Promise.all([json(endpoint('rows', role, requestedPage)), json(endpoint('provenance', role))]);
        if (own !== revision) return;
        if (!sameSource(catalogue, value) || !sameSource(catalogue, proof) || value.table !== role || proof.document?.role !== role) throw new Error('The public source changed. Reload public data.');
        renderTable(doc, table, value); page = value.page;
        previous.disabled = value.previousPage == null; next.disabled = value.nextPage == null;
        download.href = endpoint('download', role); download.hidden = false;
        source.href = officialUrl(role); source.hidden = false;
        status.textContent = value.rows.length ? 'Rows ' + value.firstRow + '–' + (value.firstRow + value.rows.length - 1) + ' of ' + value.totalRows.toLocaleString() : 'No source rows.';
        get('public-data-provenance').textContent = 'Observed ' + proof.observedAt + ' · Source member SHA256 ' + proof.document.memberSha256 + ' · Generation ' + proof.sourceDigest;
      } catch (error) { if (own === revision) { clear(); status.textContent = error.message; } }
    }
    async function load() {
      const own = ++revision; loaded = true; clear(); select.disabled = true; select.replaceChildren(); status.textContent = 'Loading public source catalogue…';
      try {
        const value = await json(endpoint('catalogue'));
        if (own !== revision) return;
        if (!value.available || !Array.isArray(value.tables) || value.tables.length !== 12 || !sameSource(value, value)) throw new Error('No complete verified public source is installed.');
        const seen = new Set();
        for (const entry of value.tables) {
          if (!ROLE.test(entry.id) || seen.has(entry.id)) throw new Error('Invalid public source catalogue.');
          seen.add(entry.id); const option = doc.createElement('option'); option.value = entry.id;
          option.textContent = entry.label + ' · ' + entry.cycle + ' · ' + entry.records.toLocaleString() + ' rows'; select.append(option);
        }
        catalogue = value; select.disabled = false; await rows(0);
      } catch (error) { if (own === revision) { catalogue = null; clear(); status.textContent = error.message; } }
    }
    select.addEventListener('change', () => rows(0));
    previous.addEventListener('click', () => rows(page - 1)); next.addEventListener('click', () => rows(page + 1));
    get('public-data-reload').addEventListener('click', load);
    return {setActive(active) { if (active && !loaded) return load(); }, reload: load};
  }
  const api = {endpoint, officialUrl, sameSource, renderTable, mount};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (global && global.document) {
    const view = mount(global.document, global.fetch.bind(global));
    global.MentatPublicData = view;
    global.addEventListener('mentat:view', event => view?.setActive(event.detail?.view === 'public-data'));
    view?.setActive(global.document.body.dataset.view === 'public-data');
  }
})(typeof window === 'undefined' ? null : window);
