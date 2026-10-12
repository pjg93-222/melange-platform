/* The Groups view (D-022 item 2), driven against a DOM stub.

   The view is the only place offices and organisations are listed now that the
   people index refuses them, and none of its behaviour -- collapsed by default,
   current people first inside a group, the `more` count, one fill per expand,
   the search, and the hand-off back to the People view -- is reachable from a
   Python test. Same shape as the other .cjs suites here: no browser, no server,
   no temp files.
*/
'use strict';
const fs = require('fs');
const nodePath = require('path');
const vm = require('vm');
const source = nodePath.join(__dirname, '..', '..', 'surfaces', 'staffer_live', 'staffer-groups.js');

function El(tag) {
  return {
    tagName: tag, children: [], textContent: '', className: '', open: false, dataset: {},
    listeners: {}, value: '', hidden: false,
    append(...kids) { this.children.push(...kids); },
    replaceChildren(...kids) { this.children = kids; },
    addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); },
    fire(name) { for (const fn of this.listeners[name] || []) fn({}); },
  };
}

const ids = {};
for (const id of ['search', 'groups-status', 'groups-note', 'groups-offices',
                  'groups-organizations', 'groups-offices-count', 'groups-organizations-count', 'groups-panel']) {
  ids[id] = El('div');
}

const payload = {
  schema: 'melange-staffer-groups-index-v1', ready: true, revision: 'rev-1',
  offices: [
    { id: 'container:office:house:a', kind: 'office', name: '2025 HON. A', people_count: 3, current_count: 1,
      more: 2, people: [{ id: 'p1', name: 'Current Person', current: true, title: 'Chief of Staff' },
                        { id: 'p2', name: 'Past Person', current: false, title: 'Aide' }] },
    { id: 'container:cmte:b', kind: 'office', name: 'House Committee B', people_count: 1, current_count: 0,
      more: 0, people: [{ id: 'p3', name: 'Committee Person', current: false, title: '' }] },
  ],
  organizations: [
    { id: 'organization:lda:registrant:1', kind: 'organization', name: 'HANCE SCARBOROUGH', people_count: 1,
      current_count: 0, more: 0, people: [{ id: 'p4', name: 'Bo Lobbyist', current: false, title: '' }] },
  ],
  counts: { offices: 2, organizations: 1, people_in_groups: 4 },
  note: 'Offices and organisations group people; they are not people.',
};

const handoff = [];
const sandbox = {
  console, Number, Math, JSON, Boolean, String, Array, Object, Set, Map, Promise, Error,
  setTimeout, clearTimeout,
  document: { getElementById: id => ids[id] || null, createElement: tag => El(tag) },
  history: { replaceState() {} },
  window: {
    addEventListener() {},
    StafferTabs: { show: view => handoff.push('tab:' + view) },
    StafferLive: { addNodes: nodes => handoff.push('add:' + nodes[0].id),
                   select: id => handoff.push('select:' + id) },
  },
  fetch: async () => ({ ok: true, json: async () => payload }),
};
sandbox.window.document = sandbox.document;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(source, 'utf8'), sandbox, { filename: 'staffer-groups.js' });

const failed = [];
let passed = 0;
const check = (name, ok) => {
  console.log((ok ? 'OK   ' : 'FAIL ') + name);
  if (ok) passed++; else failed.push(name);
};

check('the view publishes StafferGroups', typeof sandbox.window.StafferGroups === 'object');

(async () => {
  ids['groups-panel'].open = true;
  ids['groups-panel'].fire('toggle');
  await new Promise(resolve => setTimeout(resolve, 50));
  const offices = ids['groups-offices'];
  const organizations = ids['groups-organizations'];
  check('two office groups rendered', offices.children.length === 2);
  check('one organisation group rendered', organizations.children.length === 1);
  check('the group summary names the office', offices.children[0].children[0].textContent === '2025 HON. A');
  check('the group summary carries the current and total counts',
        offices.children[0].children[0].children[0].textContent === '1 current · 3 total');
  check('groups stay collapsed until opened', offices.children[0].children.length === 1);
  check('the status line reports the totals',
        ids['groups-status'].textContent === '2 offices · 1 organisations · 4 people listed');
  check('the note is shown verbatim', ids['groups-note'].textContent === payload.note);
  check('the office count line shows shown-of-total', ids['groups-offices-count'].textContent === '2 of 2');

  const group = offices.children[0];
  group.open = true;
  group.fire('toggle');
  const body = group.children[1];
  check('expanding renders the people list', Boolean(body) && body.className === 'group-people');
  check('the current person is first', body.children[0].children[0].textContent === 'Current Person');
  check('the person line marks Current and the title',
        body.children[0].children[0].children[0].textContent === 'Current · Chief of Staff');
  check('a past person is not marked Current',
        body.children[1].children[0].children[0].textContent === 'Aide');
  check('the capped remainder is counted, never dropped silently',
        group.children[2].textContent.startsWith('2 more in this group'));
  const filled = group.children.length;
  group.fire('toggle');
  check('a second toggle does not duplicate the list', group.children.length === filled);

  body.children[0].children[0].onclick();
  check('a person hands off to People: seed the node, then select it',
        handoff.join(',') === 'tab:explore,add:p1,select:p1');

  ids['search'].value = 'committee';
  sandbox.window.StafferGroups.search();
  check('search narrows the office list', offices.children.length === 1 &&
        offices.children[0].children[0].textContent === 'House Committee B');
  check('search reports how many are shown', ids['groups-offices-count'].textContent === '1 of 1');
  check('a search with no organisation match shows the empty line',
        organizations.children.length === 1 &&
        organizations.children[0].textContent === 'No organisations match that search.');

  console.log(`\n${passed} passed, ${failed.length} failed`);
  process.exit(failed.length ? 1 : 0);
})();
