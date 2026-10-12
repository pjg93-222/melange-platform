const $ = id => document.getElementById(id);
const renderedHtml = new Map();
function setHtml(id, html) { if (renderedHtml.get(id) !== html) { $(id).innerHTML = html; renderedHtml.set(id, html); } }
let token = '', upload = null, selectedRun = '', selectedLane = '', busy = false, warningIds = [];
let readOnly = false, evidenceCollectionAvailable=false;
let rosterRevision = 0, companySequence = 0, currentView = 'loading', splitView = false;
let runCatalog = [], runGroup = '', runArchiveBusy = false, resumeBusy = false;
let savedCoverage = [];
/* Loading (prompt 07): 1 Roster -> 2 Review names -> 3 Choose cycles -> 4 Collect
   (Start collection opens Collection). `upload` stays the roster the server
   accepted (its id is what /api/start receives); `review` is the readiness
   being reviewed in step 2 and where it came from ('draft', 'upload' or
   'saved'); `uploadedRoster`/`uploadedFile` are step 1's spreadsheet. */
let loadingStep = 1, rosterMode = 'type', review = null, uploadedRoster = null, uploadedFile = null;
let currentDecisions = [], decisionBusy = false, rosterExpanded = false, coverageSelection = null, keysTouched = false;
function rosterSectors() {
  if (review && loadingStep > 1) return (review.readiness?.readiness || []).map(group => group.industry);
  if (rosterMode === 'upload') return (uploadedRoster?.readiness?.readiness || []).map(group => group.industry);
  return [$('sector').value];
}
function renderLoadingCoverage() {
  const model = MentatCoverage.matrix(savedCoverage, rosterSectors());
  const cells = model.flatMap(line => line.cells);
  const chosen = coverageSelection && cells.find(cell => cell.sector === coverageSelection.sector && cell.cycle === coverageSelection.cycle);
  const fallback = (model.find(line => line.roster) || model[0])?.cells.at(-1) || null;
  setHtml('loading-coverage', MentatCoverage.grid(model, chosen || fallback));
  renderCycleCards();
}
$('loading-coverage').addEventListener('click', event => {
  const cell = event.target.closest('.coverage-cell'); if (!cell) return;
  coverageSelection = {sector:cell.dataset.sector, cycle:Number(cell.dataset.cycle)};
  renderLoadingCoverage();
  [...$('loading-coverage').querySelectorAll('.coverage-cell')].find(item => item.dataset.sector === coverageSelection.sector && Number(item.dataset.cycle) === coverageSelection.cycle)?.focus();
});
document.querySelectorAll('input[name=cycle]').forEach(input => input.addEventListener('change', () => renderSetupSummary()));
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = value => value == null ? '—' : Number(value).toLocaleString();
const label = value => String(value || '').replaceAll('_', ' ');
const duration = value => value == null ? '—' : value < 60 ? '<1 min' : value < 3600 ? Math.ceil(value / 60) + ' min' : (value / 3600).toFixed(1) + ' hr';
function showError(message) { $('error').classList.add('error'); $('error').textContent = message; $('error').hidden = !message; }
function showStatus(message) { $('error').classList.remove('error'); $('error').textContent = message; $('error').hidden = !message; }
/* Where the reader came from. Three controls take the reader out of the tab they
   pressed them in: Graph's "Open in Chart Studio", Chart Studio's "Why
   Cross-reference differs", and Chart Studio's "Classic". Paul's ruling C keeps
   every tab switch on replaceState, so the browser's own Back leaves the console
   and cannot be the way back - each of the three says where it came from, and
   the destination carries one chip until the reader leaves it by any other
   route. In memory only: nothing here reaches storage, and nothing reaches the
   URL, which goes on carrying the scope and nothing else. */
const TAB_NAMES = {loading:'Loading', collection:'Collection', graph:'Graph',
  'chart-studio':'Chart Studio', insights:'Insights', 'cross-reference':'Cross-reference', evidence:'PAC evidence'};
const MentatReturn = (() => {
  let from = '', to = '';
  function render(view) {
    const host = $('return-chip');
    if (!host) return;
    if (!from || view !== to) { from = ''; to = ''; host.hidden = true; host.replaceChildren(); return; }
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = '\u2190 Back to ' + (TAB_NAMES[from] || from);
    button.onclick = () => { const back = from; from = ''; to = ''; showView(back); };
    host.replaceChildren(button);
    host.hidden = false;
  }
  return {
    // arrive(fromView, toView) is called by the control, BEFORE it switches.
    arrive(fromView, toView) { from = fromView || ''; to = toView || ''; },
    render,
    // Only for the suites: the chip is otherwise unreadable from outside.
    state() { return {from, to}; }
  };
})();
window.MentatReturn = MentatReturn;
function showView(view, focus = false) {
  if (view === 'charts') view = 'graph';
  // A completed action belongs to its tab; it must not label an unrelated view.
  if (view !== currentView && !$('error').classList.contains('error')) $('error').hidden = true;
  currentView = ['loading', 'collection', 'cross-reference', 'evidence', 'graph', 'chart-studio', 'insights'].includes(view) ? view : 'loading';
  document.body.dataset.view = currentView;
  for (const name of ['loading', 'collection', 'cross-reference', 'evidence', 'graph', 'chart-studio', 'insights']) {
    const active = name === currentView;
    const pane = $('view-' + name), paired = splitView && ['graph','evidence'].includes(currentView) && ['graph','evidence'].includes(name);
    pane.hidden = !(active || paired);
    pane.setAttribute('role', paired ? 'region' : 'tabpanel');
    $('tab-' + name).setAttribute('aria-selected', String(active));
    $('tab-' + name).tabIndex = active ? 0 : -1;
  }
  $('workspace-title').textContent = {loading:'Load a sector',collection:'Collection','cross-reference':'Cross-reference',evidence:'PAC evidence',graph:'Graph','chart-studio':'Chart Studio',insights:'Insights'}[currentView];
  // The tab is named in the window title too, so a second console window is
  // identifiable from the taskbar rather than being a second "Mentat".
  document.title = 'MENTAT \u00b7 ' + (TAB_NAMES[currentView] || 'Console');
  MentatReturn.render(currentView);
  window.MentatCrossReference?.setActive(currentView === 'cross-reference');
  window.MentatInsights?.setActive(currentView === 'insights');
  $('stage').hidden = currentView !== 'collection';
  $('graph-actions').hidden = currentView !== 'graph';
  const paired = splitView && ['graph','evidence'].includes(currentView);
  document.querySelector('.workspace').classList.toggle('split-workspace', paired);
  $('split-view').hidden = !['graph','evidence'].includes(currentView);
  $('split-view').setAttribute('aria-pressed', String(paired));
  $('split-view').textContent = paired ? 'Single view' : currentView === 'graph' ? 'Split with PAC evidence' : 'Split with Graph';
  if (paired) { $('workspace-title').textContent = 'Graph & PAC evidence'; $('graph-actions').hidden = false; }
  // Fragment-only, so it resolves against the current URL and KEEPS the query
  // string: the hash names the tab and the query carries the scope (scope.js),
  // so switching tabs never loses a permalink's scope. replaceState, never
  // pushState - Back does not step through tabs today (Paul's ruling C).
  history.replaceState(null, '', '#' + currentView);
  window.dispatchEvent(new CustomEvent('mentat:view', {detail:{view:currentView,split:paired}}));
  revealCurrentTab();
  if (focus) {
    const activeTab = $('tab-' + currentView);
    activeTab.focus({preventScroll:true});
    activeTab.scrollIntoView({block:'nearest', inline:'nearest'});
  }
}
document.querySelector('.workspace-tabs').addEventListener('click', event => {
  const tab = event.target.closest('[role=tab]'); if (tab) showView(tab.id.slice(4));
});
window.addEventListener('hashchange', () => showView(location.hash.slice(1)));
document.querySelector('.skip-link').addEventListener('click', event => {
  event.preventDefault(); document.getElementById('workspace-main').focus();
});
document.querySelector('.workspace-tabs').addEventListener('keydown', event => {
  const names = [...document.querySelectorAll('.workspace-tabs [role=tab]')].map(tab => tab.id.slice(4)), index = names.indexOf(currentView);
  const next = {ArrowRight:(index + 1) % names.length, ArrowDown:(index + 1) % names.length, ArrowLeft:(index + names.length - 1) % names.length, ArrowUp:(index + names.length - 1) % names.length, Home:0, End:names.length - 1}[event.key];
  if (next != null) { event.preventDefault(); showView(names[next], true); }
});
const sidebarLayout = window.matchMedia('(min-width: 1100px)');
function revealCurrentTab() {
  const nav = document.querySelector('.workspace-tabs'), tab = $('tab-' + currentView);
  if (!nav || !tab || window.matchMedia('(min-width: 1100px)').matches) return;
  const bounds = tab.getBoundingClientRect(), frame = nav.getBoundingClientRect();
  if (bounds.left < frame.left) nav.scrollLeft -= frame.left - bounds.left;
  else if (bounds.right > frame.right) nav.scrollLeft += bounds.right - frame.right;
}
function updateNavigationOrientation() {
  document.querySelector('.workspace-tabs').setAttribute('aria-orientation', sidebarLayout.matches ? 'vertical' : 'horizontal');
  revealCurrentTab();
}
window.addEventListener('resize', revealCurrentTab, {passive:true});
$('split-view').addEventListener('click', () => { splitView = !splitView; showView(currentView); });
sidebarLayout.addEventListener('change', updateNavigationOrientation);
updateNavigationOrientation();
/* The server's own keys: a search name's key is its letters and digits in
   upper case (validate_industry_coverage.py), a company's is its casefolded,
   whitespace-collapsed name (server.py create_roster). */
const nameKey = value => String(value ?? '').toUpperCase().replace(/[^A-Z0-9]+/g, '');
const companyKey = value => String(value ?? '').trim().replace(/\s+/g, ' ').toLocaleLowerCase();
const plural = (count, one, many = one + 's') => `${number(count)} ${count === 1 ? one : many}`;
const listJoin = items => items.length < 3 ? items.join(' and ') : items.slice(0, -1).join(', ') + ' and ' + items.at(-1);
const checkedCycles = () => [...document.querySelectorAll('input[name=cycle]:checked')].map(c => Number(c.value));
function aliasChip(value) {
  const chip = document.createElement('li'), text = document.createElement('span'), remove = document.createElement('button');
  chip.className = 'loading-alias'; chip.dataset.value = value; text.textContent = value;
  remove.type = 'button'; remove.className = 'loading-alias-remove'; remove.setAttribute('aria-label', `Remove ${value}`); remove.textContent = '×';
  chip.append(text, remove); return chip;
}
function rowAliases(row) {
  const values = [...row.querySelectorAll('.loading-alias')].map(chip => chip.dataset.value);
  const pending = row.querySelector('.company-alias-input').value.trim();
  return pending ? [...values, pending] : values;
}
function draftData() {
  return {sector:$('sector').value, companies:[...document.querySelectorAll('.company-entry')].map(row => ({
    name:row.querySelector('.company-name').value,
    aliases:rowAliases(row)
  }))};
}
// What Review names sends: the draft without rows the reader left entirely blank.
function rosterPayload() {
  const draft = draftData();
  return {sector:draft.sector, companies:draft.companies.filter(company => company.name.trim() || company.aliases.length)};
}
function saveDraft() {
  try { localStorage.setItem('mentat-roster-draft-v1', JSON.stringify(draftData()));
    $('draft-status').textContent = 'Draft saved in this browser. Review it when ready.';
  } catch { $('draft-status').textContent = 'Browser storage unavailable. Keep this tab open until you review your roster.'; }
}
function invalidateRoster() {
  rosterRevision++; upload = null; review = null; $('start').disabled = true; $('start').textContent = 'Start collection';
  saveDraft(); numberCompanies(); renderSetupSummary(); renderLoadingCoverage();
}
function addCompany(company = {name:'',aliases:[]}) {
  const id = ++companySequence, row = document.createElement('div'); row.className = 'company-entry';
  row.innerHTML = `<label class="sr-only" for="company-${id}">Company name</label><input id="company-${id}" class="company-name" type="text" maxlength="250" placeholder="e.g. Harborlight Credit Union" value="${escapeHtml(company.name)}"><div class="loading-alias-cell"><ul class="loading-aliases"></ul><label class="sr-only" for="aliases-${id}">Other names searched</label><input id="aliases-${id}" class="company-alias-input" type="text" maxlength="250" autocomplete="off"></div><button type="button" class="remove-company loading-row-remove" aria-label="Remove company"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg></button>`;
  row.querySelector('.loading-aliases').append(...company.aliases.map(aliasChip));
  $('company-editor').append(row); numberCompanies(); return row;
}
function numberCompanies() {
  const rows = [...document.querySelectorAll('.company-entry')];
  rows.forEach((row,index) => {
    const name = row.querySelector('.company-name').value.trim(), remove = row.querySelector('.remove-company');
    row.querySelector('label').textContent = `Company ${index + 1} name`;
    row.querySelector('.company-alias-input').placeholder = row.querySelector('.loading-alias') ? '' : 'Add a name';
    remove.disabled = rows.length === 1;
    remove.setAttribute('aria-label', `Remove company ${index + 1}` + (name ? ` (${name})` : '')); });
  $('add-company').disabled = rows.length >= 1000;
}
function commitAlias(input, text = input.value, separator = /,/) {
  const row = input.closest('.company-entry'), list = row.querySelector('.loading-aliases');
  const have = new Set([...list.children].map(chip => chip.dataset.value.toLocaleLowerCase()));
  const values = String(text).split(separator).map(value => value.trim()).filter(Boolean);
  for (const value of values) if (!have.has(value.toLocaleLowerCase())) { have.add(value.toLocaleLowerCase()); list.append(aliasChip(value)); }
  input.value = ''; numberCompanies(); return values.length;
}
function commitPendingAliases() {
  document.querySelectorAll('.company-alias-input').forEach(input => { if (input.value.trim()) commitAlias(input); });
}
/* Paste from spreadsheet: tab- or comma-separated rows, the first column the
   company and the rest its other names. A canonical_name/alias header (the
   upload template) is read by its columns, one alias per row. */
function clipboardCompanies(text) {
  const lines = String(text).split(/\r?\n/).filter(line => line.trim());
  const delimiter = lines.some(line => line.includes('\t')) ? '\t' : ',';
  let rows = lines.map(line => line.split(delimiter).map(cell => cell.trim().replace(/^"(.*)"$/, '$1')));
  let pick = cells => [cells[0], ...cells.slice(1)];
  const head = (rows[0] || []).map(cell => cell.toLowerCase());
  if (head.includes('canonical_name')) {
    const name = head.indexOf('canonical_name'), alias = head.indexOf('alias');
    pick = cells => [cells[name], alias < 0 ? '' : cells[alias]]; rows = rows.slice(1);
  } else if (/^(company( name)?|name)$/.test(head[0] || '')) rows = rows.slice(1);
  const companies = new Map();
  for (const cells of rows) {
    const [name, ...aliases] = pick(cells).map(value => String(value ?? '').trim());
    if (!name) continue;
    const entry = companies.get(companyKey(name)) || {name, aliases:[]};
    for (const alias of aliases) if (alias && !entry.aliases.some(a => a.toLocaleLowerCase() === alias.toLocaleLowerCase())) entry.aliases.push(alias);
    companies.set(companyKey(name), entry);
  }
  return [...companies.values()];
}
function pasteCompanies(text) {
  const companies = clipboardCompanies(text);
  const blanks = [...document.querySelectorAll('.company-entry')].filter(row => !row.querySelector('.company-name').value.trim() && !rowAliases(row).length);
  for (const company of companies) {
    const row = blanks.shift();
    if (!row) { addCompany(company); continue; }
    row.querySelector('.company-name').value = company.name;
    row.querySelector('.loading-aliases').append(...company.aliases.map(aliasChip));
  }
  if (companies.length) invalidateRoster();
  return companies.length;
}
function commitNewCompany(focusAliases) {
  const input = $('new-company'), name = input.value.trim();
  if (!name) return;
  const rows = [...document.querySelectorAll('.company-entry')];
  const blank = rows.length === 1 && !rows[0].querySelector('.company-name').value.trim() && !rowAliases(rows[0]).length ? rows[0] : null;
  const row = blank || addCompany();
  row.querySelector('.company-name').value = name; input.value = '';
  invalidateRoster();
  if (focusAliases) row.querySelector('.company-alias-input').focus();
}
function restoreDraft() {
  try {
    const draft = JSON.parse(localStorage.getItem('mentat-roster-draft-v1') || 'null');
    if (draft && typeof draft.sector === 'string' && Array.isArray(draft.companies) && draft.companies.length <= 1000 && draft.companies.every(c => typeof c.name === 'string' && Array.isArray(c.aliases) && c.aliases.every(a => typeof a === 'string'))) {
      $('sector').value = draft.sector; draft.companies.forEach(addCompany);
    }
  } catch { /* An invalid or unavailable local draft never prevents a fresh roster. */ }
  if (!$('company-editor').children.length) addCompany();
}
$('roster-form').addEventListener('input', event => { if (event.target.id !== 'new-company') invalidateRoster(); });
$('add-company').addEventListener('click', () => { const row = addCompany(); invalidateRoster(); row.querySelector('input').focus(); });
$('company-editor').addEventListener('click', event => {
  const aliasRemove = event.target.closest('.loading-alias-remove');
  if (aliasRemove) {
    const row = aliasRemove.closest('.company-entry');
    aliasRemove.closest('.loading-alias').remove(); invalidateRoster();
    row.querySelector('.company-alias-input').focus(); return;
  }
  if (event.target.classList.contains('loading-alias-cell')) { event.target.querySelector('.company-alias-input').focus(); return; }
  if (!event.target.closest('.remove-company')) return;
  const row = event.target.closest('.company-entry');
  const next = row.nextElementSibling || row.previousElementSibling;
  row.remove(); numberCompanies(); invalidateRoster();
  (next?.querySelector('.company-name') || $('add-company')).focus();
});
$('company-editor').addEventListener('keydown', event => {
  const input = event.target.closest('.company-alias-input'); if (!input) return;
  if (event.key === 'Enter' || event.key === ',') { event.preventDefault(); if (commitAlias(input)) invalidateRoster(); }
  else if (event.key === 'Backspace' && !input.value) {
    const last = input.closest('.company-entry').querySelector('.loading-alias:last-of-type');
    if (last) { event.preventDefault(); last.remove(); invalidateRoster(); }
  }
});
$('company-editor').addEventListener('focusout', event => {
  const input = event.target.closest?.('.company-alias-input');
  if (input && input.value.trim() && commitAlias(input)) invalidateRoster();
});
$('roster-form').addEventListener('paste', event => {
  const text = event.clipboardData?.getData('text/plain') || '';
  if (!/[\t\r\n]/.test(text.trim())) return;
  if (event.target.matches('.company-alias-input')) { event.preventDefault(); commitAlias(event.target, text, /[\t\r\n]+/); invalidateRoster(); }
  else if (event.target.matches('.company-name, #new-company')) { event.preventDefault(); const added = pasteCompanies(text); if (added) $('draft-status').textContent = `Added ${plural(added, 'company', 'companies')} from the clipboard.`; }
});
$('new-company').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); commitNewCompany(true); } });
$('new-company').addEventListener('change', () => commitNewCompany(false));
$('clear-new-company').addEventListener('click', () => { $('new-company').value = ''; $('new-company').focus(); });
$('paste-roster').addEventListener('click', async () => {
  try {
    const added = pasteCompanies(await navigator.clipboard.readText());
    $('draft-status').textContent = added ? `Added ${plural(added, 'company', 'companies')} from the clipboard.` : 'The clipboard holds no company names. Copy spreadsheet rows: the company first, then its other names.';
  } catch { $('draft-status').textContent = 'The browser did not allow reading the clipboard. Click a company name and press Ctrl+V instead.'; }
});
async function acceptRoster(value, revision, source) {
  if (revision !== rosterRevision) return;
  upload = value; roster(value.readiness, source); runGroup = 'progress'; selectedRun = value.id; selectedLane = ''; clearRunDisplay();
  $('start').textContent = 'Start collection';
  $('draft-status').textContent = value.state === 'ready' ? 'Roster validated and saved. Choose cycles, then start collection.' : 'Review the name conflicts before starting.';
  if(!await refreshRuns())await refreshRun();
}
/* Step 2 for a typed draft whose company names repeat: the server refuses such
   a draft outright ("appears more than once"), so the repeat is found here and
   offered as a decision before anything is sent. */
function draftDuplicates(draft) {
  const groups = new Map();
  for (const company of draft.companies) {
    const key = companyKey(company.name); if (!key) continue;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(company.name.trim());
  }
  return [...groups.values()].filter(names => names.length > 1);
}
function draftReadiness(draft, duplicates) {
  const companies = draft.companies.filter(company => company.name.trim()).map(company => {
    const variants = [...new Map([company.name, ...company.aliases].map(value => value.trim()).filter(Boolean).map(value => [value.replace(/\s+/g, ' '), value])).values()];
    return {company:company.name.trim(), variant_count:variants.length, variants:variants.map(variant => ({variant})), exclusion_count:0, exclusions:[]};
  });
  return {status:'failed', readiness:[{industry:draft.sector.trim(), ready:false, company_count:companies.length,
    variant_count:companies.reduce((n, company) => n + company.variant_count, 0), companies,
    duplicate_companies:duplicates.map(names => names[0]), duplicate_counts:Object.fromEntries(duplicates.map(names => [companyKey(names[0]), names.length])),
    cross_company_variants:[], parse_errors:[]}]};
}
const reviewLabel = $('review-roster').innerHTML;
async function reviewDraft() {
  showError(''); commitPendingAliases();
  const draft = rosterPayload(), duplicates = draftDuplicates(draft), revision = ++rosterRevision;
  upload = null; $('start').disabled = true;
  if (duplicates.length) { roster(draftReadiness(draft, duplicates), 'draft'); setLoadingStep(2); return; }
  $('review-roster').disabled = true; $('review-roster').textContent = 'Checking names…';
  try { await acceptRoster(await api('/api/roster', draft), revision, 'draft'); if (revision === rosterRevision) setLoadingStep(2); }
  catch (error) { if (revision === rosterRevision) showError(error.message); }
  finally { $('review-roster').disabled = false; $('review-roster').innerHTML = reviewLabel; }
}
$('roster-form').addEventListener('submit', event => { event.preventDefault(); reviewDraft(); });
$('review-roster').addEventListener('click', () => {
  if (rosterMode === 'type') { $('roster-form').requestSubmit(); return; }
  if (!uploadedRoster) return;
  showError(''); upload = uploadedRoster; roster(uploadedRoster.readiness, 'upload'); setLoadingStep(2, true);
});
$('choose-cycles').addEventListener('click', () => setLoadingStep(3, true));
$('roster-back').addEventListener('click', () => setLoadingStep(loadingStep - 1, true));
$('roster-mode-type').addEventListener('click', () => setRosterMode('type'));
$('roster-mode-upload').addEventListener('click', () => setRosterMode('upload'));
function setRosterMode(mode) {
  rosterMode = mode;
  $('roster-mode-type').setAttribute('aria-pressed', String(mode === 'type'));
  $('roster-mode-upload').setAttribute('aria-pressed', String(mode === 'upload'));
  $('roster-form').hidden = mode !== 'type'; $('roster-upload').hidden = mode !== 'upload';
  renderUploadPane(); renderSetupSummary(); renderLoadingCoverage();
}
function setLoadingStep(step, focus = false) {
  loadingStep = Math.min(3, Math.max(1, step));
  $('roster-panel').dataset.step = String(loadingStep);
  document.querySelectorAll('#loading-steps .loading-step').forEach(item => {
    const n = Number(item.dataset.step), done = n < loadingStep, active = n === loadingStep;
    item.classList.toggle('is-done', done); item.classList.toggle('is-active', active);
    if (active) item.setAttribute('aria-current', 'step'); else item.removeAttribute('aria-current');
    item.querySelector('.loading-step-mark').textContent = done ? '' : String(n);
    item.querySelector('.loading-step-state').textContent = done ? ', done' : active ? ', current step' : n === 4 ? ', opens Collection' : '';
  });
  $('loading-step-roster').hidden = loadingStep !== 1;
  $('upload-review').hidden = loadingStep !== 2;
  $('loading-step-cycles').hidden = loadingStep !== 3;
  $('review-roster').hidden = loadingStep !== 1; $('choose-cycles').hidden = loadingStep !== 2; $('start').hidden = loadingStep !== 3;
  $('roster-back').hidden = loadingStep === 1;
  renderSetupSummary(); renderLoadingCoverage();
  if (focus) {
    const heading = document.querySelector({1:'#loading-step-roster h2', 2:'#upload-review h2', 3:'#loading-step-cycles h2'}[loadingStep]);
    heading.tabIndex = -1; heading.focus({preventScroll:true});
  }
}
function rosterFigures() {
  const fromGroups = groups => ({sectors:groups.map(group => group.industry), companies:groups.reduce((n, g) => n + g.company_count, 0), names:groups.reduce((n, g) => n + g.variant_count, 0)});
  if (loadingStep > 1 && review) return fromGroups(review.readiness?.readiness || []);
  if (rosterMode === 'upload') return fromGroups(uploadedRoster?.readiness?.readiness || []);
  const draft = rosterPayload(), companies = draft.companies.filter(company => company.name.trim());
  const names = companies.reduce((n, company) => n + new Set([company.name, ...company.aliases].map(value => value.trim().replace(/\s+/g, ' ')).filter(Boolean)).size, 0);
  return {sectors:[draft.sector.trim()].filter(Boolean), companies:companies.length, names};
}
function renderSetupSummary() {
  const figures = rosterFigures(), cycles = checkedCycles().length, sectors = figures.sectors.join(', ');
  const counts = `${plural(figures.companies, 'company', 'companies')} · ${plural(figures.names, 'name')}`;
  $('setup-summary').innerHTML = loadingStep === 1
    ? `<p><b>Sector:</b> ${sectors ? escapeHtml(sectors) : '<span class="loading-summary-empty">not named yet</span>'}</p><p>${counts} to search</p>`
    : `<p><b>${escapeHtml(sectors)}</b> · ${counts}${loadingStep === 3 ? ' · ' + plural(cycles, 'cycle') : ''}</p>`;
  const open = currentDecisions.length;
  $('setup-note').textContent = loadingStep === 1 ? 'Nothing is collected until you press Start collection.' : open ? `Settle ${plural(open, 'name')} to continue` : 'Every name is settled. Choose cycles next.';
  $('setup-note').hidden = loadingStep === 3;
  $('next-searches').textContent = `Searches ${plural(figures.names, 'name')} across ${plural(cycles, 'cycle')}`;
  const settled = !!upload && review?.readiness?.status === 'succeeded' && !open && (review.readiness.readiness || []).every(group => group.ready);
  $('choose-cycles').disabled = !settled;
  if ($('start').textContent === 'Start collection') $('start').disabled = !settled || !cycles;
  $('review-roster').disabled = rosterMode === 'upload' && !uploadedRoster;
}
/* Every problem /api/roster or /api/upload reports becomes one decision:
   cross_company_variants [{variant, companies}], duplicate_companies [names],
   parse_errors [messages]. */
function decisionsFor(readiness) {
  const decisions = [];
  for (const group of readiness?.readiness || []) {
    for (const item of group.cross_company_variants || []) decisions.push({kind:'variant', sector:group.industry, variant:item.variant, companies:item.companies || []});
    const repeats = new Map();
    for (const name of group.duplicate_companies || []) { const key = companyKey(name); if (!repeats.has(key)) repeats.set(key, []); repeats.get(key).push(name); }
    for (const [key, names] of repeats) decisions.push({kind:'duplicate', sector:group.industry, names, count:group.duplicate_counts?.[key] || Math.max(2, names.length)});
    for (const message of group.parse_errors || []) decisions.push({kind:'error', sector:group.industry, message});
  }
  return decisions;
}
function decisionPlain(decision) {
  if (decision.kind === 'variant') return `${decision.variant} is listed under ${listJoin(decision.companies)}`;
  if (decision.kind === 'duplicate') return `${decision.names[0]} appears ${decision.count === 2 ? 'twice' : decision.count + ' times'}`;
  return decision.message.replace(/\.$/, '');
}
const uploadTableHead = () => (uploadedFile?.table?.[0] || []).map(cell => String(cell).trim().toLowerCase());
function decisionEditable() {
  return review?.source === 'draft' || (review?.source === 'upload' && uploadTableHead().includes('canonical_name'));
}
function decisionActions(decision, index) {
  const button = (action, text, company = '') => `<button type="button" class="secondary" data-decision="${index}" data-action="${action}" data-company="${escapeHtml(company)}">${escapeHtml(text)}</button>`;
  if (decision.kind === 'error' || !decisionEditable()) return button('fix', 'Fix in step 1');
  if (decision.kind === 'duplicate') return button('merge', 'Merge') + button('remove', 'Remove');
  // A search name that IS a company's own name can only be kept for that company.
  const owners = decision.companies.filter(company => nameKey(company) === nameKey(decision.variant));
  const keep = owners.length > 1 ? [] : owners.length ? owners : decision.companies;
  if (!keep.length) return button('fix', 'Fix in step 1');
  return keep.map(company => button('keep', `Keep for ${company}`, company)).join('') + (owners.length ? '' : button('remove', 'Remove'));
}
function renderDecisions() {
  const host = $('roster-decisions'), count = currentDecisions.length, sectors = new Set(currentDecisions.map(d => d.sector)).size;
  host.hidden = !count;
  host.innerHTML = count ? `<h3 id="roster-decisions-title"><span class="loading-alert" aria-hidden="true">!</span>${plural(count, 'name')} ${count === 1 ? 'needs' : 'need'} a decision</h3><ul aria-labelledby="roster-decisions-title">` + currentDecisions.map((decision, index) => {
    const text = decision.kind === 'variant' ? `<strong>${escapeHtml(decision.variant)}</strong> is listed under ${listJoin(decision.companies.map(escapeHtml))}`
      : decision.kind === 'duplicate' ? `<strong>${escapeHtml(decision.names[0])}</strong> appears ${decision.count === 2 ? 'twice' : decision.count + ' times'}` : escapeHtml(decision.message);
    return `<li class="loading-decision"><p>${sectors > 1 ? `<small>${escapeHtml(decision.sector)}</small>` : ''}${text}</p><div class="loading-decision-actions">${decisionActions(decision, index)}</div></li>`;
  }).join('') + '</ul>' : '';
}
function roster(readiness, source = review?.source || 'saved') {
  review = {readiness, source};
  const groups = readiness.readiness || [];
  const count = groups.reduce((n,g) => n + g.company_count, 0);
  const variants = groups.reduce((n,g) => n + g.variant_count, 0);
  $('roster-counts').textContent = `${count} ${count === 1 ? 'company' : 'companies'} · ${variants} search names · ${groups.length} ${groups.length === 1 ? 'sector' : 'sectors'}`;
  currentDecisions = decisionsFor(readiness);
  renderDecisions();
  const flagged = new Set(currentDecisions.filter(d => d.kind === 'variant').map(d => nameKey(d.variant)));
  const repeated = new Set(currentDecisions.filter(d => d.kind === 'duplicate').map(d => companyKey(d.names[0])));
  const dot = '<span class="loading-dot" role="img" aria-label="needs a decision"></span>';
  const limit = rosterExpanded ? Infinity : 6;
  let shown = 0;
  const list = groups.map(group => {
    const companies = group.companies.filter(() => shown++ < limit);
    // The company's own name heads its row, so its chips are the other search names (as L2 draws them).
    return companies.length ? `<h3 class="loading-sector">${escapeHtml(group.industry)}</h3>` + companies.map(company => `<div class="roster-company"><b>${escapeHtml(company.company)}${repeated.has(companyKey(company.company)) || flagged.has(nameKey(company.company)) ? dot : ''}</b><ul class="loading-chips">` +
      company.variants.filter(v => v.variant.trim() !== company.company.trim()).map(v => `<li class="loading-chip">${escapeHtml(v.variant)}${flagged.has(nameKey(v.variant)) ? dot : ''}</li>`).join('') +
      company.exclusions.map(name => `<li class="loading-chip loading-chip--exclude">Exclude: <span>${escapeHtml(name)}</span></li>`).join('') + '</ul></div>').join('') : '';
  }).join('');
  const more = count > 6 ? `<button type="button" id="roster-more" class="loading-more" aria-expanded="${rosterExpanded}">${rosterExpanded ? 'Show fewer companies' : `and ${plural(count - 6, 'more company', 'more companies')}`}</button>` : '';
  $('roster-list').innerHTML = list + more;
  renderSetupSummary(); renderCycleCards();
}
$('roster-list').addEventListener('click', event => {
  if (!event.target.closest('#roster-more') || !review) return;
  rosterExpanded = !rosterExpanded; roster(review.readiness, review.source); $('roster-more')?.focus();
});
function draftEdit(decision, action, company) {
  const rows = [...document.querySelectorAll('.company-entry')];
  const rowName = row => row.querySelector('.company-name').value;
  if (decision.kind === 'variant') {
    const key = nameKey(decision.variant);
    for (const row of rows) if (action === 'remove' || companyKey(rowName(row)) !== companyKey(company))
      row.querySelectorAll('.loading-alias').forEach(chip => { if (nameKey(chip.dataset.value) === key) chip.remove(); });
  } else if (decision.kind === 'duplicate') {
    const [first, ...rest] = rows.filter(row => companyKey(rowName(row)) === companyKey(decision.names[0]));
    if (action === 'merge') for (const row of rest) for (const alias of rowAliases(row))
      if (!rowAliases(first).some(value => value.toLocaleLowerCase() === alias.toLocaleLowerCase())) first.querySelector('.loading-aliases').append(aliasChip(alias));
    rest.forEach(row => row.remove());
  }
  numberCompanies(); saveDraft();
}
function tableEdit(decision, action, company) {
  const table = uploadedFile.table, head = uploadTableHead();
  const canonical = head.indexOf('canonical_name'), alias = head.indexOf('alias');
  let body = table.slice(1);
  if (decision.kind === 'variant' && alias >= 0) {
    const key = nameKey(decision.variant);
    for (const row of body) if (nameKey(row[alias]) === key && (action === 'remove' || companyKey(row[canonical]) !== companyKey(company))) row[alias] = '';
  } else if (decision.kind === 'duplicate') {
    const keep = decision.names[0], key = companyKey(keep);
    if (action === 'merge') body.forEach(row => { if (companyKey(row[canonical]) === key) row[canonical] = keep; });
    else body = body.filter(row => companyKey(row[canonical]) !== key || String(row[canonical]).trim() === keep);
  }
  uploadedFile.table = [table[0], ...body];
}
$('roster-decisions').addEventListener('click', async event => {
  const button = event.target.closest('button[data-decision]');
  const decision = button && currentDecisions[Number(button.dataset.decision)];
  if (!decision || decisionBusy) return;
  const {action, company} = button.dataset;
  if (action === 'fix') { setRosterMode(review?.source === 'upload' ? 'upload' : 'type'); setLoadingStep(1, true); return; }
  decisionBusy = true; $('roster-decisions').querySelectorAll('button').forEach(item => { item.disabled = true; });
  try {
    if (review.source === 'draft') { draftEdit(decision, action, company); await reviewDraft(); }
    else { tableEdit(decision, action, company); await uploadRoster(null); }
  } finally { decisionBusy = false; $('roster-decisions').querySelectorAll('button').forEach(item => { item.disabled = false; }); }
});
/* Step 1's spreadsheet (board L1). The file goes to /api/upload exactly as
   before ({filename, content}); a CSV or TSV is also read here, so the column
   check names the real headers and a step-2 decision can edit its rows and
   send the edited table under the same file name. */
function parseTable(text, delimiter) {
  const rows = []; let row = [], cell = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) { if (c !== '"') cell += c; else if (text[i + 1] === '"') { cell += '"'; i++; } else quoted = false; }
    else if (c === '"' && cell === '') quoted = true;
    else if (c === delimiter) { row.push(cell); cell = ''; }
    else if (c === '\n' || c === '\r') { if (c === '\r' && text[i + 1] === '\n') i++; row.push(cell); rows.push(row); row = []; cell = ''; }
    else cell += c;
  }
  if (cell !== '' || row.length) { row.push(cell); rows.push(row); }
  return rows;
}
function tableText(rows, delimiter) {
  return rows.map(row => row.map(cell => { const value = String(cell ?? ''); return /["\t,\r\n]/.test(value) ? '"' + value.replaceAll('"', '""') + '"' : value; }).join(delimiter)).join('\r\n') + '\r\n';
}
function base64Text(text) {
  const bytes = new TextEncoder().encode(text); let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}// file: a File from the picker or a drop; null re-sends the edited table.
async function uploadRoster(file) {
  showError(''); const revision = ++rosterRevision, again = !file;
  upload = null; uploadedRoster = null; $('start').disabled = true;
  if (file) uploadedFile = {name:file.name, table:null, delimiter:/\.tsv$/i.test(file.name) ? '\t' : ','};
  Object.assign(uploadedFile, {error:'', checking:true});
  if (!again) setRosterMode('upload'); else renderUploadPane();
  try {
    let content;
    if (again) content = base64Text(tableText(uploadedFile.table, uploadedFile.delimiter));
    else {
      if (file.size > 5000000) throw new Error('Upload a file smaller than 5 MB.');
      // One read: the same bytes are sent (base64, as before) and, for a CSV or TSV, parsed here.
      const bytes = new Uint8Array(await file.arrayBuffer().catch(() => { throw new Error('The file could not be read. Choose it again.'); }));
      if (/\.(csv|tsv)$/i.test(file.name)) uploadedFile.table = parseTable(new TextDecoder().decode(bytes).replace(/^﻿/, ''), uploadedFile.delimiter);
      let binary = '';
      for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      content = btoa(binary);
    }
    const value = await api('/api/upload', {filename:uploadedFile.name, content});
    if (revision !== rosterRevision) return;
    uploadedRoster = value;
    await acceptRoster(value, revision, 'upload');
  } catch (error) { if (revision === rosterRevision) { uploadedFile.error = error?.message || 'The file could not be uploaded.'; if (again) showError(uploadedFile.error); } }
  finally { if (revision === rosterRevision) { uploadedFile.checking = false; renderUploadPane(); renderSetupSummary(); renderLoadingCoverage(); } }
}
$('file').addEventListener('change', () => { const file = $('file').files[0]; $('file').value = ''; if (file) uploadRoster(file); });
for (const zone of [$('roster-drop'), $('upload-drop')]) {
  zone.addEventListener('click', () => $('file').click());
  zone.addEventListener('dragover', event => { event.preventDefault(); zone.classList.add('is-over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('is-over'));
  zone.addEventListener('drop', event => { event.preventDefault(); zone.classList.remove('is-over'); const file = event.dataTransfer?.files?.[0]; if (file) uploadRoster(file); });
}
$('upload-replace').addEventListener('click', () => $('file').click());
$('upload-remove').addEventListener('click', () => {
  rosterRevision++; uploadedRoster = null; uploadedFile = null;
  if (review?.source === 'upload') { review = null; upload = null; }
  setRosterMode('upload'); $('upload-drop').focus();
});
const COLUMN_CHECKS = [['canonical_name', 'Company name', true], ['alias', 'Other names'], ['sector', 'Sector'], ['exclusion', 'Excluded names']];
function columnChecks() {
  const head = uploadTableHead(), groups = uploadedRoster?.readiness?.readiness || [];
  const missing = /canonical_name/.test(uploadedFile.error || '');
  if (uploadedFile.table) return COLUMN_CHECKS.map(([column, label, required]) => {
    const name = column === 'sector' && !head.includes('sector') && head.includes('industry') ? 'industry' : column;
    return {name, label, state:head.includes(name) ? 'ok' : required ? 'missing' : 'absent'};
  });
  // An Excel roster is read by the server only; its columns are inferred from what it returned.
  if (!groups.length) return missing ? [{name:'canonical_name', label:'Company name', state:'missing'}] : [];
  const companies = groups.flatMap(group => group.companies);
  const seen = {canonical_name:true, alias:companies.some(c => c.variant_count > 1), sector:groups.some(g => g.industry !== 'Uploaded names'), exclusion:companies.some(c => c.exclusions.length)};
  return COLUMN_CHECKS.map(([column, label]) => ({name:column, label, state:seen[column] ? 'ok' : 'absent'}));
}
function renderUploadPane() {
  const file = uploadedFile, groups = uploadedRoster?.readiness?.readiness || [];
  $('upload-drop').hidden = !!file; $('upload-result').hidden = !file;
  if (!file) { $('upload-note').hidden = true; return; }
  $('upload-file-name').textContent = file.name;
  const rows = file.table ? file.table.slice(1).filter(row => row.some(cell => String(cell).trim())).length : null;
  const companies = groups.reduce((n, g) => n + g.company_count, 0);
  $('upload-file-meta').textContent = file.checking ? 'Checking names…' : file.error ? 'Not loaded' : [rows == null ? '' : plural(rows, 'row'), plural(companies, 'company', 'companies'), plural(groups.length, 'sector')].filter(Boolean).join(' · ');
  $('upload-columns').innerHTML = columnChecks().map(check => `<li class="loading-column" data-state="${check.state}"><i aria-hidden="true"></i><span>${escapeHtml(check.name)} -&gt; ${escapeHtml(check.label)}</span>${check.state === 'ok' ? '' : `<span class="sr-only">${check.state === 'missing' ? ', missing' : ', not in this file'}</span>`}</li>`).join('');
  const all = groups.flatMap(group => group.companies.map(company => ({...company, sector:group.industry})));
  const chips = values => values.map(value => `<span class="loading-chip">${escapeHtml(value)}</span>`).join('') || '-';
  $('upload-preview').innerHTML = all.length ? '<div class="table-scroll loading-preview"><table><thead><tr><th scope="col">Company name</th><th scope="col">Other names</th><th scope="col">Sector</th><th scope="col">Excluded</th></tr></thead><tbody>' +
    all.slice(0, 6).map(company => `<tr><td>${escapeHtml(company.company)}</td><td>${chips(company.variants.slice(1).map(v => v.variant))}</td><td>${escapeHtml(company.sector)}</td><td>${chips(company.exclusions)}</td></tr>`).join('') +
    (all.length > 6 ? `<tr class="loading-preview-more"><td colspan="4">and ${plural(all.length - 6, 'more company', 'more companies')}</td></tr>` : '') + '</tbody></table></div>' : '';
  const decisions = decisionsFor(uploadedRoster?.readiness);
  const note = file.error || (decisions.length === 1 ? `${decisionPlain(decisions[0])}. You can settle it in step 2.` : decisions.length ? `${plural(decisions.length, 'name')} need a decision. You can settle them in step 2.` : '');
  $('upload-note').hidden = !note;
  $('upload-note').innerHTML = note ? `<span class="loading-alert" aria-hidden="true">!</span><span>${escapeHtml(note)}</span>` : '';
}
function renderCycleCards() {
  const sectors = rosterSectors().map(sector => String(sector || '').trim()).filter(Boolean);
  document.querySelectorAll('.loading-cycle-status').forEach(host => {
    const cycle = Number(host.dataset.cycle);
    const lines = (sectors.length ? sectors : ['']).map(sector => {
      const row = sector && (savedCoverage.find(r => r.industry === sector && r.cycle === cycle) || savedCoverage.find(r => r.industry.toLocaleLowerCase() === sector.toLocaleLowerCase() && r.cycle === cycle));
      const state = !row ? 'none' : row.verified_data_through && ['current','stale'].includes(row.freshness) ? row.freshness : 'unverifiable';
      const date = row ? escapeHtml(MentatCoverage.formatDate(row.verified_data_through)) : '';
      const text = {current:`<b>Saved:</b> verified through ${date}`, stale:`<b>Saved:</b> verified through ${date} · stale`, unverifiable:'Saved: coverage unverifiable', none:'Not collected yet'}[state];
      return `<span class="loading-saved" data-state="${state}"><i aria-hidden="true"></i><span>${sectors.length > 1 ? `<small>${escapeHtml(sector)}</small>` : ''}${text}</span></span>`;
    }).join('');
    if (host.innerHTML !== lines) host.innerHTML = lines;
  });
}
function renderKeyStatus(available, configured, active, stored, message) {
  $('key-status').textContent = available === false ? message : configured ? `${number(active)} enabled · ${number(stored - active)} disabled` : 'No enabled FEC keys saved';
  $('key-status').dataset.state = available === false ? 'unavailable' : configured ? 'ok' : 'none';
  if (!keysTouched && !configured && $('key-editor').hidden) { $('key-editor').hidden = false; $('keys-toggle').setAttribute('aria-expanded', 'true'); }
}
$('keys-toggle').addEventListener('click', () => {
  keysTouched = true; const open = $('key-editor').hidden;
  $('key-editor').hidden = !open; $('keys-toggle').setAttribute('aria-expanded', String(open));
  if (open) $('api-key').focus();
});
async function api(path, data) {
  if(readOnly && data !== undefined)throw new Error('This preview is read-only while the original console manages collection.');
  const response = await fetch(path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-Mentat-Token':token}, body:JSON.stringify(data)});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || 'The request could not be completed.');
  return body;
}
let keySave = null;
$('save-keys').addEventListener('click', async () => {
  $('save-keys').disabled = true;
  keySave = api('/api/keys', {keys:$('api-key').value});
  try { const result = await keySave; $('api-key').value = ''; $('key-hint').textContent = `${result.active_key_count} enabled keys; ${result.stored_key_count - result.active_key_count} disabled. Saved with Windows encryption.`;
    renderKeyStatus(true, result.active_key_count > 0, result.active_key_count, result.stored_key_count); }
  catch (error) { showError(error.message); }
  finally { keySave = null; $('save-keys').disabled = false; }
});
$('clear-quality').addEventListener('click', async () => {
  try { await api('/api/quality/acknowledge', {id:selectedRun, warning_ids:warningIds}); await refreshRun(); }
  catch (error) { showError(error.message); }
});
$('start').addEventListener('click', async () => {
  if (!upload) return;
  $('start').disabled = true; $('start').textContent = 'Starting…'; showError('');
  try {
    if (keySave) await keySave;
    await api('/api/start', {id:upload.id, cycles:[...document.querySelectorAll('input[name=cycle]:checked')].map(c => Number(c.value)), api_key:$('api-key').value});
    $('api-key').value = ''; $('start').textContent = 'Collection started'; upload = null;
    // Step 4 is Collection. Loading returns to step 1 for the next roster; the
    // started upload is not offered again.
    review = null; uploadedRoster = null; uploadedFile = null; renderUploadPane(); setLoadingStep(1);
    showView('collection');
    await refreshRun();
  } catch (error) { showError(error.message); $('start').disabled = false; $('start').textContent = 'Start collection'; }
});
$('stop').addEventListener('click', async event => {
  if (!selectedRun) return;
  const force = !event.shiftKey;
  const question = force
    ? 'Stop this collection? In-flight lanes are terminated. Lane data is kept and the run can be resumed.'
    : 'Stop this collection gracefully? In-flight lanes are allowed to finish first, and the run cannot be un-stopped.';
  if (!confirm(question)) return;
  try {
    const result = await api('/api/stop', {id:selectedRun, force});
    await refreshRun();
    if (result.ok === false) showError(result.message || 'The collection could not be stopped.');
  }
  catch (error) { showError(error.message); }
});
$('resume-run').addEventListener('click', async () => {
  if (!selectedRun || resumeBusy) return;
  resumeBusy = true; $('resume-run').disabled = true; showError('');
  try {
    await api('/api/resume', {id:selectedRun});
    runGroup = 'progress'; showView('collection');
    if (!await refreshRuns()) await refreshRun();
    showStatus('Resuming the saved collection. Company/cycle work that already succeeded is kept; only unfinished work is collected again.');
  }
  catch (error) { showError(error.message); }
  finally { resumeBusy = false; }
});
$('apply-workers').addEventListener('click', async () => {
  if (!selectedRun) return;
  try {
    const workers = Number($('worker-target').value);
    await api('/api/workers', {id:selectedRun, workers});
    await refreshRun();
    showStatus(`Workers set to ${workers} - takes effect within a few seconds`);
  }
  catch (error) { showError(error.message); }
});
$('runs').addEventListener('change', async () => { selectedRun = $('runs').value; selectedLane = ''; clearRunDisplay(); renderRunLibrary(); await refreshRun().catch(e => showError(e.message)); });
$('lanes').addEventListener('change', async () => { selectedLane = $('lanes').value; await refreshRun().catch(e => showError(e.message)); });
function collectionGroup(run) {
  return run.archived ? 'archived' : ['completed','failed','stopped','launch failed'].includes(run.state) ? 'history' : 'progress';
}
function collectionChoice(run) {
  const created = new Date(typeof run.created_at === 'number' ? run.created_at*1000 : run.created_at || '');
  return [run.name,label(run.state),Number.isFinite(created.getTime()) ? created.toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit'}) : ''].filter(Boolean).join(' · ');
}
function clearRunDisplay() {
  timerRun = null; $('monitor').hidden = true; $('resume-setup').hidden = true;
  $('run-readiness-note').hidden = true; $('run-details').hidden = true; $('run-card-note').hidden = true;
  $('run-subtitle').textContent = ''; closeWorkingFolders();
  for (const id of ['stop','resume-run','apply-workers','worker-target','clear-quality','view-partial-results']) $(id).disabled = true;
  $('run-title').textContent = selectedRun ? 'Loading collection…' : 'No collections in this folder';
  setRunPill(selectedRun ? 'loading' : 'none');
}
function renderRunLibrary() {
  if (!runGroup) runGroup = runCatalog.some(run => collectionGroup(run)==='progress') || !runCatalog.length ? 'progress' : 'history';
  const choices = runCatalog.filter(run => collectionGroup(run) === runGroup);
  const previous = selectedRun;
  if (!choices.some(run => run.id === selectedRun)) selectedRun = choices[0]?.id || '';
  if (previous !== selectedRun) { selectedLane = ''; clearRunDisplay(); }
  setHtml('runs', choices.map(run => `<option value="${escapeHtml(run.id)}">${escapeHtml(collectionChoice(run))}</option>`).join('') || '<option value="">No collections in this folder</option>');
  $('runs').value = selectedRun;
  document.querySelectorAll('[data-run-group]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.runGroup === runGroup));
    $('run-count-' + button.dataset.runGroup).textContent = runCatalog.filter(run => collectionGroup(run) === button.dataset.runGroup).length;
  });
  const selected = choices.find(run => run.id === selectedRun);
  $('archive-run').textContent = selected?.archived ? 'Restore run' : 'Archive run';
  $('archive-run').disabled = runArchiveBusy || readOnly || !selected || (!selected.archived && !selected.can_archive);
  $('archive-run').title = !selected ? 'Choose a collection' : !selected.archived && !selected.can_archive ? 'Active collections must finish or stop before archiving' : 'Hide or restore this run in the collection list; keep its files';
  $('run-library-note').textContent = {progress:'Rosters and collection work waiting or running. Checkpoints stay in the collection working folder.',history:'Finished and stopped collections. Archive a run to remove it from this list; its files remain available.',archived:'Archived runs are hidden from the main list. Restore one whenever you need its collection records.'}[runGroup];
  // B22: the folders are tabs. The run select sits in a compact row when In
  // progress holds more than one run; History and Archived list their runs as
  // compact cards, newest first (/api/runs order), with the selected run open
  // in full in its place. Archive/Restore is a button on the run card.
  const listed = runGroup !== 'progress';
  $('run-toolbar').hidden = listed || choices.length < 2;
  $('archive-run').hidden = !selected || readOnly || (!selected.archived && !selected.can_archive);
  renderRunHistory(listed ? choices : []);
  $('run-card').hidden = !selectedRun; $('empty').hidden = !!selectedRun;
  $('empty-title').textContent = {progress:'Start with a list of names.', history:'No finished collections yet.', archived:'No archived collections.'}[runGroup];
  if (!selectedRun) {
    $('monitor').hidden = true; $('resume-setup').hidden = true; $('run-details').hidden = true; $('run-card-note').hidden = true;
    $('run-title').textContent = 'No collections in this folder'; setRunPill('none');
  }
  return previous !== selectedRun;
}
document.querySelectorAll('[data-run-group]').forEach(button => button.addEventListener('click', async () => {
  runGroup = button.dataset.runGroup; renderRunLibrary(); await refreshRun().catch(error=>showError(error.message));
}));
$('archive-run').addEventListener('click', async () => {
  const run = runCatalog.find(run=>run.id===selectedRun); if (!run || runArchiveBusy) return;
  runArchiveBusy = true; $('archive-run').disabled = true;
  try { await api('/api/run-archive', {id:run.id, archived:!run.archived}); if(!await refreshRuns())await refreshRun(); showStatus(run.archived ? 'Run restored.' : 'Run archived. Its files are preserved.'); }
  catch (error) { showError(error.message); }
  finally { runArchiveBusy = false; renderRunLibrary(); }
});
$('view-completed-reports').addEventListener('click', () => showView('graph'));
// Build 08 (board B22): the run card. The header pill, the stage stepper and
// the per-company table read only what /api/run already returns: the run and
// industry stages, every worker's finished-task receipts (company_tasks),
// the selected worker's checkpoint rows (preview.progress) and the roster.
function runPill(state) {
  if (state === 'completed') return {kind:'completed', text:'Completed'};
  if (['failed','launch failed'].includes(state)) return {kind:'failed', text:'Failed'};
  if (state === 'stopped') return {kind:'stopped', text:'Stopped'};
  if (state === 'ready') return {kind:'draft', text:'Ready'};
  if (state === 'needs review') return {kind:'draft', text:'Needs review'};
  return {kind:'running', text:'Running'};
}
function setRunPill(pill) {
  const value = typeof pill === 'string' ? ({none:{kind:'none', text:''}, loading:{kind:'draft', text:'Loading'}})[pill] : pill;
  $('stage').textContent = value.text; $('stage').dataset.runState = value.kind;
}
function parseRunTime(value) {
  if (value == null || value === '') return NaN;
  if (typeof value === 'number') return value * 1000;
  // R writes %z offsets without a colon (-0400); Date wants -04:00.
  return new Date(String(value).replace(/([+-]\d\d)(\d\d)$/, '$1:$2')).getTime();
}
function runDate(ms) {
  return Number.isFinite(ms) ? new Date(ms).toLocaleString(undefined, {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'}) : '';
}
function checkpointTime(ms) {
  if (!Number.isFinite(ms)) return '';
  const date = new Date(ms), today = date.toDateString() === new Date().toDateString();
  return date.toLocaleString(undefined, today ? {hour:'numeric', minute:'2-digit'} : {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'});
}
function etaText(value) {
  const match = /^About (\d+):(\d\d):(\d\d)$/.exec(value.remaining);
  if (match) {
    const minutes = Number(match[1]) * 60 + Number(match[2]) + (Number(match[3]) >= 30 ? 1 : 0);
    return minutes < 1 ? 'under a minute left' : minutes < 60 ? `about ${minutes} min left` : `about ${Math.floor(minutes / 60)} h ${minutes % 60} min left`;
  }
  if (['Estimating…','Waiting for checkpoint','Estimate exceeded'].includes(value.remaining) && value.elapsed !== 'Unavailable') return `Elapsed ${value.elapsed}`;
  return value.remaining;
}
// Stepper mapping (Start-MentatResilientIndustryReports.ps1 stages and the
// fanout's own): the roster's names are loaded and their search work is set
// up while a sector is starting/initializing; every search task fetches its
// receipts during collecting; the merge, coverage and raw-data gates verify;
// workbook generation publishes.
const RUN_STEPS = ['Search names','Fetch receipts','Verify','Publish'];
const RUN_STEP_OF_STAGE = {starting:0, pending:0, preparing:0, initializing:0, collecting:1, recovering:1, splitting:1, merging:1,
  finalizing_database:2, coverage_gate:2, raw_data_gate:2, generator_queued:3, pipelining:3, waiting_for_generators:3};
function runStep(run, status) {
  if (run.state === 'completed' || status.stage === 'completed') return 4;
  const active = (status.industries || []).map(industry => RUN_STEP_OF_STAGE[industry.stage]).filter(step => step != null);
  if (active.length) return Math.min(...active);
  return RUN_STEP_OF_STAGE[status.stage] ?? RUN_STEP_OF_STAGE[run.state] ?? null;
}
function renderRunSteps(step, halted) {
  const check = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.5 12.5l3.6 3.6 7.4-8.2"/></svg>';
  setHtml('run-steps', RUN_STEPS.map((name, index) => {
    const state = step == null ? 'upcoming' : index < step ? 'done' : index === step ? (halted ? 'halted' : 'current') : 'upcoming';
    const spoken = {done:'done', current:'in progress', halted:'stopped here', upcoming:'not started'}[state];
    return `<li class="run-step run-step--${state}"${state === 'current' ? ' aria-current="step"' : ''}><span class="run-step-mark" aria-hidden="true">${state === 'done' ? check : ''}</span><span class="run-step-name">${name}<span class="sr-only">, ${spoken}</span></span></li>`;
  }).join(''));
}
// The run's election cycles: /api/run publishes them from the run's status,
// its saved roster or its fanout (run.cycles); older payloads carry only
// status.cycles, which a stopped or legacy run may never have written.
function runCycles(run, status) {
  return (Array.isArray(run.cycles) && run.cycles.length ? run.cycles : status.cycles) || [];
}
function runCompanies(run, status, terminal) {
  const cycles = runCycles(run, status), companies = new Map();
  const key = name => String(name || '').normalize('NFKC').trim().toLocaleLowerCase('en-US');
  const entry = name => {
    const id = key(name);
    if (!companies.has(id)) companies.set(id, {name:String(name), finished:0, failed:0, receipts:0, last:NaN, active:false, seen:false, units:new Set()});
    return companies.get(id);
  };
  const later = (item, value) => { const ms = parseRunTime(value); if (Number.isFinite(ms) && !(ms <= item.last)) item.last = ms; };
  for (const group of run.readiness?.readiness || []) for (const company of group.companies || []) if (company.company) entry(company.company);
  // company_tasks is every worker's newest attempt per task, earlier
  // invocations included; a worker's current state lists only its latest.
  const tasks = Array.isArray(run.company_tasks) ? run.company_tasks : (run.lanes || []).flatMap(lane => lane.state?.tasks || []);
  for (const task of tasks) {
    if (!task.company) continue;
    const item = entry(task.company); item.seen = true; item.units.add(String(task.cycle));
    item.finished += 1; if (task.status !== 'succeeded') item.failed += 1;
    item.receipts += Number(task.rows) || 0; later(item, task.ended_at);
  }
  for (const lane of run.lanes || []) {
    const state = lane.state || {};
    if (state.status === 'running' && state.current_company) { const item = entry(state.current_company); item.active = true; item.seen = true; }
  }
  // Checkpoint rows of the selected worker add what its unfinished tasks have
  // fetched so far; a finished task's rows are already in its receipt.
  for (const row of run.preview?.progress || []) {
    if (!row.company) continue;
    const item = entry(row.company); item.seen = true; later(item, row.updated_at);
    if (!item.units.has(String(row.cycle))) item.receipts += Number(row.rows_seen) || 0;
  }
  return [...companies.values()].map(item => {
    const pct = cycles.length && item.seen ? Math.min(100, Math.round(100 * item.finished / cycles.length)) : null;
    return {...item, pct, queued:!item.seen, waiting:terminal ? 'Not started' : 'Queued'};
  });
}
function renderRunCompanies(run, status, terminal) {
  const rows = runCompanies(run, status, terminal);
  setHtml('run-companies', rows.map(row => row.queued
    ? `<tr class="run-company run-company--queued"><td title="${escapeHtml(row.name)}">${escapeHtml(row.name)}</td><td><span class="run-company-progress"><span class="run-bar"></span><span class="run-pct">${row.waiting}</span></span></td><td></td><td></td></tr>`
    : `<tr class="run-company"><td title="${escapeHtml(row.name)}">${escapeHtml(row.name)}</td><td><span class="run-company-progress"><span class="run-bar"><span class="run-bar-fill" data-pct="${row.pct ?? 0}"></span></span><span class="run-pct">${row.pct == null ? 'In progress' : row.pct + '%'}${row.failed ? ` <span class="run-company-failed">· ${number(row.failed)} failed</span>` : ''}</span></span></td><td>${number(row.receipts)}</td><td>${escapeHtml(checkpointTime(row.last))}</td></tr>`).join('')
    || `<tr class="run-companies-empty"><td colspan="4">${terminal ? 'No company checkpoints were recorded for this run.' : 'Companies appear here when the roster is read.'}</td></tr>`);
  document.querySelectorAll('#run-companies [data-pct]').forEach(bar => { bar.style.width = bar.dataset.pct + '%'; });
  const lanes = (run.lanes || []).length, selected = (run.lanes || []).find(lane => lane.id === run.selected_lane);
  $('run-companies-note').hidden = lanes < 2;
  // company_tasks can include a worker whose current status never landed and
  // so is missing from the Workers list; the note does not count them.
  $('run-companies-note').textContent = lanes < 2 ? '' : `Finished tasks come from every worker's receipts; receipts still being fetched come from the ${selected?.label || 'selected'} worker's latest checkpoints (Workers, below).`;
}
function runHistoryItem(run) {
  const pill = runPill(run.state), created = runDate(parseRunTime(run.created_at)), id = escapeHtml(run.id);
  return `<li class="run-history-item"><button type="button" class="run-history-open" data-run-id="${id}"><span class="run-history-title">${escapeHtml(run.name)}</span>${created ? `<span class="run-history-date">Created ${escapeHtml(created)}</span>` : ''}<span class="run-pill" data-run-state="${pill.kind}">${pill.text}</span></button><button type="button" class="run-history-reports" data-run-id="${id}" data-open="reports" aria-label="Reports for ${escapeHtml(run.name)}">Reports</button></li>`;
}
// The selected run opens in full in its place in the list: the runs above it
// stay above the card, the older ones follow its details.
function renderRunHistory(runs) {
  const index = runs.findIndex(run => run.id === selectedRun);
  const before = index < 0 ? runs : runs.slice(0, index), after = index < 0 ? [] : runs.slice(index + 1);
  $('run-history').hidden = !before.length; $('run-history-more').hidden = !after.length;
  setHtml('run-history', before.map(runHistoryItem).join(''));
  setHtml('run-history-more', after.map(runHistoryItem).join(''));
}
function renderRunCard(run, context) {
  const {status, fanout, terminal, displayStage, active, notStarted} = context;
  const industries = status.industries || [];
  const starts = industries.map(industry => parseRunTime(industry.started_at)).filter(Number.isFinite);
  let started = starts.length ? Math.min(...starts) : NaN, verb = 'Started';
  if (!Number.isFinite(started) && !terminal && typeof run.elapsed_seconds === 'number') started = Date.now() - run.elapsed_seconds * 1000;
  if (!Number.isFinite(started)) { started = parseRunTime(runCatalog.find(item => item.id === run.id)?.created_at); verb = 'Created'; }
  const roster = (run.readiness?.readiness || []).reduce((sum, group) => sum + (Number(group.company_count) || (group.companies || []).length), 0);
  const cycles = runCycles(run, status).join(', ');
  $('run-subtitle').textContent = [Number.isFinite(started) ? `${verb} ${runDate(started)}` : '', roster ? `${number(roster)} ${roster === 1 ? 'company' : 'companies'}` : '', cycles ? `cycles ${cycles}` : ''].filter(Boolean).join(' · ');
  const known = !!fanout.total, pct = known ? Math.min(100, 100 * fanout.completed / fanout.total) : run.state === 'completed' ? 100 : 0;
  $('progress').style.width = pct + '%';
  $('run-percent').textContent = known ? `${Math.floor(pct)}%` : ''; $('run-percent').hidden = !known;
  document.querySelector('#view-collection .run-progress-sep').hidden = !known;
  $('freshness').hidden = !(run.launch_error || (!run.status && !notStarted) || (run.status && !terminal && run.age_seconds > 60));
  renderRunSteps(runStep(run, status), terminal && run.state !== 'completed' && displayStage !== 'completed');
  renderRunCompanies(run, status, terminal);
  const records = (run.preview?.records || []).length;
  $('view-partial-results').textContent = run.state === 'completed' ? 'View records' : 'View partial results';
  $('view-partial-results').disabled = !records;
  $('view-partial-results').title = records ? 'Open the Activity log of stored records' : 'Available once the collection has stored records';
  $('run-details').hidden = notStarted; $('run-card-note').hidden = !active;
  // Archive/Restore sits with the card's own actions: beside Continue setup
  // for a saved roster, in the footer otherwise.
  $('run-setup-actions').hidden = !notStarted;
  const home = notStarted ? $('run-setup-actions') : $('run-controls');
  if ($('archive-run').parentElement !== home) home.insertBefore($('archive-run'), notStarted ? null : $('stopping-banner'));
}
function closeWorkingFolders() { $('working-folders').hidden = true; $('open-working-folder').setAttribute('aria-expanded', 'false'); }
$('open-working-folder').addEventListener('click', () => {
  const open = $('working-folders').hidden;
  $('working-folders').hidden = !open; $('open-working-folder').setAttribute('aria-expanded', String(open));
});
$('view-collection').addEventListener('keydown', event => {
  if (event.key === 'Escape' && !$('working-folders').hidden) { closeWorkingFolders(); $('open-working-folder').focus(); }
});
document.addEventListener('click', event => { if (!$('working-folders').hidden && !event.target.closest('.run-folder')) closeWorkingFolders(); });
$('view-partial-results').addEventListener('click', () => {
  const log = $('collection-activity-details'); log.open = true;
  log.scrollIntoView({block:'start', behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  log.querySelector('summary').focus({preventScroll:true});
});
$('empty-load-sector').addEventListener('click', () => showView('loading', true));
async function openHistoryRun(event) {
  const button = event.target.closest('[data-run-id]'); if (!button) return;
  const reports = button.dataset.open === 'reports';
  if (button.dataset.runId !== selectedRun) {
    selectedRun = button.dataset.runId; selectedLane = ''; clearRunDisplay(); renderRunLibrary();
    $('run-title').focus({preventScroll:reports});
    await refreshRun().catch(error => showError(error.message));
  }
  if (reports) {
    const freshness = $('collection-freshness-details'); freshness.open = true;
    freshness.scrollIntoView({block:'start', behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
    freshness.querySelector('summary').focus({preventScroll:true});
  }
}
$('run-history').addEventListener('click', openHistoryRun);
$('run-history-more').addEventListener('click', openHistoryRun);
async function refreshRuns(initial) {
  const body = initial || await api('/api/runs?include_archived=1');
  savedCoverage = body.coverage || []; renderLoadingCoverage();
  setHtml('sector-options', [...new Set(savedCoverage.map(row => row.industry))].sort().map(name => `<option value="${escapeHtml(name)}"></option>`).join(''));
  runCatalog = body.runs || []; const changed = renderRunLibrary();
  $('collection-directory').textContent = body.collection_directory || 'Local collection workspace';
  $('completed-directory').textContent = body.report_directory || 'Configured completed-report folder';
  if (changed && selectedRun) await refreshRun();
  window.dispatchEvent(new CustomEvent('mentat:runs', {detail:body.runs}));
  renderKeyStatus(body.key_status_available, !!body.key_configured, body.active_key_count, body.stored_key_count, body.key_status_message);
  if(body.key_status_available===false)$('key-hint').textContent=body.key_status_message;
  if (body.key_configured) $('key-hint').textContent = `${body.active_key_count} enabled keys; ${body.stored_key_count - body.active_key_count} disabled. Stored with Windows encryption. Leave blank to use them.`;
  return changed;
}
let timerRun=null, timerSample=0;
function updateTimer() {
  if(!timerRun)return;
  const value=MentatState.timer(timerRun,(performance.now()-timerSample)/1000);
  $('eta').textContent=etaText(value); $('elapsed').textContent=value.elapsed;
}
setInterval(updateTimer,1000);
function render(run) {
  $('run-title').textContent = run.name;
  $('run-card').hidden = false; $('empty').hidden = true;
  setHtml('collection-coverage', MentatCoverage.html(run.fec_coverage || []));
  const status = run.status || {}, fanout = status.fanout || run.completed_collection || {};
  $('worker-panel').hidden = status.kind === 'report_repair';
  $('records-panel').hidden = status.kind === 'report_repair';
  $('collection-activity-details').hidden = status.kind === 'report_repair';
  const terminalStates = ['completed','failed','stopped','launch failed'];
  const terminal = terminalStates.includes(run.state) || terminalStates.includes(status.stage);
  const displayStage = run.state === 'stopped' ? 'stopped' : [run.state,status.stage].find(value => ['failed','launch failed'].includes(value)) || (terminalStates.includes(run.state) ? run.state : status.stage || run.state);
  setRunPill(runPill(terminal ? displayStage : run.state));
  const assurance = run.assurance || [];
  const passed = assurance.filter(check => check.collection === 'passed').length;
  const failed = assurance.some(check => check.collection === 'failed');
  const oldReports = run.reports.some(report => report.quality?.direction_columns === false);
  const changedReports = run.reports.some(report => report.quality?.matches_run === false);
  const allWarnings = run.reports.flatMap(report => (report.quality?.warnings || []).map(warning => ({...warning, report:report.name})));
  const warnings = allWarnings.filter(warning => !warning.reviewed_at);
  const reviewed = allWarnings.filter(warning => warning.reviewed_at);
  warningIds = warnings.filter(warning => warning.severity !== 'fail').map(warning => warning.id);
  $('clear-quality').hidden = !warningIds.length;
  $('clear-quality').disabled = false;
  $('quality-title').textContent = oldReports ? 'Report warning: downstream directions are combined' : failed ? 'Collection coverage checks failed' : passed && passed === assurance.length ? 'Saved collection checks passed · downstream coverage unverified' : 'Collection coverage not yet verified';
  $('quality-detail').textContent = oldReports ? 'An exported workbook lacks separate support and opposition columns. Its party totals may include spending against that party’s candidates.' : changedReports ? 'A workbook has changed since this run. Its current contents do not match the run’s recorded file.' : 'A finished search is not proof that every contribution or downstream transfer was captured.';
  let qualityHtml = assurance.map(check => `<span class="quality-chip">${escapeHtml(check.industry)} · ${escapeHtml(check.collection)}${check.recorded_checks ? ' (' + check.recorded_checks + ' checks)' : ''}</span>`).join('');
  if (warnings.length) {
    $('quality-title').textContent = `${warnings.length} report quality warnings require review`;
    $('quality-detail').textContent = 'The repaired workbooks retain unresolved collection and classification warnings. Open Data Quality in each workbook for the evidence.';
    qualityHtml += `<details><summary>Review ${warnings.length} report warnings</summary>` + warnings.map(warning => `<details><summary>${escapeHtml(warning.report)} · ${escapeHtml(label(warning.check))}</summary><p>${escapeHtml(warning.detail)}</p></details>`).join('') + '</details>';
  }
  if (reviewed.length) {
    if (!warnings.length && !failed && !oldReports && !changedReports) {
      $('quality-title').textContent = 'Saved report warnings reviewed';
      $('quality-detail').textContent = 'The previous notices are cleared. Their evidence remains available below and in each workbook; new or changed report warnings will appear again.';
    }
    qualityHtml += `<details><summary>${reviewed.length} reviewed notices</summary>` + reviewed.map(warning => `<details><summary>${escapeHtml(warning.report)} · ${escapeHtml(label(warning.check))}</summary><p>${escapeHtml(warning.detail)}</p></details>`).join('') + '</details>';
  }
  setHtml('quality-checks', qualityHtml);
  $('run-quality').classList.toggle('reviewed', !!reviewed.length && !warnings.length && !failed && !oldReports && !changedReports);
  $('quality-summary').textContent = warnings.length ? `${warnings.length} report warnings to review` : oldReports ? 'Report warning' : failed ? 'Coverage checks failed' : changedReports ? 'Workbook changed since this run' : reviewed.length ? 'Report warnings reviewed' : 'Coverage unverified';
  setHtml('source-snapshots', (run.source_snapshots || []).map(source => `<li>${escapeHtml(source.name)}: local file modified ${escapeHtml(source.modified_at.slice(0,10))} (${source.age_days} days ago). This is not a verified data-through date.</li>`).join('') || '<li>No local source file dates available.</li>');
  const dateCell = value => escapeHtml(value || 'Unknown');
  setHtml('report-freshness', run.reports.map(report => {
    const fresh = report.freshness || {}, sources = fresh.sources || [];
    return `<details class="report-freshness-card"><summary><strong>${escapeHtml(report.name)}</strong> · Report freshness unknown · ${fresh.verified_source_count || 0}/${sources.length} sources with verified data-through dates</summary><p>Report file modified: ${dateCell(fresh.report_modified_at)}. ${escapeHtml(fresh.note)}</p>` +
      (fresh.issues || []).map(issue => `<p class="freshness-unknown">${escapeHtml(issue)}</p>`).join('') +
      '<div class="table-scroll"><table><thead><tr><th>Source / cycle</th><th>Retrieved</th><th>Local source file modified</th><th>Verified data-through</th><th>Freshness</th><th>Other receipt evidence</th></tr></thead><tbody>' +
      sources.map(source => `<tr><td>${escapeHtml(source.industry || '')} ${escapeHtml(source.source)}<small>${escapeHtml(source.cycle)}</small></td><td>${dateCell(source.retrieved_at)}</td><td>${dateCell(source.file_modified_at)}</td><td>${dateCell(source.verified_data_through)}</td><td class="${source.freshness === 'verified' ? 'freshness-verified' : 'freshness-unknown'}">${source.freshness === 'verified' ? 'Verified date in receipt' : 'Unknown'}</td><td>Checked: ${dateCell(source.checked_at)}<small>Server last modified: ${dateCell(source.server_last_modified)}</small><details><summary>Receipt</summary><p>${escapeHtml(source.receipt)}<br>${escapeHtml(source.local_receipt)}<br>${escapeHtml(source.sha256)}<br>${escapeHtml(source.note)}<br>${escapeHtml(source.verification_evidence)}</p></details></td></tr>`).join('') + '</tbody></table></div></details>';
  }).join('') || '<p class="muted">No completed reports available. Freshness is unknown.</p>');
  const archived = !!runCatalog.find(item=>item.id===run.id)?.archived;
  $('resume-setup').hidden = run.state !== 'ready' || archived;
  $('empty').hidden = true; $('monitor').hidden = false;

  if (!run.status) {
    $('freshness').textContent = run.launch_error || (run.state === 'ready' ? 'Roster validated. Choose cycles and start collection.' : `Run ${label(run.state)}. Waiting for runner status.`);
  } else {
    $('freshness').textContent = terminal ? `Last recorded update: ${status.updated_at || 'unavailable'}` : run.age_seconds > 60 ? `No recent worker update · last checkpoint ${duration(run.age_seconds)} ago. The collector may be waiting, paused, or no longer running.` : `Monitoring · refreshed ${new Date().toLocaleTimeString()} · runner update ${number(run.age_seconds)} seconds ago`;
  }
  const notStarted = ['ready','needs review'].includes(run.state);
  const active = !!selectedRun && !terminal && !notStarted;
  $('monitor').hidden = notStarted;
  $('run-readiness-note').hidden = !notStarted;
  $('run-readiness-note').textContent = archived ? 'Restore this saved roster above before continuing collection setup.' : run.state === 'ready' ? 'Roster saved. Continue setup to choose cycles and start collection, or archive this draft.' : 'This roster needs review before collection. Review the names in Loading.';
  // A resumable run is terminal, so the controls stay visible for it with
  // only Resume enabled: that is where the operator looks for it.
  const resumable = !!run.resumable && !readOnly && !archived;
  // B22 footer: Pause while running when the lanes it keeps let the run be
  // resumed afterwards (a forced stop keeps lane data), otherwise Stop; once
  // stopped, Resume.
  const pausable = active && (run.lanes || []).length > 0;
  $('stop').hidden = !active; $('resume-run').hidden = active || !resumable;
  $('stop').classList.toggle('is-pause', pausable); $('stop-label').textContent = pausable ? 'Pause' : 'Stop';
  $('resume-run').disabled = !resumable || resumeBusy;
  $('resume-run').title = resumable
    ? 'Continue this collection in its own run folder. Lane data already collected is kept and only unfinished company/cycle work is collected again.'
    : run.resumed_from ? `Resumed from run ${run.resumed_from}.`
    : 'Resume is available for a stopped or failed collection with saved lane data';
  const canControl = active && !readOnly;
  const inactiveReason = readOnly ? 'Read-only preview. Manage collection in the original console.' : active ? '' : 'No collection is running';
  $('stop').disabled = !canControl; $('stop').title = canControl ? 'Shift-click to stop gracefully and let in-flight lanes finish first.' : inactiveReason;
  $('worker-target').disabled = !canControl; $('worker-target').title = inactiveReason;
  $('apply-workers').disabled = !canControl; $('apply-workers').title = inactiveReason;
  $('completed').textContent = fanout.total == null ? '—' : `${number(fanout.completed)} / ${number(fanout.total)}`;
  $('workers').textContent = number(fanout.running); $('failed').textContent = number(fanout.failed);
  const recordedWorkers = terminal || run.age_seconds == null || run.age_seconds > 60;
  $('worker-metric-label').textContent = recordedWorkers ? 'Workers at last checkpoint' : 'Active workers';
  const currentWorkers = ((status.industries || []).find(item => item.industry === status.current_industry) || {}).workers;
  if(currentWorkers?.request_pool) $('workers').textContent=number(currentWorkers.active_workers);
  $('worker-detail').textContent = currentWorkers ? `${number(currentWorkers.active_workers)} active / ${number(currentWorkers.requested_workers)} requested` + (currentWorkers.request_pool ? ` · shared fetch workers · ${number(currentWorkers.database_writers)} database coordinators · ${number(currentWorkers.request_pool.queued_requests)} requests queued` : currentWorkers.scheduling === 'company_work_sharing' ? ` · ${number(currentWorkers.pending_companies)} companies waiting · ${number(currentWorkers.company_handoffs)} handoffs` : '') : '';
  if (recordedWorkers && $('worker-detail').textContent) $('worker-detail').textContent = 'Last recorded: ' + $('worker-detail').textContent;
  $('stopping-banner').hidden = terminal || !(run.control && run.control.stop);
  timerRun = run; timerSample = performance.now(); updateTimer();
  renderRunCard(run, {status, fanout, terminal, displayStage, active, notStarted});
  $('phase-description').textContent = terminal ? `${label(displayStage)}. ${status.current_industry ? `Last checkpoint: ${status.current_industry} · ${label(status.stage)}.` : 'Showing saved collection records.'}` : status.current_industry ? `${status.current_industry} · ${label(status.stage)}. Tasks measure company/cycle attempts; successful coverage is verified separately.` : label(displayStage);
  setHtml('industries', (status.industries || []).map(industry => `<div class="industry"><b>${escapeHtml(industry.industry)}</b> ${terminal ? 'Last checkpoint: ' : ''}${escapeHtml(label(industry.stage))}${industry.detail ? '<details><summary>Run details</summary><p>' + escapeHtml(industry.detail) + '</p></details>' : ''}</div>`).join(''));
  const choices = run.lanes.map(lane => `<option value="${escapeHtml(lane.id)}">${escapeHtml(lane.industry)} / ${escapeHtml(lane.label)} / ${escapeHtml(label(lane.state.status))}</option>`).join('') || '<option value="">No workers yet</option>';
  if ($('lanes').innerHTML !== choices) $('lanes').innerHTML = choices;
  selectedLane = run.selected_lane || ''; $('lanes').value = selectedLane;
  const selected = run.lanes.find(l => l.id === selectedLane);
  $('lane-summary').textContent = selected ? `${label(selected.state.status)} · ${selected.state.current_company || 'No current company'} ${selected.state.current_cycle || ''} · ${selected.state.completed || 0}/${selected.state.total || 0} tasks` : 'Waiting for collection workers.';
  const preview = run.preview || {};
  setHtml('checkpoints', (preview.progress || []).map(row => `<tr><td>${escapeHtml(row.company)}</td><td>${escapeHtml(row.cycle)}</td><td>${escapeHtml(label(row.kind))}</td><td>${number(row.rows_seen)}</td><td>${number(row.records_total)}</td><td>${row.stored_complete ? 'Stored' : row.fetched_complete ? 'Fetched' : 'Collecting'}</td></tr>`).join('') || '<tr><td colspan="6">No readable collection checkpoints yet.</td></tr>');
  setHtml('records', (preview.records || []).map(row => `<tr><td>${escapeHtml(row.contributor_name || 'Unreported')}<small>${escapeHtml(row.company)} · ${escapeHtml(row.contribution_receipt_date)}</small></td><td class="number">${currency(row.contribution_receipt_amount)}</td><td>${escapeHtml(({Chief:'Chief','Middle Management':'Middle-Management',Employee:'Employees'})[row.employee_tier] || row.employee_tier || MentatState.titlePosition(row.contributor_occupation))}</td><td>${escapeHtml(row.contributor_occupation || 'Unreported')}</td><td>${escapeHtml(row.committee_name || 'Unreported')}<small>${escapeHtml(row.sub_id)}</small></td></tr>`).join('') || `<tr><td colspan="5">${escapeHtml(preview.message || 'Records will appear as the selected worker stores them.')}</td></tr>`);
  setHtml('events', run.events.slice().reverse().map(event => `<div class="event"><b>${escapeHtml(label(event.event))}</b> ${escapeHtml(event.industry)}<small>${escapeHtml(event.timestamp || event.at || event.time || '')}</small>${escapeHtml(event.detail || event.message || '')}</div>`).join('') || '<p class="muted">No activity recorded yet.</p>');
  setHtml('reports', run.reports.map(report => `<a class="report-link" download="${escapeHtml(report.name)}" href="/api/report?id=${encodeURIComponent(run.id)}&index=${report.index}">${escapeHtml(report.name)} ↗</a><p class="muted">${report.quality?.direction_columns === false ? 'Older layout: support and opposition are combined.' : report.quality?.direction_columns === true ? 'Opposition is shown in a separate column.' : 'Direction columns not verified.'}${report.quality?.matches_run === false ? ' File changed since this run.' : ''}</p>`).join('') || `<p class="muted">${escapeHtml(run.report_source_notice || 'Reports appear after collection and validation succeed.')}</p>`);
}
async function refreshRun() {
  if (!selectedRun) return;
  const requested = selectedRun, lane = selectedLane;
  const run = await api('/api/run?id=' + encodeURIComponent(requested) + '&lane=' + encodeURIComponent(selectedLane));
  if (requested === selectedRun && lane === selectedLane) { render(run); window.dispatchEvent(new CustomEvent('mentat:run', {detail:run})); }
}
async function poll() {
  if (busy || document.hidden || !['loading','collection'].includes(currentView)) return;
  busy = true;
  try { const changed=await refreshRuns(); if(!changed)await refreshRun(); $('connection').textContent = '● Local connection'; }
  catch (error) { $('connection').textContent = 'Connection interrupted · retrying'; }
  finally { busy = false; }
}
async function init() {
  try { const session = await api('/api/session?light=1'); token = session.token; readOnly=!!session.read_only;
    try { renderBuild(session); scheduleBuildPoll(); } catch { }
    evidenceCollectionAvailable=!!session.evidence_collection_available;
    // The collector form lives in the PAC evidence tab's New snapshot drawer
    // now (evidence.js owns that whole tab), so what this session says about
    // the collector is handed over rather than reached for from here.
    window.MentatEvidence?.setCollector(evidenceCollectionAvailable, readOnly);
    if(readOnly)showStatus('Read-only preview · the original console continues managing your active collection.');
    $('connection').textContent = '● Local connection';
    if(['loading','collection'].includes(currentView))poll(); }
  catch (error) { showError(error.message); }
  setInterval(poll, 3000);
}
window.addEventListener('mentat:view',()=>{if(token&&['loading','collection'].includes(currentView))poll();});
$('resume-setup').addEventListener('click', async () => {
  try {
    const value = await api('/api/run?id=' + encodeURIComponent(selectedRun));
    if (value.state !== 'ready') throw new Error('This roster is no longer awaiting collection.');
    rosterRevision++; upload = {id:value.id,readiness:value.readiness}; roster(value.readiness, 'saved');
    $('start').textContent = 'Start collection'; setLoadingStep(2); showView('loading');
  } catch (error) { showError(error.message); }
});
const currency = value => value == null ? 'Unresolved' : Number(value).toLocaleString('en-US', {style:'currency', currency:'USD', maximumFractionDigits:2});
/* The PAC evidence tab - its scope bar, its panes, the saved-snapshot reader and
   the collector form - lives in app/evidence.js (S6). This file keeps the tab
   and split-view plumbing, the `mentat:graph` dispatch Graph uses and
   `MentatEvidence.setCollector`, which hands over what /api/session says about
   the collector. `currency` stays here because every view formats money. */
/* Build stamp and stale-build guard. A console window outlives edits to the
   server tree, so the page states which build answered it, and offers the
   same-port restart the launcher performs. Both elements are built here
   because index.html is owned elsewhere this lane. */
let buildStatus = null, buildPollTimer = 0, buildRestarting = false, buildBannerMode = '';
// The "already current" confirmation clears itself; held here so a second press
// restarts the countdown instead of stacking timers.
let currentNoticeTimer = 0;
function buildStartedText(value) {
  const at = typeof value === 'number' ? new Date(value * 1000) : new Date(value ?? NaN);
  return Number.isFinite(at.getTime()) ? at.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) : 'unknown';
}
function buildStampText(build) {
  const revision = build.git_sha && build.git_sha !== 'unknown' ? String(build.git_sha) : String(build.code_sha256 || 'unknown');
  return `build ${revision.slice(0, 7)} · port ${build.port ?? '?'}`;
}
// Board M4 draws the stamp as one line ("build 862a6c4 · port 8806"); when
// this process started stays readable as the stamp's tooltip.
function buildStampTitle(build) {
  return `Up since ${buildStartedText(build.started_at)}`;
}
function mountBuildElements() {
  if ($('build-stamp')) return;
  const stamp = document.createElement('span');
  stamp.id = 'build-stamp';
  stamp.className = 'build-stamp';
  stamp.setAttribute('role', 'status');
  document.querySelector('.app-navigation')?.append(stamp);
  const banner = document.createElement('div');
  banner.id = 'stale-banner';
  banner.className = 'stale-banner';
  banner.hidden = true;
  banner.setAttribute('role', 'status');
  const message = document.createElement('span');
  message.id = 'stale-banner-message';
  const action = document.createElement('button');
  action.id = 'stale-restart';
  action.type = 'button';
  action.textContent = 'Restart';
  action.addEventListener('click', () => { restartConsole(); });
  banner.append(message, action);
  const workspace = document.querySelector('.workspace'), anchor = $('error');
  if (workspace && anchor && anchor.parentNode === workspace) workspace.insertBefore(banner, anchor);
  else workspace?.prepend(banner);
}
function renderBuild(response) {
  const build = response && response.code_sha256 === undefined && response.build ? response.build : response;
  if (!build || typeof build !== 'object' || typeof build.code_sha256 !== 'string') return;
  buildStatus = build;
  mountBuildElements();
  const stamp = $('build-stamp'), banner = $('stale-banner'), action = $('stale-restart');
  if (stamp) { stamp.textContent = buildStampText(build); stamp.title = buildStampTitle(build); }
  if (!banner || buildRestarting) return;
  // A reload cannot refresh this process's frozen build fields (server.py
  // latches code_sha256/assets_sha256 at import time), so an assets-only
  // mismatch needs the same restart as a code mismatch - not a Reload
  // button that would just redraw this same stale banner forever.
  const codeChanged = !!build.on_disk_code_sha256_now && build.on_disk_code_sha256_now !== build.code_sha256;
  const assetsChanged = !!build.on_disk_assets_sha256_now && build.on_disk_assets_sha256_now !== build.assets_sha256;
  buildBannerMode = (codeChanged || assetsChanged) ? 'code' : '';
  banner.hidden = !buildBannerMode;
  action.disabled = false;
  if (buildBannerMode === 'code') {
    // The launcher rebinds this same port, so this origin is where the
    // current build answers once the restart finishes. A read-only console
    // cannot restart itself and this origin is the same stale process, so its
    // reader is told what brings the current build instead (2026-09-25).
    $('stale-banner-message').textContent = readOnly
      ? `This read-only console runs an older build. It shows the current build once its process is restarted.`
      : `This console runs an older build. Press Restart, or close this tab and open ${location.origin}.`;
    action.textContent = 'Restart';
    action.hidden = readOnly;
  }
}
async function restartConsole() {
  const action = $('stale-restart'), message = $('stale-banner-message');
  const before = buildStatus && buildStatus.pid;
  buildRestarting = true;
  action.disabled = true;
  action.textContent = 'Restarting…';
  let answer;
  try { answer = await api('/api/restart', {}); }
  catch (error) {
    buildRestarting = false; action.disabled = false; action.textContent = 'Restart';
    message.textContent = `${error.message} Close this tab and open ${location.origin} instead.`;
    return;
  }
  // A console already running the build on disk answers current:true and does
  // not restart. Polling for a new pid then could only ever time out, so the
  // reader is told the console is current and the poll never starts. The telling
  // is transient: a standing bar offering Restart when nothing is stale is the
  // same lie in a quieter voice, so the confirmation clears itself and hands the
  // banner back to whatever renderBuild computes - which is hidden, on a console
  // that is current.
  if (answer && answer.current) {
    buildRestarting = false;
    action.disabled = false;
    action.textContent = 'Restart';
    if (answer.build) renderBuild(answer);
    const banner = $('stale-banner'), note = $('stale-banner-message') || message;
    const settle = () => { if (answer.build) renderBuild(answer); else if (banner) banner.hidden = true; };
    if (banner) banner.hidden = false;
    action.hidden = true;
    note.textContent = 'Already up to date.';
    clearTimeout(currentNoticeTimer);
    currentNoticeTimer = setTimeout(settle, 5000);
    return;
  }
  message.textContent = 'Restarting. This page reloads when the new build is ready.';
  const deadline = Date.now() + 120000;
  const waitForNewBuild = async () => {
    if (Date.now() > deadline) {
      buildRestarting = false; action.disabled = false; action.textContent = 'Restart';
      message.textContent = `Restart is taking too long. Close this tab and open ${location.origin}.`;
      return;
    }
    try {
      const health = await api('/api/health');
      if (health.pid && before && health.pid !== before) { location.reload(); return; }
    } catch { }
    setTimeout(waitForNewBuild, 1500);
  };
  setTimeout(waitForNewBuild, 1500);
}
function scheduleBuildPoll() {
  clearTimeout(buildPollTimer);
  buildPollTimer = setTimeout(pollBuild, 60000);
}
async function pollBuild() {
  if (!document.hidden && !buildRestarting) {
    try { renderBuild(await api('/api/health')); } catch { }
  }
  scheduleBuildPoll();
}
restoreDraft();
setRosterMode('type'); setLoadingStep(1);
showView(location.hash.slice(1));
init();
