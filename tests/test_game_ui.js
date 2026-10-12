// Run with: node --test tests/test_game_ui.js
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function screen(storage=new Map()){
 const elements=new Map();
 const ctx=vm.createContext({localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},
  document:{getElementById:id=>{if(!elements.has(id))elements.set(id,{hidden:false,textContent:''});return elements.get(id)}}});
 const source=fs.readFileSync(path.join(__dirname,'../static/game.js'),'utf8');
 vm.runInContext(source.slice(0,source.lastIndexOf('\nwire();')),ctx);
 vm.runInContext("game={id:'g_one',pendingKS:[],lastIdRemaps:[{scenario:'scene1',speaker:'airi',oldId:'v01',newId:'v03'}]}",ctx);
 return {ctx,elements,run:code=>vm.runInContext(code,ctx)};
}

test('dismissal survives re-render and reopening; new changes and other projects are shown',()=>{
 const storage=new Map(),one=screen(storage);
 one.run('renderPendingKS()');assert.equal(one.elements.get('idRemapInfo').hidden,false);
 one.run('dismissRemapNotice();renderPendingKS()');assert.equal(one.elements.get('idRemapInfo').hidden,true);
 const reopened=screen(storage);reopened.run('renderPendingKS()');assert.equal(reopened.elements.get('idRemapInfo').hidden,true);
 reopened.run("game.lastIdRemaps[0].newId='v04';renderPendingKS()");assert.equal(reopened.elements.get('idRemapInfo').hidden,false);
 reopened.run("game.id='g_other';game.lastIdRemaps[0].newId='v03';renderPendingKS()");assert.equal(reopened.elements.get('idRemapInfo').hidden,false);
 reopened.run('game.lastIdRemaps=[];renderPendingKS()');assert.equal(reopened.elements.get('idRemapInfo').hidden,true);
});

test('dismissal still works when browser storage is unavailable',()=>{
 const one=screen();
 one.run("localStorage.getItem=()=>{throw Error('disabled')};localStorage.setItem=()=>{throw Error('disabled')};dismissRemapNotice();renderPendingKS()");
 assert.equal(one.elements.get('idRemapInfo').hidden,true);
});


test('scenario file checkboxes keep selections across files and filter already-generated lines',()=>{
 const view=screen();
 view.run(`
   renderVoiceRows=()=>{};renderAll=()=>{};message=()=>{};
   game={id:'g_test',scenarios:[{key:'one',filename:'scene1.ks'},{key:'two',filename:'scene2.ks'}],
     voices:[{voiceId:'v1',scenario:'one',audio:'ready.wav',stale:false},
             {voiceId:'v2',scenario:'one',audio:'',stale:false},
             {voiceId:'v3',scenario:'two',audio:'old.wav',stale:true}]};
   document.getElementById('scenarioList').querySelectorAll=()=>[];
   markScenario('one',true);markScenario('two',true);
 `);
 assert.deepEqual(JSON.parse(view.run('JSON.stringify([...marked].sort())')),['v2','v3']);
 view.run("showScenario('two')");
 assert.deepEqual(JSON.parse(view.run('JSON.stringify([...marked].sort())')),['v2','v3']);
 view.run("includeReady=true;markScenario('one',true)");
 assert.deepEqual(JSON.parse(view.run('JSON.stringify([...marked].sort())')),['v1','v2','v3']);
 view.run("includeReady=false;markScenario('one',false)");
 assert.deepEqual(JSON.parse(view.run('JSON.stringify([...marked].sort())')),['v3']);
 view.run("markAllScenarios('all')");
 assert.equal(view.run('marked.size'),3);
 view.run("markAllScenarios('clear')");
 assert.equal(view.run('marked.size'),0);
});
