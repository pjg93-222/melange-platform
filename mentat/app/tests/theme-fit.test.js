/* The measured text fitter, from the side the panels use it.

   node has no canvas, so MentatTheme.measureText falls back to its
   per-character estimate here and the same function is both the fitter's ruler
   and this suite's oracle: every assertion below says "whatever it measured
   with, the answer still fits in the budget it was given".

   Run this suite with the glob form; Node 24 rejects a bare directory, and the
   glob cannot live in the block comment above because it closes it:
     node --test "app/tests/**"/"*.test.js"      (cwd: mentat/) */
const test = require('node:test');
const assert = require('node:assert/strict');
const theme = require('../theme.js');

const SIZE = 12.5;
const width = text => theme.measureText(text, SIZE, '');
const fit = (text, budget, suffix) => theme.fitText(text, budget, SIZE, '', suffix);

// The label columns the four panels actually hand it: CHART_WIDTH 420..760 puts
// LABEL_RIGHT between 120 and 220, less 18 for the candidates panel's dot.
const BUDGETS = [102, 108, 120, 134, 152, 202, 220];
const NAMES = [
  'KHANNA, ROHIT',
  'WARNER, MARK ROBERT',
  'BLUNT ROCHESTER, LISA',
  'AMERICA\u2019S CREDIT UNIONS PAC OF CREDIT UNION NATIONAL ASSOCIATION, INC.',
  'Bank of America NA',
  'JPMorgan Chase & Co.',
  'Wells Fargo',
  'A'
];

test('[theme-fit] a string that already fits comes back untouched, suffix and all', () => {
  for (const suffix of ['', ' \u00b7 D']) {
    const text = 'KHANNA, ROHIT';
    assert.equal(fit(text, width(text + suffix) + 1, suffix), text + suffix);
    // exactly on the budget is a fit, not a cut
    assert.equal(fit(text, width(text + suffix), suffix), text + suffix);
  }
});

test('[theme-fit] nothing it returns is ever wider than the budget it was given', () => {
  for (const name of NAMES) {
    for (const budget of BUDGETS) {
      for (const suffix of ['', ' \u00b7 D', ' \u00b7 R']) {
        const answer = fit(name, budget, suffix);
        assert.ok(width(answer) <= budget + 1e-9,
          '"' + answer + '" is ' + width(answer) + 'px against a ' + budget + 'px budget');
      }
    }
  }
});

test('[theme-fit] the party survives the cut: the suffix is always on the end', () => {
  const suffix = ' \u00b7 D';
  for (const name of NAMES) {
    for (const budget of BUDGETS) {
      const answer = fit(name, budget, suffix);
      assert.ok(answer.endsWith(suffix), '"' + answer + '" lost its suffix');
    }
  }
  // and a budget too small for any of the name still keeps it
  assert.equal(fit('WARNER, MARK ROBERT', 1, suffix), '\u2026' + suffix);
});

test('[theme-fit] a cut never ends on the separator it cut next to', () => {
  for (const name of NAMES) {
    for (const budget of BUDGETS) {
      const answer = fit(name, budget, '');
      if (answer === name) continue;
      const head = answer.slice(0, -1);
      assert.ok(!/[\s,\u00b7]$/.test(head),
        '"' + answer + '" kept a trailing separator in front of the ellipsis');
    }
  }
  // the exact case, measured: a budget that lands the cut just past the comma
  const budget = width('WARNER, ') + width('\u2026');
  assert.equal(fit('WARNER, MARK ROBERT', budget, ''), 'WARNER\u2026');
  // and a middle dot is trimmed the same way a comma is
  assert.equal(fit('ACME \u00b7 X', width('ACME \u00b7 ') + width('\u2026'), ''), 'ACME\u2026');
});

test('[theme-fit] a degenerate budget answers with the ellipsis rather than throwing', () => {
  for (const budget of [0, -1, -400, NaN, undefined, null]) {
    assert.equal(fit('WARNER, MARK ROBERT', budget, ''), '\u2026');
    assert.equal(fit('WARNER, MARK ROBERT', budget, ' \u00b7 D'), '\u2026 \u00b7 D');
  }
  // an empty or absent name is not an error either
  assert.equal(fit('', 100, ''), '');
  assert.equal(fit(null, 100, ' \u00b7 D'), ' \u00b7 D');
  // a budget that cannot even hold the ellipsis and the suffix still answers
  assert.equal(fit('WARNER, MARK ROBERT', 2, ' \u00b7 D'), '\u2026 \u00b7 D');
});

test('[theme-fit] the fallback metric is finite, monotone, and zero for nothing', () => {
  assert.equal(width(''), 0);
  assert.ok(width('W') > 0);
  assert.ok(width('WW') > width('W'));
  assert.ok(width('WWWW') > width('WW'));
  assert.ok(width('W') > width('w'), 'uppercase is wider than lowercase');
  assert.equal(theme.measureText('AB', 0, ''), 0, 'a zero size measures nothing');
  assert.ok(Number.isFinite(width(NAMES[3])));
});
