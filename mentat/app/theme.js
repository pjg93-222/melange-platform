/* Share the locally served brand tokens with inline SVG and image exports.

   This file also owns the one measured text fitter the console draws labels
   with. A budget counted in CHARACTERS cannot fit a proportional face: the
   6.6px-per-character guess it replaces is 28% short of Arial's mean uppercase
   advance, which is how a candidate name grew leftwards out of its column and
   through the party dot. The budget here is pixels, measured on a canvas
   context cached for the life of the page.

   node:test has no canvas, so measurement falls back to a deterministic
   per-character estimate in em. The same code path runs either way, so the
   geometry tests are reproducible without a browser. */
(function (root) {
  'use strict';
  const hasDocument = typeof document !== 'undefined' && !!document.documentElement;
  const style = hasDocument && typeof getComputedStyle === 'function'
    ? getComputedStyle(document.documentElement) : null;
  const token = name => (style ? style.getPropertyValue(name).trim() : '');

  // One canvas for the life of the page. A context per measurement is what
  // would make a measured fitter slower than the guess it replaces.
  let context;
  function measureContext() {
    if (context !== undefined) return context;
    context = null;
    try {
      if (hasDocument && typeof document.createElement === 'function') {
        const canvas = document.createElement('canvas');
        context = (canvas.getContext && canvas.getContext('2d')) || null;
      }
    } catch (error) { context = null; }
    return context;
  }

  // Advance widths in em for the no-canvas case. These are averages, not a
  // font: they are here so a budget is still refused rather than unbounded.
  const UPPER = 0.68, LOWER = 0.50, DIGIT = 0.556, SPACE = 0.278, OTHER = 0.30;
  function estimate(text, sizePx) {
    let em = 0;
    for (const character of text) {
      if (character === ' ') em += SPACE;
      else if (character >= '0' && character <= '9') em += DIGIT;
      else if (character >= 'A' && character <= 'Z') em += UPPER;
      else if (character >= 'a' && character <= 'z') em += LOWER;
      else em += OTHER;
    }
    return em * sizePx;
  }

  function measureText(text, sizePx, family) {
    const value = text == null ? '' : String(text);
    const size = Number(sizePx) || 0;
    if (!value || size <= 0) return 0;
    const ctx = measureContext();
    if (!ctx) return estimate(value, size);
    ctx.font = size + 'px ' + (family || theme.sans || 'sans-serif');
    return ctx.measureText(value).width;
  }

  const ELLIPSIS = '\u2026';
  // A cut name may not end on the comma or the middle dot that was only there
  // to separate it from the part just removed.
  const TRAILING = /[\s,\u00b7]+$/;

  // `suffix` is the part that must survive the cut - a candidate's party, so a
  // truncated row still says which side of the aisle it is on.
  function fitText(text, maxPx, sizePx, family, suffix) {
    const value = text == null ? '' : String(text);
    const tail = suffix == null ? '' : String(suffix);
    const budget = Number(maxPx);
    if (!(budget > 0)) return ELLIPSIS + tail;
    if (measureText(value + tail, sizePx, family) <= budget) return value + tail;
    const room = budget - measureText(ELLIPSIS, sizePx, family) - measureText(tail, sizePx, family);
    if (!(room > 0)) return ELLIPSIS + tail;
    const characters = Array.from(value);
    let low = 0, high = characters.length;
    while (low < high) {
      const mid = Math.ceil((low + high) / 2);
      if (measureText(characters.slice(0, mid).join(''), sizePx, family) <= room) low = mid;
      else high = mid - 1;
    }
    const head = characters.slice(0, low).join('').replace(TRAILING, '');
    return head ? head + ELLIPSIS + tail : ELLIPSIS + tail;
  }

  function wrapText(value, maxWidth, sizePx, family) {
    const lines=[]; let line='';
    for(const word of String(value).trim().split(/\s+/)) {
      if(line && measureText(line+' '+word,sizePx,family)>maxWidth){lines.push(line);line='';}
      for(const character of (line?' '+word:word)) {
        if(line && measureText(line+character,sizePx,family)>maxWidth){lines.push(line);line='';}
        line+=character;
      }
    }
    if(line)lines.push(line);
    return lines.length?lines:[''];
  }

  // Twelve categorical colours for colouring groups (Paul, 2026-09-24). Slots
  // 1-4 are the brand's own teal, purple, mauve and gold - palette[0..3] - so a
  // chart of four groups or fewer is drawn exactly as before. Slots 5-12 (rose,
  // brown, sea green, wine, olive, lilac, terracotta, blue) replace the pale
  // aqua, lilac and taupe, whose aqua beside lilac measured 2.2 colour-blind and
  // 8.1 normal-vision difference. Measured with the dataviz validator (OKLab
  // x100): each new slot clears its neighbours and slot 1 - a pie closes its
  // last slice onto its first - by >= 10.6 colour-blind (protan/deutan) and
  // >= 15.1 normal-vision; each takes white or plum text at >= 4.5:1; none
  // sits within 11.9 of the party colours (--party-dem, --party-rep). The one
  // pair under the validator's normal-vision floor is the brand's own purple
  // beside mauve (14.3), which slots 1-4 keep as they were. Slots 5-12 are
  // literal values: the checks were run on these exact hexes. Slots 1-4 are
  // read from the same four tokens palette reads (2026-09-25), so a brand
  // change moves categoryColour(0..3) with palette[0..3]; the four literals
  // are only the fallback when a token cannot be read (node:test has no
  // stylesheet), and theme-palette.test.js holds them equal to acu-theme.css.
  // A token as #rrggbb, the one form mixHex reads: '#abc', '#aabbcc', rgb()
  // and rgba() (alpha dropped). Anything else is null, so the fallback is used.
  function hexOf(value) {
    const text = String(value || '').trim().toLowerCase();
    if (/^#[0-9a-f]{6}$/.test(text)) return text;
    if (/^#[0-9a-f]{3}$/.test(text)) return '#' + Array.from(text.slice(1), c => c + c).join('');
    const rgb = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)\s*(?:[,/]\s*[\d.]+%?\s*)?\)$/.exec(text);
    const channels = rgb ? rgb.slice(1, 4).map(Number) : [];
    if (!rgb || !channels.every(n => n >= 0 && n <= 255)) return null;
    return '#' + channels.map(n => Math.round(n).toString(16).padStart(2, '0')).join('');
  }
  const BRAND_FALLBACK = ['#327a7a', '#57447b', '#8b6984', '#eca91c'];
  const CATEGORICAL = Object.freeze(['--acu-teal', '--acu-purple', '--acu-mauve', '--acu-gold']
    .map((name, at) => hexOf(token(name)) || BRAND_FALLBACK[at]).concat(
      ['#de8aab', '#7d4b00', '#3cb0a3', '#83333e', '#99973f', '#a09eea', '#d88766', '#509ecd']));
  // Mix a #rrggbb colour toward another by a weight in 0-1, channel by channel.
  function mixHex(hex, toward, weight) {
    const channel = (value, at) => parseInt(value.slice(1 + at, 3 + at), 16);
    return '#' + [0, 2, 4].map(at => {
      const from = channel(hex, at);
      return Math.round(from + (channel(toward, at) - from) * weight).toString(16).padStart(2, '0');
    }).join('');
  }
  // The colour of group `index` (0-based). Past slot 12 a group takes slot
  // (index mod 12) mixed 45% toward white (groups 13-24) or 30% toward black
  // (25 on): the same colour for the same position every time, never generated
  // at random. The first 36 groups never share a colour; from group 37 the
  // shades repeat (hover text, the table and the CSV still name every group).
  function categoryColour(index) {
    const at = Number.isFinite(index) && index > 0 ? Math.floor(index) : 0;
    const base = CATEGORICAL[at % CATEGORICAL.length];
    if (at < CATEGORICAL.length) return base;
    return at < 2 * CATEGORICAL.length ? mixHex(base, '#ffffff', 0.45) : mixHex(base, '#000000', 0.30);
  }
  // Conduit (ActBlue, WinRed) is an earmark platform: the money that comes through it is individual
  // donors' earmarked giving, not a committee's own. It is the one recipient category with a colour of
  // its own - a dark slate that no categorical slot uses - and it takes no slot, so every other group in
  // a chart is coloured as it would be were Conduit not there. #1f242b is 19.1 OKLab x100 from the
  // nearest of the twelve (slot 2's purple) and 12.5 from the nearest under protan or deutan simulation
  // (slot 8's wine), so it clears the 10.6 colour-blind floor the slots keep; it holds 14.7:1 on the
  // paper and 15.6:1 on white, so white text on it reads. The earlier #555b66 was 8.5 from slot 2: no
  // neutral lighter than about OKLab L 0.27 clears the floor, because the dark slots (purple, wine,
  // brown) are far enough from grey only by lightness. theme-palette.test.js holds all of this.
  // categoryColours(names) is the list a renderer colours a named set of groups with; categoryNote(name)
  // is the hover text a Conduit mark, row or legend item carries.
  const CONDUIT = 'Conduit', CONDUIT_COLOUR = '#1f242b';
  const CONDUIT_NOTE = 'Conduit: an earmark platform (ActBlue, WinRed). These are individual donors\' earmarked contributions passed through it, not super PAC money.';
  function categoryColours(names) {
    let slot = 0;
    return Array.from(names || [], name => name === CONDUIT ? CONDUIT_COLOUR : categoryColour(slot++));
  }
  function categoryNote(name) { return name === CONDUIT ? CONDUIT_NOTE : ''; }
  const theme = Object.freeze({
    sans:token('--font-sans'),serif:token('--font-serif'),
    ink:token('--chart-ink'),muted:token('--muted'),grid:token('--chart-grid'),negative:token('--chart-negative'),
    palette:['--acu-teal','--acu-purple','--acu-mauve','--acu-gold','--acu-aqua','--acu-lilac','--acu-taupe'].map(token),
    categorical: CATEGORICAL, categoryColour: categoryColour,
    categoryColours: categoryColours, categoryNote: categoryNote, conduitColour: CONDUIT_COLOUR,
    measureText: measureText, fitText: fitText, wrapText: wrapText
  });

  if (typeof module !== 'undefined' && module.exports) module.exports = theme;
  root.MentatTheme = theme;
})(typeof globalThis === 'undefined' ? this : globalThis);

// Mobile scope disclosures preserve every field while leaving room for data.
(()=>{if(typeof document==='undefined'||typeof matchMedia==='undefined')return;const media=matchMedia('(max-width:760px)');const sync=()=>document.querySelectorAll('.analysis-scope-disclosure').forEach(node=>node.open=!media.matches);if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',sync);else sync();media.addEventListener('change',sync);})();
