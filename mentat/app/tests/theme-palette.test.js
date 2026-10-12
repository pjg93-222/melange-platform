/* The categorical palette's brand slots and the stylesheet they come from
   (code-review finding 12, 2026-09-25).

   theme.js reads slots 1-4 of MentatTheme.categorical from the same four
   tokens MentatTheme.palette reads first (--acu-teal, --acu-purple,
   --acu-mauve, --acu-gold), so a brand change moves categoryColour(0..3) with
   palette[0..3]. node has no stylesheet, so here those slots fall back to four
   literal hexes: this suite parses acu-theme.css and fails when a token and
   its literal drift apart, then loads theme.js against a stand-in computed
   style to show the slots follow the tokens in the forms a browser reports. */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP = path.join(__dirname, '..');
const BRAND_TOKENS = ['--acu-teal', '--acu-purple', '--acu-mauve', '--acu-gold'];
const CSS = fs.readFileSync(path.join(APP, 'acu-theme.css'), 'utf8');
const SOURCE = fs.readFileSync(path.join(APP, 'theme.js'), 'utf8');
const fallback = require('../theme.js');   // node: no stylesheet, so the literals

// The value acu-theme.css gives a custom property; each brand token is set once.
function declared(name) {
  const found = [...CSS.matchAll(new RegExp('(?:^|[;{\\s])' + name + '\\s*:\\s*([^;}]+)', 'g'))].map(m => m[1].trim().toLowerCase());
  assert.equal(found.length, 1, name + ' is declared once in acu-theme.css');
  return found[0];
}
// theme.js in a fresh context whose computed style answers from `tokens`.
function themeWith(tokens) {
  const sandbox = {document: {documentElement: {}}, getComputedStyle: () => ({getPropertyValue: name => tokens[name] || ''})};
  vm.runInNewContext(SOURCE, sandbox, {filename: path.join(APP, 'theme.js')});
  return sandbox.MentatTheme;
}
// Arrays from another context fail deepStrictEqual on their prototype alone.
const plain = list => [...list];

test('[theme-palette] the fallback literals for slots 1-4 are the acu-theme.css brand tokens', () => {
  const tokens = BRAND_TOKENS.map(declared);
  for (const value of tokens) assert.match(value, /^#[0-9a-f]{6}$/, 'a brand token is a #rrggbb hex');
  assert.deepEqual(fallback.categorical.slice(0, 4), tokens,
    'theme.js BRAND_FALLBACK drifted from acu-theme.css: change both together');
});

test('[theme-palette] with the stylesheet, slots 1-4 are the four tokens palette reads first', () => {
  const tokens = Object.fromEntries(BRAND_TOKENS.map(name => [name, declared(name)]));
  const theme = themeWith(tokens);
  assert.deepEqual(plain(theme.categorical.slice(0, 4)), plain(theme.palette.slice(0, 4)));
  assert.deepEqual(plain(theme.categorical), plain(fallback.categorical));
  assert.ok(Object.isFrozen(theme.categorical));
});

test('[theme-palette] a changed brand token moves categoryColour with it, in every form and past slot 12', () => {
  const theme = themeWith({'--acu-teal': ' #ABC ', '--acu-purple': 'rgb(1, 2, 3)',
    '--acu-mauve': 'rgba(255, 128, 0, 0.5)', '--acu-gold': 'rgb(16 32 48 / 50%)'});
  assert.deepEqual([0, 1, 2, 3].map(i => theme.categoryColour(i)), ['#aabbcc', '#010203', '#ff8000', '#102030']);
  assert.equal(theme.categoryColour(12), '#d0dae3', 'the tint rule reads the token too: #aabbcc 45% toward white');
  assert.equal(theme.categoryColour(24), '#77838f', 'and the shade rule: #aabbcc 30% toward black');
  assert.deepEqual(plain(theme.categorical.slice(4)), fallback.categorical.slice(4), 'slots 5-12 stay literal');
});

test('[theme-palette] a token it cannot read keeps that slot’s fallback', () => {
  const theme = themeWith({'--acu-teal': 'teal', '--acu-purple': 'rgb(300, 0, 0)', '--acu-mauve': '#12345'});
  assert.deepEqual(plain(theme.categorical.slice(0, 4)), fallback.categorical.slice(0, 4));
});

// OKLab x100, the measure theme.js's palette notes use. `mode` simulates protan or deutan colour
// blindness (Machado, Oliveira and Fernandes 2009, severity 1, in linear light) before measuring:
// the 10.6 floor below is the colour-blind one the twelve slots were validated to.
const FLOOR = 10.6;
const CVD = {
  none: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
  protan: [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
  deutan: [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.01182, 0.04294, 0.968881]]
};
const linear = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
  .map(c => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
function oklab(hex, mode) {
  const rgb = linear(hex);
  const [r, g, b] = CVD[mode].map(row => Math.min(1, Math.max(0, row[0] * rgb[0] + row[1] * rgb[1] + row[2] * rgb[2])));
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
}
const distance = (a, b, mode) => 100 * Math.hypot(...oklab(a, mode).map((v, i) => v - oklab(b, mode)[i]));
const luminance = hex => { const [r, g, b] = linear(hex); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const contrast = (a, b) => (Math.max(luminance(a), luminance(b)) + 0.05) / (Math.min(luminance(a), luminance(b)) + 0.05);

test('[theme-palette] slots 5-12 are the literals the colour checks were run on', () => {
  assert.deepEqual(fallback.categorical.slice(4),
    ['#de8aab', '#7d4b00', '#3cb0a3', '#83333e', '#99973f', '#a09eea', '#d88766', '#509ecd']);
  assert.equal(fallback.categorical.length, 12);
});

test('[theme-palette] Conduit’s own colour clears the slots’ 10.6 floor against all twelve, in normal, protan and deutan vision', () => {
  const conduit = fallback.conduitColour;
  assert.match(conduit, /^#[0-9a-f]{6}$/);
  assert.ok(!fallback.categorical.includes(conduit), 'Conduit takes no categorical slot');
  for (const mode of ['none', 'protan', 'deutan']) {
    fallback.categorical.forEach((slot, at) => {
      const gap = distance(conduit, slot, mode);
      assert.ok(gap >= FLOOR, `Conduit ${conduit} is ${gap.toFixed(1)} from slot ${at + 1} (${slot}) in ${mode} vision; the floor is ${FLOOR}`);
    });
  }
});

test('[theme-palette] Conduit’s colour holds 3:1 against the chart backgrounds, and white text on it 4.5:1', () => {
  const conduit = fallback.conduitColour;
  for (const background of ['#ffffff', declared('--acu-paper')]) {
    const ratio = contrast(conduit, background);
    assert.ok(ratio >= 3, `Conduit ${conduit} is ${ratio.toFixed(2)}:1 on ${background}; a mark needs 3:1`);
  }
  assert.ok(contrast(conduit, '#ffffff') >= 4.5, 'a label drawn in white on a Conduit mark stays readable');
});
