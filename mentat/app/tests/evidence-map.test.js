/* The pure arithmetic behind the interactive PAC map.
 *
 * evidence-map.js exports these through the same UMD guard graph.js uses, so
 * this suite loads the real shipped file - not a copy - and runs without a
 * document. Everything that needs a browser is proved in
 * tests/test_evidence_map_interaction_browser.cjs instead.
 */
const test = require('node:test');
const assert = require('node:assert');
const map = require('../evidence-map.js');
test('state inspection keeps neighboring states in the visible heat domain',()=>{
 const view={layer:'state',direction:'raised',focus:{kind:'state',key:'TX'},body:{by_state:[{key:'TX',amount:20},{key:'CA',amount:800}]}};
 assert.deepEqual(map.domainFor(view),[20,800]);
});

test('heat range follows Texas focus and settled viewport, excluding national outliers', () => {
  const shapes = {states:{TX:{bbox:[0,0,20,20]},CA:{bbox:[50,0,70,20]}}, districts:{
    'TX-01':{bbox:[0,0,10,20]},'TX-02':{bbox:[10,0,20,20]},'CA-01':{bbox:[50,0,70,20]}}};
  const view = {layer:'district',direction:'spent',shapes,body:{kinds:[{kind:'candidates',districts:[
    {key:'TX-01',amount:100},{key:'TX-02',amount:500},{key:'CA-01',amount:1000000}]}]}};
  assert.deepEqual(map.domainFor(view),[100,1000000]);
  assert.deepEqual(map.domainFor({...view,focus:{kind:'state',key:'TX'}}),[100,500]);
  assert.deepEqual(map.domainFor({...view,focus:{kind:'state',key:'TX'},comparisonScale:true}),[100,1000000],
    'Selecting a comparison bar preserves the national legend instead of recoloring unrelated states');
  assert.deepEqual(map.domainFor(view,[-1,-1,21,21]),[100,500]);
  assert.deepEqual(map.domainFor({...view,focus:{kind:'district',key:'TX-01'}},[-1,-1,21,21]),[100,500],
    'inspecting one district preserves comparison with the visible districts');
  assert.deepEqual(map.domainFor(view,[11,1,19,19]),[500,500]);
  assert.deepEqual(map.domainFor({...view,focus:{kind:'state',key:'TX'}},[51,1,69,19]),[1000000,1000000]);
  assert.deepEqual(map.domainFor(view,[90,90,100,100]),[0,0]);
  assert.deepEqual(map.domainFor({...view,off:new Set(['candidates'])},[-1,-1,21,21]),[0,0]);
});

test('ZIP and legislative heat use only visible, focused geography; reversals remain separate', () => {
  const points = {points:{'75001':[2,2,'lower48','TX'],'75002':[12,2,'lower48','TX'],'90001':[60,2,'lower48','CA']}};
  const body = {by_zip:[{zip:'75001',amount:20},{zip:'75002',amount:80},{zip:'90001',amount:900000}]};
  const shapes = {states:{TX:{bbox:[0,0,20,20]}}};
  const view = {layer:'zip',body,points,shapes,focus:{kind:'state',key:'TX'}};
  assert.deepEqual(map.domainFor(view),[20,80]);
  assert.deepEqual(map.domainFor(view,[10,0,20,20]),[80,80]);
  const sld = {layer:'sld',sldBundle:{state:'TX',districts:[{id:'1',bbox:[0,0,5,5]},
    {id:'2',bbox:[10,0,20,5]},{id:'3',bbox:[50,0,70,5]}]},sldRows:[
    {id:'1',amount:-10},{id:'2',amount:200},{id:'3',amount:900000}]};
  assert.deepEqual(map.domainFor(sld,[-1,-1,21,21]),[200,200]);
  assert.deepEqual(map.domainFor(sld,[-1,-1,6,6]),[0,0]);
  assert.equal(map.heatColour(-10,null,[0,0]),'#b85c67');
});

test('legislative estimates keep chambers, missing assignments, reversals and hidden receipts separate', () => {
  const bundle = {state:'VT', districts:[{id:'50R-7',name:'District R-7'}, {id:'50001',name:'District 1'}]};
  const body = {by_zip:[{zip:'00100',amount:70,rows:2}, {zip:'00101',amount:-10,rows:1},
    {zip:'00102',amount:900,rows:1}, {zip:'00103',amount:500,rows:1}]};
  const crosswalk = {'00100':['50001','50R-7'], '00101':['50001','50R-7'], '00102':['99001',''], '00103':['','']};
  assert.deepEqual(map.legislativeRows(body,bundle,crosswalk,'lower'),
    [{kind:'sld',key:'VT-lower-50R-7',id:'50R-7',name:'VT · District R-7',amount:60,rows:3,zips:2}]);
  assert.equal(map.legislativeRows(body,bundle,crosswalk,'upper')[0].id,'50001');
  assert.deepEqual(map.legislativeRows(body,null,crosswalk,'lower'),[]);
  assert.deepEqual(map.legislativeRows(body,bundle,crosswalk,'lower',new Set(['contributors'])),[]);
  assert.deepEqual(map.domainFor({layer:'sld',sldRows:[{amount:-3},{amount:60},{amount:600}]}),[60,600]);
});

test('spatial index matches a full scan without changing amounts or hanging on overscroll',()=>{
  const points=Array.from({length:3000},(_,i)=>({x:i%100*10,y:Math.floor(i/100)*10,amount:i-1500}));
  const index=map.indexPoints(points), box=[120,30,260,150];
  const expected=points.filter(p=>p.x>=box[0]&&p.x<=box[2]&&p.y>=box[1]&&p.y<=box[3]);
  assert.deepEqual(new Set(map.pointsInBox(index,box)),new Set(expected));
  assert.equal(map.pointsInBox(index,[-1e20,-1e20,1e20,1e20]).length,points.length);
  assert.deepEqual(map.pointsInBox(index,[1e10,1e10,1e20,1e20]),[]);
  assert.deepEqual(map.pointsInBox(map.indexPoints([]),box),[]);
});

test('the zoom clamp refuses to leave 1x..40x', () => {
  assert.equal(map.clampScale(0.2), 1);
  assert.equal(map.clampScale(1), 1);
  assert.equal(map.clampScale(17.5), 17.5);
  assert.equal(map.clampScale(400), 40);
  // A reader who scrolls past the end stays at the end rather than inverting.
  assert.equal(map.clampScale(-3), 1);
  assert.equal(map.clampScale(NaN), 1);
});

test('fly-to centres a bbox and honours the padding', () => {
  // A 100x100 box in a 1000x1000 canvas with 50px of padding: the widest the
  // box can be drawn is 900px, so 9x, and it lands in the middle.
  const view = map.flyToTransform([100, 100, 200, 200], 1000, 1000, 50);
  assert.equal(view.scale, 9);
  assert.equal(view.x, 500 - 9 * 150);
  assert.equal(view.y, 500 - 9 * 150);
  // The centre of the box maps to the centre of the canvas.
  assert.equal(view.x + view.scale * 150, 500);
  assert.equal(view.y + view.scale * 150, 500);
});

test('fly-to never exceeds the maximum zoom, however small the shape', () => {
  const view = map.flyToTransform([10, 10, 10.05, 10.05], 975, 610, 40);
  assert.equal(view.scale, map.MAX_SCALE);
});

test('fly-to on the whole canvas is 1x with no translation', () => {
  const view = map.flyToTransform([0, 0, 975, 610], 975, 610, 0);
  assert.equal(view.scale, 1);
  assert.equal(Math.round(view.x), 0);
  assert.equal(Math.round(view.y), 0);
});

test('circles closer than six screen pixels merge, and the merge sums', () => {
  const points = [
    {x: 100, y: 100, amount: 500, contributors: 2, rows: 2, zip: '10001'},
    {x: 101, y: 100.5, amount: 900, contributors: 3, rows: 3, zip: '10002'},
    {x: 400, y: 300, amount: 100, contributors: 1, rows: 1, zip: '90210'},
  ];
  const merged = map.mergeCircles(points, 1, 6);
  assert.equal(merged.length, 2);
  const crowd = merged.find(item => item.places > 1);
  assert.ok(crowd, JSON.stringify(merged));
  assert.equal(crowd.places, 2);
  assert.equal(crowd.amount, 1400);
  assert.equal(crowd.contributors, 5);
  // The member kept is the largest of the two, so the tooltip names the place
  // that carries most of the money.
  assert.equal(crowd.member.zip, '10002');
});

test('the same two circles come apart once the reader has zoomed in', () => {
  const points = [
    {x: 100, y: 100, amount: 500, contributors: 2},
    {x: 101, y: 100.5, amount: 900, contributors: 3},
  ];
  assert.equal(map.mergeCircles(points, 1, 6).length, 1);
  assert.equal(map.mergeCircles(points, 40, 6).length, 2);
});

test('merged circles are ordered biggest first, so small ones stay clickable', () => {
  const merged = map.mergeCircles([
    {x: 10, y: 10, amount: 10, contributors: 1},
    {x: 300, y: 300, amount: 900, contributors: 12},
    {x: 600, y: 200, amount: 400, contributors: 6},
  ], 1, 6);
  assert.deepEqual(merged.map(item => item.contributors), [12, 6, 1]);
});

test('circle size is the donor count, in four buckets', () => {
  assert.equal(map.donorRadius(1), 3);
  assert.equal(map.donorRadius(2), 5);
  assert.equal(map.donorRadius(5), 5);
  assert.equal(map.donorRadius(6), 8);
  assert.equal(map.donorRadius(10), 8);
  assert.equal(map.donorRadius(11), 11);
  assert.equal(map.donorRadius(4000), 11);
  // A place with no counted contributor still draws: it has money on it.
  assert.equal(map.donorRadius(0), 3);
});

test('the search box reads a district, a ZIP, a postal code and a name', () => {
  assert.deepEqual(map.parseSearch('TX-19'), {kind: 'district', value: 'TX-19'});
  assert.deepEqual(map.parseSearch('tx 19'), {kind: 'district', value: 'TX-19'});
  assert.deepEqual(map.parseSearch('AK-AL'), {kind: 'district', value: 'AK-AL'});
  assert.deepEqual(map.parseSearch('CA-SEN'), {kind: 'district', value: 'CA-SEN'});
  assert.deepEqual(map.parseSearch('79401'), {kind: 'zip', value: '79401'});
  assert.deepEqual(map.parseSearch('tx'), {kind: 'state', value: 'TX'});
  assert.deepEqual(map.parseSearch('Texas'), {kind: 'name', value: 'texas'});
  assert.deepEqual(map.parseSearch('   '), {kind: '', value: ''});
  // Four digits is not a ZIP and must not be answered as one.
  assert.deepEqual(map.parseSearch('7940'), {kind: 'name', value: '7940'});
});

test('the heat ramp is monotone and answers nothing for a zero', () => {
  assert.equal(map.heatColour(0), '');
  assert.equal(map.heatColour(-5), '#b85c67');
  const small = map.heatColour(1000);
  const large = map.heatColour(10000000);
  assert.match(small, /^#[0-9a-f]{6}$/);
  assert.match(large, /^#[0-9a-f]{6}$/);
  const darkness = hex => [1, 3, 5].reduce((total, at) => total + parseInt(hex.slice(at, at + 2), 16), 0);
  assert.ok(darkness(large) < darkness(small), `${small} -> ${large}`);
});

test('the level-of-detail constants are the ones the brief set', () => {
  assert.equal(map.DETAIL_SCALE, 2.5);
  assert.equal(map.COARSE_CIRCLES, 150);
  assert.equal(map.MERGE_PIXELS, 6);
  assert.equal(map.MIN_SCALE, 1);
  assert.equal(map.MAX_SCALE, 40);
});

test('each PAC and layer uses its own positive range, including singleton and empty views', () => {
  assert.deepEqual(map.heatDomain([0, -2, NaN, 10, 100]), [10,100]);
  assert.deepEqual(map.heatDomain([]), [0,0]);
  assert.equal(map.heatColour(10, null, [10,100]), map.TEAL_RAMP[0]);
  assert.equal(map.heatColour(100, null, [10,100]), map.TEAL_RAMP.at(-1));
  assert.equal(map.heatColour(7, null, [7,7]), map.TEAL_RAMP.at(-1));
  assert.equal(map.heatColour(1000000, null, [1000000,10000000]), map.TEAL_RAMP[0]);
});

test('district spend includes House races only; state view includes all kinds', () => {
  const body={kinds:[{kind:'candidates',districts:[{key:'KY-06',amount:30},{key:'IL-SEN',amount:100}]},
    {kind:'operating',states:[{key:'IL',amount:1000}]}],
    by_state:[{key:'KY',amount:30,kinds:{candidates:30}},
      {key:'IL',amount:1100,kinds:{candidates:100,operating:1000}}]};
  const district={body,layer:'district',direction:'spent',off:new Set()};
  assert.deepEqual([...map.amountsFor(district).districts], [['KY-06',30]]);
  assert.equal(map.amountsFor(district).states.size,0);
  assert.deepEqual(map.domainFor(district),[30,30]);
  assert.deepEqual(map.domainFor({...district,layer:'state'}),[30,1100]);
  assert.deepEqual(map.domainFor({...district,layer:'state',off:new Set(['operating'])}),[30,100]);
});

test('build 06: two compared cycles share one heat range, the union over the same visible area', () => {
  const a = {layer:'state',direction:'raised',body:{by_state:[{key:'TX',amount:200},{key:'CA',amount:900}]}};
  const b = {layer:'district',direction:'spent',body:{by_state:[{key:'TX',amount:50},{key:'NY',amount:400}]}};
  assert.deepEqual(map.unionDomain([200,900],[50,400]),[50,900]);
  assert.deepEqual(map.unionDomain([0,0],[50,400]),[50,400], 'an empty side takes the other range');
  assert.deepEqual(map.unionDomain([200,900],[0,0]),[200,900]);
  assert.deepEqual(map.rangeFor(a),[200,900], 'one map alone keeps its own range');
  // The companion is read on THIS map's layer, direction and kinds, so a
  // stale layer on the other view cannot skew the shared scale.
  assert.deepEqual(map.rangeFor({...a,companion:b}),[50,900]);
  assert.deepEqual(map.rangeFor({...b,layer:'state',direction:'raised',companion:a}),[50,900],
    'both maps compute the same shared range');
});

/* Lane G2b (boards #48, #55): the frame that fits the whole map beside an open Layer control. */
const REGIONS = {states: {
  AK: {bbox: [20.3, 425.2, 235.6, 540.7], region: 'alaska'}, HI: {bbox: [258.0, 526.6, 352.0, 587.4], region: 'hawaii'},
  PR: {bbox: [876.1, 565.4, 924.9, 576.5], region: 'caribbean'},
  CA: {bbox: [99.1, 150.0, 260.0, 420.0], region: 'lower48'}, ME: {bbox: [800.0, 6.0, 876.1, 120.0], region: 'lower48'},
  TX: {bbox: [300.0, 300.0, 560.0, 496.0], region: 'lower48'}}};
const placedBox = (frame, p) => [frame.x + frame.scale * p[0], frame.y + frame.scale * p[1], frame.x + frame.scale * p[2], frame.y + frame.scale * p[3]];
const overlaps = (a, b) => a[2] > b[0] + 1e-6 && a[0] < b[2] - 1e-6 && a[3] > b[1] + 1e-6 && a[1] < b[3] - 1e-6;

test('G2b: the map\'s pieces are its regions - the lower 48 and each inset', () => {
  const boxes = map.regionBoxes(REGIONS, 975, 610);
  assert.strictEqual(boxes.length, 4);
  assert.deepStrictEqual(boxes.find(box => box[0] === 99.1), [99.1, 6.0, 876.1, 496.0], 'the lower 48 is the union of its states');
  assert.deepStrictEqual(map.regionBoxes({}, 975, 610), [[0, 0, 975, 610]], 'a file without states is one piece: the canvas');
  assert.deepStrictEqual(map.regionBoxes({states: {XX: {bbox: [1, 2, 3, 4]}}}, 975, 610), [[1, 2, 3, 4]], 'no region tag counts as the lower 48');
});

test('G2b: with nothing over the map, or covers clear of it, the frame is the full fit', () => {
  const pieces = map.regionBoxes(REGIONS, 975, 610);
  assert.deepStrictEqual(map.frameAround(pieces, [], 975, 610, 6), {scale: 1, x: 0, y: 0});
  assert.deepStrictEqual(map.frameAround(pieces, [[0, 0, 15, 90]], 975, 610, 6), {scale: 1, x: 0, y: 0}, 'a toolbar in the empty corner');
  assert.deepStrictEqual(map.frameAround(pieces, [[5, 5, 5, 90]], 975, 610, 6), {scale: 1, x: 0, y: 0}, 'an empty rect covers nothing');
});

test('G2b: an open control at the top left: the whole map fits in the room to its right, insets below it', () => {
  const pieces = map.regionBoxes(REGIONS, 975, 610);
  // The Layer control and the toolbar column, in canvas units (b55's measured place), with the 10px gap.
  const covers = [[103, 29, 465, 405], [5, 5, 60, 250]];
  const frame = map.frameAround(pieces, covers, 975, 610, 6);
  assert.ok(frame.scale < 1 && frame.scale > 0.5, 'scaled to fit, not shrunk to nothing: ' + frame.scale);
  for (const piece of pieces) {
    const box = placedBox(frame, piece);
    assert.ok(box[0] >= 6 - 1e-6 && box[1] >= 6 - 1e-6 && box[2] <= 969 + 1e-6 && box[3] <= 604 + 1e-6, 'inside the canvas: ' + box);
    for (const cover of covers) assert.ok(!overlaps(box, cover), 'clear of ' + cover + ': ' + box);
  }
  const lower48 = placedBox(frame, pieces.find(box => box[0] === 99.1));
  assert.ok(lower48[0] >= 465 - 1e-6, 'the lower 48 sits right of the control');
  const alaska = placedBox(frame, pieces.find(box => box[0] === 20.3));
  assert.ok(alaska[1] >= 405 - 1e-6 || alaska[0] >= 465 - 1e-6, 'Alaska is below the control or right of it');
  // Nearly the largest fit: 2% more scale has no clear placement on a fine grid.
  const bigger = frame.scale * 1.02, fits = [];
  for (let x = -200; x <= 975; x += 2.5) {
    for (let y = -100; y <= 610; y += 2.5) {
      const trial = {scale: bigger, x, y};
      if (pieces.every(piece => { const box = placedBox(trial, piece);
        return box[0] >= 6 && box[1] >= 6 && box[2] <= 969 && box[3] <= 604 && covers.every(cover => !overlaps(box, cover)); })) fits.push([x, y]);
    }
  }
  assert.deepStrictEqual(fits, [], 'no clear placement at 2% more scale');
});

test('G2b: beside a control as tall as the map, the whole map sits right of it, centred top to bottom', () => {
  const pieces = map.regionBoxes(REGIONS, 975, 610);
  const covers = [[103, 29, 465, 590], [5, 5, 60, 250]];
  const frame = map.frameAround(pieces, covers, 975, 610, 6);
  assert.ok(frame.scale < 0.62 && frame.scale > 0.5, 'the room right of the control sets the scale: ' + frame.scale);
  const boxes = pieces.map(piece => placedBox(frame, piece));
  for (const box of boxes) assert.ok(box[0] >= 465 - 1e-6, 'every piece right of the control: ' + box);
  const top = Math.min(...boxes.map(box => box[1])), bottom = Math.max(...boxes.map(box => box[3]));
  assert.ok(Math.abs((top + bottom) / 2 - 305) < 1, 'centred in the canvas height: ' + top + '..' + bottom);
});

test('G2b: a control that leaves no readable room keeps the full fit', () => {
  const pieces = map.regionBoxes(REGIONS, 975, 610);
  assert.deepStrictEqual(map.frameAround(pieces, [[0, 0, 900, 610]], 975, 610, 6), {scale: 1, x: 0, y: 0});
  assert.strictEqual(map.FRAME_MIN, 0.3);
});
