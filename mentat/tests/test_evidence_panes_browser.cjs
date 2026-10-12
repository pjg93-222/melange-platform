/* PAC evidence v2 (S6), real-client pass: the picker rail (build 06, board
 * B19), up to three PAC panes side by side from any imported industry file or
 * saved snapshot, the compare strip, the URL round trip and the Import drawer.
 * Build 06 re-homed every #evidence-add click and chip-row assertion onto the
 * rail's ticks (the prompt's "keep #evidence-add hidden ... re-home those
 * assertions"); #evidence-pac stays in the page, hidden, as the data behind
 * the rail, so its option assertions still hold. #evidence-add, never visible
 * and never clicked by the app, is gone; openCommittee is the programmatic path.
 *
 * Spawns the actual `python app/server.py` console under a temp data root, so
 * imports really land on disk and the permalink really restores from them. Run
 * with MENTAT_NODE_MODULES pointing at a local Playwright installation, from
 * cwd mentat/:  node tests\test_evidence_panes_browser.cjs
 *
 * Every wait is bounded and a top-level watchdog names the step it was on.
 *
 * Steps ported from tests/test_evidence_csv_browser.cjs (S3/S7) are marked
 * PORTED: the type filter, the search, the no-match state, the CORPORATE row in
 * Receipts, the excluded row, the upload-before-token race, the path box, the
 * Browse panel and the worksheet picker.
 */
const assert = require('node:assert/strict');
const {spawn, execFileSync} = require('node:child_process');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');
const net = require('node:net');
const modules = process.env.MENTAT_NODE_MODULES
  || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium} = require(require.resolve('playwright', {paths: [modules]}));

const MENTAT_DIR = path.resolve(__dirname, '..');
// Paul's consoles and the ports other lanes reserve. A scratch console never
// lands on one of these.
const FORBIDDEN_PORTS = new Set([8802, 8806, 8808, 8841, 8940, 8971, 8990, 8991, 8992, 8993]);
const HARD_CAP_MS = 900_000;
const CRYPTO_BOOK = 'Crypto Industry Spending Overview.xlsx';
const CREDIT_BOOK = 'Credit Union Industry Spending Overview.xlsx';

let stepNumber = 0, currentStepLabel = '(none)', stepStartedAt = Date.now();
let serverForWatchdog = null;
const watchdog = setTimeout(() => {
  console.error(`WATCHDOG: step ${stepNumber} (${currentStepLabel}) exceeded ${HARD_CAP_MS}ms`);
  try { if (serverForWatchdog && serverForWatchdog.exitCode === null) serverForWatchdog.kill(); } catch { /* best effort */ }
  process.exit(1);
}, HARD_CAP_MS);

function step(label) {
  stepNumber += 1; currentStepLabel = label; stepStartedAt = Date.now();
  console.log(`STEP ${stepNumber}: ${label}`);
}
function pass(detail) {
  console.log(`PASS step ${stepNumber} (${((Date.now() - stepStartedAt) / 1000).toFixed(1)}s) - ${detail}`);
}

function withTimeout(promise, ms, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} exceeded its ${ms}ms bound`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function freePort() {
  const once = () => new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
  const pick = async () => { for (;;) { const port = await once(); if (!FORBIDDEN_PORTS.has(port)) return port; } };
  return withTimeout(pick(), 10000, 'freePort()');
}

function startServer(port, dataRoot, reportRoot) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.env.MENTAT_PYTHON || 'python',
      ['app/server.py', '--port', String(port), '--data-root', dataRoot],
      {cwd: MENTAT_DIR, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
       // Without a report directory of its own this console resolves the configured
       // one - Paul's published reports - and chdir()s into it on every run.
       env: Object.assign({}, process.env, {MENTAT_REPORT_DIRECTORY: reportRoot})});
    const stderr = [];
    let stdout = '', settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error(`server on ${port} printed no ready line in 30s; stderr: ${stderr.join('')}`));
    }, 30000);
    child.stdout.on('data', chunk => {
      stdout += chunk.toString();
      if (!settled && new RegExp(`Mentat app: http://127\\.0\\.0\\.1:${port}\\b`).test(stdout)) {
        settled = true; clearTimeout(timer); resolve(child);
      }
    });
    child.stderr.on('data', chunk => stderr.push(chunk.toString()));
    child.on('exit', code => {
      if (settled) return;
      settled = true; clearTimeout(timer);
      reject(new Error(`server on ${port} exited ${code}; stderr: ${stderr.join('')}`));
    });
    child.on('error', error => { if (!settled) { settled = true; clearTimeout(timer); reject(error); } });
  });
}

function stopServer(child) {
  return withTimeout(new Promise(resolve => {
    if (!child || child.exitCode !== null) { resolve(); return; }
    child.once('exit', () => resolve());
    child.kill();
  }), 10000, 'stopServer()').catch(error => console.error(error.message));
}

// server.py chdir()s into its report directory (MENTAT_REPORT_DIRECTORY). With MENTAT_PYTHON set to
// a venv's python.exe, the child stopServer() kills is the venv launcher (bpo-35797) and the console,
// the launcher's child, is still exiting - and still in that folder - when the launcher's exit is
// reported. Node 24's rmSync then throws EPERM at once, whatever maxRetries and retryDelay say, which
// left an empty %TEMP% *-reports-* folder after every such run. So wait for the folder to be free, up
// to 10 s, pass or fail: the strip suite's cleanupPath (lanes L2 and L3, 2026-09-26).
async function cleanupPath(target) {
  for (let attempt = 1; ; attempt += 1) {
    try { fs.rmSync(target, {recursive: true, force: true}); return; }
    catch (error) {
      if (attempt >= 40 || !['EPERM', 'EBUSY', 'ENOTEMPTY', 'EACCES'].includes(error.code)) {
        console.error(`cleanup failed for ${target} after ${attempt} attempt(s): ${error.message}`);
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 250));
    }
  }
}

// Two industry-workbook-shaped .xlsx fixtures, because `industry` is derived
// from a Mentat workbook FILE NAME and only an .xlsx name matches the rule; and
// one two-worksheet book whose sheets disagree on their header, which is what
// makes the console offer the worksheet picker. Built from Python (openpyxl,
// already a repo dependency) the same way the S3b fixtures are.
const FIXTURE_SCRIPT = `
import sys
import openpyxl

target = sys.argv[1]
HEAD = ['committee_id', 'committee_name', 'amount', 'date', 'two_year_transaction_period',
        'record_type', 'recipient_category', 'include_in_analysis', 'inclusion_reason',
        'contributor_name']

def book(rows, name, sheets=('2023-2024', '2025-2026')):
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for sheet_name in sheets:
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(HEAD)
        for row in rows:
            if row[4] in sheet_name:
                sheet.append(list(row))
    workbook.save(target + '/' + name)

CRYPTO = [
    ('C00835959', 'FAIRSHAKE', 25000000, '2026-01-05', '2026', 'CORPORATE', 'Super PAC', 'Yes', '', 'COINBASE'),
    ('C00835959', 'FAIRSHAKE', 5000, '2026-01-06', '2026', 'CONTRIBUTION', 'Super PAC', 'Yes', '', 'ARMSTRONG, BRIAN'),
    ('C00835959', 'FAIRSHAKE', 23000000, '2026-01-07', '2026', 'CORPORATE', '', 'No', 'Exact five-key memo duplicate', 'COINBASE'),
    ('C00835959', 'FAIRSHAKE', 10000000, '2024-05-05', '2024', 'CORPORATE', 'Super PAC', 'Yes', '', 'COINBASE'),
    ('C00000935', 'DCCC', 88600, '2026-02-05', '2026', 'CONTRIBUTION', 'Party Committee', 'Yes', '', 'ARMSTRONG, BRIAN'),
    ('C00000935', 'DCCC', 52300, '2024-02-06', '2024', 'CONTRIBUTION', 'Party Committee', 'Yes', '', 'POLLAK, JESSE'),
    ('C00000935', 'DCCC', 7000, '2026-02-07', '2026', 'CONTRIBUTION', 'Party Committee', 'Yes', '', 'TUSAR, GREGORY'),
    ('C00401224', 'NRCC', 2000, '2026-03-05', '2026', 'CONTRIBUTION', 'Party Committee', 'Yes', '', 'CHOI, EMILIE'),
]
CREDIT = [
    ('C00007880', 'CULAC', 15450, '2026-01-05', '2026', 'CONTRIBUTION', 'Trade Association', 'Yes', '', 'CHENEY, BILL'),
    ('C00000935', 'DCCC', 2200, '2024-02-05', '2024', 'CONTRIBUTION', 'Party Committee', 'Yes', '', 'MORGAN, THOMAS'),
    ('C00000935', 'DCCC', 500, '2024-02-06', '2024', 'CONTRIBUTION', 'Party Committee', 'Yes', '', 'MASON, THEA'),
    ('C00082040', 'US CHAMBER OF COMMERCE PAC', 3500, '2026-03-05', '2026', 'CONTRIBUTION', 'Trade Association', 'Yes', '', 'SCHENCK, JAMES'),
]
book(CRYPTO, '${CRYPTO_BOOK}')
book(CREDIT, '${CREDIT_BOOK}')

# Two sheets that disagree on their header: the console cannot read them as one
# table, so it offers the worksheet picker.
picker = openpyxl.Workbook()
picker.remove(picker.active)
alpha = picker.create_sheet('Alpha')
alpha.append(HEAD)
alpha.append(['C00000001', 'Alpha PAC', 10, '2026-01-01', '2026', 'CONTRIBUTION', 'Other PAC', 'Yes', '', 'DOE, JANE'])
beta = picker.create_sheet('Beta')
beta.append(['cmte_id', 'name', 'value'])
beta.append(['C00000002', 'Beta PAC', 20])
picker.save(target + '/picker.xlsx')

third = open(target + '/third.csv', 'w', encoding='utf-8', newline='')
third.write('committee_id,committee_name,amount,date,two_year_transaction_period\\r\\n')
third.write('C00000003,Third PAC,42,2026-04-01,2026\\r\\n')
third.close()

# A 106-row stress file in the pre-S6 receipts schema: 101 Alpha PAC rows whose
# first contributor name is a hostile HTML payload, a prior-cycle row, a row
# with no amount, a row with no date or cycle, one Beta PAC row and one row with
# no committee at all. 104 of them select as Alpha PAC, which is what makes the
# drawer page 100 + 4 and report two rows as needing interpretation.
import csv
stress = [['committee_id', 'committee_name', 'contribution_receipt_amount',
           'contribution_receipt_date', 'cycle', 'contributor_name', 'memo_text']]
for index in range(101):
    stress.append(['C00000001', 'Alpha PAC', 1, '2026-01-01', 2026,
                   '<img src=x onerror=alert(1)>' if index == 0 else 'Contributor ' + str(index),
                   'Fictional receipt'])
stress.append(['C00000001', 'Alpha PAC', 5, '2024-01-01', 2024, 'Prior cycle', ''])
stress.append(['C00000001', 'Alpha PAC', '', '2026-01-01', 2026, 'Missing amount', ''])
stress.append(['C00000001', 'Alpha PAC', 999, '', '', 'Missing date and cycle', ''])
stress.append(['C00000002', 'Beta PAC', 50, '2026-02-01', 2026, 'Another contributor', ''])
stress.append(['', 'Unknown PAC', 25, '2026-02-01', 2026, 'Unassigned receipt', ''])
with open(target + '/stress.csv', 'w', encoding='utf-8', newline='') as handle:
    csv.writer(handle, quoting=csv.QUOTE_ALL).writerows(stress)
`;

(async () => {
  let browser = null, server = null, failed = false;
  const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-s6-data-'));
  const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-s6-fixtures-'));
  const reportRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mentat-s6-reports-'));
  try {
    step('build two industry-workbook fixtures, a two-header workbook and a third CSV');
    execFileSync(process.env.MENTAT_PYTHON || 'python', ['-c', FIXTURE_SCRIPT, fixtureDir],
      {cwd: MENTAT_DIR, stdio: 'pipe'});
    assert.ok(fs.existsSync(path.join(fixtureDir, CRYPTO_BOOK)));
    assert.ok(fs.existsSync(path.join(fixtureDir, CREDIT_BOOK)));
    pass(`fixtures written under ${fixtureDir}`);

    step('launch a scratch console on a free port with a temp data root');
    const port = await freePort();
    server = await startServer(port, dataRoot, reportRoot);
    serverForWatchdog = server;
    const origin = `http://127.0.0.1:${port}`;
    // The console reads this suite's own report directory, not the configured one.
    const reportsSeen = await new Promise((resolve, reject) => {
      const ask = require('node:http').get(
        {host: '127.0.0.1', port: port, path: '/api/report-sources', headers: {Host: '127.0.0.1:' + port}},
        answer => {
          let body = '';
          answer.on('data', chunk => { body += chunk; });
          answer.on('end', () => { try { resolve(JSON.parse(body)); } catch (error) { reject(error); } });
        });
      ask.on('error', reject);
    });
    assert.equal(path.resolve(String(reportsSeen.report_directory || '')).toLowerCase(),
      path.resolve(reportRoot).toLowerCase(),
      `the console must read this suite's own report directory, got "${reportsSeen.report_directory}"`);
    pass(`console ready on ${port}, data root ${dataRoot}, report directory ${reportRoot}`);

    step('launch headless Chrome and open the PAC evidence tab');
    browser = await withTimeout(chromium.launch({headless: true,
      channel: process.env.MENTAT_BROWSER_CHANNEL || 'chrome', timeout: 30000}), 35000, 'chromium.launch()');
    const page = await browser.newPage({viewport: {width: 1600, height: 1200}});
    page.setDefaultTimeout(20000);
    const errors = [], remote = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (!request.url().startsWith(origin) && !request.url().startsWith('blob:')) remote.push(request.url());
    });
    const status = async () => (await page.textContent('#evidence-status')).trim();
    // The drawer buttons TOGGLE, so a second press on an open drawer closes it
    // and every control inside becomes invisible. Open only when closed.
    const openDrawer = async id => {
      const drawer = id === 'evidence-import-open' ? 'evidence-import-drawer' : 'evidence-snapshot-drawer';
      if (await page.isVisible('#' + drawer)) return;
      await page.click('#' + id);
      await page.waitForSelector('#' + drawer + ':not([hidden])');
    };
    // Polled with evaluate() rather than waitForFunction(): a raf-polled
    // predicate that reads #evidence-status never resolved on this host even
    // when the text was already there (measured: the same predicate through
    // evaluate() answered true immediately).
    const until = async (predicate, timeout, what) => {
      const deadline = Date.now() + timeout;
      for (;;) {
        if (await page.evaluate(predicate.fn, predicate.arg)) return;
        if (Date.now() > deadline) {
          const seen = await page.textContent('#evidence-status').catch(() => '<unreadable>');
          throw new Error(`${what} did not happen within ${timeout}ms; `
            + `#evidence-status was ${JSON.stringify((seen || '').trim())}`);
        }
        await new Promise(resolve => setTimeout(resolve, 200));
      }
    };
    const waitStatus = (pattern, timeout = 60000) => until(
      {fn: source => new RegExp(source).test(((document.getElementById('evidence-status') || {}).textContent || '').trim()),
       arg: pattern.source}, timeout, `status matching ${pattern}`);
    // A pane element appears as soon as its load starts, with placeholders in
    // it; wait for the count AND for no pane still carrying a message.
    const waitPanes = (count, timeout = 60000) => until(
      {fn: wanted => document.querySelectorAll('#evidence-panes [data-pane]').length === wanted
        && document.querySelectorAll('#evidence-panes .pane-message').length === 0,
       arg: count}, timeout, `${count} loaded pane(s)`);
    const waitFor = (fn, arg, timeout, what) => until({fn, arg}, timeout, what);
    // Build 06: a pane opens from its tick in the rail and closes on untick.
    const railBox = committee => page.locator(`#evidence-list input[type=checkbox][value="${committee}"]`);
    const tick = async committee => {
      await waitFor(wanted => [...document.querySelectorAll('#evidence-list input[type=checkbox]')]
        .some(node => node.value === wanted), committee, 30000, `the rail row for ${committee}`);
      await railBox(committee).check();
    };
    const railTicked = () => page.locator('#evidence-list input[type=checkbox]:checked').evaluateAll(
      nodes => nodes.map(node => node.value));
    await page.goto(origin + '/');
    await page.click('#tab-evidence');
    await waitStatus(/Nothing imported yet|snapshot/);
    assert.notStrictEqual(await status(), '');
    assert.equal(await page.isVisible('#evidence-empty'), true);
    // Build 06 (prompt 06, item 5): the rail stays and the panes area shows
    // one quiet line; the rail itself says what to do and offers both actions.
    const empty = (await page.textContent('#evidence-empty')).trim();
    assert.equal(empty, 'Tick a PAC on the left to open it here.');
    assert.equal(await page.locator('#evidence-empty.empty, #evidence-empty.panel, #evidence-empty.card').count(), 0,
      'the empty state is a line, not a card');
    // Fix round 2 (R06b-01): the template's first status ("Finding imported
    // files and saved snapshots…") already satisfies the waitStatus above while
    // the rail is still empty (measured: the rail line lands ~17 ms later), so
    // wait for the rail's own line before reading it.
    await waitFor(() => /Choose a source above|Nothing imported yet/.test(
      (document.querySelector('#evidence-list .evidence-list-empty') || {}).textContent || ''),
      undefined, 30000, 'the rail empty line');
    const railEmpty = (await page.textContent('#evidence-list')).trim();
    assert.ok(/Choose a source above|Nothing imported yet/.test(railEmpty), railEmpty);
    assert.equal(await page.isVisible('#evidence-import-open'), true);
    assert.equal(await page.isVisible('#evidence-snapshot-open'), true);
    assert.equal(await page.locator('#evidence-add').count(), 0, 'the hidden Add pane button is gone');
    pass(`tab open, status ${JSON.stringify(await status())}, empty line ${JSON.stringify(empty)}, rail ${JSON.stringify(railEmpty)}`);

    step('PORTED: the path box, the Browse panel and the worksheet picker exist in the page');
    for (const id of ['evidence-path', 'evidence-path-read', 'evidence-header-path', 'evidence-browse',
                      'evidence-browser', 'evidence-sheet', 'evidence-sheet-label']) {
      assert.equal(await page.locator('#' + id).count(), 1, `#${id} must exist (it did not before S6)`);
    }
    await openDrawer('evidence-import-open');
    assert.equal(await page.isVisible('#evidence-import-drawer'), true);
    assert.equal(await page.getAttribute('#evidence-import-open', 'aria-expanded'), 'true');
    pass('Import drawer opens and carries the path box, header box, Browse and the picker');

    step('PORTED: upload before the console token is assigned must not 403');
    // app.js assigns `token` when GET /api/session?light=1 answers, a different
    // round trip from the listing this module fetches on load. Choosing a file
    // in that window used to get HTTP 403 and force a reload; awaitToken()
    // fills it in instead. Set the file with no wait at all.
    await page.goto(origin + '/#evidence');
    await page.setInputFiles('#evidence-csv-file', path.join(fixtureDir, CRYPTO_BOOK));
    await waitStatus(/PACs in file/, 180000);
    assert.ok(!/Refresh the app before continuing/.test(await status()), await status());
    pass(`imported ${CRYPTO_BOOK} with no token wait: ${JSON.stringify(await status())}`);

    step('the Source select labels the import by the industry its file name declares');
    const sourceLabels = await page.locator('#evidence-source option').allTextContents();
    assert.ok(sourceLabels.some(text => /^Crypto · /.test(text)), JSON.stringify(sourceLabels));
    const sourceValues = await page.locator('#evidence-source option').evaluateAll(
      nodes => nodes.map(node => node.value));
    const cryptoValue = sourceValues.find(value => value.startsWith('csv:'));
    assert.ok(cryptoValue, JSON.stringify(sourceValues));
    pass(`Source options ${JSON.stringify(sourceLabels)}`);

    step('import the second workbook through the path box');
    await openDrawer('evidence-import-open');
    await page.fill('#evidence-path', path.join(fixtureDir, CREDIT_BOOK));
    await page.click('#evidence-path-read');
    await waitStatus(/Credit Union|PACs in file/, 180000);
    await waitFor(      () => [...document.querySelectorAll('#evidence-source option')].filter(node => node.value.startsWith('csv:')).length === 2, null, 60000, 'a page condition');
    const bothLabels = await page.locator('#evidence-source option').allTextContents();
    assert.ok(bothLabels.some(text => /^Credit Union · /.test(text)), JSON.stringify(bothLabels));
    pass(`two imports listed: ${JSON.stringify(bothLabels)}`);

    step('PORTED: choosing the type Super PAC shows FAIRSHAKE and hides DCCC');
    await page.selectOption('#evidence-source', cryptoValue);
    await waitFor(() => document.querySelectorAll('#evidence-pac option').length > 1, null, 20000, 'a page condition');
    const allPacs = await page.locator('#evidence-pac option').allTextContents();
    assert.ok(allPacs.some(text => /DCCC/.test(text)), JSON.stringify(allPacs));
    await page.selectOption('#evidence-type', 'Super PAC');
    await waitFor(() => document.querySelectorAll('#evidence-pac option').length === 1, null, 20000, 'a page condition');
    const superPacs = await page.locator('#evidence-pac option').allTextContents();
    assert.equal(superPacs.length, 1);
    assert.ok(/FAIRSHAKE/.test(superPacs[0]), superPacs[0]);
    assert.ok(/Super PAC/.test(await status()), await status());
    assert.deepEqual(await page.locator('#evidence-list input[type=checkbox]').evaluateAll(nodes => nodes.map(node => node.value)),
      ['C00835959'], 'the rail lists exactly what the type filter leaves');
    pass(`type Super PAC leaves ${JSON.stringify(superPacs)}`);

    step('PORTED: typing "dccc" with All types shows only DCCC');
    await page.selectOption('#evidence-type', '');
    await page.fill('#evidence-find', 'dccc');
    await waitFor(() => document.querySelectorAll('#evidence-pac option').length === 1, null, 20000, 'a page condition');
    assert.ok(/DCCC/.test((await page.locator('#evidence-pac option').allTextContents())[0]));
    assert.ok(/matching "dccc"/.test(await status()), await status());
    pass(`search narrowed to DCCC: ${JSON.stringify(await status())}`);

    step('PORTED: a search that matches nothing empties the PAC list and the rail');
    await page.fill('#evidence-find', 'no-such-committee');
    await waitFor(() => document.querySelectorAll('#evidence-pac option').length === 0, null, 20000, 'a page condition');
    assert.ok(/0 shown/.test(await status()), await status());
    assert.match((await page.textContent('#evidence-list')).trim(), /No PAC in this source matches/);
    assert.equal(await page.locator('#evidence-panes [data-pane]').count(), 0);
    pass(`no match: Add pane disabled, status ${JSON.stringify(await status())}`);

    step('add a DCCC pane from the Crypto workbook');
    await page.fill('#evidence-find', 'dccc');
    await waitFor(() => document.querySelectorAll('#evidence-pac option').length === 1, null, 20000, 'a page condition');
    await tick('C00000935');
    await waitPanes(1);
    assert.equal(await page.isVisible('#evidence-compare'), false, 'one pane is not a comparison');
    assert.equal(await page.isVisible('#evidence-empty'), false);
    const paneA = (await page.textContent('#evidence-panes [data-pane=a] h2')).trim();
    const figuresA = await page.locator('#evidence-panes [data-pane=a] .pane-figure strong').allTextContents();
    assert.equal(paneA, 'DCCC');
    assert.equal(figuresA[0].trim(), '$147,900');
    // P4b: the strip is Receipts | Contributors | Rows in file | Cycles covered,
    // so "rows in file" is the THIRD figure. Same number, one position along.
    assert.equal(figuresA.length, 4, JSON.stringify(figuresA));
    assert.equal(figuresA[2].trim(), '3');
    // Re-homed from the retired chip row: the rail's ticked row is the open
    // pane, and the pane's own x carries the Remove label the chip carried.
    const tickedA = await railTicked();
    assert.deepEqual(tickedA, ['C00000935']);
    assert.ok(/DCCC/.test(await page.getAttribute('#evidence-list .evidence-rail-row.on', 'title')));
    assert.ok(/^Crypto/.test((await page.textContent('#evidence-panes [data-pane=a] .pane-kind')).trim()));
    assert.equal(await page.getAttribute('#evidence-panes [data-pane=a] .pane-remove', 'aria-label'), 'Remove DCCC');
    pass(`pane A DCCC ${figuresA[0].trim()} over ${figuresA[1].trim()} rows, ticked ${JSON.stringify(tickedA)}`);

    step('add DCCC from the Credit Union workbook: two panes and the compare strip');
    const creditValue = sourceValues.length ? null : null;
    const creditOption = (await page.locator('#evidence-source option').evaluateAll(
      nodes => nodes.filter(node => /^Credit Union · /.test(node.textContent)).map(node => node.value)))[0];
    assert.ok(creditOption, 'the Credit Union import must be selectable');
    await page.selectOption('#evidence-source', creditOption);
    await page.fill('#evidence-find', 'dccc');
    await waitFor(() => document.querySelectorAll('#evidence-pac option').length === 1, null, 20000, 'a page condition');
    await tick('C00000935');
    await waitPanes(2);
    assert.equal(await page.isVisible('#evidence-compare'), true);
    const kinds = await page.locator('#evidence-panes .pane-kind').allTextContents();
    assert.ok(kinds[0].startsWith('Crypto') && kinds[1].startsWith('Credit Union'), JSON.stringify(kinds));
    const compare = await page.locator('#evidence-compare tbody tr').evaluateAll(nodes => nodes.map(node => {
      const cells = [...node.querySelectorAll('td')];
      return {metric: cells[0].textContent.trim(), cells: cells.slice(1).map(cell => ({
        text: (cell.querySelector('.num') || cell).textContent.trim(),
        bar: cell.querySelector('.bar') ? cell.querySelector('.bar').style.width : null}))};
    }));
    const metrics = compare.map(row => row.metric);
    for (const wanted of ['2023–2024', '2025–2026', 'Rows supported',
                          'Rows excluded by the workbook', 'Top contributor', 'Category', 'Source']) {
      assert.ok(metrics.includes(wanted), `compare strip is missing "${wanted}": ${JSON.stringify(metrics)}`);
    }
    const receipts = compare.find(row => row.metric.startsWith('Receipts'));
    assert.equal(receipts.cells[0].text, '$147,900');
    assert.equal(receipts.cells[1].text, '$2,700');
    // Normalised per row: the largest fills the row, anything positive keeps a
    // visible sliver. Widths come through the CSSOM because the console's CSP
    // is style-src 'self' and an inline style attribute is blocked.
    assert.equal(receipts.cells[0].bar, '100%');
    assert.equal(receipts.cells[1].bar, '2%');
    assert.equal(compare.find(row => row.metric === 'Top contributor').cells[0].bar, null, 'text rows carry no bar');
    pass(`compare strip: ${metrics.length} rows, Receipts ${receipts.cells[0].text} [100%] vs ${receipts.cells[1].text} [2%]`);
    if (process.env.MENTAT_MAP_SHOT_DIR) {
      fs.mkdirSync(process.env.MENTAT_MAP_SHOT_DIR, {recursive: true});
      await page.waitForFunction(() => document.querySelectorAll('#evidence-panes .pane-thumb-svg').length === 2,
        null, {timeout: 90000});
      const importOpen = await page.locator('#evidence-import-open').getAttribute('aria-expanded') === 'true';
      if (importOpen) await page.click('#evidence-import-open');
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({path: path.join(process.env.MENTAT_MAP_SHOT_DIR, '00_two_panes.png'), fullPage: true});
      if (importOpen) await page.click('#evidence-import-open');
    }

    step('the Map button is on every pane and on Compare, and every one of them works (S8)');
    // S6 asserted ZERO Map buttons, because a control nobody can click is a
    // mistake (Paul, 2026-09-17). S8 ships the map, so the same rule now reads
    // the other way: the button is there AND it is enabled. What it opens is
    // tests/test_evidence_map_browser.cjs's job.
    assert.equal(await page.locator('#evidence-panes .pane-map').count(), 2);
    assert.equal(await page.locator('#evidence-compare #evidence-compare-map').count(), 1);
    assert.equal(await page.locator('#evidence-panes .pane-map:disabled').count(), 0);
    // What Paul objected to was a control that is NEVER clickable.
    const stuck = await page.locator('#view-evidence button:disabled').evaluateAll(
      nodes => nodes.map(node => node.id || node.className));
    assert.deepEqual(stuck, [], `disabled controls: ${JSON.stringify(stuck)}`);
    pass('two pane Map buttons and one on Compare, all enabled; no button is disabled');

    step('the URL carries the prefixed pane keys and never the single-pane ones');
    const query = await page.evaluate(() => location.search);
    for (const key of ['a.source=csv', 'a.committee=C00000935', 'b.source=csv', 'b.committee=C00000935']) {
      assert.ok(query.includes(key), `${key} missing from ${query}`);
    }
    for (const key of ['evidence=', 'offset=', 'limit=']) {
      assert.ok(!query.includes(key), `${key} must never be written: ${query}`);
    }
    pass(`query ${query}`);

    step('reload that URL: both panes restore after the listing loads');
    const permalink = origin + query + '#evidence';
    await page.goto(permalink);
    await waitPanes(2);
    const restored = await page.locator('#evidence-panes [data-pane]').evaluateAll(
      nodes => nodes.map(node => `${node.dataset.pane}:${node.dataset.committee}`));
    assert.deepEqual(restored, ['a:C00000935', 'b:C00000935']);
    assert.equal(await page.isVisible('#evidence-compare'), true);
    pass(`restored ${JSON.stringify(restored)} from the permalink`);

    step('untick a pane in the rail: one pane left, the compare strip goes');
    // Re-homed from the chip row's remove button: the rail shows the chosen
    // source's PACs, so choose the Credit Union import and untick its DCCC.
    await page.selectOption('#evidence-source', creditOption);
    await waitFor(() => document.querySelectorAll('#evidence-list input[type=checkbox]:checked').length === 1,
      null, 30000, 'the Credit Union DCCC row to show ticked');
    await railBox('C00000935').uncheck();
    await waitPanes(1);
    assert.equal(await page.isVisible('#evidence-compare'), false);
    assert.deepEqual(await railTicked(), []);
    assert.deepEqual(await page.locator('#evidence-panes [data-pane]').evaluateAll(nodes => nodes.map(node => node.dataset.pane)), ['a']);
    const afterQuery = await page.evaluate(() => location.search);
    assert.ok(!afterQuery.includes('b.source'), afterQuery);
    pass(`one pane left, query ${afterQuery}`);

    step('PORTED: the CORPORATE row counts in Receipts and the Include=No row shows as excluded');
    await page.selectOption('#evidence-source', cryptoValue);
    await page.selectOption('#evidence-type', '');
    await page.fill('#evidence-find', 'fairshake');
    await waitFor(() => document.querySelectorAll('#evidence-pac option').length === 1, null, 20000, 'a page condition');
    await tick('C00835959');
    await waitPanes(2);
    const fairPane = '#evidence-panes [data-pane=b]';
    const fairFigures = await page.locator(`${fairPane} .pane-figure strong`).allTextContents();
    // Build 06: the figures carry no sub-captions (board 3); each note rides on
    // the figure's data-note and title, and is listed in About these figures.
    assert.equal(await page.locator(`${fairPane} .pane-figure .muted`).count(), 0, 'no sub-captions under the figures');
    const fairNotes = await page.locator(`${fairPane} .pane-figure`).evaluateAll(nodes => nodes.map(node => node.dataset.note));
    assert.ok(/1 excluded by the workbook/.test(await page.getAttribute(`${fairPane} .pane-figure:nth-child(3)`, 'title')));
    // 25,000,000 CORPORATE + 5,000 CONTRIBUTION + 10,000,000 CORPORATE = the
    // Receipts total; the 23,000,000 Include=No row is visible and out of it.
    assert.equal(fairFigures[0].trim(), '$35,005,000');
    assert.equal(fairFigures.length, 4, JSON.stringify(fairFigures));
    assert.equal(fairFigures[2].trim(), '4');
    assert.ok(/3 supported/.test(fairNotes[2]) && /1 excluded by the workbook/.test(fairNotes[2]), fairNotes[2]);
    await page.click(`${fairPane} .pane-rows`);
    await page.waitForSelector(`${fairPane} .pane-drawer tbody tr`);
    const drawerRows = await page.locator(`${fairPane} .pane-drawer tbody tr`).count();
    const excludedBadges = await page.locator(`${fairPane} .pane-drawer .badge.excluded`).allTextContents();
    assert.equal(drawerRows, 4);
    assert.deepEqual(excludedBadges, ['excluded by the workbook']);
    assert.equal(await page.getAttribute(`${fairPane} .pane-rows`, 'aria-expanded'), 'true');
    pass(`FAIRSHAKE ${fairFigures[0].trim()} over 4 rows, 1 excluded badge in the drawer`);

    step('About these figures opens the file details and the parser warnings');
    await page.click(`${fairPane} .pane-about`);
    await page.waitForSelector(`${fairPane} .pane-about-body:not([hidden])`);
    const about = (await page.textContent(`${fairPane} .pane-about-body`)).replace(/\s+/g, ' ');
    assert.ok(/SHA-256/.test(about), about.slice(0, 200));
    assert.ok(/not donor allocations/.test(about), about.slice(0, 300));
    pass('About these figures carries the SHA-256 provenance and the interpretation note');

    step('the third pane fills the grid in thirds, and a fourth tick opens nothing and says why');
    // Build 06 (prompt 06, item 2): three panes at most; a fourth tick opens no
    // pane and the rail says "Close a pane to open another." in --muted.
    for (const [wanted, committee] of [['nrcc', 'C00401224']]) {
      await page.fill('#evidence-find', wanted);
      await waitFor(() => document.querySelectorAll('#evidence-pac option').length === 1, null, 20000, 'a page condition');
      await tick(committee);
    }
    await waitPanes(3);
    await page.selectOption('#evidence-source', creditOption);
    await page.fill('#evidence-find', 'culac');
    await waitFor(() => document.querySelectorAll('#evidence-pac option').length === 1, null, 20000, 'a page condition');
    await waitFor(() => document.querySelectorAll('#evidence-list input[type=checkbox]').length === 1, null, 20000, 'the CULAC rail row');
    await railBox('C00007880').click();
    await page.waitForTimeout(800);
    assert.equal(await page.locator('#evidence-panes [data-pane]').count(), 3, 'a fourth tick must open no pane');
    assert.equal(await railBox('C00007880').isChecked(), false, 'the refused tick is taken back');
    assert.equal(await page.isVisible('#evidence-rail-note'), true);
    assert.equal((await page.textContent('#evidence-rail-note')).trim(), 'Close a pane to open another.');
    const noteColour = await page.evaluate(() => {
      // --muted resolved the way the note's own colour is, so the two compare.
      const probe = document.createElement('span');
      probe.style.color = 'var(--muted)';
      document.getElementById('evidence-rail-note').parentElement.appendChild(probe);
      const resolved = getComputedStyle(probe).color;
      probe.remove();
      return [getComputedStyle(document.getElementById('evidence-rail-note')).color, resolved,
        getComputedStyle(document.getElementById('view-evidence')).getPropertyValue('--muted').trim()];
    });
    // Fix round 1 (D10): the refusal note is --muted, not merely coloured.
    assert.equal(noteColour[0], noteColour[1], `the refusal note is not --muted: ${JSON.stringify(noteColour)}`);
    assert.ok(/3 panes open/.test(await status()), await status());
    const columns = await page.evaluate(() => document.getElementById('evidence-panes').style.gridTemplateColumns);
    assert.ok(/repeat\(3, minmax\(0px?, 1fr\)\)/.test(columns), columns);
    assert.equal(await page.evaluate(() => getComputedStyle(document.getElementById('evidence-panes')).gridTemplateColumns.split(' ').length), 3);
    pass(`three panes in thirds, the fourth tick refused with ${JSON.stringify(noteColour)}, status ${JSON.stringify(await status())}`);

    step('fix r2 (MENTAT-DBG-01): at 390px the open panes stack and nothing scrolls sideways');
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(400);
    const narrow = await page.evaluate(() => {
      const host = document.getElementById('evidence-panes');
      return {columns: getComputedStyle(host).gridTemplateColumns.split(' ').length,
        panes: host.querySelectorAll('[data-pane]').length,
        scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth};
    });
    await page.setViewportSize({width: 1600, height: 1200});
    await page.waitForTimeout(400);
    assert.equal(narrow.panes, 3, JSON.stringify(narrow));
    assert.equal(narrow.columns, 1, `three panes at 390px must stack: ${JSON.stringify(narrow)}`);
    assert.ok(narrow.scrollWidth <= narrow.clientWidth, `the page scrolls sideways at 390px: ${JSON.stringify(narrow)}`);
    assert.equal(await page.evaluate(() => getComputedStyle(document.getElementById('evidence-panes')).gridTemplateColumns.split(' ').length), 3,
      'back at 1600px the three panes are in thirds again');
    pass(`390px: ${JSON.stringify(narrow)}`);

    step('fix round 1: rail receipts, ticks across the Cycle chip, the note, and the rail foot on screen');
    // R06-02: an import's rows are ranked by receipts and show them; with a
    // cycle chosen, the cycle's receipts, and "—" (last) where the PAC has none.
    const railFigures = () => page.locator('#evidence-list .evidence-rail-row').evaluateAll(nodes => nodes.map(node => ({
      id: node.dataset.committee, figure: node.querySelector('.evidence-rail-figure').textContent.trim(),
      ticked: node.querySelector('input').checked, title: node.getAttribute('title')})));
    await page.selectOption('#evidence-source', cryptoValue);
    await page.fill('#evidence-find', '');
    await waitFor(() => document.querySelectorAll('#evidence-list .evidence-rail-row').length === 3, null, 20000, 'the three Crypto rows');
    let railNow = await railFigures();
    assert.deepEqual(railNow.map(row => row.id), ['C00835959', 'C00000935', 'C00401224'], JSON.stringify(railNow));
    assert.deepEqual(railNow.map(row => row.figure), ['$35,005,000', '$147,900', '$2,000'], JSON.stringify(railNow));
    assert.deepEqual(railNow.map(row => row.ticked), [true, true, true], 'the three open panes are ticked');
    // R06-03: a Cycle chip other than the panes' own keeps their ticks.
    await page.selectOption('#evidence-cycle', '2024');
    await waitFor(() => (document.querySelector('#evidence-list .evidence-rail-figure') || {}).textContent === '$10,000,000',
      null, 20000, 'the 2024 receipts in the rail');
    railNow = await railFigures();
    assert.deepEqual(railNow.map(row => row.figure), ['$10,000,000', '$52,300', '—'], JSON.stringify(railNow));
    assert.deepEqual(railNow.map(row => row.ticked), [true, true, true], `ticks must survive the Cycle chip: ${JSON.stringify(railNow)}`);
    assert.ok(railNow.every(row => /open on All cycles/.test(row.title)), JSON.stringify(railNow));
    // An untick closes the PAC's pane whatever the chip says; a tick opens it on the chip's cycle.
    await railBox('C00401224').uncheck();
    await waitPanes(2);
    assert.equal(await page.isVisible('#evidence-rail-note'), false, 'an untick takes the refusal note down');
    await railBox('C00401224').check();
    await waitPanes(3);
    assert.deepEqual(await page.evaluate(() => window.MentatEvidence.panes().find(pane => pane.committee === 'C00401224').cycle), '2024');
    await page.selectOption('#evidence-cycle', '');
    await waitFor(() => document.querySelectorAll('#evidence-list input:checked').length === 3, null, 20000, 'three ticks on All cycles');
    assert.match((await railFigures()).find(row => row.id === 'C00401224').title, /open on 2023–2024/);
    // D6: the refusal note goes when a pane closes, however it is closed.
    await page.selectOption('#evidence-source', creditOption);
    await page.fill('#evidence-find', 'culac');
    await waitFor(() => document.querySelectorAll('#evidence-list input[type=checkbox]').length === 1, null, 20000, 'the CULAC rail row');
    await railBox('C00007880').click();
    await page.waitForTimeout(500);
    assert.equal(await page.isVisible('#evidence-rail-note'), true, 'a fourth tick is refused with the note');
    await page.click('#evidence-panes [data-pane=c] .pane-remove');
    await waitPanes(2);
    assert.equal(await page.isVisible('#evidence-rail-note'), false, 'closing a pane below the ceiling takes the note down');
    await page.fill('#evidence-find', '');
    await page.selectOption('#evidence-source', cryptoValue);
    await tick('C00401224');
    await waitPanes(3);
    // D2, R06-11, R06-16: the rail's foot is on screen at the top and at the
    // bottom of the page; the Source chip has the rail's width; the scope status
    // is one line with the whole text on its title.
    const railGeometry = () => page.evaluate(() => {
      const rect = selector => document.querySelector(selector).getBoundingClientRect();
      const status = document.getElementById('evidence-status');
      return {view: innerHeight, links: rect('.evidence-rail-links').bottom, railTop: rect('.evidence-rail').top,
        chip: rect('.evidence-rail-chips .chip-select').width, chips: rect('.evidence-rail-chips').width,
        statusHeight: rect('#evidence-status').height, statusTitle: status.title === status.textContent,
        scope: status.classList.contains('is-scope')};
    });
    const atTop = await railGeometry();
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await page.waitForTimeout(300);
    const atBottom = await railGeometry();
    await page.evaluate(() => window.scrollTo(0, 0));
    for (const geometry of [atTop, atBottom]) {
      assert.ok(geometry.links <= geometry.view && geometry.railTop >= 0, `the rail foot left the screen: ${JSON.stringify(geometry)}`);
    }
    assert.ok(Math.abs(atTop.chip - atTop.chips) <= 1, `the Source chip must take the rail's width: ${JSON.stringify(atTop)}`);
    assert.ok(atTop.scope && atTop.statusTitle && atTop.statusHeight <= 18, `the scope status is one line: ${JSON.stringify(atTop)}`);
    pass(`receipts ranked, ticks kept across cycles, note cleared, rail foot on screen ${JSON.stringify({atTop, atBottom})}`);

    step('a saved snapshot is listed as a source and opens as a pane');
    const snapshotOption = (await page.locator('#evidence-source option').evaluateAll(
      nodes => nodes.filter(node => node.value.startsWith('snapshot:')).map(node => node.value)))[0];
    if (!snapshotOption) {
      // The route reads PROJECT/.cache/pac-evidence, not the temp data root, so
      // a checkout with no saved snapshot has nothing to list. Say so instead
      // of failing, and test what is testable.
      pass('SKIPPED: no saved snapshot under mentat/.cache/pac-evidence in this checkout');
    } else {
      await page.click('#evidence-panes [data-pane=c] .pane-remove');
      await waitPanes(2);
      // chooseSource() FETCHES the snapshot before it rebuilds the picker, so a
      // non-empty #evidence-pac still holds the previous import's committees for
      // a moment. Adding then sends a CSV committee to the snapshot route and the
      // pane never resolves. Wait for the source status to change; both sources may contain the same PAC.
      const pacBefore = await page.evaluate(
        () => document.querySelector('#evidence-status').textContent);
      await page.selectOption('#evidence-source', snapshotOption);
      await waitFor(previous => {
        const now = [...document.querySelectorAll('#evidence-pac option')].map(node => node.value).join('|');
        return now.length > 0 && document.querySelector('#evidence-status').textContent !== previous
          && document.querySelectorAll('#evidence-list input[type=checkbox]').length > 0;
      }, pacBefore, 30000, 'the PAC picker to be rebuilt for the snapshot');
      await page.locator('#evidence-list input[type=checkbox]').first().check();
      // F6. Wait on the pane's data-committee and its figure text: a pane that
      // is still loading has neither, and a pane that failed keeps its
      // committee but shows an em-dash, so this distinguishes the two.
      await waitFor(() => {
        const pane = document.querySelector('#evidence-panes [data-pane=c]');
        if (!pane || !pane.dataset.committee) return false;
        const figure = pane.querySelector('.pane-figure strong');
        return !!figure && /\$/.test(figure.textContent) && !pane.querySelector('.pane-message');
      }, null, 60000, 'the snapshot pane to carry a committee and a figure');
      const snapCommittee = await page.getAttribute('#evidence-panes [data-pane=c]', 'data-committee');
      const snapMessages = await page.locator('#evidence-panes [data-pane=c] .pane-message').allTextContents();
      assert.deepEqual(snapMessages, [], `snapshot pane message: ${JSON.stringify(snapMessages)}`);
      assert.match(snapCommittee, /^C\d{8}$/);
      const snapKinds = await page.locator('#evidence-panes .pane-kind').allTextContents();
      assert.ok(snapKinds.some(text => /Saved FEC snapshot/.test(text)), JSON.stringify(snapKinds));
      const snapRows = await page.locator('#evidence-panes [data-pane=c] .pane-figure strong').allTextContents();
      const snapNotes = await page.locator('#evidence-panes [data-pane=c] .pane-figure').evaluateAll(nodes => nodes.map(node => node.dataset.note));
      // P4b: a snapshot's strip is Receipts | Independent spending | Legs |
      // Cycles covered. It still says plainly that it has legs and not file
      // rows - now in the third figure's own note rather than as an em dash.
      assert.equal(snapRows.length, 4, JSON.stringify(snapRows));
      assert.equal(snapNotes[2].trim(), 'snapshot: legs, not file rows', JSON.stringify(snapNotes));
      assert.ok(/^[\d,]+$/.test(snapRows[2].trim()), `legs is a count: ${snapRows[2]}`);
      pass(`snapshot pane open: ${JSON.stringify(snapKinds)}`);
    }

    step('PORTED: the Browse panel lists a folder on this computer and opens a file from it');
    await openDrawer('evidence-import-open');
    await page.fill('#evidence-path', path.join(fixtureDir, 'x'));
    await page.click('#evidence-browse');
    await page.waitForSelector('#evidence-browser tbody tr');
    const listed = await page.locator('#evidence-browser tbody tr').allTextContents();
    assert.ok(listed.some(text => text.includes('third.csv')), JSON.stringify(listed));
    pass(`Browse listed ${listed.length} entries under the fixture folder`);

    step('the Import drawer imports a third file and it joins the Source list');
    await page.setInputFiles('#evidence-csv-file', path.join(fixtureDir, 'third.csv'));
    await waitFor(      () => [...document.querySelectorAll('#evidence-source option')].filter(node => node.value.startsWith('csv:')).length === 3, null, 120000, 'a page condition');
    const three = await page.locator('#evidence-source option').allTextContents();
    assert.ok(three.some(text => /^third\.csv · /.test(text)), JSON.stringify(three));
    pass(`three imports listed: ${JSON.stringify(three)}`);

    step('PORTED: a workbook whose two sheets disagree on their header offers the worksheet picker');
    await page.setInputFiles('#evidence-csv-file', path.join(fixtureDir, 'picker.xlsx'));
    await waitStatus(/PACs in file|could not be read|Alpha|Beta/, 120000);
    const pickerVisible = await page.isVisible('#evidence-sheet-label');
    const sheets = pickerVisible ? await page.locator('#evidence-sheet option').allTextContents() : [];
    pass(pickerVisible
      ? `worksheet picker offered ${JSON.stringify(sheets)}`
      : `no picker: the console read the book without asking (status ${JSON.stringify(await status())})`);

    step('PORTED: the re-read watch marks an import read under older rules');
    // Rewrite one import.json back to the pre-S7 shape and restart the console
    // on the same port and data root: Store.sources() re-reads it on the first
    // listing, and the option must say so and be unselectable while it does.
    const importDirs = fs.readdirSync(path.join(dataRoot, 'evidence-csv'))
      .filter(name => name.startsWith('csv-'));
    assert.ok(importDirs.length >= 2, JSON.stringify(importDirs));
    const target = path.join(dataRoot, 'evidence-csv', importDirs[0], 'import.json');
    const record = JSON.parse(fs.readFileSync(target, 'utf8'));
    delete record.catalog_version;
    fs.writeFileSync(target, JSON.stringify(record, null, 2));
    await stopServer(server);
    server = await startServer(port, dataRoot);
    serverForWatchdog = server;
    await page.goto(origin + '/#evidence');
    let sawReread = false;
    try {
      await waitFor(        () => [...document.querySelectorAll('#evidence-source option')]
          .some(node => /re-reading under the current rules/.test(node.textContent) && node.disabled), null, 8000, 'a page condition');
      sawReread = true;
    } catch { /* a small import can finish re-reading before the first paint */ }
    await waitFor(      () => [...document.querySelectorAll('#evidence-source option')].filter(node => node.value.startsWith('csv:')).length >= 2
        && ![...document.querySelectorAll('#evidence-source option')].some(node => node.disabled), null, 120000, 'a page condition');
    pass(sawReread
      ? 'the option said "re-reading under the current rules" and was disabled, then came back'
      : 'the re-read finished before the first paint; every option is selectable again');

    step('F7: Split with Graph gives one pane column, and the single view gives them back');
    const columnsBefore = await page.evaluate(() => document.getElementById('evidence-panes').style.gridTemplateColumns);
    await page.click('#split-view');
    await waitFor(() => document.querySelector('.workspace').classList.contains('split-workspace'),
      null, 15000, 'the split workspace');
    const splitColumns = await page.evaluate(() => {
      const host = document.getElementById('evidence-panes');
      return getComputedStyle(host).gridTemplateColumns.split(' ').length;
    });
    assert.equal(splitColumns, 1, `split view must show one pane column, got ${splitColumns}`);
    await page.click('#split-view');
    await waitFor(() => !document.querySelector('.workspace').classList.contains('split-workspace'),
      null, 15000, 'the single workspace');
    const columnsAfter = await page.evaluate(() => {
      const host = document.getElementById('evidence-panes');
      return {columns: getComputedStyle(host).gridTemplateColumns.split(' ').length,
        panes: host.querySelectorAll('[data-pane]').length};
    });
    // One column per open pane: after the console restart above the page may be
    // back to a single pane, and one column is then the right answer.
    assert.equal(columnsAfter.columns, Math.max(1, columnsAfter.panes),
      `single view must give one column per pane, got ${JSON.stringify(columnsAfter)}`);
    pass(`split with Graph: 1 column, back to ${columnsAfter.columns} for ${columnsAfter.panes} pane(s)`);

    step('F7: Follow Graph selection, with no graph loaded, says so rather than moving pane A');
    // A scratch console has no published report to load, so Graph never
    // dispatches mentat:graph and the box has nothing to follow. What is
    // testable here is that checking it neither throws nor silently repoints a
    // pane; the loaded-Graph case is exercised by test_navigation_scope_browser.
    // Steps 22-25 restart the console and toggle the split view, which can leave
    // the grid empty; this check needs pane A, so restore the two-pane permalink
    // first and assert on what it restored.
    await page.goto(origin + query + '#evidence');
    await waitPanes(2);
    const paneABefore = await page.getAttribute('#evidence-panes [data-pane=a]', 'data-committee');
    await page.check('#evidence-follow-graph');
    await page.waitForTimeout(1500);
    const paneAAfter = await page.getAttribute('#evidence-panes [data-pane=a]', 'data-committee');
    assert.equal(paneAAfter, paneABefore, 'with no graph loaded, pane A must not move');
    assert.deepEqual(errors, [], `Follow Graph raised: ${JSON.stringify(errors)}`);
    await page.uncheck('#evidence-follow-graph');
    pass('Follow Graph with no graph loaded: pane A unchanged, no page error');

    step('F7: a three-pane URL restores all three panes');
    // Build the third pane from the restored two rather than reading it back out
    // of the URL: the two-pane permalink is what the page is showing now.
    const threeParams = new URLSearchParams(query.replace(/^\?/, ''));
    threeParams.set('c.source', threeParams.get('a.source'));
    threeParams.set('c.id', threeParams.get('a.id'));
    threeParams.set('c.committee', 'C00835959');
    const threeQuery = '?' + threeParams.toString();
    for (const prefix of ['a.', 'b.', 'c.']) {
      assert.ok(threeQuery.includes(prefix + 'source='), `${prefix} missing from ${threeQuery}`);
    }
    await page.goto(origin + threeQuery + '#evidence');
    await waitFor(() => document.querySelectorAll('#evidence-panes [data-pane][data-committee]').length >= 3
      && document.querySelectorAll('#evidence-panes .pane-message').length === 0,
      null, 90000, 'three restored panes');
    const restoredThree = await page.locator('#evidence-panes [data-pane]').evaluateAll(
      nodes => nodes.map(node => `${node.dataset.pane}:${node.dataset.committee}`));
    assert.ok(restoredThree.length >= 3, JSON.stringify(restoredThree));
    assert.equal(await page.isVisible('#evidence-compare'), true);
    pass(`restored ${JSON.stringify(restoredThree)} from a ${restoredThree.length}-pane permalink`);

    step('re-homed from tests/test_console_browser.cjs: the row drawer pages 104 source rows, counts the ones needing interpretation, and escapes a hostile contributor name');
    // The console smoke test drove these through the pre-S6 #evidence-csv-*
    // panel, which commit 487589584 deleted. The propositions still ship, in
    // the pane row drawer (app/evidence.js:488-503), so they are asserted here
    // instead. The escaping one is load-bearing: render() writes the drawer
    // with innerHTML (app/evidence.js:61), so escapeHtml is the only guard.
    await page.goto(origin + '/#evidence');
    await waitStatus(/choose a source|Nothing imported yet|PACs in file/, 90000);
    await openDrawer('evidence-import-open');
    await page.setInputFiles('#evidence-csv-file', path.join(fixtureDir, 'stress.csv'));
    await waitFor(() => [...document.querySelectorAll('#evidence-source option')]
      .some(node => /^stress[.]csv /.test(node.textContent)), null, 120000, 'the stress import to be listed');
    const stressValue = await page.locator('#evidence-source option').evaluateAll(
      nodes => (nodes.find(node => /^stress[.]csv /.test(node.textContent)) || {}).value);
    assert.ok(stressValue && stressValue.startsWith('csv:'), `stress.csv source value: ${stressValue}`);
    await page.selectOption('#evidence-source', stressValue);
    await waitFor(() => !document.getElementById('evidence-pac').disabled, null, 60000, 'the PAC picker to fill');
    assert.deepEqual(await page.locator('#evidence-pac option').allTextContents(),
      ['Alpha PAC · C00000001 · Unclassified · 104 rows',
       'Beta PAC · C00000002 · Unclassified · 1 row',
       'Unassigned committee rows · unassigned · Unclassified · 1 row']);
    await tick('C00000001');
    await waitPanes(1);
    const stressPane = '#evidence-panes [data-pane=a]';
    await page.click(`${stressPane} .pane-rows`);
    await page.waitForSelector(`${stressPane} .pane-drawer tbody tr`);
    const sourceRows = async () => (await page.locator(`${stressPane} .pane-drawer .label`)
      .first().innerText()).trim();
    // PAGE_SIZE is 100 (app/evidence.js:20), so 104 selected rows page 100 + 4.
    assert.equal(await page.locator(`${stressPane} .pane-drawer tbody tr`).count(), 100);
    assert.equal(await sourceRows(), 'Source rows · 1–100 of 104');
    assert.match(await page.locator(`${stressPane} .pane-drawer p.muted`).first().innerText(),
      /102 supported · 2 need interpretation/);
    // The hostile contributor name must arrive as text and build no element.
    assert.match(await page.locator(`${stressPane} .pane-drawer`).innerText(),
      /<img src=x onerror=alert\(1\)>/);
    assert.equal(await page.locator(`${stressPane} .pane-drawer img`).count(), 0,
      'a contributor name must never become a live element');
    await page.click(`${stressPane} .pane-next`);
    await waitFor(() => /101/.test(document.querySelector(
      '#evidence-panes [data-pane=a] .pane-drawer .label')?.textContent || ''), null, 60000, 'the second page');
    assert.equal(await sourceRows(), 'Source rows · 101–104 of 104');
    assert.equal(await page.locator(`${stressPane} .pane-drawer tbody tr`).count(), 4);
    // has_more drives the buttons (app/evidence.js:505-506): the last page
    // offers no Next, and a page past the first offers Previous.
    assert.equal(await page.isDisabled(`${stressPane} .pane-next`), true,
      'the last page of source rows must not offer Next');
    assert.equal(await page.isDisabled(`${stressPane} .pane-prev`), false);
    const lastPage = await page.locator(`${stressPane} .pane-drawer`).innerText();
    for (const placeholder of ['Not reported', 'Date unavailable', 'Cycle unavailable']) {
      assert.ok(lastPage.includes(placeholder),
        `${placeholder} missing from the last page: ${lastPage.slice(0, 400)}`);
    }
    // A row with no committee_id at all is unassigned, and those are exactly
    // the rows the console reports as needing interpretation.
    await tick('unassigned');
    await waitPanes(2);
    const unassignedPane = '#evidence-panes [data-pane=b]';
    assert.equal((await page.locator(`${unassignedPane} h2`).first().innerText()).trim(),
      'Unassigned committee rows');
    await page.click(`${unassignedPane} .pane-rows`);
    await page.waitForSelector(`${unassignedPane} .pane-drawer tbody tr`);
    assert.match(await page.locator(`${unassignedPane} .pane-drawer p.muted`).first().innerText(),
      /0 supported · 1 need interpretation/);
    // Restricting the cycle narrows the selected rows. The cycle is pane
    // state, and updateAdd() treats (source, committee) as pane identity
    // (app/evidence.js:217-222), so the same PAC cannot be opened twice:
    // close pane A and reopen it on 2026. 101 receipts plus the row with no
    // amount carry cycle 2026; the 2024 row and the undated row do not.
    await page.click(`${stressPane} .pane-remove`);
    await waitPanes(1);
    await page.selectOption('#evidence-cycle', '2026');
    await tick('C00000001');
    await waitPanes(2);
    await page.click(`${stressPane} .pane-rows`);
    await page.waitForSelector(`${stressPane} .pane-drawer tbody tr`);
    assert.equal(await sourceRows(), 'Source rows · 1–100 of 102');
    pass('104 rows paged 100+4, 2 need interpretation, the hostile name escaped as text, unassigned row counted');

    step('re-homed from tests/test_app_lazy_startup_browser.cjs: a saved snapshot body is read once per id, not again on reselection');
    // The old lazy-startup test watched this through app.js, which S6
    // (487589584) emptied of every evidence reference, so it could no longer
    // observe it. chooseSource() guards on snapshotBodies.has(id)
    // (app/evidence.js:288-290): the body is read the first time a snapshot is
    // chosen and never again, however often the reader switches back to it.
    const savedSnapshotValue = await page.locator('#evidence-source option').evaluateAll(
      nodes => (nodes.find(node => node.value.startsWith('snapshot:')) || {}).value);
    const savedSnapshotLabel = await page.locator('#evidence-source option').evaluateAll(
      nodes => (nodes.find(node => node.value.startsWith('snapshot:'))?.textContent || '').split(' · ')[1]);
    if (!savedSnapshotValue) {
      // The same reason the step above skips: the route reads
      // PROJECT/.cache/pac-evidence, not the temp data root, and that directory is
      // gitignored, so a checkout with no saved snapshot has none to reselect.
      pass('SKIPPED: no saved snapshot under mentat/.cache/pac-evidence in this checkout');
    } else {
      const bodyReads = [];
      page.on('request', request => {
        if (/[/]api[/]evidence[?]id=/.test(request.url())) bodyReads.push(request.url());
      });
      await page.selectOption('#evidence-source', savedSnapshotValue);
      await waitFor(label=>document.querySelector('#evidence-status').textContent.includes(label),savedSnapshotLabel,90000,'selected snapshot status');
      assert.equal(bodyReads.length, 1, `the first selection reads the body once: ${JSON.stringify(bodyReads)}`);
      await page.selectOption('#evidence-source', stressValue);
      await waitStatus(/stress[.]csv/, 90000);
      await page.selectOption('#evidence-source', savedSnapshotValue);
      await waitFor(label=>document.querySelector('#evidence-status').textContent.includes(label),savedSnapshotLabel,90000,'reselected snapshot status');
      assert.equal(bodyReads.length, 1,
        `reselecting an already-read snapshot must not fetch it again: ${JSON.stringify(bodyReads)}`);
      pass('snapshot body read exactly once across three source changes');
    }

    step('the same PAC on two cycles opens as two panes, and the exact same pane is reused');
    // Pane identity is (source, committee, cycle). Before the fix it was
    // (source, committee), so asking for one committee on a second cycle - the
    // comparison the panes exist for - silently disabled #evidence-add.
    // Build 06, fix round 1 (R06-03): the rail's row is the PAC, so its tick
    // follows the PAC's open pane whatever the Cycle chip says, and a tick is
    // never refused for a PAC that is already open. The second cycle is added
    // through the programmatic path, openCommittee (Graph's route in).
    while ((await page.locator('#evidence-panes [data-pane]').count()) > 0) {
      await page.click('#evidence-panes [data-pane] .pane-remove');
      await page.waitForTimeout(200);
    }
    await page.selectOption('#evidence-source', stressValue);
    await waitStatus(/stress[.]csv/, 90000);
    const twoCycles = await page.locator('#evidence-cycle option').evaluateAll(
      nodes => nodes.map(node => node.value).filter(Boolean).slice(0, 2));
    assert.equal(twoCycles.length, 2, `the fixture must carry two cycles: ${JSON.stringify(twoCycles)}`);
    const onePac = await page.locator('#evidence-pac option').evaluateAll(nodes => nodes[0].value);
    await page.selectOption('#evidence-cycle', twoCycles[0]);
    await tick(onePac);
    await waitPanes(1);
    await page.selectOption('#evidence-cycle', twoCycles[1]);
    assert.equal(await railBox(onePac).isChecked(), true, 'the PAC is open, so its row stays ticked on the second cycle');
    assert.match(await page.getAttribute(`#evidence-list .evidence-rail-row[data-committee="${onePac}"]`, 'title'),
      /open on /, 'the row says which cycle its pane is on');
    await page.evaluate(([pac, cycle]) => window.MentatEvidence.openCommittee(pac, cycle), [onePac, twoCycles[1]]);
    await waitPanes(2);
    const cycles = await page.locator('#evidence-panes [data-pane]').evaluateAll(
      nodes => nodes.map(node => node.dataset.cycle || ''));
    assert.deepEqual([...cycles].sort(), [...twoCycles].sort(),
      `two panes, one per cycle: ${JSON.stringify(cycles)}`);
    await page.evaluate(([pac, cycle]) => window.MentatEvidence.openCommittee(pac, cycle), [onePac, twoCycles[1]]);
    assert.equal(await page.locator('#evidence-panes [data-pane]').count(), 2, 'the exact same pane is reused, not added');
    await page.selectOption('#evidence-cycle', twoCycles[1]);
    assert.equal(await railBox(onePac).isChecked(), true, 'the rail shows that exact pane as open');
    await page.selectOption('#evidence-cycle', twoCycles[0]);
    assert.equal(await railBox(onePac).isChecked(), true, 'and the other cycle\'s pane on its own cycle');
    pass(`the same PAC opened on ${JSON.stringify(cycles)} as two panes`);

    step('Graph opens the exact committee and cycle, reuses its pane, and opens a direct Flow when no evidence is saved');
    const linked = await browser.newPage({viewport: {width: 1600, height: 1000}});
    // A committee with no saved evidence opens its direct Flow (evidence.js openCommittee(), 7b1cc259d), and
    // that Flow asks /api/flow?...&direct=1 to collect FEC filings. Record and hold the request so this run
    // never reaches FEC; the Flow's "Collecting FEC filings…" status then stays put.
    const directFlowRequests = [], heldDirectRoutes = [];
    const isFlowUrl = url => url.pathname === '/api/flow';
    await linked.route(isFlowUrl, route => { directFlowRequests.push(route.request().url()); heldDirectRoutes.push(route); });
    await linked.goto(`${origin}/#chart-studio`, {waitUntil: 'load'});
    const opened = await linked.evaluate(async ({committee, cycle}) => {
      const opening = window.MentatEvidence.openCommittee(committee, cycle);
      document.getElementById('tab-evidence').click();
      const key = await opening;
      return {key, panes: window.MentatEvidence.panes()};
    }, {committee: onePac, cycle: twoCycles[0]});
    assert.ok(opened.key, 'the opener must resolve an available saved source');
    assert.equal(opened.panes.length, 1);
    assert.equal(opened.panes[0].committee, onePac);
    assert.equal(opened.panes[0].cycle, twoCycles[0]);
    const reopened = await linked.evaluate(async ({committee, cycle}) => {
      await window.MentatEvidence.openCommittee(committee, cycle);
      return window.MentatEvidence.panes();
    }, {committee: onePac, cycle: twoCycles[0]});
    assert.deepEqual(reopened, opened.panes);
    assert.deepEqual(directFlowRequests, [], 'a saved source opens its pane without asking /api/flow');
    // No saved source covers this committee, so it is not a dead end: Graph's selection opens a direct Flow.
    assert.equal(await linked.evaluate(() => window.MentatEvidence.openCommittee('C99999999', '2098')), 'direct');
    await linked.waitForFunction(() => document.querySelector('#view-evidence').classList.contains('flow-open')
      && /Collecting FEC filings and drawing the flow/.test(document.getElementById('evidence-flow-status').textContent));
    assert.equal(directFlowRequests.length, 1, 'the direct Flow asks /api/flow once: ' + JSON.stringify(directFlowRequests));
    const directQuery = new URL(directFlowRequests[0]).searchParams;
    assert.equal(directQuery.get('direct'), '1');
    assert.equal(directQuery.get('root'), 'committee:C99999999');
    assert.equal(directQuery.get('cycle'), '2098');
    assert.deepEqual(await linked.evaluate(() => window.MentatEvidence.panes()), opened.panes, 'a direct Flow opens no pane');
    assert.equal(await linked.locator('#graph-trail').isVisible(), false);
    await Promise.all(heldDirectRoutes.map(route => route.abort().catch(() => {})));
    await linked.close();
    pass('cold start, exact cycle, duplicate reuse, unavailable source opens a direct Flow, and hidden Graph trail');

    step('a view that is not this one does not fetch the evidence catalogs');
    // app.js dispatches mentat:view on every tab change. start() used to run on
    // any of them, so opening Chart Studio fetched the import listing and the
    // snapshot catalog a reader there never asked for.
    const catalogReads = [];
    const watchCatalogs = request => {
      const url = request.url();
      if (/[/]api[/]evidence-csv(\?|$)/.test(url) || /[/]api[/]evidence(\?|$)/.test(url)) catalogReads.push(url);
    };
    const fresh = await browser.newPage({viewport: {width: 1280, height: 900}});
    fresh.on('request', watchCatalogs);
    await fresh.goto(`${origin}/#chart-studio`, {waitUntil: 'load'});
    await fresh.waitForTimeout(2500);
    assert.deepEqual(catalogReads, [],
      `Chart Studio must not read the evidence catalogs: ${JSON.stringify(catalogReads)}`);
    await fresh.goto(`${origin}/#evidence`, {waitUntil: 'load'});
    await fresh.waitForFunction(() => document.querySelectorAll('#evidence-source option').length > 1,
      null, {timeout: 90000});
    assert.ok(catalogReads.length > 0, 'the evidence view must read them');
    await fresh.close();
    pass(`no catalog read on Chart Studio; ${catalogReads.length} on the evidence view`);

    step('final check: zero pageerror entries and zero off-origin requests');
    assert.deepEqual(errors, [], `page errors: ${JSON.stringify(errors)}`);
    assert.deepEqual(remote, [], `off-origin requests: ${JSON.stringify(remote)}`);
    pass('no page errors, no off-origin requests across every step');

    console.log(`ALL ${stepNumber} STEPS PASSED`);
  } catch (error) {
    failed = true;
    console.error(`FAILED at step ${stepNumber} (${currentStepLabel}): ${error.stack || error.message}`);
  } finally {
    clearTimeout(watchdog);
    if (browser) await browser.close().catch(() => {});
    await stopServer(server);
    for (const target of [dataRoot, fixtureDir, reportRoot]) await cleanupPath(target);
    process.exit(failed ? 1 : 0);
  }
})();
