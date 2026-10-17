// Run with: node --test tests/test_timeline_selection.js
// Exercise the actual editor functions without an audio engine or TTS models.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function editor(){
 const elements=new Map(),shell={clientWidth:728,scrollLeft:0,scrollTop:0};
 const ctx=vm.createContext({structuredClone,setTimeout:()=>0,clearTimeout:()=>{},console,
  localStorage:{setItem(){},getItem(){return null}},
  document:{getElementById:id=>{if(!elements.has(id))elements.set(id,{textContent:'',innerHTML:'',value:'',style:{},classList:{toggle(){}}});return elements.get(id)},
   querySelector:selector=>selector==='.timeline-shell'?shell:null,querySelectorAll:()=>[]}});
 const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
 vm.runInContext(source.slice(0,source.lastIndexOf('\ninstallPanelResizers();')),ctx);
 vm.runInContext(`
  renderInspector=()=>{};renderTimeline=()=>{};renderAll=()=>{};toast=()=>{};stopNodes=()=>{};
  state={title:'test',revision:1,scenes:[{id:'s'},{id:'other'}],lines:[],assets:[],voices:[],settings:{},
    tracks:[{id:'dialogue'},{id:'bgm'},{id:'sfx'}],clips:[
    {id:'a',sceneId:'s',trackId:'bgm',start:1.03,duration:2,pinned:false},
    {id:'b',sceneId:'s',trackId:'bgm',start:5.07,duration:3,pinned:false},
    {id:'c',sceneId:'s',trackId:'sfx',start:10,duration:1,pinned:false},
    {id:'d',sceneId:'other',trackId:'bgm',start:7,duration:2}]};
  selectedScene='s';pxPerSec=20;clearEditHistory();
  function event(id,x=0,extra={}){return {button:0,pointerId:1,clientX:x,preventDefault(){},
    target:{closest:selector=>selector==='.tl-clip'?{dataset:{clip:id}}:null},...extra}}
 `,ctx);
 return {run:code=>vm.runInContext(code,ctx),get:code=>JSON.parse(vm.runInContext(`JSON.stringify(${code})`,ctx))};
}

test('Ctrl toggle and Shift range use chronological order on the same track',()=>{
 const e=editor();e.run("dragStart(event('a'));dragEnd();dragStart(event('b',0,{ctrlKey:true}));dragEnd()");
 assert.deepEqual(e.get('[...selectedClips]'),['a','b']);
 e.run("dragStart(event('a',0,{ctrlKey:true}));dragEnd()");
 assert.deepEqual(e.get('[...selectedClips]'),['b']);
 e.run("selectClip('a');pointerSelectClip(clipById('b'),{shiftKey:true},false)");
 assert.deepEqual(e.get('[...selectedClips]'),['a','b']);
});

test('group drag clamps as a group and preserves fractional spacing; undo/redo',()=>{
 const e=editor();e.run("selectClipGroup(activeClips().slice(0,2));dragStart(event('b'));dragMove(event('b',-200));dragEnd()");
 const clips=e.get('state.clips');
 assert.equal(clips[0].start,0);assert.ok(Math.abs(clips[1].start-4.04)<1e-9);
 assert.equal(clips[2].start,10);assert.equal(clips[3].start,7);
 assert.deepEqual(e.get('[...selectedClips]'),['a','b']);
 e.run('undoEdit()');assert.equal(e.get('state.clips[0].start'),1.03);
 e.run('redoEdit()');assert.equal(e.get('state.clips[0].start'),0);
});

test('track and time selection stay in the current scene',()=>{
 const e=editor();e.run("selectTrackAndBelow('bgm')");assert.deepEqual(e.get('[...selectedClips]'),['a','b','c']);
 e.run("selectClip('b');selectFollowingClips()");assert.deepEqual(e.get('[...selectedClips]'),['b','c']);
 e.run('selectTrackClips()');assert.deepEqual(e.get('[...selectedClips]'),['a','b']);
 e.run("selectedScene='other';syncClipSelection()");assert.deepEqual(e.get('[...selectedClips]'),[]);
});

test('anchored followers move once and independent moves update their offset',()=>{
 const e=editor();e.run("state.clips[0].trackId='dialogue';state.clips[0].lineId='line';state.clips[1].anchorLineId='line';state.clips[1].anchorOffset=4.04;selectClip('a');dragStart(event('a'));dragMove(event('a',40));dragEnd()");
 assert.equal(e.get('state.clips[1].start'),7.07);assert.equal(e.get('state.clips[1].pinned'),false);
 e.run("selectClipGroup(activeClips().slice(0,2));dragStart(event('a'));dragMove(event('a',40));dragEnd()");
 assert.equal(e.get('state.clips[1].start'),9.07);
 assert.ok(Math.abs(e.get('state.clips[1].anchorOffset')-4.04)<1e-9);
 e.run("selectClip('b');dragStart(event('b'));dragMove(event('b',40));dragEnd()");
 assert.ok(Math.abs(e.get('state.clips[1].anchorOffset')-6.04)<1e-9);
});

test('pointer cancellation restores positions, pin flags and offsets',()=>{
 const e=editor();const before=e.get('state.clips');
 e.run("selectClipGroup(activeClips());dragStart(event('b'));dragMove(event('b',100));dragEnd({pointerId:1,type:'pointercancel'})");
 assert.deepEqual(e.get('state.clips'),before);assert.equal(e.get('undoHistory.length'),0);
});

test('resize changes just the active clip while preserving the selection',()=>{
 const e=editor();e.run("selectClipGroup(activeClips());const ev=event('b');ev.target.closest=selector=>selector==='.tl-clip'?{dataset:{clip:'b'}}:selector==='[data-resize]'?{}:null;dragStart(ev);dragMove(event('b',40));dragEnd()");
 assert.equal(e.get('state.clips[1].duration'),5);assert.equal(e.get('state.clips[0].duration'),2);
 assert.deepEqual(e.get('[...selectedClips]'),['a','b','c']);
});

test('library titles resolve old takes, current takes, and clip links without renaming files',()=>{
 const e=editor();e.run(`state.scenes[0].name='港';state.lines=[{id:'line',sceneId:'s',speaker:'愛理',text:'海が見える。',takes:[{assetId:'old'},{assetId:'new'}]}];state.clips[0].lineId='line';state.clips[0].assetId='linked';`);
 for(const id of ['old','new','linked'])assert.deepEqual(e.get(`libraryDisplayInfo({id:'${id}',name:'line_generated.wav'},libraryDialogueMap())`),{title:'海が見える。',context:'港 · 愛理'});
 assert.deepEqual(e.get("libraryDisplayInfo({id:'bgm',name:'港の音楽.wav'},libraryDialogueMap())"),{title:'港の音楽.wav',context:''});
 e.run("state.lines[0].text='海がきれい！'");assert.equal(e.get("libraryDisplayInfo({id:'old',name:'old.wav'},libraryDialogueMap()).title"),'海がきれい！');
});

test('shared audio shows its distinct dialogue associations and ignores duplicate takes',()=>{
 const e=editor();e.run(`state.scenes[0].name='港';state.scenes[1].name='館';state.lines=[{id:'x',sceneId:'s',speaker:'愛理',text:'こんにちは。',takes:[{assetId:'shared'},{assetId:'shared'}]},{id:'y',sceneId:'other',speaker:'真歩流',text:'行こう！',takes:[{assetId:'shared'}]}];`);
 assert.deepEqual(e.get("libraryDisplayInfo({id:'shared',name:'voice.wav'},libraryDialogueMap())"),{title:'こんにちは。 / 行こう！',context:'港 · 愛理 / 館 · 真歩流'});
});

test('library tabs search dialogue content separately from music and sound titles',()=>{
 const e=editor();e.run(`state.lines=[{id:'x',text:'港の向こうへ行こう！',takes:[{assetId:'voice'}]}];state.assets=[{id:'voice',kind:'voice',name:'generated.wav'},{id:'music',kind:'bgm',name:'Ocean BGM.wav'},{id:'sound',kind:'sfx',name:'ドアの音.wav'},{id:'ref',kind:'reference',name:'港の向こう.wav'}];`);
 assert.deepEqual(e.get("filteredLibraryAssets('voice','向こう').map(a=>a.id)"),['voice']);
 assert.deepEqual(e.get("filteredLibraryAssets('voice','generated').map(a=>a.id)"),[]);
 assert.deepEqual(e.get("filteredLibraryAssets('bgm',' ｏｃｅａｎ ').map(a=>a.id)"),['music']);
 assert.deepEqual(e.get("filteredLibraryAssets('sfx','ドア').map(a=>a.id)"),['sound']);
 assert.deepEqual(e.get("filteredLibraryAssets('bgm','向こう').map(a=>a.id)"),[]);
 assert.deepEqual(e.get("filteredLibraryAssets('voice','').map(a=>a.id)"),['voice']);
});

test('legacy dialogue audio is discoverable while ambience and unassigned files stay available',()=>{
 const e=editor();e.run(`state.lines=[{id:'x',text:'こんにちは。',takes:[{assetId:'legacy'}]}];state.assets=[{id:'legacy',kind:'other',name:'legacy.wav'},{id:'unassigned',kind:'voice',name:'未割当.wav'},{id:'sea',kind:'ambience',name:'波の音.wav'},{id:'unknown',kind:'other',name:'素材.wav'}];`);
 assert.deepEqual(e.get("filteredLibraryAssets('voice','こんにちは').map(a=>a.id)"),['legacy']);
 assert.deepEqual(e.get("filteredLibraryAssets('voice','未割当').map(a=>a.id)"),['unassigned']);
 assert.deepEqual(e.get("filteredLibraryAssets('ambience','波').map(a=>a.id)"),['sea']);
 assert.deepEqual(e.get("filteredLibraryAssets('other','').map(a=>a.id)"),['unknown']);
});

test('timeline dialogue selection scrolls the corresponding script line without rerendering',()=>{
 const e=editor();
 e.run(`
  state.lines=[{id:'dialogue-1',sceneId:'s',speaker:'ナレーション',text:'選択する台詞'}];
  state.clips[0].lineId='dialogue-1';
  const activeIds=[];
  const lineNode={dataset:{id:'dialogue-1'},classList:{toggle(name,active){if(name==='active'&&active)activeIds.push('dialogue-1')}},
    getBoundingClientRect(){return {top:400,height:50}}};
  const pane=$('editorContent');
  pane.scrollTop=75;
  pane.querySelectorAll=selector=>selector==='.script-line'?[lineNode]:[];
  pane.getBoundingClientRect=()=>({top:100,height:200});
  let scrollPosition=-1;
  pane.scrollTo=({top})=>{scrollPosition=top};
  window={matchMedia:()=>({matches:true})};
  selectClip('a');
 `);
 assert.equal(e.get("scrollPosition"),300);
 assert.deepEqual(e.get("activeIds"),['dialogue-1']);
 e.run("selectClip('b')");
 assert.equal(e.get("scrollPosition"),300); // BGM has no associated script line.
});

test('zoom out adjusts ticks from seconds to minutes while keeping clip timing unchanged',()=>{
 const e=editor();
 e.run("state.assets=[{id:'audio',name:'sound.wav'}];state.clips[0].assetId='audio';");
 const original=e.get('state.clips');
 assert.equal(e.get('timelineTickStep(38)'),5);
 assert.equal(e.get('timelineTickStep(.5)'),300);
 assert.equal(e.get('timelineTickStep(.02)'),7200);
 e.run("setTimelineZoom(.5)");
 assert.equal(e.get('pxPerSec'),.5);
 assert.equal(e.get("$('zoomScale').textContent"),'目盛: 5分');
 assert.ok(e.get("$('timeline').innerHTML.includes('00:00')"));
 assert.deepEqual(e.get('state.clips'),original);
});

test('fit-to-scene shows hours on narrow timeline and preserves source positions',()=>{
 const e=editor();
 e.run("state.clips[0].start=5400;state.clips[0].duration=900;state.assets=[{id:'audio',name:'sound.wav'}];state.clips[0].assetId='audio';");
 const clips=e.get('state.clips');
 e.run("fitTimeline()");
 const duration=e.get('timelineDuration()'),scale=e.get('pxPerSec');
 assert.ok(scale<1,scale);
 assert.ok(duration*scale<=600+1e-6,{duration,scale});
 assert.equal(e.get("document.querySelector('.timeline-shell').scrollLeft"),0);
 assert.deepEqual(e.get('state.clips'),clips);
 assert.ok(e.get("$('zoomNumber').textContent.includes('px/s')"));
});

test('zooming preserves the center time in a scrolled timeline',()=>{
 const e=editor();
 e.run("state.clips[0].start=300;pxPerSec=20;document.querySelector('.timeline-shell').scrollLeft=1600;");
 const centerBefore=e.get("(document.querySelector('.timeline-shell').scrollLeft+728/2-108)/pxPerSec");
 e.run("setTimelineZoom(40)");
 const centerAfter=e.get("(document.querySelector('.timeline-shell').scrollLeft+728/2-108)/pxPerSec");
 assert.ok(Math.abs(centerBefore-centerAfter)<1e-9);
 assert.equal(e.get('pxPerSec'),40);
});

test('time-axis zoom slider uses a logarithmic scale for long scenes',()=>{
 const e=editor();
 for(const px of [0.001,.03,.2,1,5,38,110,200]){
  const value=e.get('timelineZoomToSlider('+px+')');
  const actual=e.get('timelineSliderToZoom('+value+')');
  assert.ok(actual>0&&actual>=.001&&actual<=200);
  assert.ok(Math.abs(Math.log(actual/px))<.07,{px,value,actual});
 }
 assert.equal(e.get('timelineTickName(300)'),'5分');
 assert.equal(e.get('timelineTickName(3600)'),'1時間');
 assert.equal(e.get('timelineTickLabel(3660)'),'1:01:00');
});


test('playback automatically scrolls all timeline lanes while the playhead advances',()=>{
 const e=editor();
 const before=e.get('state.clips');
 e.run("playing=true;pxPerSec=20;currentTime=30;followPlaybackPosition()");
 const first=e.get("document.querySelector('.timeline-shell').scrollLeft");
 const expected=108+30*20-(108+(728-108)*.72);
 assert.ok(Math.abs(first-expected)<1e-9,{first,expected});
 e.run("currentTime=31;followPlaybackPosition()");
 const next=e.get("document.querySelector('.timeline-shell').scrollLeft");
 assert.ok(next>first, {first,next});
 assert.deepEqual(e.get('state.clips'),before); // No edits to clip offsets/timing.
 e.run("playing=false;currentTime=35;followPlaybackPosition()");
 assert.equal(e.get("document.querySelector('.timeline-shell').scrollLeft"),next);
});

test('playback does not scroll an overview that already shows the playhead',()=>{
 const e=editor();
 e.run("playing=true;pxPerSec=.5;currentTime=500;followPlaybackPosition()");
 assert.equal(e.get("document.querySelector('.timeline-shell').scrollLeft"),0);
});

test('pausing and auditioning one regenerated take preserve timeline playhead time',async()=>{
 const e=editor();
 e.run("playing=true;ctx={currentTime:63};playStart=21;stopNodes=()=>{playing=false};pause()");
 assert.equal(e.get('currentTime'),42);
 e.run("ensureContext=async()=>{};decodeAsset=async()=>({duration:1});ctx={destination:{},createBufferSource:()=>({connect(){},start(){}})};");
 await e.run("playPreview('regenerated-asset')");
 assert.equal(e.get('currentTime'),42);
});

test('starting a generation job pauses playback without returning to zero',()=>{
 const e=editor();
 e.run("globalThis.clearInterval=()=>{};globalThis.setInterval=()=>0;modal=()=>{};globalThis.fetch=async()=>({ok:true,headers:{get:()=> 'application/json'},json:async()=>({status:'running',message:'processing',completed:0,total:1})});ctx={currentTime:97};playStart=35;playing=true;stopNodes=()=>{playing=false};showJob('job_1')");
 assert.equal(e.get('currentTime'),62);
 assert.equal(e.get('playing'),false);
});
