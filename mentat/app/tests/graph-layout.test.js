/* The bar-row geometry, asserted without a browser.

   graph.js returns at its own `typeof document === 'undefined'` guard, so
   requiring it here costs one pure function and no DOM. The invariant is the
   point: dot, label, bar, value, share and sparkline are pairwise disjoint and
   all inside [0, W], at every width the panels are drawn at.

   Run this suite with the glob form; Node 24 rejects a bare directory, and the
   glob cannot live in the block comment above because it closes it:
     node --test "app/tests/**"/"*.test.js"      (cwd: mentat/) */
const test = require('node:test');
const assert = require('node:assert/strict');
const layout = require('../graph.js');
const theme = require('../theme.js');

// 506/508 is the candidates panel in the 1fr column at a 1920 viewport and
// 888 the categories panel in the 1.7fr column beside it; 520 is
// panelWidth()'s own fallback for a panel with no width yet, and 920 is the
// clamp ceiling.
const WIDTHS = [420, 508, 520, 585, 760, 888, 920];
// $0.00 through a trillion, and then two value columns wider than the panel can
// hold - the case a fixed 96px gutter and an unclamped measurement both lose.
const VALUE_WIDTHS = [0, 34, 78, 102, 137, 400, 4000];

function boxes(box) {
  const named = [];
  if (box.dotColumn > 0) named.push(['dot', 0, box.dotColumn]);
  named.push(['label', box.labelLeft, box.labelRight]);
  named.push(['bar', box.barLeft, box.barLeft + box.barMax]);
  if (box.valueWidth > 0) named.push(['value', box.valueRight - box.valueWidth, box.valueRight]);
  named.push(['share', box.shareRight - box.shareWidth, box.shareRight]);
  if (box.sparkWidth > 0) named.push(['spark', box.sparkX, box.sparkX + box.sparkWidth]);
  return named;
}

for (const width of WIDTHS) {
  for (const party of [false, true]) {
    for (const spark of [false, true]) {
      test('[graph-layout] W=' + width + ' party=' + party + ' spark=' + spark
        + ': the six columns are pairwise disjoint and inside the panel', () => {
        for (const valueWidth of VALUE_WIDTHS) {
          const box = layout.layoutColumns(width, {party: party, spark: spark, valueWidth: valueWidth});
          const named = boxes(box);
          const where = 'W=' + width + ' party=' + party + ' spark=' + spark + ' valueWidth=' + valueWidth;
          assert.equal(box.width, width, where + ': the width came back changed');
          for (const entry of named) {
            assert.ok(entry[2] > entry[1], where + ': the ' + entry[0] + ' box has no width');
            assert.ok(entry[1] >= 0, where + ': ' + entry[0] + ' starts at ' + entry[1] + ', outside the panel');
            assert.ok(entry[2] <= box.width, where + ': ' + entry[0] + ' ends at ' + entry[2] + ', past W=' + box.width);
          }
          for (let i = 0; i < named.length; i += 1) {
            for (let j = i + 1; j < named.length; j += 1) {
              const a = named[i], b = named[j];
              assert.ok(a[2] <= b[1] || b[2] <= a[1],
                where + ': ' + a[0] + ' [' + a[1] + ', ' + a[2] + '] overlaps '
                  + b[0] + ' [' + b[1] + ', ' + b[2] + ']');
            }
          }
        }
      });
    }
  }
}

/* The approved board's card rhythm. These are constants because two drawings
   read them - the single-line panels and the two-line Top organizations - and a
   pitch decided twice is a pitch that drifts. */
test('[graph-layout] the row rhythm is the board\u2019s, and the two-line row is taller', () => {
  assert.equal(layout.ROW_PITCH, 23, 'board M4 measures 23px between row baselines');
  assert.equal(layout.ROW_HEAD, 24, 'and 24px of head room above the first');
  assert.equal(layout.LABEL_SIZE, 13, 'label and value are 13px');
  assert.equal(layout.SHARE_SIZE, 12, 'the share is 12px and muted');
  // Top organizations gives the name its own line at the card's full width, so
  // its rows are taller than a single-line row by the drop plus the bar band.
  assert.ok(layout.TWO_LINE_PITCH > layout.ROW_PITCH,
    'a two-line row cannot be shorter than a one-line row');
  assert.ok(layout.TWO_LINE_NAME_DROP > 0 && layout.TWO_LINE_NAME_DROP < layout.TWO_LINE_PITCH,
    'the bar line sits below the name and inside the row');
  // A row is drawn from top-15 for pitch-4, so consecutive rows never touch.
  for (const pitch of [layout.ROW_PITCH, layout.TWO_LINE_PITCH]) {
    assert.ok(pitch - 4 < pitch, 'the row band leaves a gap to the next row');
    assert.ok(pitch - 4 > layout.LABEL_SIZE, 'and is taller than the text it holds');
  }
});

test('[graph-layout] a left-read label starts at labelLeft and still fits its column', () => {
  // Candidates and companies read left from one x (the board), so the first
  // glyph lands at labelLeft rather than at labelRight minus the measured
  // width. Either way the drawn text has to fit between the two.
  for (const width of WIDTHS) {
    for (const party of [false, true]) {
      const box = layout.layoutColumns(width, {party: party, spark: false, valueWidth: 78});
      const drawn = theme.fitText('AMERICAN BANKERS ASSOCIATION PAC', box.labelWidth,
        box.labelSize, '', party ? ' \u00b7 D' : '');
      const drawnWidth = theme.measureText(drawn, box.labelSize, '');
      assert.ok(box.labelLeft + drawnWidth <= box.labelRight + 1e-9,
        'W=' + width + ': a left-read label runs past its column into the bar');
      assert.ok(box.labelLeft + drawnWidth <= box.barLeft,
        'W=' + width + ': and must never reach the bar');
    }
  }
});

test('[graph-layout] the label column is the same in every panel at the same width', () => {
  for (const width of WIDTHS) {
    const plain = layout.layoutColumns(width, {party: false, spark: false, valueWidth: 78});
    const withSpark = layout.layoutColumns(width, {party: false, spark: true, valueWidth: 78});
    const withDot = layout.layoutColumns(width, {party: true, spark: false, valueWidth: 78});
    assert.equal(plain.labelRight, withSpark.labelRight, 'bars start at the same x with or without a sparkline');
    assert.equal(plain.labelRight, withDot.labelRight, 'and with or without a party dot');
    assert.equal(plain.barLeft, withSpark.barLeft);
    assert.equal(plain.barLeft, withDot.barLeft);
    assert.ok(plain.labelRight >= 130 && plain.labelRight <= 260, 'the label column stays inside its clamp');
  }
});

test('[graph-layout] the party dot sits inside the column reserved for it', () => {
  for (const width of WIDTHS) {
    const box = layout.layoutColumns(width, {party: true, spark: true, valueWidth: 78});
    assert.equal(box.dotColumn, layout.DOT_COL);
    assert.ok(box.dotCX - 4.5 >= 0, 'the dot does not run off the left edge');
    assert.ok(box.dotCX + 4.5 <= box.dotColumn, 'and does not reach into the label');
    assert.equal(box.labelLeft, box.dotColumn, 'the label starts where the dot column ends');
    const plain = layout.layoutColumns(width, {party: false, spark: true, valueWidth: 78});
    assert.equal(plain.labelLeft, 0, 'a panel with no dots uses the whole label column');
    assert.equal(plain.labelWidth - box.labelWidth, layout.DOT_COL);
  }
});

test('[graph-layout] the sparkline slot is the width the sparkline is drawn at', () => {
  for (const width of WIDTHS) {
    const box = layout.layoutColumns(width, {party: false, spark: true, valueWidth: 78});
    assert.equal(box.sparkWidth, layout.SPARK_W);
    // drawSparklines() lays its last point at SPARK_W - 2 and the end dot has
    // r = 1.8, so the line and its dot stay inside the slot.
    assert.ok(layout.SPARK_W - 2 + 1.8 <= box.sparkWidth);
    // Paul, 2026-09-23 ("Keep the dollar column, match the aesthetic"): the
    // slot ends SPARK_INSET inside the panel's right edge, where boards M4 and
    // 1 end Company comparison's sparkline (it was 4px), and never past it.
    assert.equal(box.sparkX + box.sparkWidth, box.width - layout.SPARK_INSET, 'the slot ends where board M4 ends it');
    assert.ok(layout.SPARK_INSET >= 4 && box.sparkX + box.sparkWidth <= box.width - 4, 'and inside the panel');
    assert.equal(box.shareRight, box.sparkX - layout.SPARK_GAP,
      'the share is anchored to its own column, not to the panel edge');
    const plain = layout.layoutColumns(width, {party: false, spark: false, valueWidth: 78});
    assert.equal(plain.sparkWidth, 0, 'a panel with no sparkline reserves nothing for one');
    assert.equal(plain.shareRight, box.width - 4);
  }
});

test('[graph-layout] the bar keeps its 48px minimum even against an absurd value column', () => {
  for (const width of WIDTHS) {
    for (const spark of [false, true]) {
      const box = layout.layoutColumns(width, {party: true, spark: spark, valueWidth: 4000});
      assert.ok(box.barMax >= layout.BAR_MIN, 'the bar never shrinks below its minimum');
      assert.ok(box.valueWidth < 4000, 'and the value column is clamped to what is left');
      assert.ok(box.barLeft + box.barMax <= box.valueRight - box.valueWidth,
        'so the bar cannot run into the value column');
    }
  }
});

/* A3. fitText() protects a suffix from the cut so a truncated candidate still
   says which side of the aisle it is on. A party CODE is worth that; a party
   VALUE is not. Live, a row rendered as "\u2026 \u00b7 Other / Unclassified":
   21 of the column's characters were spent on a suffix that had to survive,
   and the name - the thing the row is about - was the bare ellipsis. */
const LONG_PARTY = 'Other / Unclassified';

test('[graph-layout] only a party code is protected from the cut', () => {
  for (const code of ['D', 'R', 'DFL', 'REP', 'IND']) {
    const parts = layout.labelParts('WARNER, MARK ROBERT', code);
    assert.equal(parts.text, 'WARNER, MARK ROBERT', code + ': the name is not touched');
    assert.equal(parts.suffix, ' \u00b7 ' + code, code + ': the code is the protected suffix');
  }
  for (const party of [LONG_PARTY, 'DEMOCRAT', 'Unknown']) {
    const parts = layout.labelParts('WARNER, MARK ROBERT', party);
    assert.equal(parts.suffix, '', party + ': a long party is not protected');
    assert.equal(parts.text, 'WARNER, MARK ROBERT \u00b7 ' + party,
      party + ': it rides on the label and is fitted with it');
  }
  for (const empty of ['', null, undefined, '   ']) {
    const parts = layout.labelParts('KHANNA, ROHIT', empty);
    assert.equal(parts.suffix, '', 'no party, no suffix');
    assert.equal(parts.text, 'KHANNA, ROHIT', 'and nothing appended');
  }
  assert.ok(layout.PARTY_CODE_MAX >= 3, 'a three-letter code such as DFL is still a code');
});

test('[graph-layout] a long party never leaves the name a bare ellipsis', () => {
  const names = ['WARNER, MARK ROBERT', 'BLUNT ROCHESTER, LISA',
    'AMERICA\u2019S CREDIT UNIONS PAC OF CREDIT UNION NATIONAL ASSOCIATION, INC.'];
  for (const width of WIDTHS) {
    const box = layout.layoutColumns(width, {party: true, spark: false, valueWidth: 78});
    for (const name of names) {
      const parts = layout.labelParts(name, LONG_PARTY);
      const drawn = theme.fitText(parts.text, box.labelWidth, box.labelSize, '', parts.suffix);
      assert.notEqual(drawn[0], '\u2026',
        'W=' + width + ': "' + drawn + '" starts with the cut mark, so no name is shown');
      assert.ok(theme.measureText(drawn, box.labelSize, '') <= box.labelWidth + 1e-9,
        'W=' + width + ': "' + drawn + '" is wider than its column');
      // A code, by contrast, still survives the cut whole.
      const coded = layout.labelParts(name, 'D');
      const codedDrawn = theme.fitText(coded.text, box.labelWidth, box.labelSize, '', coded.suffix);
      assert.ok(codedDrawn.endsWith(' \u00b7 D'),
        'W=' + width + ': "' + codedDrawn + '" lost the party code');
    }
  }
});

test('[graph-layout] a measured label always fits the column the layout gives it', () => {
  const names = ['WARNER, MARK ROBERT', 'BLUNT ROCHESTER, LISA', 'KHANNA, ROHIT',
    'AMERICA\u2019S CREDIT UNIONS PAC OF CREDIT UNION NATIONAL ASSOCIATION, INC.'];
  for (const width of WIDTHS) {
    for (const party of [false, true]) {
      const box = layout.layoutColumns(width, {party: party, spark: false, valueWidth: 78});
      for (const name of names) {
        const drawn = theme.fitText(name, box.labelWidth, box.labelSize, '', party ? ' \u00b7 D' : '');
        const drawnWidth = theme.measureText(drawn, box.labelSize, '');
        assert.ok(drawnWidth <= box.labelWidth + 1e-9,
          '"' + drawn + '" is ' + drawnWidth + 'px in a ' + box.labelWidth + 'px column at W=' + width);
        // the text is anchored at labelRight, so this is where its first glyph lands
        assert.ok(box.labelRight - drawnWidth >= box.labelLeft - 1e-9,
          '"' + drawn + '" starts at ' + (box.labelRight - drawnWidth) + ', left of the label column');
      }
    }
  }
});

/* Build 02 fix round 1 (R02-M1): a compare half's columns. The half bodies
   measure 254/270px at 1440, 292/312px at 1600 and 331/353px at 1920 (two
   scopes), 370px for a third at 1440. */
const HALF_WIDTHS = [200, 254, 270, 292, 312, 331, 353, 370, 480];
test('[graph-layout] compare halves: every column is disjoint and inside the half', () => {
  for (const width of HALF_WIDTHS) {
    for (const party of [false, true]) {
      for (const spark of [false, true]) {
        for (const shareWidth of [0, 46, 60]) {
          for (const bar of [true, false]) {
            for (const labelWidth of [0, 40, 97, 147, 400]) {
              for (const valueWidth of [0, 53, 66, 400]) {
                const box = layout.compactColumns(width, {party, spark, shareWidth, bar, labelWidth, valueWidth,
                  leftRead: party});
                const where = JSON.stringify({width, party, spark, shareWidth, bar, labelWidth, valueWidth});
                const named = boxes(box).filter(entry => entry[2] > entry[1]);
                for (const entry of named) {
                  assert.ok(entry[1] >= 0 && entry[2] <= box.width, where + ': ' + entry[0] + ' leaves the half');
                }
                for (let i = 0; i < named.length; i += 1) {
                  for (let j = i + 1; j < named.length; j += 1) {
                    const a = named[i], b = named[j];
                    assert.ok(a[2] <= b[1] || b[2] <= a[1], where + ': ' + a[0] + ' overlaps ' + b[0]);
                  }
                }
                assert.equal(box.barMax > 0, bar, where + ': a bar exactly when one was asked for');
                if (bar) assert.ok(box.barMax >= layout.COMPACT_BAR_MIN, where + ': bar under its floor');
              }
            }
          }
        }
      }
    }
  }
});

test('[graph-layout] compare halves: the bar is a magnitude, not a swatch (R02-M1)', () => {
  // G1 at 1440: recipient types (254px, no share column, "$2,838,523" 66px,
  // "Party Committee" 97px) and candidates (270px, party, "$116,533" 53px).
  const types1440 = layout.compactColumns(254, {shareWidth: 0, valueWidth: 66, labelWidth: 97});
  assert.ok(types1440.barMax >= 60, 'recipient-type bar at 1440 is ' + types1440.barMax + 'px');
  assert.ok(types1440.labelWidth >= 97, 'the widest category name still fits whole at 1440');
  const people1440 = layout.compactColumns(270, {party: true, leftRead: true, shareWidth: 0, valueWidth: 53, labelWidth: 125});
  assert.ok(people1440.barMax >= 60, 'candidate bar at 1440 is ' + people1440.barMax + 'px');
  assert.ok(people1440.labelWidth >= 100, 'candidate names keep at least 100px at 1440');
  // The boards' 1600 frame keeps the share column (G1 prints it).
  const types1600 = layout.compactColumns(292, {shareWidth: 46, valueWidth: 66, labelWidth: 97});
  assert.ok(types1600.barMax >= 70, 'recipient-type bar at 1600 is ' + types1600.barMax + 'px');
  const types1920 = layout.compactColumns(331, {shareWidth: 46, valueWidth: 66, labelWidth: 97});
  assert.ok(types1920.barMax >= 95, 'recipient-type bar at 1920 is ' + types1920.barMax + 'px');
  // A company half with no bar gives the name everything up to the amount.
  const firms = layout.compactColumns(254, {spark: true, bar: false, shareWidth: 0, valueWidth: 66, leftRead: true});
  assert.equal(firms.barMax, 0);
  assert.ok(firms.labelWidth >= 120, 'company names keep ' + firms.labelWidth + 'px beside the sparkline');
  // Below the share threshold a half drops its share column; at it, keeps it.
  assert.ok(layout.COMPACT_SHARE_MIN_W > 270 && layout.COMPACT_SHARE_MIN_W <= 292,
    'the share column drops at 1440 (270px) and stays at 1600 (292px)');
});

test('[graph-layout] short forms of a compared value, longest first (R02-M3, R02-m9)', () => {
  const dash = String.fromCharCode(0x2013);
  assert.deepEqual(layout.nameForms('company', 'Goldman Sachs Bank NA'), ['Goldman Sachs Bank NA', 'Goldman Sachs', 'Goldman']);
  assert.deepEqual(layout.nameForms('company', 'JP Morgan Chase Bank NA'), ['JP Morgan Chase Bank NA', 'JP Morgan Chase', 'JP Morgan']);
  assert.deepEqual(layout.nameForms('company', 'Bank of America NA'), ['Bank of America NA', 'Bank of America']);
  assert.deepEqual(layout.nameForms('company', 'Navy Federal Credit Union'), ['Navy Federal Credit Union', 'Navy Federal', 'Navy']);
  assert.deepEqual(layout.nameForms('company', 'Citibank'), ['Citibank']);
  const toDate = ' ' + String.fromCharCode(0xb7) + ' to date';
  assert.deepEqual(layout.nameForms('cycle', '2025' + dash + '2026' + toDate),
    ['2025' + dash + '2026' + toDate, '2025' + dash + '26' + toDate]);
  assert.deepEqual(layout.nameForms('cycle', '2023' + dash + '2024'), ['2023' + dash + '2024', '2023' + dash + '24']);
  assert.deepEqual(layout.nameForms('industry', 'Credit Union'), ['Credit Union'], 'an industry is never shortened');
});

/* Final fix round (TA1-01..03): what a two-scope half gives up so that its
   names are not cut, at the widths the console draws the halves at
   (compactWidth of the half bodies, 2026-09-23: candidates 311/312px at 1600
   and 352/353px at 1920; recipient types and companies 292/293px and
   330/331px). Label widths are MentatTheme.measureText at 13px of the live
   names comparing Banking | Credit Union and Goldman Sachs | JP Morgan Chase:
   "HUIZENGA, WILLIAM P · R" 161.1px (the widest whole candidate name G1
   draws), "WARNER, MARK ROBERT · D" 180.6px and its short form
   "WARNER, MARK R · D" 136px, "Party Committee" 96.8px, "JP Morgan Chase
   Bank NA" 159px; amounts "$116,533" 53.3px and "$2,838,523" 65.1px. */
test('[graph-layout] compare halves: no candidate name is cut at 1600 or 1920 (TA1-01)', () => {
  for (const width of [311, 312, 352, 353]) {
    // Comparing industries (G1, G4): the bar goes and the share stays.
    const g1 = layout.compactYield(width, {party: true, leftRead: true, shareWidth: 46, valueWidth: 53.3, labelWidth: 161.1}, ['bar']);
    const box1 = layout.compactColumns(width, g1);
    assert.deepEqual(g1.yielded, ['bar']);
    assert.ok(box1.labelWidth >= 161.1, width + 'px: G1 name column ' + box1.labelWidth);
    assert.ok(box1.labelWidth >= 136, width + 'px: "WARNER, MARK R" fits');
    assert.equal(box1.barMax, 0);
    assert.equal(box1.shareWidth, 46, 'G1 keeps its share column');
    // Comparing companies (G3): the share goes and the bar stays; the whole
    // of "WARNER, MARK ROBERT" fits beside a bar that is not a swatch.
    const g3 = layout.compactYield(width, {party: true, leftRead: true, valueWidth: 53.3, labelWidth: 180.6}, ['share', 'room']);
    const box3 = layout.compactColumns(width, g3);
    assert.ok(box3.labelWidth >= 180.6, width + 'px: G3 name column ' + box3.labelWidth);
    assert.equal(box3.shareWidth, 0, 'G3 draws no share column');
    assert.ok(box3.barMax >= 30, width + 'px: G3 bar ' + box3.barMax);
  }
  // A half wide enough for its names gives nothing up.
  const wide = layout.compactYield(480, {party: true, leftRead: true, shareWidth: 46, valueWidth: 53.3, labelWidth: 120}, ['bar', 'share', 'room']);
  assert.deepEqual(wide.yielded, []);
  assert.ok(layout.compactColumns(480, wide).barMax > 0);
});

test('[graph-layout] compare halves: a company half gives up its share before its name (TA1-02)', () => {
  for (const width of [292, 293, 330, 331]) {
    const plan = layout.compactYield(width, {spark: true, bar: false, leftRead: true, shareWidth: 46, valueWidth: 65.1, labelWidth: 159},
      width < layout.COMPACT_NAME_SHARE_W ? ['bar', 'share'] : ['bar']);
    const box = layout.compactColumns(width, plan);
    assert.deepEqual(plan.yielded, ['share'], 'the bar was already gone; the share goes');
    assert.ok(box.labelWidth >= 159, width + 'px: "JP Morgan Chase Bank NA" fits in ' + box.labelWidth);
    assert.equal(box.sparkWidth, 40, 'the sparkline slot stays');
  }
  assert.ok(layout.COMPACT_NAME_SHARE_W > 331 && layout.COMPACT_NAME_SHARE_W <= 353,
    'the share yields in a two-scope half at 1920 (331px) and stays in a 1440 third (353px and up)');
});

test('[graph-layout] compare halves: "Party Committee" stays whole and the bar shrinks first (TA1-03)', () => {
  for (const width of [292, 293, 330, 331]) {
    const plan = layout.compactYield(width, {shareWidth: 46, valueWidth: 65.1, labelWidth: 96.8}, ['room']);
    const box = layout.compactColumns(width, plan);
    assert.ok(box.labelWidth >= 96.8, width + 'px: recipient-type label column ' + box.labelWidth);
    assert.equal(box.shareWidth, 46, 'the share column stays');
    // The bar pays for the whole name: at 1600 it is 62px where R02-M1's
    // default layout (above) keeps 70px and cuts the name.
    assert.ok(box.barMax >= 60, width + 'px: the bar is still a magnitude, ' + box.barMax + 'px');
  }
});

test('[graph-layout] short forms of a candidate keep the surname and first given name (TA1-01)', () => {
  assert.deepEqual(layout.nameForms('person', 'WARNER, MARK ROBERT'), ['WARNER, MARK ROBERT', 'WARNER, MARK R', 'WARNER, MARK']);
  assert.deepEqual(layout.nameForms('person', 'KEAN, THOMAS H. JR.'), ['KEAN, THOMAS H. JR.', 'KEAN, THOMAS H', 'KEAN, THOMAS']);
  assert.deepEqual(layout.nameForms('person', 'HAGERTY, BILL'), ['HAGERTY, BILL']);
  assert.deepEqual(layout.nameForms('person', 'MADONNA'), ['MADONNA'], 'a name without a comma is never shortened');
});

// Paul, 2026-09-23, on the drawer's "Contributions / Other" segment: "It
// should be two separate segments".
test('[graph-layout] the drawer offers Contributions and Other as two segments beside Support and Oppose', () => {
  const side = (direction, candidates, amount, extra) => Object.assign({direction, candidates, amount}, extra || {});
  const names = totals => layout.drawerSides(totals).map(row => row.direction);
  assert.deepEqual(layout.DRAWER_SIDE_ORDER, ['Contribution', 'Other', 'Support', 'Oppose', 'Contribution / Other', 'Unknown']);
  // The measured default scope (Banking, 2025-2026, ActBlue): two halves, then
  // the independent expenditures; the empty unsplit side and Unknown stay out.
  const banking = [side('Contribution', 1000, 57425697), side('Other', 45, 23587307),
    side('Support', 120, 59963513), side('Oppose', 59, 82983605),
    side('Contribution / Other', 0, 0), side('Unknown', 0, 0)];
  assert.deepEqual(names(banking), ['Contribution', 'Other', 'Support', 'Oppose']);
  // An empty half is still a measurement when the source records codes.
  assert.deepEqual(names([side('Contribution', 14, 54500), side('Other', 0, 0), side('Support', 0, 0),
    side('Oppose', 0, 0), side('Contribution / Other', 0, 0), side('Unknown', 0, 0)]),
    ['Contribution', 'Other', 'Support', 'Oppose']);
  // A report that records no transaction type cannot be split: its one
  // recorded side stands where the two halves would, never beside two zeros.
  assert.deepEqual(names([side('Contribution', 0, 0), side('Other', 0, 0), side('Support', 1, 810),
    side('Oppose', 1, 999), side('Contribution / Other', 1, 111), side('Unknown', 0, 0)]),
    ['Support', 'Oppose', 'Contribution / Other']);
  // Mixed sources: coded halves and an uncoded remainder all show.
  assert.deepEqual(names([side('Contribution', 2, 116), side('Other', 1, 40), side('Support', 1, 777),
    side('Oppose', 1, 999), side('Contribution / Other', 1, 7), side('Unknown', 0, 5, {unidentified_amount: 5})]),
    ['Contribution', 'Other', 'Support', 'Oppose', 'Contribution / Other', 'Unknown']);
  // Order is the drawer's own, whatever order the rows arrive in.
  assert.deepEqual(names(banking.slice().reverse()), ['Contribution', 'Other', 'Support', 'Oppose']);
  assert.deepEqual(layout.drawerSides(null), []);
});

// Paul, 2026-09-23: "It should be two separate segments". Round-3 verify (D1):
// each segment counts its own candidates, so 1,000 + 45 is not the 1,005
// candidates behind them - 40 have both a 24K and a 24C. The drawer says so
// under the segments, from the server's count of the recorded bucket
// (graph_data.recorded_bucket, contribution_other_union).
test('[graph-layout] the drawer reconciles the two segments\' counts when a candidate is on both', () => {
  const side = (direction, candidates, amount) => ({direction, candidates, amount});
  const n = value => Number(value).toLocaleString();
  const union = (candidates, shared, sides) => ({direction: 'Contribution / Other',
    sides: sides || ['Contribution', 'Other', 'Contribution / Other'], candidates, on_more_than_one_side: shared});
  const halves = (contribution, other, unsplit) => [side('Contribution', contribution, 1), side('Other', other, 1),
    side('Support', 120, 1), side('Oppose', 59, 1), side('Contribution / Other', unsplit || 0, unsplit ? 1 : 0), side('Unknown', 0, 0)];
  // The measured default scope (Banking, 2025-2026, ActBlue C00401224).
  const actblue = layout.drawerOverlap(halves(1000, 45), union(1005, 40));
  assert.deepEqual(actblue.sides, ['Contribution', 'Other']);
  assert.equal(actblue.counted - actblue.shared, actblue.candidates, '1,000 + 45 - 40 = 1,005');
  assert.equal(actblue.text, `Contributions and Other: ${n(1005)} different candidates; 40 have both, so they are in both counts.`);
  assert.equal(actblue.basis('Contribution'), '40 of them are also counted under Other');
  assert.equal(actblue.basis('Other'), '40 of them are also counted under Contributions');
  // Credit Union 2025-2026, CULAC C00007880: every Other candidate also has a contribution.
  const culac = layout.drawerOverlap(halves(634, 9), union(634, 9));
  assert.equal(culac.text, 'Contributions and Other: 634 different candidates; 9 have both, so they are in both counts.');
  // One candidate: singular.
  assert.equal(layout.drawerOverlap(halves(2, 1), union(2, 1)).text,
    'Contributions and Other: 2 different candidates; 1 has both, so it is in both counts.');
  assert.equal(layout.drawerOverlap(halves(2, 1), union(2, 1)).basis('Other'), '1 of them is also counted under Contributions');
  // Nobody on both: the counts already add up, and nothing is said.
  assert.equal(layout.drawerOverlap(halves(1, 1), union(2, 0)), null);
  // An empty half (C00390633: 14 | 0) cannot overlap; neither can a bucket the server did not count.
  assert.equal(layout.drawerOverlap(halves(14, 0), union(14, 0)), null);
  assert.equal(layout.drawerOverlap(halves(1000, 45), null), null);
  assert.equal(layout.drawerOverlap(halves(1000, 45), undefined), null);
  // A mixed source (coded halves and an uncoded remainder): three counts.
  const mixed = layout.drawerOverlap(halves(2, 1, 1), union(3, 1));
  assert.deepEqual(mixed.sides, ['Contribution', 'Other', 'Contribution / Other']);
  assert.equal(mixed.text, 'Contributions, Other and Contributions / Other: 3 different candidates; 1 is in more than one count.');
  assert.equal(mixed.basis('Other'), '1 candidate is counted under more than one of Contributions, Other and Contributions / Other');
  // The drawer's labels are the one table graph.js draws its segments with.
  assert.deepEqual(layout.DRAWER_SIDE_LABEL, {'Contribution': 'Contributions', 'Other': 'Other',
    'Contribution / Other': 'Contributions / Other', 'Support': 'Support', 'Oppose': 'Oppose', 'Unknown': 'Unknown'});
});

// r4 code review F5: the page's Direction select offered all six sides in
// every candidate view, so a reader could land on a side that cannot hold
// anything and read the empty panel as a zero. It offers what the view holds.
test('[graph-layout] the Direction select offers the sides this candidate view can hold (F5)', () => {
  const side = (direction, candidates, amount) => ({direction, candidates, amount});
  const choose = (mode, totals, current) => layout.directionChoices(mode, totals, current);
  // The golden page's six options, in its order and wording.
  assert.deepEqual(layout.DIRECTION_OPTIONS.map(option => option[0]),
    ['Support', 'Oppose', 'Contribution', 'Other', 'Contribution / Other', 'Unknown']);
  const html = require('node:fs').readFileSync(require('node:path').join(__dirname, '..', 'index.html'), 'utf8');
  const golden = html.match(/<select id="graph-direction">(.*?)<\/select>/)[1];
  assert.deepEqual([...golden.matchAll(/<option value="([^"]*)">([^<]*)<\/option>/g)].map(m => [m[1], m[2]]),
    layout.DIRECTION_OPTIONS, 'the select the page starts with is the list the code narrows');
  // 8812, Banking, activity (the review's measurement): every row carries a
  // code, so the unsplit side is empty (0 candidates, $0) - not offered, and
  // an empty Unknown is not offered either. Support and Oppose always are.
  const banking = [side('Contribution', 2665, 371539491), side('Other', 387, 157538526), side('Support', 120, 59963513),
    side('Oppose', 59, 82983605), side('Contribution / Other', 0, 0), side('Unknown', 0, 0)];
  assert.deepEqual(choose('activity', banking, 'Support').values, ['Support', 'Oppose', 'Contribution', 'Other']);
  // An old permalink's 'Contribution / Other' becomes the larger half it was
  // split into, as the drawer opens on its largest side.
  assert.equal(choose('activity', banking, 'Contribution / Other').value, 'Contribution');
  assert.equal(choose('activity', [side('Contribution', 1, 10), side('Other', 1, 90), side('Support', 0, 0),
    side('Oppose', 0, 0), side('Contribution / Other', 0, 0), side('Unknown', 0, 0)], 'Contribution / Other').value, 'Other');
  // An empty Unknown falls back to the page's default.
  assert.equal(choose('activity', banking, 'Unknown').value, 'Support');
  // A report written before the Transaction Type column: the halves cannot
  // hold anything, so the recorded side stands in their place, and a chosen
  // half becomes it.
  const uncoded = [side('Contribution', 0, 0), side('Other', 0, 0), side('Support', 1, 810), side('Oppose', 1, 999),
    side('Contribution / Other', 1, 111), side('Unknown', 0, 0)];
  assert.deepEqual(choose('activity', uncoded, 'Support').values, ['Support', 'Oppose', 'Contribution / Other']);
  assert.equal(choose('activity', uncoded, 'Contribution').value, 'Contribution / Other');
  assert.equal(choose('activity', uncoded, 'Other').value, 'Contribution / Other');
  // A mixed source offers all of it, Unknown too when it holds an amount.
  assert.deepEqual(choose('activity', [side('Contribution', 2, 116), side('Other', 1, 40), side('Support', 1, 777),
    side('Oppose', 1, 999), side('Contribution / Other', 1, 7), Object.assign(side('Unknown', 0, 5), {unidentified_amount: 5})], 'Other').values,
    ['Support', 'Oppose', 'Contribution', 'Other', 'Contribution / Other', 'Unknown']);
  // Estimates carry no transaction type (graph_data.estimate_rows): the two
  // halves never apply there, and a half chosen in activity becomes the
  // recorded bucket rather than an empty estimates panel.
  const estimates = choose('estimates', null, 'Contribution');
  assert.deepEqual(estimates.values, ['Support', 'Oppose', 'Contribution / Other', 'Unknown']);
  assert.equal(estimates.value, 'Contribution / Other');
  assert.equal(choose('estimates', null, 'Other').value, 'Contribution / Other');
  assert.equal(choose('estimates', null, 'Oppose').value, 'Oppose');
  assert.deepEqual(estimates.options.find(option => option[0] === 'Contribution / Other'), ['Contribution / Other', 'Contributions / other']);
  // Before an answer names its sides (or when activity is unavailable), the
  // split sides are offered and the chosen side is kept - a permalink is not
  // rewritten before it is read.
  assert.deepEqual(choose('activity', null, 'Support').values, ['Support', 'Oppose', 'Contribution', 'Other']);
  assert.deepEqual(choose('activity', [], 'Contribution / Other').values, ['Support', 'Oppose', 'Contribution', 'Other', 'Contribution / Other']);
  assert.equal(choose('activity', [], 'Unknown').value, 'Unknown');
  assert.equal(choose('activity', null, 'nonsense').value, 'Support', 'a value the server does not answer for is not kept');
  // Labels are the golden page's.
  assert.deepEqual(choose('activity', banking, 'Support').options,
    [['Support', 'Support'], ['Oppose', 'Opposition'], ['Contribution', 'Contributions'], ['Other', 'Other activity']]);
});

// r4 audit AUDIT-MEN-A-2: G2's change chips re-derived. The Banking 2023-2024
// | 2025-2026 figures below are the two scopes' own /api/graph answers (r5
// men-graph report, g2_2024.json / g2_2026.json), recomputed independently.
test('[graph-layout] a cycle change chip is the latest against the earlier, rounded once (G2)', () => {
  const chip = (latest, earlier) => layout.changeText(latest, earlier);
  const M = '−';
  // Headline figures: net giving, direct to candidates, recipient organizations.
  assert.deepEqual(chip(12003181.12, 26107204.16), {text: M + '54.0%', direction: 'down'});
  assert.deepEqual(chip(2160828.13, 3795016.39), {text: M + '43.1%', direction: 'down'});
  assert.deepEqual(chip(9822364.89, 20517150.25), {text: M + '52.1%', direction: 'down'});
  // Recipient types, candidates and companies as drawn on the live G2.
  assert.equal(chip(2838522.75, 7632130.98).text, M + '62.8%');
  assert.equal(chip(2276748.42, 2461229.27).text, M + '7.5%');
  assert.equal(chip(116533, 16010).text, '+628%', 'WARNER: 627.9% is at least 100%, so whole percent');
  assert.equal(chip(58250, 9250).text, '+530%');
  assert.equal(chip(46500, 52505).text, M + '11.4%');
  assert.equal(chip(44375, 4000).text, '+1009%');
  assert.equal(chip(811222.4, 903972.27).text, M + '10.3%', 'PNC is outside 2023-2024\'s top six but in its data: a chip, not "new"');
  // A name the earlier cycle does not have at all.
  assert.deepEqual(chip(45238.09, undefined), {text: 'new', direction: ''});
  assert.deepEqual(chip(45238.09, null), {text: 'new', direction: ''});
  // An earlier net of zero has no percentage.
  assert.deepEqual(chip(100, 0), {text: 'new', direction: ''});
  assert.deepEqual(chip(0, 0), {text: '0.0%', direction: ''}, 'nothing then and nothing now is no change, not "new"');
  // An earlier net below zero was there: not "new", and the change is in dollars.
  assert.deepEqual(chip(100, -10), {text: '+$110', direction: 'up', dollars: true});
  assert.deepEqual(chip(-50, -10), {text: M + '$40', direction: 'down', dollars: true});
  assert.deepEqual(chip(-10, -10), {text: '$0', direction: '', dollars: true});
  // Rounded once: never "+100.0%", never a signed zero.
  assert.equal(chip(199.96, 100).text, '+100%');
  assert.equal(chip(199.94, 100).text, '+99.9%');
  assert.deepEqual(chip(100.04, 100), {text: '0.0%', direction: ''});
  assert.deepEqual(chip(99.96, 100), {text: '0.0%', direction: ''});
  assert.deepEqual(chip(100, 100), {text: '0.0%', direction: ''});
  // Falling to nothing is the whole of it.
  assert.deepEqual(chip(0, 250), {text: M + '100%', direction: 'down'});
});

// Paul, 2026-09-23: "You should be able to expand each section individually
// like with a census panel".
test('[graph-layout] every Graph card folds on its own, under its own remembered key', () => {
  assert.deepEqual(layout.FOLD_SECTIONS, ['category-panel', 'candidate-panel', 'company-panel', 'level-panel',
    'organization-panel', 'compare-table', 'drawer-panel']);
  assert.equal(new Set(layout.FOLD_SECTIONS).size, layout.FOLD_SECTIONS.length, 'one key per section');
  assert.equal(layout.FOLD_KEY, 'mentat-graph-folded:');
  // Every one of them is a card in the golden page, with a heading to fold on.
  const html = require('node:fs').readFileSync(require('node:path').join(__dirname, '..', 'index.html'), 'utf8');
  for (const name of layout.FOLD_SECTIONS)
    assert.match(html, new RegExp('<section id="graph-' + name + '" class="panel graph-panel[^"]*"[^>]*><div class="panel-heading">'), name);
});
