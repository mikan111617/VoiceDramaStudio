// Run with: node --test tests/test_clip_split.js
// Splitting clips in the multitrack, cutting out a part, and restoring the whole voice.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function editor(){
 const elements=new Map(),toasts=[];
 const ctx=vm.createContext({structuredClone,setTimeout:()=>0,clearTimeout:()=>{},console,confirm:()=>true,toasts,
  localStorage:{setItem(){},getItem(){return null}},
  document:{getElementById:id=>{if(!elements.has(id))elements.set(id,{textContent:'',innerHTML:'',value:'',style:{},classList:{toggle(){}}});return elements.get(id)},
   querySelector:()=>null,querySelectorAll:()=>[]}});
 const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
 vm.runInContext(source.slice(0,source.lastIndexOf('\ninstallPanelResizers();')),ctx);
 vm.runInContext(`
  renderInspector=()=>{};renderTimeline=()=>{};renderAll=()=>{};toast=(m,bad)=>toasts.push([m,!!bad]);stopNodes=()=>{};
  state={title:'test',revision:1,scenes:[{id:'s'}],voices:[],settings:{},tracks:[{id:'dialogue'},{id:'bgm'}],
    lines:[{id:'l1',sceneId:'s',speaker:'愛理',text:'海が見える。',after:0.5,takes:[{id:'t1',assetId:'v1'},{id:'t2',assetId:'v2'}],selectedTakeId:'t1'},
           {id:'l2',sceneId:'s',speaker:'真歩流',text:'行こう！',after:0.5,takes:[{id:'t3',assetId:'v3'}],selectedTakeId:'t3'}],
    assets:[{id:'v1',duration:2},{id:'v2',duration:1.5},{id:'v3',duration:1},{id:'m',duration:30}],
    clips:[{id:'h1',sceneId:'s',trackId:'dialogue',lineId:'l1',assetId:'v1',start:0,duration:2,trimStart:0,trimEnd:0,fadeIn:0.1,fadeOut:0.3,gain:1,pinned:false},
           {id:'h2',sceneId:'s',trackId:'dialogue',lineId:'l2',assetId:'v3',start:2.5,duration:1,trimStart:0,trimEnd:0,fadeIn:0,fadeOut:0,gain:1,pinned:false},
           {id:'bgm',sceneId:'s',trackId:'bgm',lineId:null,assetId:'m',start:0,duration:10,trimStart:2,trimEnd:0,fadeIn:1,fadeOut:2,gain:.5,loop:false,pinned:true}]};
  selectedScene='s';pxPerSec=20;clearEditHistory();
  function event(id,x=0){return {button:0,pointerId:1,clientX:x,preventDefault(){},
    target:{closest:selector=>selector==='.tl-clip'?{dataset:{clip:id}}:null}}}
 `,ctx);
 return {run:code=>vm.runInContext(code,ctx),get:code=>JSON.parse(vm.runInContext(`JSON.stringify(${code})`,ctx)),toasts};
}
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-9,`${a} != ${b}`);

test('splitting a music clip keeps the left part and plays on from the same audio',()=>{
 const e=editor();e.run("selectClip('bgm');currentTime=4;splitSelectedAtPlayhead()");
 const [left,right]=e.get("state.clips.filter(c=>c.trackId==='bgm')");
 assert.deepEqual([left.id,left.start,left.duration,left.trimStart,left.fadeIn,left.fadeOut],['bgm',0,4,2,1,0]);
 assert.deepEqual([right.start,right.duration,right.trimStart,right.fadeIn,right.fadeOut,right.gain,right.lineId],[4,6,6,0,2,.5,null]);
 assert.equal(right.pieceOf,undefined);
 assert.deepEqual(e.get('[...selectedClips]'),[right.id]);          // the right part, ready for the next split
 e.run('undoEdit()');assert.equal(e.get('state.clips.length'),3);assert.equal(e.get("clipById('bgm').duration"),10);
});

test('a split voice follows its line and the next line waits for every piece',()=>{
 const e=editor();e.run("selectClip('h1');currentTime=0.8;splitSelectedAtPlayhead()");
 const piece=e.get("state.clips.find(c=>c.pieceOf==='l1')");
 assert.deepEqual([piece.start,piece.duration,piece.trimStart,piece.anchorLineId,piece.anchorOffset,piece.fadeOut],[0.8,1.2,0.8,'l1',0.8,0.3]);
 assert.equal(e.get("selectedLine"),'l1');
 e.run("lineById('l1').after=1;reflow()");
 assert.equal(e.get("clipById('h2').start"),3);
 e.run("clipById('h1').start=1;reflow()");                          // unpinned: back to the line's slot
 assert.equal(e.get("clipById('h1').start"),0);
 e.run("selectClip('h1');dragStart(event('h1'));dragMove(event('h1',20));dragEnd();reflow()");  // dragged 1 s later
 near(e.get("clipById('h1').start"),1);assert.equal(e.get("clipById('h1').pinned"),true);
 near(e.get("state.clips.find(c=>c.pieceOf==='l1').start"),1.8);
 near(e.get("clipById('h2').start"),4);
});

test('cutting out the middle closes the gap and pulls the next line forward',()=>{
 const e=editor();e.run("selectClip('h1');currentTime=0.8;splitSelectedAtPlayhead();currentTime=1.1;splitSelectedAtPlayhead()");
 const [middle,rest]=e.get("state.clips.filter(c=>c.pieceOf==='l1').sort((a,b)=>a.start-b.start)");
 near(middle.duration,0.3);
 e.run(`deleteClipClosingGap(clipById('${middle.id}'))`);
 const after=e.get(`clipById('${rest.id}')`);
 near(after.start,0.8);near(after.anchorOffset,0.8);near(after.trimStart,1.1);
 near(e.get("clipById('h2').start"),2.2);                          // 0.8 + 0.9 + 0.5
});

test('removing the first part hands the line to the next piece',()=>{
 const e=editor();e.run("selectClip('h1');currentTime=0.5;splitSelectedAtPlayhead()");
 const piece=e.get("state.clips.find(c=>c.pieceOf==='l1')").id;
 e.run("deleteClipClosingGap(clipById('h1'))");
 const head=e.get(`clipById('${piece}')`);
 assert.deepEqual([head.lineId,head.pieceOf,head.anchorLineId,head.start,head.pinned],['l1',undefined,undefined,0,false]);
 near(e.get("clipById('h2').start"),2);
 // A plain delete leaves the gap: the piece stays where it was.
 const f=editor();f.run("selectClip('h1');currentTime=0.5;splitSelectedAtPlayhead()");
 const kept=f.get("state.clips.find(c=>c.pieceOf==='l1')").id;
 f.run("removeClips(['h1']);reflow()");
 assert.deepEqual(f.get(`[clipById('${kept}').lineId,clipById('${kept}').start,clipById('${kept}').pinned]`),['l1',0.5,true]);
});

test('switching takes or restoring plays the whole audio again',()=>{
 const e=editor();e.run("selectClip('h1');currentTime=0.5;splitSelectedAtPlayhead();currentTime=1;splitSelectedAtPlayhead()");
 assert.equal(e.get('lineVoiceEdited("l1")'),true);
 e.run("restoreLineVoice('l1')");
 assert.equal(e.get("state.clips.filter(c=>c.pieceOf==='l1').length"),0);
 assert.deepEqual(e.get("[clipById('h1').duration,clipById('h1').trimStart,clipById('h1').fadeOut]"),[2,0,0.3]);
 assert.equal(e.get('lineVoiceEdited("l1")'),false);
 e.run("selectClip('h1');currentTime=0.5;splitSelectedAtPlayhead();resetLineVoice('l1',asset('v2'))");
 assert.deepEqual(e.get("[clipById('h1').assetId,clipById('h1').duration,state.clips.length]"),['v2',1.5,3]);
 near(e.get("clipById('h2').start"),2);
});

test('loops, edges and an unselected playhead are refused without changes',()=>{
 const e=editor();
 e.run("selectClip('bgm');clipById('bgm').loop=true;currentTime=4;splitSelectedAtPlayhead()");
 e.run("clipById('bgm').loop=false;currentTime=0.01;splitSelectedAtPlayhead()");
 e.run("selectedClips.clear();selectedClip=null;splitSelectedAtPlayhead()");
 assert.equal(e.get('state.clips.length'),3);
 assert.deepEqual(e.toasts.map(t=>t[1]),[true,true,true]);
 assert.match(e.toasts[0][0],/ループ/);assert.match(e.toasts[1][0],/再生位置/);assert.match(e.toasts[2][0],/選択/);
});

test('deleting a line takes its pieces with it',()=>{
 const e=editor();e.run("selectClip('h1');currentTime=0.5;splitSelectedAtPlayhead();removeLine('l1')");
 assert.deepEqual(e.get('state.clips.map(c=>c.id)'),['h2','bgm']);
});
