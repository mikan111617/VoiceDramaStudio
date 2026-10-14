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


test('single-line generation first saves edited TTS text, then submits generation',async()=>{
 const view=screen(),calls=[];
 const voice={voiceId:'v00100',scenario:'scene1',speaker:'愛理',
   text:'元の文章です。',ttsText:'元の文章です。',caption:'',audio:'',stale:false};
 view.ctx.fetch=async(url,options={})=>{
   const body=options.body?JSON.parse(options.body):null;
   calls.push({url,method:options.method||'GET',body});
   if(options.method==='PUT'){
     const updated=body.changes.voices[0];
     voice.ttsText=updated.ttsText;voice.caption=updated.caption;
     return {ok:true,headers:{get:()=> 'application/json'},json:async()=>({id:'g_one',revision:2,
       voices:[voice],pendingKS:[],format:'wav'})};
   }
   return {ok:true,headers:{get:()=> 'application/json'},json:async()=>({id:'gj_test',status:'running',total:1})};
 };
 view.ctx.confirm=()=>true;
 view.run("game={id:'g_one',revision:1,voices:[{voiceId:'v00100',scenario:'scene1',speaker:'愛理',text:'元の文章です。',ttsText:'元の文章です。',caption:'',audio:'',stale:false}],pendingKS:[],format:'wav'};focusedVoice='v00100';renderAll=()=>{};message=()=>{};poll=()=>{};");
 view.run("$('ttsText');$('caption');$('numSteps');$('exportFormat')");
 view.elements.get('ttsText').value='変更した文章を読み上げる。';
 view.elements.get('caption').value='落ち着いて話す';
 view.elements.get('numSteps').value='40';
 view.elements.get('exportFormat').value='wav';
 await view.run("startGeneration(['v00100'])");
 assert.equal(calls.length,2);
 assert.equal(calls[0].method,'PUT');
 assert.equal(calls[0].body.changes.voices[0].ttsText,'変更した文章を読み上げる。');
 assert.equal(calls[0].body.changes.voices[0].caption,'落ち着いて話す');
 assert.equal(calls[1].method,'POST');
 assert.deepEqual(JSON.parse(JSON.stringify(calls[1].body.voiceIds)),['v00100']);
 assert.equal(view.run("game.voices[0].ttsText"),'変更した文章を読み上げる。');
});

test('failed spoken-text save must never fall back to original sentence',async()=>{
 const view=screen(),calls=[];
 view.ctx.fetch=async(url,options={})=>{
  calls.push(options.method||'GET');
  return {ok:false,status:409,headers:{get:()=> 'application/json'},
    json:async()=>({detail:'編集の競合を検出しました'})};
 };
 view.ctx.confirm=()=>true;
 view.run("game={id:'g_one',revision:1,voices:[{voiceId:'v00100',scenario:'scene1',speaker:'愛理',text:'元の文章。',ttsText:'元の文章。',caption:'',audio:'',stale:false}],pendingKS:[],format:'wav'};focusedVoice='v00100';renderAll=()=>{};message=()=>{};poll=()=>{};");
 view.run("$('ttsText');$('caption');$('numSteps');$('exportFormat')");
 view.elements.get('ttsText').value='変更された文章。';
 view.elements.get('caption').value='';
 view.elements.get('numSteps').value='40';
 view.elements.get('exportFormat').value='wav';
 await view.run("startGeneration(['v00100'])");
 assert.deepEqual(calls,['PUT']);
});

test('generation summary reports only files successfully saved',()=>{
 const view=screen();
 const job={total:12,completed:10,succeeded:7,failed:3,unprocessed:2,status:'cancelled'};
 view.run('renderGenerationSummary('+JSON.stringify(job)+')');
 const value=view.elements.get('generationSummary').textContent;
 assert.match(value,/成功 7件/);
 assert.match(value,/失敗 3件/);
 assert.match(value,/未処理 2件/);
 assert.equal(view.elements.get('generationSummary').hidden,false);
});
