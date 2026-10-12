/* Exercise the real scope and cross-reference modules with deliberately delayed
   synthetic responses. No production server, report or FEC request is used. */
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const os = require('node:os');
const {chromium} = require(require.resolve('playwright', {paths: [process.env.MENTAT_NODE_MODULES ||
  path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')]}));
const app = path.resolve(__dirname, '../app');
const report = industry => ({available: true, status: 'completed', selected_by: 'industry', industry,
  run_id: 'fixture-' + industry, report: {scope: {industries: [industry], companies: ['Synthetic company'], cycles: [2026]}}});
(async () => {
  const browser = await chromium.launch({channel: process.env.MENTAT_BROWSER_CHANNEL || 'chrome', headless: true});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('http://mentat.test/**', route => route.fulfill({contentType: 'text/html', body:
      '<section id="view-cross-reference"><button id="xref-refresh">Refresh</button><p id="xref-refresh-status" role="status"></p><div id="xref-results"></div></section>'}));
    await page.goto('http://mentat.test/?industry=Alpha#cross-reference');
    await page.evaluate(() => {
      // Ignore abort signals deliberately: late replies must be rejected even
      // after a response body has arrived and cancellation cannot stop it.
      window.requests = [];
      window.fetch = url => new Promise((resolve, reject) => window.requests.push({url, resolve, reject}));
      window.respond = (index, body) => window.requests[index].resolve({ok: true, json: async () => body});
      window.reject = index => window.requests[index].reject(new Error('Synthetic dependency failure'));
      // Manual refreshes drive this test; the polling timer must not repair a
      // missed scope change before the assertion can see it.
      window.setInterval = () => 0;
    });
    await page.addScriptTag({path: path.join(app, 'scope.js')});
    await page.addScriptTag({path: path.join(app, 'cross-reference.js')});
    assert.equal(await page.evaluate(() => requests.length), 1);
    await page.evaluate(() => MentatScope.write({industry: 'Beta'}));
    await page.evaluate(data => respond(0, data), report('Alpha'));
    const afterSwitch = await page.locator('#xref-results').innerText();
    console.log(JSON.stringify({scenario: 'late response after changing Alpha to Beta', displayed: afterSwitch,
      requests: await page.evaluate(() => requests.map(request => request.url))}));
    assert.doesNotMatch(afterSwitch, /fixture-Alpha/, 'A previous industry must never paint into the new scope');
    assert.equal(await page.evaluate(() => requests.length), 2, 'The new scope must start without waiting for the polling timer');
    await page.evaluate(data => respond(1, data), report('Beta'));
    assert.match(await page.locator('#xref-results').innerText(), /fixture-Beta/);

    // Clear the previous scope immediately, including while the replacement
    // request fails. Same-scope refresh failures may retain a labeled snapshot.
    await page.evaluate(() => MentatScope.write({industry: 'Gamma'}));
    assert.doesNotMatch(await page.locator('#xref-results').innerText(), /fixture-Beta/);
    await page.evaluate(() => reject(2));
    assert.match(await page.locator('#xref-refresh-status').innerText(), /Synthetic dependency failure/);
    assert.doesNotMatch(await page.locator('#xref-results').innerText(), /fixture-Beta/);
    await page.locator('#xref-refresh').click();
    await page.evaluate(data => respond(3, data), report('Gamma'));
    await page.locator('#xref-refresh').click();
    await page.evaluate(() => reject(4));
    assert.match(await page.locator('#xref-results').innerText(), /fixture-Gamma/);
    assert.match(await page.locator('#xref-refresh-status').innerText(), /older snapshot/);

    await page.evaluate(() => MentatScope.write({industry: 'Empty'}));
    await page.evaluate(() => respond(5, {available: false, status: 'empty', industry: 'Empty'}));
    assert.match(await page.locator('#xref-refresh-status').innerText(), /No cross-reference run for Empty/);
    assert.doesNotMatch(await page.locator('#xref-results').innerText(), /fixture-Gamma/);
    await page.evaluate(() => MentatScope.write({industry: 'Slow'}));
    await page.evaluate(() => MentatCrossReference.setActive(false));
    await page.evaluate(() => MentatScope.write({industry: 'Return'}));
    await page.evaluate(data => respond(6, data), report('Slow'));
    assert.doesNotMatch(await page.locator('#xref-results').innerText(), /fixture-Slow/);
    await page.evaluate(() => MentatCrossReference.setActive(true));
    await page.evaluate(data => respond(7, data), report('Return'));
    assert.match(await page.locator('#xref-results').innerText(), /fixture-Return/);

    await page.evaluate(() => MentatScope.write({industry: 'Older'}));
    await page.evaluate(() => MentatScope.write({industry: 'Newest'}));
    await page.evaluate(data => respond(9, data), report('Newest'));
    await page.evaluate(data => respond(8, data), report('Older'));
    assert.match(await page.locator('#xref-results').innerText(), /fixture-Newest/,
      'A reply arriving after the current result cannot overwrite it');
    await page.evaluate(() => MentatScope.write({industry: 'Failing'}));
    await page.evaluate(() => MentatScope.write({industry: 'Final'}));
    await page.evaluate(data => respond(11, data), report('Final'));
    await page.evaluate(() => reject(10));
    assert.match(await page.locator('#xref-results').innerText(), /fixture-Final/);
    assert.match(await page.locator('#xref-refresh-status').innerText(), /^Newest run for Final/,
      'A late failure cannot overwrite the current success status');
    assert.deepEqual(errors, []);
    console.log('PASS scope replacement, late response rejection, empty scope, failure, retry and tab reactivation');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
