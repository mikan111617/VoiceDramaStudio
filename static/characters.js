/* Characters shared by every VoiceDrama project and game project.
   One editor on both screens: settings, reference audio, Voice Design
   (regenerate without closing) and importing from other works. A line uses a
   character when its speaker equals the character's name or an alias. */
'use strict';
(function(global){
 const API='/api/characters';
 const LANGUAGES=['Japanese','English','Chinese','Auto'];
 const DESIGN_SAMPLE='こんにちは！ 今日はどんな素敵なことが起こるのか、楽しみですね。';
 const QWEN_DESIGN='Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign';
 const LIST_LIMIT=200;
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const norm=value=>String(value??'').normalize('NFKC').trim().toLowerCase();
 const splitList=value=>String(value||'').split(/[、,，\n]/).map(x=>x.trim()).filter(Boolean);

 // Same order as the server: the first name that matches any character wins.
 function match(registry,names){
  for(const name of names){
   const wanted=norm(name);if(!wanted)continue;
   const found=(registry?.characters||[]).find(c=>norm(c.name)===wanted||(c.aliases||[]).some(a=>norm(a)===wanted));
   if(found)return found;
  }
  return null;
 }
 function byId(registry,id){return (registry?.characters||[]).find(c=>c.id===id)||null}
 async function call(url,options={}){
  const response=await fetch(url,options);
  if(!response.ok){
   let message=await response.text();
   try{message=JSON.parse(message).detail||message}catch(_){}
   const error=new Error(typeof message==='string'?message:JSON.stringify(message));
   error.status=response.status;throw error;
  }
  return response.json();
 }
 const jsonBody=(method,body)=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 function fetchRegistry(){return call(API)}
 const wait=ms=>new Promise(done=>setTimeout(done,ms));

 function createEditor(root,options={}){
  const opt={toast:()=>{},onLoad:()=>{},onSaved:()=>{},beforeImport:async()=>{},usage:()=>'',actions:()=>[],
   models:{irodori:[],qwen:[]},classes:{},...options};
  const cls={button:'',primary:'',danger:'',...opt.classes};
  let data=null,loadError='',conflict=false,filter='',busy=false;
  const drafts=new Map(),open=new Set();
  let player=null,overlay=null,design=null;
  const btn=(label,attrs='',kind='button',type='button')=>`<button type="${type}" class="${esc(cls[kind]||cls.button)}" ${attrs}>${label}</button>`;
  const assets=()=>new Map((data?.assets||[]).map(a=>[a.id,a]));
  const modelOptions=(engine,value)=>{
   const list=[...(opt.models[engine]||opt.models.irodori||[])];
   if(value&&!list.some(([id])=>id===value))list.push([value,value]);
   return list.map(([id,label])=>`<option value="${esc(id)}" ${id===value?'selected':''}>${esc(label)}</option>`).join('');
  };

  function cardHtml(c){
   const d=drafts.get(c.id)||c,refs=assets(),usage=opt.usage(c)||'';
   const extra=opt.actions(c).map((a,i)=>btn(esc(a.label),`data-ch-extra="${i}" title="${esc(a.title||'')}"`)).join('');
   return `<details class="ch-card" data-ch-id="${esc(c.id)}" ${open.has(c.id)?'open':''}>
    <summary><b>${esc(c.name)}</b>${(c.aliases||[]).length?`<span class="ch-aliases">別名：${esc(c.aliases.join('、'))}</span>`:''}<span class="ch-meta">${esc(c.engine==='qwen'?'Qwen3-TTS':'Irodori-TTS')} · ${esc(String(c.model||'').split('/').pop())} · 参照音声 ${(c.refs||[]).length}件${usage?' · '+esc(usage):''}</span></summary>
    <form class="ch-form" data-ch-form>
     <div class="ch-grid">
      <label>名前<input name="name" maxlength="60" required value="${esc(d.name)}"></label>
      <label class="ch-wide">別名（読点区切り。ゲームの話者IDなど。台詞の話者がこの名前でも使います）<input name="aliases" value="${esc((Array.isArray(d.aliases)?d.aliases:splitList(d.aliases)).join('、'))}"></label>
      <label>TTSエンジン<select name="engine"><option value="irodori" ${d.engine!=='qwen'?'selected':''}>Irodori-TTS</option><option value="qwen" ${d.engine==='qwen'?'selected':''}>Qwen3-TTS</option></select></label>
      <label>生成方式<select name="mode"><option value="clone" ${d.mode!=='design'?'selected':''}>参照音声</option><option value="design" ${d.mode==='design'?'selected':''}>Voice Design</option></select></label>
      <label>言語<select name="language">${LANGUAGES.map(x=>`<option ${x===d.language?'selected':''}>${x}</option>`).join('')}</select></label>
      <label class="ch-wide">音声モデル<select name="model">${modelOptions(d.engine||'irodori',d.model)}</select></label>
      <label class="ch-wide">声の説明（Voice Design・演技の基礎）<textarea name="description" rows="2" maxlength="2000">${esc(d.description||'')}</textarea></label>
      <label class="ch-wide">参照音声の書き起こし（Qwen Base・任意）<textarea name="refText" rows="2" maxlength="4000">${esc(d.refText||'')}</textarea></label>
     </div>
     <div class="ch-refs"><b>${esc(c.name)} の参照音声（${(c.refs||[]).length}件）</b>${(c.refs||[]).map(id=>refs.get(id)).filter(Boolean).map(a=>`<div class="ch-ref"><span title="${esc(a.name)}">${esc(a.name)} · ${Number(a.duration||0).toFixed(1)}秒</span><span>${btn('▶ 試聴',`data-ch-play="${esc(a.id)}"`)}${btn('外す',`data-ch-unref="${esc(a.id)}" title="このキャラクターの参照音声から外します（ファイルは残ります）"`)}</span></div>`).join('')||'<p class="ch-empty">参照音声はまだありません。</p>'}${libraryHtml(c)}</div>
     <div class="ch-actions">${btn('保存','','primary','submit')}${btn('＋ 参照音声を登録','data-ch-upload')}${btn('✦ Voice Designで声を作成','data-ch-design')}${extra}${btn('削除','data-ch-delete','danger')}</div>
     <input type="file" hidden multiple accept=".wav,.mp3,.flac,.ogg,.m4a,.aac,.opus,audio/*" data-ch-file>
    </form></details>`;
  }
  // Reference audio stays in the shared library when it is unlinked (or a
  // later import replaces it), so it can be linked again from here.
  function libraryHtml(c){
   const linked=new Set(c.refs||[]),users=new Map();
   for(const other of data.characters)for(const id of other.refs||[])if(other.id!==c.id)users.set(id,[...(users.get(id)||[]),other.name]);
   const rest=(data.assets||[]).filter(a=>!linked.has(a.id)).sort((a,b)=>(users.has(a.id)?1:0)-(users.has(b.id)?1:0));
   if(!rest.length)return '';
   return `<details class="ch-library"><summary>保存済みの参照音声から追加（${rest.length}件）</summary>${rest.map(a=>`<div class="ch-ref"><span title="${esc(a.name)}">${esc(a.name)} · ${Number(a.duration||0).toFixed(1)}秒${users.has(a.id)?` <small>（${esc(users.get(a.id).join('、'))}で使用中）</small>`:''}</span><span>${btn('▶ 試聴',`data-ch-play="${esc(a.id)}"`)}${btn('＋ 追加',`data-ch-link="${esc(a.id)}"`)}</span></div>`).join('')}</details>`;
  }
  async function linkReference(id,aid){
   const c=byId(data,id);if(!c||(c.refs||[]).includes(aid))return;
   const asset=assets().get(aid);
   await mutate(rev=>call(`${API}/${encodeURIComponent(id)}`,jsonBody('PUT',{character:{...c,refs:[...(c.refs||[]),aid]},expectedRevision:rev})),
    `「${asset?.name||aid}」を${c.name}の参照音声に追加しました`);
  }
  function renderList(){
   const list=root.querySelector('[data-ch-list]');if(!list||!data)return;
   const q=norm(filter);
   const shown=data.characters.filter(c=>!q||[c.name,...(c.aliases||[])].some(n=>norm(n).includes(q)));
   list.innerHTML=shown.slice(0,LIST_LIMIT).map(cardHtml).join('')||
    `<p class="ch-empty">${q?'絞り込みに一致するキャラクターがありません。':'共通キャラクターはまだありません。名前を入力して追加するか、「⇩ 作品・ゲームから取り込む」で既存のキャストを取り込んでください。'}</p>`;
   if(shown.length>LIST_LIMIT)list.insertAdjacentHTML('beforeend',`<p class="ch-empty">ほか ${shown.length-LIST_LIMIT}人。絞り込みで表示してください。</p>`);
  }
  function render(){
   if(!data){
    root.innerHTML=`<div class="ch-editor"><p class="${loadError?'ch-error':'ch-empty'}">${esc(loadError||'共通キャラクターを読み込み中…')}</p>${loadError?btn('↻ 再読み込み','data-ch-action="reload"'):''}</div>`;
    return;
   }
   root.innerHTML=`<div class="ch-editor">
    <p class="ch-help">ボイスドラマとゲーム音声のすべての作品で共通のキャラクターです。台詞の話者が名前か別名と一致すると、このキャラクターの声で生成します。変更はすべての作品に反映され、声が変わる台詞は「要再生成」になります。</p>
    ${conflict?`<div class="ch-conflict" role="alert">別の画面で共通キャラクターが変更されたため、保存しませんでした。再読み込みしてから編集し直してください。${btn('↻ 再読み込み','data-ch-action="reload"','primary')}</div>`:''}
    <div class="ch-toolbar"><span class="ch-count">共通キャラクター ${data.characters.length}人</span>
     <form class="ch-add" data-ch-add><input name="name" maxlength="60" required placeholder="新しいキャラクター名" aria-label="新しいキャラクター名">${btn('＋ 追加','','primary','submit')}</form>
     ${btn('⇩ 作品・ゲームから取り込む','data-ch-action="import"')}${btn('↻ 再読み込み','data-ch-action="reload"')}
     <input type="search" data-ch-filter value="${esc(filter)}" placeholder="名前・別名で絞り込み" aria-label="キャラクターを絞り込み"></div>
    <div class="ch-list" data-ch-list></div></div>`;
   renderList();
  }
  function readForm(form){
   const f=name=>form.elements.namedItem(name)?.value??'';
   return {name:f('name').trim(),aliases:splitList(f('aliases')),engine:f('engine')||'irodori',mode:f('mode')||'clone',
    language:f('language')||'Japanese',model:f('model'),description:f('description'),refText:f('refText')};
  }
  // Keep unsaved typing in other cards when the list is redrawn.
  function captureDrafts(){
   if(!data)return;
   for(const card of root.querySelectorAll('[data-ch-id]')){
    const id=card.dataset.chId,saved=byId(data,id),form=card.querySelector('[data-ch-form]');
    if(card.open)open.add(id);else open.delete(id);
    if(!saved||!form){drafts.delete(id);continue}
    const draft=readForm(form);
    const same=['name','engine','mode','language','model','description','refText'].every(k=>(draft[k]||'')===(saved[k]||''))&&
     JSON.stringify(draft.aliases)===JSON.stringify(saved.aliases||[]);
    if(same)drafts.delete(id);else drafts.set(id,draft);
   }
  }
  function rerender(){captureDrafts();render()}
  async function load(){
   try{data=await fetchRegistry();loadError='';conflict=false;drafts.clear();opt.onLoad(data)}
   catch(e){if(!data)loadError='共通キャラクターを読み込めません: '+e.message;opt.toast('共通キャラクターを読み込めません: '+e.message,true)}
   render();return data;
  }
  // Every change sends the registry revision; a stale screen must reload.
  async function mutate(request,done,info){
   if(busy||!data)return null;
   busy=true;captureDrafts();
   try{
    const result=await request(data.revision);
    data=result.registry||result;conflict=false;render();
    if(done)opt.toast(done);
    await opt.onSaved(data,info);
    return result;
   }catch(e){
    if(e.status===409){conflict=true;render()}
    opt.toast(e.message,true);return null;
   }finally{busy=false}
  }
  async function create(spec){
   const before=new Set((data?.characters||[]).map(c=>c.id));
   const result=await mutate(rev=>call(API,jsonBody('POST',{character:spec,expectedRevision:rev})),`「${spec.name}」を共通キャラクターに追加しました`);
   const added=result&&data.characters.find(c=>!before.has(c.id));
   if(added){open.add(added.id);render()}
   return added||null;
  }
  async function save(form,id){
   const current=byId(data,id);if(!current)return;
   const spec={...current,...readForm(form)};
   if(!spec.name)return opt.toast('キャラクター名を入力してください',true);
   drafts.delete(id);
   if(!await mutate(rev=>call(`${API}/${encodeURIComponent(id)}`,jsonBody('PUT',{character:spec,expectedRevision:rev})),`「${spec.name}」を保存しました（すべての作品に反映）`))
    drafts.set(id,readForm(form));
  }
  async function remove(id){
   const c=byId(data,id);if(!c)return;
   if(!confirm(`「${c.name}」を共通キャラクターから削除しますか？\nこのキャラクターを使っていた台詞は、名前が一致する各作品のキャストか標準の声で生成されます。参照音声のファイルは残ります。`))return;
   drafts.delete(id);open.delete(id);
   await mutate(rev=>call(`${API}/${encodeURIComponent(id)}?expectedRevision=${rev}`,{method:'DELETE'}),`「${c.name}」を削除しました`);
  }
  async function upload(id,files){
   const list=[...files];if(!list.length||!data)return;
   let done=0;
   for(const file of list){
    const form=new FormData();form.append('file',file);
    const ok=await mutate(rev=>call(`${API}/${encodeURIComponent(id)}/references?expectedRevision=${rev}`,{method:'POST',body:form}));
    if(!ok)break;done++;
   }
   if(done)opt.toast(`参照音声を${done}件登録しました`);
  }
  async function unref(id,aid){
   if(!confirm('この参照音声をキャラクターから外しますか？（ファイルは残ります）'))return;
   await mutate(rev=>call(`${API}/${encodeURIComponent(id)}/references/${encodeURIComponent(aid)}?expectedRevision=${rev}`,{method:'DELETE'}),'参照音声を外しました');
  }
  function play(aid){
   if(player){player.pause();player=null}
   player=new Audio(`${API}/assets/${encodeURIComponent(aid)}`);
   player.play().catch(e=>opt.toast('試聴できません: '+e.message,true));
  }
  function engineChanged(form){
   const engine=form.elements.namedItem('engine').value,mode=form.elements.namedItem('mode').value;
   const model=engine==='qwen'?(mode==='design'?QWEN_DESIGN:(opt.models.qwen?.[0]?.[0]||'')):(opt.models.irodori?.[0]?.[0]||'');
   form.elements.namedItem('model').innerHTML=modelOptions(engine,model);
  }

  // A small dialog shared by Voice Design and importing.
  function showDialog(html){
   closeDialog();
   overlay=document.createElement('div');overlay.className='ch-overlay';
   overlay.innerHTML=`<div class="ch-dialog" role="dialog" aria-modal="true">${html}</div>`;
   overlay.addEventListener('click',e=>{if(e.target===overlay||e.target.closest('[data-ch-close]'))closeDialog()});
   overlay.addEventListener('keydown',e=>{if(e.key==='Escape')closeDialog()});
   document.body.append(overlay);
   overlay.querySelector('textarea,select,input,button')?.focus();
   return overlay;
  }
  function closeDialog(){overlay?.remove();overlay=null;if(design)design.closed=true}

  // Voice Design: results stay listed so they can be compared, regenerated
  // and chosen without closing the dialog.
  function openDesign(id){
   const c=byId(data,id);if(!c)return;
   design={id,name:c.name,sampleText:DESIGN_SAMPLE,history:[],current:null,running:false,closed:false,message:'サンプル台詞を確認して「✦ 生成」を押してください。'};
   showDialog('<div data-ch-design-body></div>');renderDesign();
  }
  function renderDesign(){
   const body=overlay?.querySelector('[data-ch-design-body]');if(!body||!design)return;
   const s=design;
   body.innerHTML=`<h3>${s.running?'声をデザイン中':'Voice Design'}：${esc(s.name)}</h3><p class="ch-status" role="status">${esc(s.message)}</p>
    <label class="ch-field">サンプル台詞（変更してから生成できます）<textarea data-ch-design-text rows="2" maxlength="500">${esc(s.sampleText)}</textarea></label>
    ${s.history.length?`<div class="ch-results">${s.history.map((r,i)=>`<div class="ch-ref"><span title="${esc(r.text)}">${i+1}回目「${esc(r.text.slice(0,24))}${r.text.length>24?'…':''}」${r.assetId===s.current?' <b>使用中</b>':''}</span><span>${btn('▶ 試聴',`data-ch-play="${esc(r.assetId)}"`)}${r.assetId===s.current?'':btn('これを使う',`data-ch-use="${esc(r.assetId)}" ${s.running?'disabled':''}`)}</span></div>`).join('')}</div>`:''}
    <p class="ch-note">${s.history.length?'「✦ 再生成」は、使用中の結果をこのキャラクターの参照音声から外して新しい結果に置き換えます。外した音声は削除せず、「これを使う」で戻せます。':'生成した声は、このキャラクターの参照音声に追加されます。'}</p>
    <div class="ch-dialog-actions">${btn(s.history.length?'✦ 再生成':'✦ 生成',`data-ch-generate ${s.running?'disabled':''}`,'primary')}${btn('閉じる','data-ch-close')}</div>`;
   body.querySelector('[data-ch-generate]').onclick=generateDesign;
   body.querySelectorAll('[data-ch-play]').forEach(b=>b.onclick=()=>play(b.dataset.chPlay));
   body.querySelectorAll('[data-ch-use]').forEach(b=>b.onclick=()=>useDesign(b.dataset.chUse));
  }
  async function generateDesign(){
   const s=design,text=overlay?.querySelector('[data-ch-design-text]')?.value.trim();
   if(!s||s.running)return;
   if(!text)return opt.toast('読み上げるサンプル台詞を入力してください',true);
   s.sampleText=text;s.running=true;s.message='TTSモデルを読み込んでいます…';renderDesign();
   try{
    let job=await call(`${API}/${encodeURIComponent(s.id)}/design`,jsonBody('POST',{sampleText:text,replaceAssetId:s.current}));
    while(job.status==='running'){
     await wait(1500);
     job=await call('/api/jobs/'+encodeURIComponent(job.id));
     if(!s.closed){s.message=job.message||'生成中…';renderDesign()}
    }
    if(job.status!=='done')throw new Error(job.message||'Voice Designに失敗しました');
    s.history.push({assetId:job.referenceAssetId,text});s.current=job.referenceAssetId;
    s.message='生成しました。試聴して、気に入らなければサンプル台詞を変えて再生成できます。';
    data=await fetchRegistry();captureDrafts();render();await opt.onSaved(data);
   }catch(e){s.message='失敗: '+e.message;opt.toast('Voice Designに失敗: '+e.message,true)}
   finally{s.running=false;renderDesign()}
  }
  async function useDesign(aid){
   const s=design,c=byId(data,s?.id),chosen=s?.history.find(r=>r.assetId===aid);
   if(!c||!chosen||s.running)return;
   const results=new Set(s.history.map(r=>r.assetId));
   const spec={...c,refs:[...(c.refs||[]).filter(id=>!results.has(id)),aid],refText:chosen.text};
   if(await mutate(rev=>call(`${API}/${encodeURIComponent(c.id)}`,jsonBody('PUT',{character:spec,expectedRevision:rev})),
     `${s.history.indexOf(chosen)+1}回目の声を${c.name}の参照音声にしました`)){s.current=aid;renderDesign()}
  }

  // Import from any VoiceDrama or game project without opening it.
  let sources=[],picked=0,chosen=new Set(),targets=new Map();
  async function openImport(preset=null){
   try{sources=await call(API+'/sources')}catch(e){return opt.toast('取り込み元を読み込めません: '+e.message,true)}
   if(!sources.length)return opt.toast('取り込めるキャラクターがある作品・ゲームがありません',true);
   picked=Math.max(0,preset?sources.findIndex(s=>s.kind===preset.kind&&s.id===preset.id):0);
   chosen=new Set(preset?.keys||sources[picked].characters.filter(c=>c.suggested).map(c=>c.key));targets=new Map();
   showDialog('<div data-ch-import-body></div>');renderImport();
  }
  function renderImport(result=''){
   const body=overlay?.querySelector('[data-ch-import-body]');if(!body)return;
   const source=sources[picked],existing=source.characters.filter(c=>c.existing).length;
   const conflictMode=body.querySelector('[data-ch-conflict]')?.value||'skip',apply=body.querySelector('[data-ch-apply]')?.checked??true;
   body.innerHTML=`<h3>作品・ゲームからキャラクターを取り込む</h3>
    <p class="ch-note">選んだ作品やゲームを開かずに、キャラクターの声の設定と参照音声を共通キャラクターへコピーします。参照音声は取り込み元に残ります。</p>
    <label class="ch-field">取り込み元<select data-ch-source>${sources.map((s,i)=>`<option value="${i}" ${i===picked?'selected':''}>${s.kind==='game'?'ゲーム音声':'ボイスドラマ'}：${esc(s.title)}（${s.characters.length}人）</option>`).join('')}</select></label>
    <div class="ch-import-head"><span>${source.characters.length}人${existing?` · 同じ名前の共通キャラクターあり ${existing}人`:''}</span><span>${btn('すべて選択','data-ch-all')}${btn('選択解除','data-ch-none')}</span></div>
    <div class="ch-import-list">${source.characters.map(c=>`<label class="ch-import-row"><input type="checkbox" data-ch-key="${esc(c.key)}" ${chosen.has(c.key)?'checked':''}><span><b>${esc(c.name)}</b>${c.aliases.length?`<small>別名：${esc(c.aliases.join('、'))}</small>`:''}<small>${c.hasVoice?`${esc(c.engine==='qwen'?'Qwen3-TTS':'Irodori-TTS')} · ${esc(String(c.model).split('/').pop())} · 参照音声 ${c.refs}件${c.voiceSource?` · 元：${esc(c.voiceSource)}`:''}`:'名前のみ（声の設定なし）'}</small></span><span class="ch-import-target"><span class="ch-tag ${c.existing||targets.get(c.key)?'ch-tag-warn':''}">${targets.get(c.key)?`上書き：${esc(byId(data,targets.get(c.key))?.name||'')}`:c.existing?`同名あり：${esc(c.existing)}`:'新規'}</span><select data-ch-target="${esc(c.key)}" aria-label="${esc(c.name)}の取り込み先" title="名前が違う共通キャラクター（例：警察 → 警察官）へ上書きするときに選びます"><option value="">取り込み先：自動（${c.existing?'同名「'+esc(c.existing)+'」':'新規追加'}）</option>${data.characters.map(x=>`<option value="${esc(x.id)}" ${targets.get(c.key)===x.id?'selected':''}>「${esc(x.name)}」へ上書き</option>`).join('')}</select></span></label>`).join('')}</div>
    <label class="ch-field">同じ名前（または別名）の共通キャラクターがある場合<select data-ch-conflict>
     <option value="skip" ${conflictMode==='skip'?'selected':''}>スキップ（今の共通キャラクターを残す）</option>
     <option value="overwrite" ${conflictMode==='overwrite'?'selected':''}>上書き更新（声の設定・参照音声を取り込み元に合わせ、別名を追加）</option>
     <option value="rename" ${conflictMode==='rename'?'selected':''}>名前を変えて別のキャラクターとして追加</option></select></label>
    <label class="ch-check"><input type="checkbox" data-ch-apply ${apply?'checked':''}> 取り込み元のシナリオでも、取り込んだ共通キャラクターを使う${source.kind==='game'?'（ゲーム専用の声設定から切り替えます。設定は記録として残ります）':''}</label>
    ${result?`<div class="ch-result" role="status">${result}</div>`:''}
    <div class="ch-dialog-actions">${btn('取り込む',`data-ch-run ${chosen.size?'':'disabled'}`,'primary')}${btn('閉じる','data-ch-close')}</div>`;
   body.querySelector('[data-ch-source]').onchange=e=>{picked=Number(e.target.value);chosen=new Set(sources[picked].characters.filter(c=>c.suggested).map(c=>c.key));targets=new Map();renderImport()};
   body.querySelectorAll('[data-ch-target]').forEach(select=>select.onchange=()=>{const k=select.dataset.chTarget;if(select.value){targets.set(k,select.value);chosen.add(k)}else targets.delete(k);renderImport()});
   body.querySelectorAll('[data-ch-key]').forEach(box=>box.onchange=()=>{if(box.checked)chosen.add(box.dataset.chKey);else chosen.delete(box.dataset.chKey);body.querySelector('[data-ch-run]').disabled=!chosen.size});
   body.querySelector('[data-ch-all]').onclick=()=>{chosen=new Set(source.characters.map(c=>c.key));renderImport()};
   body.querySelector('[data-ch-none]').onclick=()=>{chosen.clear();renderImport()};
   body.querySelector('[data-ch-run]').onclick=runImport;
  }
  async function runImport(){
   const body=overlay?.querySelector('[data-ch-import-body]'),source=sources[picked];
   if(!body||!chosen.size)return;
   const conflictMode=body.querySelector('[data-ch-conflict]').value,apply=body.querySelector('[data-ch-apply]').checked;
   const picks=Object.fromEntries([...targets].filter(([k])=>chosen.has(k)));
   if(((conflictMode==='overwrite'&&source.characters.some(c=>chosen.has(c.key)&&c.existing))||Object.keys(picks).length)&&
     !confirm('選んだ共通キャラクターの声の設定と参照音声を、取り込み元の内容で上書きします（取り込み元に参照音声がなければ今の参照音声を残します）。\nこのキャラクターを使うすべての作品に反映されます。続行しますか？'))return;
   body.querySelector('[data-ch-run]').disabled=true;
   await opt.beforeImport();
   const info={source:{kind:source.kind,id:source.id}};
   // The host is told what was imported (and from where) when it refreshes.
   const result=await mutate(rev=>call(API+'/import',jsonBody('POST',{source:info.source,keys:[...chosen],conflict:conflictMode,applyToSource:apply,targets:picks,expectedRevision:rev}))
     .then(r=>{info.summary=r.summary;return r}),null,info);
   if(!result){renderImport();return}
   const s=result.summary;
   const parts=[`追加 ${s.added.length}人`,`上書き更新 ${s.updated.length}人`,`スキップ ${s.skipped.length}人`,`名前を変えて追加 ${s.renamed.length}人`];
   const text=`<b>取り込みました</b>：${parts.join(' · ')}${apply?` · シナリオに適用 ${s.applied}話者`:''}`+
    (s.renamed.length?`<br>名前を変えて追加：${s.renamed.map(r=>esc(r.from)+' → '+esc(r.to)).join('、')}`:'')+
    (s.droppedAliases.length?`<br>ほかのキャラクターと重なるため付けなかった別名：${esc(s.droppedAliases.join('、'))}`:'');
   opt.toast(`キャラクターを取り込みました（追加 ${s.added.length}・上書き ${s.updated.length}・スキップ ${s.skipped.length}）`);
   targets=new Map();
   try{sources=await call(API+'/sources');picked=Math.max(0,sources.findIndex(x=>x.kind===info.source.kind&&x.id===info.source.id))}catch(_){}
   renderImport(text);
  }

  root.addEventListener('click',e=>{
   const action=e.target.closest('[data-ch-action]')?.dataset.chAction;
   if(action==='reload')return load();
   if(action==='import')return openImport();
   const card=e.target.closest('[data-ch-id]');if(!card)return;
   const id=card.dataset.chId;
   const playButton=e.target.closest('[data-ch-play]');if(playButton)return play(playButton.dataset.chPlay);
   const unrefButton=e.target.closest('[data-ch-unref]');if(unrefButton)return unref(id,unrefButton.dataset.chUnref);
   const linkButton=e.target.closest('[data-ch-link]');if(linkButton)return linkReference(id,linkButton.dataset.chLink);
   if(e.target.closest('[data-ch-upload]'))return card.querySelector('[data-ch-file]').click();
   if(e.target.closest('[data-ch-design]'))return openDesign(id);
   if(e.target.closest('[data-ch-delete]'))return remove(id);
   const extra=e.target.closest('[data-ch-extra]');
   if(extra){const c=byId(data,id);if(c)opt.actions(c)[Number(extra.dataset.chExtra)]?.run(c)}
  });
  root.addEventListener('submit',e=>{
   const form=e.target.closest('form');if(!form||!root.contains(form))return;
   e.preventDefault();
   if(form.matches('[data-ch-add]')){
    const name=form.elements.namedItem('name').value.trim();
    if(name)create({name}).then(added=>{if(added)root.querySelector('[data-ch-add]')?.reset()});
    return;
   }
   const card=form.closest('[data-ch-id]');if(card)save(form,card.dataset.chId);
  });
  root.addEventListener('change',e=>{
   if(e.target.matches('[data-ch-file]')){const card=e.target.closest('[data-ch-id]');const files=[...e.target.files];e.target.value='';if(card)upload(card.dataset.chId,files);return}
   if(e.target.matches('select[name="engine"],select[name="mode"]')){const form=e.target.closest('[data-ch-form]');if(form&&(e.target.name==='engine'||form.elements.namedItem('engine').value==='qwen'))engineChanged(form)}
  });
  root.addEventListener('input',e=>{if(e.target.matches('[data-ch-filter]')){captureDrafts();filter=e.target.value;renderList()}});
  root.addEventListener('toggle',e=>{const card=e.target.closest?.('[data-ch-id]');if(card===e.target){if(card.open)open.add(card.dataset.chId);else open.delete(card.dataset.chId)}},true);
  render();
  return {load,openImport,create,refresh:rerender,
   focusAdd(){const input=root.querySelector('[data-ch-add] input');input?.scrollIntoView?.({block:'center'});input?.focus()},
   get data(){return data}};
 }

 global.CharacterRegistry={match,byId,fetchRegistry,createEditor};
})(globalThis);
