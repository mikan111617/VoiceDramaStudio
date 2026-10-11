'use strict';
/* Entirely separate game-voice state from the VoiceDrama Studio timeline editor. */
let game=null,scenarios=[],castProjects=[],scenarioKey='',focusedVoice='',marked=new Set(),pollTimer=null;
let sideTab='files';
const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const request=async(url,options={})=>{const response=await fetch(url,options);if(!response.ok){let msg=await response.text();try{let j=JSON.parse(msg);msg=j.detail||msg}catch{}throw Error(msg)}return response.headers.get('content-type')?.includes('application/json')?response.json():response};
const json=(method,body)=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
function message(value,bad=false){const div=document.createElement('div');div.className='toast';div.style.borderColor=bad?'#bc7a7a':'#728ba1';div.textContent=value;$('toasts').append(div);setTimeout(()=>div.remove(),4800)}
function busy(label){$('saveNotice').textContent=label}
function showImportError(messageText){const node=$('importError');node.textContent='KS取り込みエラー: '+messageText;node.hidden=false;message(node.textContent,true)}
function clearImportError(){const node=$('importError');node.textContent='';node.hidden=true}
function voicesOf(scen=scenarioKey){return (game?.voices||[]).filter(v=>v.scenario===scen)}
function selectedVoice(){return game?.voices.find(v=>v.voiceId===focusedVoice)}
function displayStatus(v){return v.stale?'要再生成':v.audio?'生成済み':'未生成'}
async function refreshGames(selected){scenarios=await request('/api/game/projects');const list=$('projectList');list.innerHTML=scenarios.map(p=>`<option value="${esc(p.id)}" ${p.id===selected?'selected':''}>${esc(p.title)}</option>`).join('');if(!scenarios.length){game=null;await createNew(false);return}await openGame(selected&&scenarios.some(s=>s.id===selected)?selected:scenarios[0].id)}
async function createNew(ask=true){let name=ask?prompt('ゲーム音声プロジェクト名','新しいゲーム音声'):'新しいゲーム音声';if(!name?.trim())return;try{const created=await request('/api/game/projects',json('POST',{title:name.trim()}));await refreshGames(created.id)}catch(e){message(e.message,true)}}
async function openGame(id){try{game=await request('/api/game/projects/'+id);castProjects=await request('/api/game/cast-projects');focusedVoice='';marked.clear();scenarioKey=game.scenarios[0]?.key||'';busy('保存済み');$('exportFormat').value=game.format||'wav';renderAll()}catch(e){message(e.message,true)}}
function renderAll(){if(!game)return;$('exportFormat').value=game.format||'wav';$('lastIssued').textContent=String(game.lastIssued||0);const speakerInfo=$('speakerFilterInfo');if(speakerInfo){const hasSheet=!!(game.speakerFilterEnabled||Object.keys(game.characterSheet||{}).length);speakerInfo.textContent=hasSheet?'キャラクターシートの登録者のみ音声化（TIPS・未登録話者は除外）':'キャラクターシート未登録：TIPSのみ自動除外。キャラクターシートの登録を推奨します。'}const selected=$('projectList');if(selected.querySelector(`option[value="${game.id}"]`))selected.value=game.id;renderScenarios();renderSource();renderVoiceRows();renderInspector();renderBindings();renderPendingKS()}
function renderPendingKS(){
 const pending=game?.pendingKS||[];
 const info=$('pendingKsInfo');
 info.textContent=pending.length?`仮登録中: ${pending.length}件\n`+pending.map(x=>'・'+x.filename).join('\n'):'仮登録中のKSはありません';
 $('finalizeKs').disabled=!pending.length;
 const remaps=game?.lastIdRemaps||[];
 $('idRemapInfo').textContent=remaps.length?`前回の一括確定で自動発行IDを${remaps.length}件変更しました。\n`+
   remaps.slice(0,8).map(x=>`${x.scenario} / ${x.speaker}: ${x.oldId} → ${x.newId}`).join('\n')+
   (remaps.length>8?'\nほか '+(remaps.length-8)+'件':''):'';
}
async function finalizeKs(){
 if(!game||!(game.pendingKS||[]).length)return message('先にKSを仮登録してください',true);
 const n=game.pendingKS.length;
 if(!confirm(`仮登録中の${n}シナリオを一括確定しますか？\nすべての既存ボイスIDを先に予約し、新しいセリフだけに未使用IDを発行します。\n過去に自動発行されたIDが既存IDと衝突した場合は、自動発行側を変更します。`))return;
 busy('全シナリオのボイスIDを照合中…');
 try{
  game=await request(`/api/game/projects/${game.id}/finalize-ks`,json('POST',{expectedRevision:game.revision}));
  scenarioKey=game.scenarios.find(s=>s.key===scenarioKey)?.key||game.scenarios.at(-1)?.key||'';
  focusedVoice='';marked.clear();renderAll();clearImportError();busy('保存済み');
  message(`全${n}件のKSを確定しました。自動発行IDの変更: ${(game.lastIdRemaps||[]).length}件`);
 }catch(e){busy('確定失敗');showImportError(e.message)}
}
function renderScenarios(){const list=$('scenarioList');list.innerHTML=game.scenarios.map(sc=>`<button class="scenario-btn ${scenarioKey===sc.key?'active':''}" data-scenario="${esc(sc.key)}"><span title="${esc(sc.filename)}">${esc(sc.key)}</span><span>${sc.count}件</span></button>`).join('');}
function showScenario(key){scenarioKey=key;focusedVoice='';marked.clear();renderAll()}
function renderSource(){let scen=game.scenarios.find(s=>s.key===scenarioKey);$('scenarioTitle').textContent=scen?.filename||'シナリオを選んでください';$('scenarioMeta').textContent=scen?`${scen.format.toUpperCase()} • ${voicesOf().length} 台詞${Object.keys(scen.ignoredSpeakers||{}).length?' • 音声化対象外: '+Object.entries(scen.ignoredSpeakers).map(([name,count])=>name+' '+count+'件').join('、'):''} • 原文は読み取り専用`:'KS・ボイスリストJSONの両形式に対応';const viewer=$('sourceViewer');if(!scen){viewer.innerHTML='<div class="empty">左の「シナリオ .ks」か「ボイスリスト JSON」から取り込んでください。</div>';return}
const rows=voicesOf();if(scen.format==='voice-list'){viewer.innerHTML=rows.map((v,i)=>`<div class="source-line voiced ${v.voiceId===focusedVoice?'selected':''}" data-from-voice="${esc(v.voiceId)}"><span class="number">${i+1}</span><span>${esc(v.speaker)} / ${esc(v.sourceId)}: ${esc(v.text)}</span></div>`).join('');return}
const map=new Map(rows.map(v=>[v.sourceLine,v]));viewer.innerHTML=scen.content.split(/\r?\n/).map((line,index)=>{const n=index+1,v=map.get(n);return `<div class="source-line ${v?'voiced':''} ${v?.voiceId===focusedVoice?'selected':''}" ${v?`data-from-voice="${esc(v.voiceId)}"`:''}><span class="number">${n}</span><span>${v?'<b class="voice-id">'+esc(v.voiceId)+'</b>  ':''}${esc(line)||'&nbsp;'}</span></div>`}).join('');}
function renderVoiceRows(){
 const query=$('voiceSearch').value.toLowerCase().trim();
 const all=voicesOf();
 const rows=all.filter(v=>!query||[v.voiceId,v.sourceId,v.speaker,v.text,v.ttsText].join(' ').toLowerCase().includes(query));
 const wrap=$('voiceList');
 $('voiceCount').textContent=`${rows.length}/${all.length}`;
 wrap.innerHTML=rows.map(v=>`<div class="voice-row ${focusedVoice===v.voiceId?'selected':''}" data-voice="${esc(v.voiceId)}"><input type="checkbox" data-mark="${esc(v.voiceId)}" ${marked.has(v.voiceId)?'checked':''} aria-label="${esc(v.voiceId)}を生成対象に選ぶ"><span class="voice-id">${esc(v.voiceId)}</span><span class="voice-speaker" title="${esc(v.speaker)}">${esc(v.speaker)}</span><span class="voice-text" title="${esc(v.text)}">${esc(v.text)}</span><span class="voice-status ${v.stale?'stale':v.audio?'ready':''}">${displayStatus(v)}</span></div>`).join('')||'<div class="empty">該当する台詞がありません。</div>';
 // Selection controls refer to the entire current scenario, even when the
 // text search hides some of its lines. Never modify other scenarios' marks.
 const totalSelected=all.filter(v=>marked.has(v.voiceId)).length;
 $('voiceSelectionCount').textContent=`このシナリオで ${totalSelected}/${all.length}件選択中`;
 $('selectAll').checked=all.length>0&&totalSelected===all.length;
 $('selectAll').indeterminate=totalSelected>0&&totalSelected<all.length;
 const speakers=[...new Set(all.map(v=>v.speaker))].sort((a,b)=>a.localeCompare(b,'ja'));
 const picker=$('speakerSelect'),chosen=picker.value;
 picker.innerHTML=speakers.map(speaker=>{const count=all.filter(v=>v.speaker===speaker).length;
   const profile=resolveSheetProfile(speaker)?.profile;
   const name=profile?.display_name&&profile.display_name!==speaker?`${speaker}（${profile.display_name}）`:speaker;
   return `<option value="${esc(speaker)}">${esc(name)} · ${count}件</option>`;
 }).join('');
 if(speakers.includes(chosen))picker.value=chosen;
 else if(speakers.length)picker.value=speakers[0];
 picker.disabled=!speakers.length;
 $('speakerSelectAll').disabled=!speakers.length;
 $('speakerDeselectAll').disabled=!speakers.length;
}
function markSpeaker(checked){
 const speaker=$('speakerSelect').value;
 if(!speaker)return;
 const matches=voicesOf().filter(v=>v.speaker===speaker);
 for(const voice of matches){if(checked)marked.add(voice.voiceId);else marked.delete(voice.voiceId)}
 renderVoiceRows();
 message(`${speaker} の${matches.length}件を${checked?'選択':'解除'}しました`);
}
function setSideTab(mode){
 sideTab=mode==='casts'?'casts':'files';
 for(const [name,button,panel] of [['files','sideTabFiles','sideFilesPanel'],['casts','sideTabCasts','sideCastsPanel']]){
  const active=sideTab===name;
  $(button).classList.toggle('active',active);
  $(button).setAttribute('aria-selected',String(active));
  $(panel).hidden=!active;
 }
}

function focusVoice(id,scrollSource=false){focusedVoice=id;renderVoiceRows();renderSource();renderInspector();if(scrollSource)$('sourceViewer').querySelector(`[data-from-voice="${CSS.escape(id)}"]`)?.scrollIntoView({behavior:'smooth',block:'center'});}
function sheetEntries(){
 const result=[];
 for(const [id,profile] of Object.entries(game?.characterSheet||{}))result.push({id,profile,group:'characters'});
 for(const [id,profile] of Object.entries(game?.referenceCharacters||{}))result.push({id,profile,group:'reference'});
 return result;
}
function resolveSheetProfile(speaker){
 const value=String(speaker||'').trim().toLowerCase();
 return sheetEntries().find(entry=>[entry.id,entry.profile.display_name,...(entry.profile.aliases||[])]
     .some(name=>String(name||'').trim().toLowerCase()===value))||null;
}
function castRoster(){
 const counts=new Map();
 for(const v of game?.voices||[])counts.set(v.speaker,(counts.get(v.speaker)||0)+1);
 const active=[...counts].map(([speaker,count])=>({speaker,count,profile:resolveSheetProfile(speaker)?.profile||{},active:true}));
 const displayed=new Set(active.map(item=>resolveSheetProfile(item.speaker)?.id).filter(Boolean));
 const sheetOnly=sheetEntries().filter(entry=>!displayed.has(entry.id)).map(entry=>({
     speaker:entry.id,count:0,profile:entry.profile,active:false,reference:entry.group==='reference'}));
 return [...active.sort((a,b)=>a.speaker.localeCompare(b.speaker)),
         ...sheetOnly.sort((a,b)=>a.speaker.localeCompare(b.speaker))];
}
function renderInspector(){const wrap=$('inspector');const v=selectedVoice();if(!v){wrap.innerHTML='<div class="empty">台詞をクリックすると、演技指示を編集したり生成音声を試聴できます。</div>';return}const profile=resolveSheetProfile(v.speaker)?.profile;wrap.innerHTML=`<h2 class="voice-inspector-title">${esc(v.speaker)}</h2><div class="keypair"><span class="tag">${esc(v.voiceId)}</span><span class="muted">${esc(v.scenario)} ${v.sourceLine?'· line '+v.sourceLine:''}</span></div>${v.sourceId?`<div class="note">ゲーム内ID: <b>${esc(v.sourceId)}</b></div>`:''}<div class="field"><label>元の台詞（シナリオ上の文章）</label><div class="note">${esc(v.text)}</div></div><div class="field"><label>読み上げテキスト（ゲーム原文は変更しません）</label><textarea id="ttsText">${esc(v.ttsText)}</textarea></div><div class="field"><label>演技指示・キャプション（任意）</label><textarea id="caption">${esc(v.caption||'')}</textarea></div><button class="primary" id="saveLine">この台詞の設定を保存</button>${v.audio?`<div class="field"><label>生成済み音声 ${v.stale?'（編集後・旧テイク）':''}</label><audio class="audio" controls preload="none" src="/api/game/projects/${game.id}/audio/${encodeURIComponent(v.voiceId)}"></audio></div>`:''}${profile?`<div class="note"><strong>${esc(profile.display_name||v.speaker)}</strong>（${esc(profile.reading||'')}）<p>${esc(profile.baseline_tone||'')}</p>${(profile.acting_notes||[]).slice(0,4).map(n=>`<p>・${esc(n)}</p>`).join('')}</div>`:''}`;}
// Same model choices and parameters as VoiceDrama Studio's cast editor.
const voiceModels={
 irodori:[['Aratako/Irodori-TTS-v4.1-Small','Irodori v4.1 Small'],['Aratako/Irodori-TTS-v4-Large','Irodori v4 Large'],['Aratako/Irodori-TTS-v4-Large-Quantized/int8-weight-only','Irodori Large INT8'],['Aratako/Irodori-TTS-v4-Large-Quantized/int4-weight-only','Irodori Large INT4'],['phasefield-audio/Irodori-TTS-v4.1-Anime','Irodori v4.1 Anime'],['phasefield-audio/Irodori-TTS-v4.1-Anime/int8-weight-only','Irodori Anime INT8']],
 qwen:[['Qwen/Qwen3-TTS-12Hz-1.7B-Base','Qwen3 Base 1.7B'],['Qwen/Qwen3-TTS-12Hz-0.6B-Base','Qwen3 Base 0.6B'],['Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign','Qwen3 Voice Design 1.7B']]
};
function localCastDefault(speaker){return {name:speaker,engine:'irodori',model:voiceModels.irodori[0][0],mode:'clone',language:'Japanese',description:'',refText:'',refs:[]}}
function castKeyFor(speaker){return resolveSheetProfile(speaker)?.id||speaker}
function renderLocalCastEditor(speaker,cast){
 const models=voiceModels[cast.engine]||voiceModels.irodori;
 const fieldSelect=(name,label,options)=>`<label>${label}<select data-local-field="${name}">${options.map(([val,title])=>`<option value="${esc(val)}" ${cast[name]===val?'selected':''}>${esc(title)}</option>`).join('')}</select></label>`;
 const refs=(cast.refs||[]).map(id=>(game.voiceAssets||[]).find(a=>a.id===id)).filter(Boolean);
 return `<details class="game-cast-detail" data-cast-detail="${esc(speaker)}" ${expandedCasts.has(speaker)?'open':''}>
 <summary>⚙ 声の詳細設定　${esc(cast.model?.split('/').at(-1)||'')}</summary>
 <div class="game-cast-settings">
 <div class="game-cast-grid">
 ${fieldSelect('engine','既定TTS',[['irodori','Irodori-TTS'],['qwen','Qwen3-TTS']])}
 ${fieldSelect('language','言語',['Japanese','English','Chinese','Auto'].map(x=>[x,x]))}
 ${fieldSelect('mode','生成方式',[['clone','参照音声'],['design','Voice Design']])}
 ${fieldSelect('model','音声モデル',models)}
 </div>
 <label>声の説明（Voice Design・演技の基礎）<textarea data-local-field="description" rows="2">${esc(cast.description||'')}</textarea></label>
 <label>Qwen Base：参照音声の書き起こし（任意）<textarea data-local-field="refText" rows="2">${esc(cast.refText||'')}</textarea></label>
 <div class="game-cast-reference"><strong>登録済み参照音声（${refs.length}件）</strong>
 ${refs.map(a=>`<div class="game-ref-row"><span title="${esc(a.name)}">${esc(a.name)}</span><button type="button" data-ref-play="${esc(a.id)}">▶ 試聴</button><button type="button" data-ref-delete="${esc(a.id)}" class="ref-remove" title="この参照音声を外す">×</button></div>`).join('')||'<p class="helper">参照音声はまだありません</p>'}
 <div class="game-cast-buttons"><button type="button" data-ref-upload="${esc(speaker)}">＋ 参照音声を登録</button><button type="button" data-ref-design="${esc(speaker)}">✦ Voice Designで声を作成</button></div>
 <p class="helper">参照音声はゲーム音声プロジェクト内に保存します。Voice Designは生成後に参照音声として登録されます。</p></div></div></details>`;
}
function renderBindings(){
 const roster=castRoster();
 const options=[{value:'',label:'キャストを選択'}];
 for(const p of castProjects)for(const cast of p.casts)
  options.push({value:p.id+'|'+encodeURIComponent(cast.name),
    label:p.title+' / '+cast.name+' ('+(cast.model?.split('/').at(-1)||cast.engine)+')'});
 const draw=item=>{
  const key=castKeyFor(item.speaker);
  const binding=game.bindings?.[item.speaker]||game.bindings?.[key];
  const selected=binding?binding.projectId+'|'+encodeURIComponent(binding.castName):'';
  const active=game.localCastMode?.[key]==='local'||game.localCastMode?.[item.speaker]==='local';
  const cast=game.localCasts?.[key]||game.localCasts?.[item.speaker]||localCastDefault(key);
  const info=item.active?`<span class="binding-status">台詞 ${item.count}件</span>`
    :'<span class="binding-status sheet-only">シート登録済み · 現在のシナリオに台詞なし</span>';
  return `<div class="binding game-cast-card ${item.active?'':'binding-unused'}" data-cast-speaker="${esc(key)}"><label class="cast-name">${esc(item.speaker)} <span class="muted">${esc(item.profile.display_name||'')}</span>${info}</label>
  <label class="game-cast-source">音声設定の使用元<select data-cast-source="${esc(key)}"><option value="shared" ${!active?'selected':''}>ボイスドラマのキャストを利用</option><option value="local" ${active?'selected':''}>ゲーム音声専用の声設定</option></select></label>
  ${active?renderLocalCastEditor(key,cast):`<label class="game-shared-cast">共有するキャスト<select data-bind="${esc(key)}">${options.map(o=>`<option value="${esc(o.value)}" ${o.value===selected?'selected':''}>${esc(o.label)}</option>`).join('')}</select></label>`}
  </div>`;
 };
 const used=roster.filter(item=>item.active);
 const unused=roster.filter(item=>!item.active);
 $('castBindings').innerHTML=(used.length?'<div class="binding-heading">シナリオ内の話者</div>'+used.map(draw).join(''):'')+
  (unused.length?`<div class="binding-heading">キャラクターシート登録者（台詞未検出：${unused.length}人）</div>`+
    '<p class="binding-help">キャスト設定を先に用意できます。KSを読み込むと台詞へ適用されます。</p>'+unused.map(draw).join(''):'')+
  (roster.length?'':'<div class="helper">キャラクターシートまたはシナリオを取り込むと一覧が表示されます。</div>');
}
let expandedCasts=new Set(),refUploadSpeaker='';
async function setCastSource(key,source){
 const modes={...(game.localCastMode||{})},casts={...(game.localCasts||{})};
 modes[key]=source;
 if(source==='local'&&!casts[key])casts[key]=localCastDefault(key);
 if(source==='local')expandedCasts.add(key);
 if(await update({localCasts:casts,localCastMode:modes})){renderBindings();message(`${key}の音声設定を変更しました`)}
}
async function setLocalCastField(key,field,value){
 const casts={...(game.localCasts||{})};const cast={...(casts[key]||localCastDefault(key))};cast[field]=value;
 if(field==='engine'){cast.model=voiceModels[value][0][0];cast.mode='clone'}
 if(field==='mode'&&cast.engine==='qwen')cast.model=value==='design'?'Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign':'Qwen/Qwen3-TTS-12Hz-1.7B-Base';
 if(field==='model'&&cast.engine==='qwen')cast.mode=value.includes('VoiceDesign')?'design':'clone';
 casts[key]=cast;expandedCasts.add(key);
 if(await update({localCasts:casts}))renderBindings();
}
async function uploadGameRef(file){
 if(!game||!refUploadSpeaker)return;
 const speaker=refUploadSpeaker;refUploadSpeaker='';
 const form=new FormData();form.append('file',file);
 try{busy('参照音声を登録中…');game=await request(`/api/game/projects/${game.id}/casts/${encodeURIComponent(speaker)}/reference?expectedRevision=${game.revision}`,{method:'POST',body:form});renderBindings();busy('保存済み');message(`${speaker}に参照音声を登録しました`)}
 catch(e){busy('保存エラー');message('参照音声の登録に失敗: '+e.message,true)}
}
async function removeGameRef(speaker,id){if(!confirm('この参照音声をゲーム用キャストから外しますか？'))return;
 try{game=await request(`/api/game/projects/${game.id}/casts/${encodeURIComponent(speaker)}/references/${encodeURIComponent(id)}?expectedRevision=${game.revision}`,{method:'DELETE'});renderBindings();message('参照音声を外しました')}
 catch(e){message(e.message,true)}
}
function previewGameRef(aid){const player=new Audio(`/api/game/projects/${game.id}/references/${encodeURIComponent(aid)}`);player.play().catch(e=>message('試聴できません: '+e.message,true));}
async function designGameCast(speaker){
 const example=prompt('新しい声のサンプル台詞を入力してください','こんにちは。今日もよろしくお願いします。');if(!example?.trim())return;
 try{const job=await request(`/api/game/projects/${game.id}/casts/${encodeURIComponent(speaker)}/design`,json('POST',{sampleText:example}));message('Voice Designを開始しました');poll(job.id)}catch(e){message(e.message,true)}
}
async function autoBindCasts(){
 const bindings={...game.bindings};let found=0;
 for(const item of castRoster()){
  const speaker=item.speaker;
  if(bindings[speaker]?.castName)continue;
  const profile=item.profile||{};
  const names=[speaker,profile.display_name,...(profile.aliases||[])].filter(Boolean)
       .map(v=>String(v).toLowerCase());
  const matches=[];
  for(const p of castProjects)for(const c of p.casts){
   if(names.includes(c.name.toLowerCase()))matches.push({projectId:p.id,castName:c.name});
  }
  if(matches.length===1){bindings[speaker]=matches[0];found++}
 }
 if(!found)return message('一致するキャストが見つかりません。手動で割り当ててください',true);
 if(await update({bindings})){renderBindings();message(`${found}人のキャストを自動対応しました`)}
}
async function update(changes){if(!game)return;busy('保存中…');try{game=await request(`/api/game/projects/${game.id}`,json('PUT',{expectedRevision:game.revision,changes}));busy('保存済み');return true}catch(e){busy('保存エラー');message(e.message,true);return false}}
async function saveVoice(){const v=selectedVoice();if(!v)return false;const text=$('ttsText')?.value.trim(),caption=$('caption')?.value;if(!text)return message('読み上げテキストを入力してください',true);const ok=await update({voices:[{voiceId:v.voiceId,ttsText:text,caption}]});if(ok){renderAll();message('台詞の設定を保存しました')}return ok}
async function setBinding(speaker,value){let next={...game.bindings};if(!value)delete next[speaker];else{const [projectId,...rem]=value.split('|');next[speaker]={projectId,castName:decodeURIComponent(rem.join('|'))}}if(await update({bindings:next}))renderBindings();}
async function uploadFiles(picker,kind){const files=[...picker.files];if(!files.length)return;
 if(kind==='ks'){
  clearImportError();
  if(files.length>30){showImportError('一度に読み込めるKSは最大30件です');picker.value='';return}
  const total=files.reduce((n,f)=>n+f.size,0);
  const tooLarge=files.find(f=>f.size>20*1024*1024);
  if(tooLarge){showImportError(`${tooLarge.name}: 20MBの上限を超えています`);picker.value='';return}
  if(total>60*1024*1024){showImportError('選択したKSの合計が60MBを超えています');picker.value='';return}
 }
 const form=new FormData();const revision=game.revision;let url=`/api/game/projects/${game.id}/`;if(kind==='ks'){url+=`stage-ks?expectedRevision=${revision}`;for(const file of files)form.append('files',file)}else{const endpoint={json:'voice-list',state:'voice-id-state',sheet:'character-sheet',story:'story-context'}[kind];url+=`${endpoint}?expectedRevision=${revision}`;form.append('file',files[0])}busy('取り込み中…');try{const result=await request(url,{method:'POST',body:form});game=result;if(kind==='ks'&&!scenarioKey)scenarioKey=game.scenarios[0]?.key||'';if(kind==='json'){const added=game.scenarios.at(-1);scenarioKey=added?.key||''}focusedVoice='';marked.clear();renderAll();busy('保存済み');if(kind==='ks')clearImportError();const ignored=(kind==='ks'||kind==='sheet')?game.scenarios.flatMap(sc=>Object.entries(sc.ignoredSpeakers||{}).map(([name,n])=>`${sc.key}: ${name} ${n}件`)):[];message(`${files.length}ファイルを${kind==='ks'?'仮登録しました。全KS登録後にID一括確定を押してください':'取り込みました'}${ignored.length?'（音声化対象外: '+ignored.slice(0,6).join('、')+(ignored.length>6?' ほか':'')+'）':''}`)}catch(e){busy('取り込み失敗');if(kind==='ks')showImportError(e.message);else message(e.message,true)}finally{picker.value=''}}
async function startGeneration(){if(!game)return;if((game.pendingKS||[]).length)return message('先に仮登録中のKSを一括確定してください',true);let ids=[...marked].filter(id=>game.voices.some(v=>v.voiceId===id));if(!ids.length)return message('台詞の左側のチェックを選んでください',true);const steps=Number($('numSteps').value);if(!Number.isInteger(steps)||steps<1||steps>80)return message('ステップ数は1〜80です',true);if(!confirm(`${ids.length}件の音声を生成します。\n選択したキャストのモデル・参照音声が使用されます。`))return;try{const job=await request(`/api/game/projects/${game.id}/generate`,json('POST',{voiceIds:ids,numSteps:steps}));message('ゲーム用音声の生成を開始しました');$('generateSelected').disabled=true;poll(job.id)}catch(e){message('生成を開始できません: '+e.message,true)}}
function poll(id){clearInterval(pollTimer);const check=async()=>{try{const job=await request('/api/game/jobs/'+id);$('jobStatus').textContent=`${job.message} — ${job.completed}/${job.total||0} 件${job.errors?.length?' · エラー'+job.errors.length:''}`;$('progressBar').style.width=job.total?Math.round(job.completed/job.total*100)+'%':'0%';if(job.status!=='running'){clearInterval(pollTimer);$('generateSelected').disabled=false;$('analyzeSelected').disabled=false;const selected=scenarioKey;game=await request('/api/game/projects/'+game.id);scenarioKey=selected;renderAll();message(job.status==='done'?'生成完了':`生成終了: ${job.message}`,job.status!=='done');}}catch(e){clearInterval(pollTimer);$('generateSelected').disabled=false;$('analyzeSelected').disabled=false;message(e.message,true)}};pollTimer=setInterval(check,1200);check();}
async function startAnalysis(){
 if(!game)return;
 const ids=[...marked].filter(id=>game.voices.some(v=>v.voiceId===id));
 if(!ids.length)return message('解析する台詞をチェックしてください',true);
 const model=$('ollamaModel').value.trim();if(!model)return message('Ollamaのモデル名を入力してください',true);
 if(!confirm(`${ids.length}件の演技指示をOllamaで自動生成します。\n既に入力したキャプションも更新されます。\n原文は変更されません。`))return;
 try{const job=await request(`/api/game/projects/${game.id}/analyze`,json('POST',{voiceIds:ids,model}));message('Ollamaで演技指示を解析中です');$('analyzeSelected').disabled=true;poll(job.id)}catch(e){message('Ollamaの解析を開始できません: '+e.message,true)}
}
function exportCaptionCsv(scope){
 if(!game||!game.voices.length)return message('先にシナリオを取り込んでください',true);
 if(scope==='scene'&&!scenarioKey)return message('シナリオを選択してください',true);
 const params=scope==='scene'?('?scenario='+encodeURIComponent(scenarioKey)):'';
 const link=document.createElement('a');link.href=`/api/game/projects/${game.id}/captions.csv`+params;
 link.download='game_captions.csv';document.body.appendChild(link);link.click();link.remove();
 message('キャプション編集用CSVを書き出します');
}
const CAPTION_AI_PROMPT=`添付のCSVの「キャプション」列だけ記入し、編集済みのCSVファイルとして返してください。
「ボイスID」「シナリオ」「キャラ名」「セリフ」の各列と行数・行順は絶対に変更しないでください。
キャプションには、元の台詞にない発話を追加せず、声の感情・話し方・テンポ・抑揚を具体的な日本語で1文（原則80字以内）で記入してください。
前後の台詞から状況を把握し、未開示の秘密や今後の展開を先取りしないでください。
出力はUTF-8のCSVとし、列名も変更せず、解説文やMarkdownをCSV内へ追加しないでください。`;
async function copyCaptionPrompt(){
 try{await navigator.clipboard.writeText(CAPTION_AI_PROMPT);message('ChatGPT・Claude用の依頼文をコピーしました')}
 catch(e){message('コピーできません。ブラウザのクリップボード権限を確認してください',true)}
}
async function importCaptionCsv(){
 const file=$('captionPicker').files[0];if(!file||!game)return;
 const gid=game.id,revision=game.revision;
 try{
  if(file.size>8*1024*1024)return message('CSVは8MB以内にしてください',true);
  const form=new FormData();form.append('file',file);
  busy('キャプションを照合中…');
  const summary=await request(`/api/game/projects/${gid}/captions/preview?expectedRevision=${revision}`,{method:'POST',body:form});
  busy('保存済み');
  if(!summary.updated)return message(`変更するキャプションはありません（空欄 ${summary.blank}件、同じ内容 ${summary.unchanged}件）`);
  const samples=summary.examples.map(x=>`${x.voiceId}: ${x.caption}`).join('\n');
  if(!confirm(`キャプションCSVを反映しますか？\n\n対象: ${summary.rows}行\n変更: ${summary.updated}件\n空欄（変更なし）: ${summary.blank}件\n同じ内容: ${summary.unchanged}件\n\n変更例:\n${samples}\n\n台詞本文・ID・音声ファイルは変更しません。変更済み音声は要再生成になります。`))return;
  busy('キャプションを反映中…');
  const result=await request(`/api/game/projects/${gid}/captions/import?expectedRevision=${revision}`,{method:'POST',body:form});
  game=result.project;renderAll();busy('保存済み');message(`キャプションを${result.summary.updated}件反映しました`);
 }catch(e){busy('保存エラー');message('CSVを取り込めません: '+e.message,true)}
 finally{$('captionPicker').value=''}
}
function downloadZip(){if((game?.pendingKS||[]).length)return message('先に「全KSのボイスIDを一括確定」してください',true);if(!game||!game.scenarios.length)return message('シナリオがありません',true);const link=document.createElement('a');link.href=`/api/game/projects/${game.id}/export.zip`;link.download='game_voice_export.zip';document.body.appendChild(link);link.click();link.remove();message('ゲーム用ZIPを準備しています。未生成音声は missing_voices.json に記録されます。')}
function wire(){$('newProject').onclick=()=>createNew();$('finalizeKs').onclick=finalizeKs;$('projectList').onchange=e=>openGame(e.target.value);for(const [button,picker,kind] of [['importKs','ksPicker','ks'],['importJson','jsonPicker','json'],['importState','statePicker','state'],['importSheet','sheetPicker','sheet'],['importStory','storyPicker','story']]){$(button).onclick=()=>(game?$(picker).click():message('プロジェクトを作成してください',true));$(picker).onchange=()=>uploadFiles($(picker),kind)}$('scenarioList').onclick=e=>{const b=e.target.closest('[data-scenario]');if(b)showScenario(b.dataset.scenario)};$('voiceList').onclick=e=>{if(e.target.matches('[data-mark]')){e.target.checked?marked.add(e.target.dataset.mark):marked.delete(e.target.dataset.mark);renderVoiceRows();return}const row=e.target.closest('[data-voice]');if(row)focusVoice(row.dataset.voice,true)};$('sourceViewer').onclick=e=>{const row=e.target.closest('[data-from-voice]');if(row)focusVoice(row.dataset.fromVoice)};$('selectAll').onchange=e=>{for(const v of voicesOf()){if(e.target.checked)marked.add(v.voiceId);else marked.delete(v.voiceId)}renderVoiceRows()};$('voiceSearch').oninput=renderVoiceRows;$('inspector').onclick=e=>{if(e.target.id==='saveLine')saveVoice()};$('autoBindCasts').onclick=autoBindCasts;$('castBindings').onchange=e=>{
 if(e.target.matches('[data-bind]'))setBinding(e.target.dataset.bind,e.target.value);
 else if(e.target.matches('[data-cast-source]'))setCastSource(e.target.dataset.castSource,e.target.value);
 else if(e.target.matches('[data-local-field]'))setLocalCastField(e.target.closest('[data-cast-speaker]').dataset.castSpeaker,e.target.dataset.localField,e.target.value);
};
$('castBindings').onclick=e=>{
 const target=e.target,speaker=target.closest('[data-cast-speaker]')?.dataset.castSpeaker;
 if(target.matches('[data-ref-upload]')){refUploadSpeaker=speaker;$('gameRefPicker').click()}
 else if(target.matches('[data-ref-play]'))previewGameRef(target.dataset.refPlay);
 else if(target.matches('[data-ref-delete]'))removeGameRef(speaker,target.dataset.refDelete);
 else if(target.matches('[data-ref-design]'))designGameCast(speaker);
};
$('castBindings').addEventListener('toggle',e=>{if(e.target.matches('[data-cast-detail]')){const key=e.target.dataset.castDetail;e.target.open?expandedCasts.add(key):expandedCasts.delete(key)}},true);
$('gameRefPicker').onchange=e=>{const file=e.target.files[0];if(file)uploadGameRef(file);e.target.value=''};$('exportFormat').onchange=async e=>{if(await update({format:e.target.value}))renderAll()};$('analyzeSelected').onclick=startAnalysis;$('generateSelected').onclick=startGeneration;$('exportZip').onclick=downloadZip;$('exportCaptionsScene').onclick=()=>exportCaptionCsv('scene');$('exportCaptionsAll').onclick=()=>exportCaptionCsv('all');$('importCaptions').onclick=()=>game?$('captionPicker').click():message('ゲーム音声プロジェクトを作成してください',true);$('captionPicker').onchange=importCaptionCsv;$('copyCaptionPrompt').onclick=copyCaptionPrompt;}
wire();
$('sideTabFiles').onclick=()=>{setSideTab('files');document.querySelector('.side-scroll').scrollTop=0};
$('sideTabCasts').onclick=()=>{setSideTab('casts');document.querySelector('.side-scroll').scrollTop=0};
$('speakerSelectAll').onclick=()=>markSpeaker(true);
$('speakerDeselectAll').onclick=()=>markSpeaker(false);
setSideTab('files');
refreshGames().catch(e=>message(e.message,true));
