const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../surfaces/staffer_live/staffer-live.js'),'utf8');
const code=source.slice(source.indexOf('  function changeZoom('),source.indexOf('  function screen('));
test('zoom passes the old ceiling while preserving the world point under the cursor',()=>{
  const context={state:{zoom:10,pan:{x:23,y:-40}},draw(){}};vm.createContext(context);vm.runInContext(code,context);
  const anchor={x:80,y:30},world={x:(80-23)/10,y:(30+40)/10};
  context.changeZoom(4,anchor);assert.equal(context.state.zoom,40);
  assert.equal((anchor.x-context.state.pan.x)/context.state.zoom,world.x);
  assert.equal((anchor.y-context.state.pan.y)/context.state.zoom,world.y);
  context.changeZoom(100,anchor);assert.equal(context.state.zoom,256);
  context.changeZoom(1e-10,anchor);assert.equal(context.state.zoom,.02);
});
test('button zoom remains centered on the same place, including after a pan',()=>{
  const context={state:{zoom:2,pan:{x:100,y:50}},draw(){}};vm.createContext(context);vm.runInContext(code,context);
  context.changeZoom(1.5);assert.equal(context.state.zoom,3);
  assert.equal(context.state.pan.x,150);assert.equal(context.state.pan.y,75);
  context.changeZoom(1/1.5);assert.equal(context.state.pan.x,100);assert.equal(context.state.pan.y,50);
});
