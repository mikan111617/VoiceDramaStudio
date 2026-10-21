// Run with: node --test tests/test_drama_generation_targets.js
// Which lines "まとめて生成" picks when scenes and characters are chosen.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function editor(){
 const elements=new Map();
 const ctx=vm.createContext({structuredClone,setTimeout:()=>0,clearTimeout:()=>{},console,
  localStorage:{setItem(){},getItem(){return null}},
  document:{getElementById:id=>{if(!elements.has(id))elements.set(id,{textContent:'',innerHTML:'',value:'',style:{},classList:{toggle(){}}});return elements.get(id)},
   querySelector:()=>null,querySelectorAll:()=>[]}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/characters.js'),'utf8'),ctx);
 const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
 vm.runInContext(source.slice(0,source.lastIndexOf('\ninstallPanelResizers();')),ctx);
 vm.runInContext(`
  renderAll=()=>{};toast=()=>{};
  const line=(id,sceneId,speaker,status,text='台詞')=>({id,sceneId,speaker,status,text});
  state={id:'p',revision:1,scenes:[{id:'s1',name:'第1幕'},{id:'s2',name:'第2幕'}],assets:[],clips:[],tracks:[],settings:{},
   voices:[{name:'警察官',refs:[]}],characterLinks:{'警察官':'chr_police'},
   lines:[line('a','s2','真歩流','missing'),line('b','s1','警察官','ready'),line('c','s1','真歩流','stale'),
          line('d','s2','警察官','missing'),line('e','s1','警察','missing'),line('f','s2','真歩流','ready'),line('g','s1','真歩流','missing','  ')]};
  characterRegistry={revision:1,assets:[],characters:[{id:'chr_police',name:'警察',aliases:[],refs:[]},{id:'chr_mahoru',name:'真歩流',aliases:[],refs:[]}]};
 `,ctx);
 return {get:code=>JSON.parse(vm.runInContext(`JSON.stringify(${code})`,ctx))};
}

test('only the chosen characters are generated, across every chosen scene in scene order',()=>{
 const e=editor();
 assert.deepEqual(e.get("sceneGenerationTargets(['s1','s2'],'pending',['真歩流']).map(l=>l.id)"),['c','a']);
 assert.deepEqual(e.get("sceneGenerationTargets(['s1','s2'],'all',['真歩流']).map(l=>l.id)"),['c','a','f']);
 assert.deepEqual(e.get("sceneGenerationTargets(['s2'],'all',['真歩流','警察官']).map(l=>l.id)"),['a','d','f']);
 assert.deepEqual(e.get("sceneGenerationTargets(['s1','s2'],'pending',[]).map(l=>l.id)"),[]);
 // No character filter: every speaker (blank lines are never sent).
 assert.deepEqual(e.get("sceneGenerationTargets(['s1'],'pending').map(l=>l.id)"),['c','e']);
});

test('a shared character finds every speaker that uses its voice in this work',()=>{
 const e=editor();
 // 警察官 is linked to 警察, and the speaker 警察 matches it by name.
 assert.deepEqual(e.get("speakersUsing(characterRegistry.characters[0])"),['警察官','警察']);
 assert.deepEqual(e.get("speakersUsing(characterRegistry.characters[1])"),['真歩流']);
});
