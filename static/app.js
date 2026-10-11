/* VoiceDrama Studio: script-first editor, layered Web Audio preview and local project API. */
'use strict';
let state=null, selectedScene=null,selectedLine=null,selectedClip=null, view='script';
let selectedClips=new Set(),clipSelectionAnchor=null,suppressClipClick=false;
let dirty=false,saveTimer=null,saving=null,pxPerSec=38,currentTime=0,playing=false,playStart=0,ctx=null,nodes=[],audioCache=new Map(),animationId=0;
let uploadContext={kind:'sfx',lineId:null,voiceName:null};
let jobTimer=null;
let bulkFiles=[],bulkMode='assign',bulkKind='voice',bulkCast='',bulkSort='natural',bulkScene='',bulkStart='',bulkOnlyMissing=true,bulkTargets={};
let bulkNextKey=0,bulkBusy=false;
let trashSelection=new Set();
let excelImportMode='merge';
let castCopySelection=new Set();
// Local edit history only: never roll back audio files, uploads or server-side
// destructive actions. Keep asset metadata and the server revision authoritative.
const HISTORY_LIMIT=80;
const HISTORY_FIELDS=['title','scenes','lines','clips','tracks','voices','settings'];
let undoHistory=[],redoHistory=[],historyBaseline=null,historyLastKey='',historyLastAt=0,historyRestoring=false;
function editSnapshot(){const snapshot={};for(const key of HISTORY_FIELDS)snapshot[key]=structuredClone(state?.[key]);return snapshot}
function historyButtons(){const a=$('undoBtn'),b=$('redoBtn');if(a)a.disabled=!undoHistory.length;if(b)b.disabled=!redoHistory.length}
function clearEditHistory(){undoHistory=[];redoHistory=[];historyLastKey='';historyLastAt=0;historyBaseline=state?editSnapshot():null;historyButtons()}
function recordEdit(key=''){
 if(!state)return;
 if(!historyBaseline){historyBaseline=editSnapshot();return}
 const next=editSnapshot();
 if(JSON.stringify(historyBaseline)===JSON.stringify(next))return;
 const now=Date.now();
 const sameTyping=key.startsWith('typing:')&&historyLastKey===key&&now-historyLastAt<1100;
 if(!sameTyping){undoHistory.push(historyBaseline);if(undoHistory.length>HISTORY_LIMIT)undoHistory.shift()}
 historyBaseline=next;redoHistory=[];historyLastKey=key;historyLastAt=now;historyButtons();
}
function markUnsaved(){dirty=true;$('saveState').textContent='未保存';clearTimeout(saveTimer);saveTimer=setTimeout(()=>saveNow().catch(err=>toast(err.message,true)),650)}
function restoreEdit(snapshot){
 if(!state)return;
 historyRestoring=true;
 stopNodes();
 for(const key of HISTORY_FIELDS)state[key]=structuredClone(snapshot[key]);
 historyBaseline=editSnapshot();historyLastKey='';historyLastAt=0;
 if(!state.scenes.some(s=>s.id===selectedScene))selectedScene=state.scenes[0]?.id||null;
 if(!state.lines.some(l=>l.id===selectedLine))selectedLine=null;
 if(!state.clips.some(c=>c.id===selectedClip))selectedClip=null;
 renderAll();markUnsaved();historyButtons();
 setTimeout(()=>{historyRestoring=false},0);
}
function undoEdit(){if(!undoHistory.length)return;redoHistory.push(editSnapshot());restoreEdit(undoHistory.pop());toast('編集を元に戻しました')}
function redoEdit(){if(!redoHistory.length)return;undoHistory.push(editSnapshot());restoreEdit(redoHistory.pop());toast('編集をやり直しました')}
function restoreEditingFocus(source){
 const field=source?.dataset?.lineField,lin=source?.closest?.('.script-line')?.dataset?.id;
 const vfield=source?.dataset?.vfield,cast=source?.closest?.('.cast-card')?.dataset?.voice;
 let target=null;
 if(field&&lin)target=[...document.querySelectorAll('[data-line-field]')].find(el=>el.dataset.lineField===field&&el.closest('.script-line')?.dataset.id===lin);
 else if(vfield&&cast!==undefined)target=[...document.querySelectorAll('[data-vfield]')].find(el=>el.dataset.vfield===vfield&&el.closest('.cast-card')?.dataset.voice===cast);
 else if(source?.id)target=$(source.id);
 if(target){target.focus({preventScroll:true});if(typeof target.setSelectionRange==='function')try{target.setSelectionRange(target.value.length,target.value.length)}catch{}}
}

const AUDIO_EXT=/\.(wav|mp3|flac|ogg|m4a|aac|opus)$/i;

const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fnum=(v,d=2)=>Number(v||0).toFixed(d);
const newid=p=>p+'_'+(globalThis.crypto?.randomUUID?.().replace(/-/g,'').slice(0,16)||Array.from({length:16},()=>Math.floor(Math.random()*16).toString(16)).join(''));
const asset=id=>state?.assets.find(a=>a.id===id);
const lineById=id=>state?.lines.find(l=>l.id===id);
const clipById=id=>state?.clips.find(c=>c.id===id);
const sceneName=id=>state?.scenes.find(s=>s.id===id)?.name||'';
const activeLines=()=>state?.lines.filter(l=>l.sceneId===selectedScene)||[];
const activeClips=()=>state?.clips.filter(c=>c.sceneId===selectedScene)||[];
const fmt=t=>`${Math.floor(Math.max(0,t)/60).toString().padStart(2,'0')}:${(Math.max(0,t)%60).toFixed(1).padStart(4,'0')}`;
const req=async(url,opt={})=>{const r=await fetch(url,opt);if(!r.ok){let txt;try{const j=await r.json();txt=j.detail||JSON.stringify(j)}catch{txt=await r.text()}throw new Error(typeof txt==='string'?txt:JSON.stringify(txt))}const type=r.headers.get('content-type')||'';return type.includes('application/json')?r.json():r;};
const json=(method,data)=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function toast(msg,bad=false){const el=document.createElement('div');el.className='toast';el.style.borderColor=bad?'#ae6d73':'#727762';el.textContent=msg;$('toasts').append(el);setTimeout(()=>el.remove(),4500)}
function modal(html){$('modalBody').innerHTML=html;$('modalBackdrop').classList.remove('hidden')}
function closeModal(){$('modalBackdrop').classList.add('hidden')}
const button=(label,attr='',kind='ghost')=>`<button class="btn ${kind}" ${attr}>${label}</button>`;
function dirtyState(historyKey=''){recordEdit(historyKey);markUnsaved()}
async function saveNow(){if(!state||!dirty)return saving||Promise.resolve();clearTimeout(saveTimer);if(saving){await saving;if(dirty)return saveNow();return}
  const revision=state.revision,payload=structuredClone(state);dirty=false;$('saveState').textContent='保存中…';
  saving=req(`/api/projects/${state.id}`,json('PUT',{state:payload,expectedRevision:revision}));
  try{const saved=await saving;state.revision=saved.revision;$('saveState').textContent=dirty?'未保存':'保存済み'}
  catch(e){dirty=true;$('saveState').textContent='保存エラー';if(e.message.includes('競合'))toast('別の操作でデータが更新されました。再読み込みしてください',true);throw e}
  finally{saving=null}
  if(dirty)return saveNow();
}
async function switchProject(pid){stop();if(state&&dirty)await saveNow();state=await req('/api/projects/'+pid);selectedScene=state.scenes[0]?.id||null;selectedLine=null;selectedClip=null;currentTime=0;audioCache.clear();trashSelection.clear();view='script';clearEditHistory();renderAll();$('saveState').textContent='保存済み'}
async function createProject(title){const s=await req('/api/projects',json('POST',{title}));await switchProject(s.id);toast('プロジェクトを作成しました')}
async function ensureProject(){const list=await req('/api/projects');if(!list.length){await createProject('新しいボイスドラマ')}else await switchProject(list[0].id)}
function getVoices(){return [...new Set([...state.voices.map(v=>v.name),...state.lines.map(l=>l.speaker)].filter(Boolean))]}
function selectScene(id){if(id===selectedScene)return;stop();selectedScene=id;selectedLine=null;selectedClip=null;currentTime=0;renderAll()}
function selectLine(id){selectedLine=id;selectedClip=null;document.querySelectorAll('.script-line').forEach(x=>x.classList.toggle('active',x.dataset.id===id));renderTimeline();renderInspector()}
function selectClip(id){selectedClips=new Set([id]);clipSelectionAnchor=id;selectedClip=id;selectedLine=clipById(id)?.lineId||null;renderTimeline();renderInspector();if(view==='script')renderEditor()}
function syncClipSelection(){
 const ids=new Set(activeClips().map(c=>c.id));
 selectedClips=new Set([...selectedClips].filter(id=>ids.has(id)));
 if(!ids.has(selectedClip))selectedClip=null;
 // Single-clip operations elsewhere in the editor still set selectedClip.
 if(selectedClip&&!selectedClips.has(selectedClip))selectedClips=new Set([selectedClip]);
 if(!selectedClip)selectedClips.clear();
}
function paintClipSelection(){
 document.querySelectorAll('.tl-clip').forEach(el=>{const selected=selectedClips.has(el.dataset.clip);el.classList.toggle('selected',selected);el.setAttribute('aria-pressed',String(selected))});
 if($('clipSelectionCount'))$('clipSelectionCount').textContent=`${selectedClips.size}件選択`;
 for(const id of ['selectTrackClips','selectFollowingClips'])if($(id))$(id).disabled=!selectedClip;
}
function selectClipGroup(clips){
 selectedClips=new Set(clips.map(c=>c.id));
 if(!selectedClips.has(selectedClip))selectedClip=clips[0]?.id||null;
 clipSelectionAnchor=selectedClip;selectedLine=clipById(selectedClip)?.lineId||null;
 paintClipSelection();renderInspector();
}
function selectTrackAndBelow(trackId){
 const index=state.tracks.findIndex(t=>t.id===trackId);if(index<0)return;
 const tracks=new Set(state.tracks.slice(index).map(t=>t.id));
 selectClipGroup(activeClips().filter(c=>tracks.has(c.trackId)));
}
function selectFollowingClips(){
 const clip=clipById(selectedClip);if(clip)selectClipGroup(activeClips().filter(c=>Number(c.start)>=Number(clip.start)));
}
function selectTrackClips(){const clip=clipById(selectedClip);if(clip)selectClipGroup(activeClips().filter(c=>c.trackId===clip.trackId))}
function pointerSelectClip(c,e,resize){
 if((e.ctrlKey||e.metaKey)&&!resize){
  if(selectedClips.has(c.id))selectedClips.delete(c.id);else selectedClips.add(c.id);
  selectedClip=selectedClips.has(c.id)?c.id:[...selectedClips].at(-1)||null;clipSelectionAnchor=selectedClip;
 }else if(e.shiftKey&&!resize){
  const anchor=clipById(clipSelectionAnchor);
  if(anchor&&anchor.trackId===c.trackId){
   const ordered=activeClips().filter(x=>x.trackId===c.trackId).sort((a,b)=>Number(a.start)-Number(b.start)||a.id.localeCompare(b.id));
   const a=ordered.findIndex(x=>x.id===anchor.id),b=ordered.findIndex(x=>x.id===c.id);
   for(const item of ordered.slice(Math.min(a,b),Math.max(a,b)+1))selectedClips.add(item.id);
  }else selectedClips.add(c.id);
  selectedClip=c.id;
 }else{
  if(!selectedClips.has(c.id))selectedClips=new Set([c.id]);
  selectedClip=c.id;clipSelectionAnchor=c.id;
 }
 selectedLine=clipById(selectedClip)?.lineId||null;paintClipSelection();renderInspector();
 return selectedClips.has(c.id);
}
function addScene(){const name=prompt('新しいシーン名','シーン'+(state.scenes.length+1));if(!name)return;const id=newid('scene');state.scenes.push({id,name});dirtyState();selectScene(id)}
function openSceneDelete(sid){
 const scene=state?.scenes.find(s=>s.id===sid);if(!scene)return;
 const lineCount=state.lines.filter(l=>l.sceneId===sid).length;
 const clipCount=state.clips.filter(c=>c.sceneId===sid).length;
 modal(`<h2>シーン「${esc(scene.name)}」を削除</h2><p>このシーンにある台詞 ${lineCount}件・配置 ${clipCount}件を削除します。
音声ファイル、参照音声、キャスト設定は保持されます。</p><p class="small muted">削除前にプロジェクト情報をバックアップします。最後のシーンを削除した場合は空の「シーン1」を作成します。</p><div class="modal-actions">${button('キャンセル','data-close="1"')}${button('このシーンを削除',`id="confirmDeleteSceneBtn" data-confirm-scene="${esc(sid)}"`,'danger')}</div>`);
}
async function confirmSceneDelete(sid){
 const btn=$('confirmDeleteSceneBtn');if(btn)btn.disabled=true;
 try{
  await saveNow();
  const result=await req(`/api/projects/${state.id}/scenes/${sid}/delete`,json('POST',{expectedRevision:state.revision}));
  stop();state=result.project;clearEditHistory();dirty=false;
  if(!state.scenes.some(s=>s.id===selectedScene))selectedScene=state.scenes[0]?.id||null;
  selectedLine=null;selectedClip=null;currentTime=0;renderAll();closeModal();
  $('saveState').textContent='保存済み';
  toast(`シーンを削除しました（台詞 ${result.summary.removedLines}件、配置 ${result.summary.removedClips}件）。バックアップあり`);
 }catch(e){toast('シーンを削除できません: '+e.message,true);if(btn)btn.disabled=false}
}
async function removeSelectedTake(){
 const line=lineById(selectedLine);const take=line?.takes?.find(t=>t.id===line.selectedTakeId);
 if(!take)return toast('先に削除するテイクを選択してください',true);
 const item=asset(take.assetId);
 if(!confirm(`「${item?.name||'選択テイク'}」をゴミ箱へ移動しますか？

選択テイクと対応するタイムライン上の音声が取り除かれます。
他のテイクがあれば自動的に選択されます。
音声ファイルはゴミ箱から復元できますが、台詞への紐付けは自動復元されません。`))return;
 try{
  await saveNow();
  const response=await req(`/api/projects/${state.id}/lines/${line.id}/takes/${take.id}/delete`,json('POST',{expectedRevision:state.revision}));
  stop();state=response.project;clearEditHistory();selectedClip=null;dirty=false;audioCache.delete(take.assetId);
  renderAll();$('saveState').textContent='保存済み';toast('選択したテイクをゴミ箱へ移動しました');
 }catch(e){toast('テイクを削除できません: '+e.message,true)}
}
function lineDefault(sceneId){return {id:newid('line'),sceneId,speaker:getVoices()[0]||'ナレーション',text:'',caption:'',after:state.settings?.defaultGap??0.65,status:'missing',takes:[],selectedTakeId:null,engine:'irodori',model:'Aratako/Irodori-TTS-v4.1-Small',mode:'clone',language:'Japanese',ttsSource:'cast'}}
function addLine(afterId){const l=lineDefault(selectedScene);const idx=afterId?state.lines.findIndex(x=>x.id===afterId):-1;if(idx>=0)state.lines.splice(idx+1,0,l);else{const indices=state.lines.map((x,i)=>x.sceneId===selectedScene?i:-1).filter(x=>x>=0);const i=indices.length?Math.max(...indices)+1:state.lines.length;state.lines.splice(i,0,l)}selectedLine=l.id;dirtyState();renderAll();setTimeout(()=>document.querySelector(`[data-id="${l.id}"] .line-text`)?.focus(),40)}
function removeLine(id){const l=lineById(id);if(!l||!confirm(`「${l.text.slice(0,30)||'空欄'}」を削除しますか？（音声素材はライブラリに保持）`))return;state.lines=state.lines.filter(x=>x.id!==id);state.clips=state.clips.filter(x=>x.lineId!==id);selectedLine=null;selectedClip=null;reflow();dirtyState();renderAll()}
function moveLine(id,change){const idx=state.lines.findIndex(x=>x.id===id);const target=idx+change;if(target<0||target>=state.lines.length||state.lines[target].sceneId!==selectedScene)return;[state.lines[idx],state.lines[target]]=[state.lines[target],state.lines[idx]];reflow();dirtyState();renderAll()}
function reflow(){let cursor=0;for(const l of activeLines()){const c=state.clips.find(c=>c.lineId===l.id&&c.trackId==='dialogue');if(c){if(!c.pinned){const old=c.start||0;c.start=+Math.max(0,cursor).toFixed(4);for(const a of activeClips()){if(a.anchorLineId===l.id&&a.id!==c.id)a.start=Math.max(0,c.start+(a.anchorOffset||0))}}cursor=Math.max(cursor,c.start+(c.duration||0))}cursor+=Math.max(0,Number(l.after)||0)}}
function renderSidebar(){const counts={};state.lines.forEach(l=>counts[l.sceneId]=(counts[l.sceneId]||0)+1);$('sceneList').innerHTML=state.scenes.map((s,i)=>`<div class="scene-row"><button class="scene-item ${selectedScene===s.id?'active':''}" data-scene="${esc(s.id)}"><span class="scene-mark"></span><span class="scene-title">${esc(s.name)}</span><span class="scene-count">${counts[s.id]||0}</span></button><button class="scene-delete-btn" data-delete-scene="${esc(s.id)}" title="このシーンを削除" aria-label="${esc(s.name)}を削除">×</button></div>`).join('');$('projectButton').innerHTML=esc(state.title)+' <span>⌄</span>';for(const v of ['script','voices','assets'])$('nav'+v[0].toUpperCase()+v.slice(1)).classList.toggle('active',view===v)}
function renderAll(){if(!state)return;renderSidebar();renderEditor();renderTimeline();renderInspector()}
function bulkDropZone(mode){return `<div class="batch-dropzone" data-audio-drop="${mode}"><div class="dropzone-icon">⇩</div><div><b>${mode==='assign'?'生成済みの台詞音声をまとめて割り当て':mode==='reference'?'キャストの参照音声をドロップ':'音声ファイルをまとめてライブラリへ追加'}</b><p>音声ファイルをここへドラッグ＆ドロップ · 複数ファイル対応 · 取り込み前に割り当てを確認</p></div></div>`}
function renderEditor(){if(!state)return;const act=$('panelActions');const title=$('editorTitle');const sub=$('editorSub');const key=$('editorKicker');
 if(view==='script'){
  key.textContent='SCRIPT EDITOR';title.textContent=sceneName(selectedScene);sub.textContent=`${activeLines().length} セリフ · 編集すると音声の更新を検出`;
  act.innerHTML=button('↑ Excel取込','id="importExcelBtn"')+button('🗑 台本を全削除','id="openClearScriptBtn"','danger')+button('↓ このシーンの音声ZIP','id="downloadSceneTakesZipBtn"')+button('↓ 既存音声を一括割当','id="batchAssignBtn"')+button('✦ 要生成を一括', 'id="generateMissingBtn"')+button('＋ セリフ','id="addLineTop"','primary');
  $('editorContent').innerHTML=bulkDropZone('assign')+activeLines().map((l,i)=>`<article class="script-line ${selectedLine===l.id?'active':''}" data-id="${esc(l.id)}"><div class="line-top"><span class="line-number">${String(i+1).padStart(2,'0')}</span><input class="speaker-select" data-line-field="speaker" list="speakers" value="${esc(l.speaker)}" title="話者を入力"><span class="line-status ${esc(l.status)}">${{ready:'音声あり',stale:'要再生成',missing:'未生成'}[l.status]||'未生成'}</span><div class="line-actions"><button class="icon-btn" data-move="-1" title="上へ">↑</button><button class="icon-btn" data-move="1" title="下へ">↓</button><button class="icon-btn" data-delete="line" title="削除">×</button></div></div><textarea class="line-text" data-line-field="text" placeholder="ここに台詞を入力">${esc(l.text)}</textarea><div class="caption-box"><span class="caption-tag">演技指示</span><textarea data-line-field="caption" placeholder="例：声をひそめて、辺りを警戒しながら…">${esc(l.caption)}</textarea></div><div class="file-name-box"><label>生成音声のファイル名（任意） <input data-line-field="outputFilename" type="text" maxlength="120" value="${esc(l.outputFilename||'')}" placeholder="未指定なら自動命名 · 例: mahoru_001.wav" spellcheck="false"></label></div><div class="line-footer"><label>次の台詞まで <input class="gap-input" type="number" min="0" max="60" step="0.1" data-line-field="after" value="${fnum(l.after,1)}"> 秒</label><span><button data-action="addBelow">＋ 次に追加</button>　<button data-action="select">${l.takes?.length||0} テイク　→</button></span></div></article>`).join('')+`<button class="editor-bigaction" id="addLineBottom">＋ セリフを追加</button><datalist id="speakers">${getVoices().map(n=>`<option value="${esc(n)}">`).join('')}</datalist>`;
 }else if(view==='voices'){
  key.textContent='VOICE LIBRARY';title.textContent='キャスト・声';sub.textContent='キャラクター用の参照音声のみ表示します。台詞音声は音声ライブラリへ';act.innerHTML=button('⇄ キャストを別作品へコピー','id="openCastCopyBtn"')+button('↓ 参照音声をまとめて登録','id="batchReferencesBtn"')+button('＋ キャスト','id="addVoiceBtn"','primary');
  $('editorContent').innerHTML=bulkDropZone('reference')+(state.voices.length?state.voices.map((v,i)=>`<article class="cast-card" data-voice="${i}" title="ここに音声をドロップすると、このキャストの参照音声として登録できます"><header class="cast-card-heading"><div class="cast-identity"><span class="cast-number">CAST ${String(i+1).padStart(2,'0')}</span><h3>${esc(v.name)}</h3></div><span class="cast-reference-count">参照音声 ${(v.refs||[]).length}件</span></header><div class="inspector-grid cast-fields"><div class="field"><label>名前</label><input data-vfield="name" value="${esc(v.name)}"></div><div class="field"><label>言語</label><select data-vfield="language">${['Japanese','English','Chinese','Auto'].map(z=>`<option ${z===v.language?'selected':''}>${z}</option>`).join('')}</select></div><div class="field"><label>既定TTS</label><select data-vfield="engine"><option value="irodori" ${v.engine!=='qwen'?'selected':''}>Irodori-TTS</option><option value="qwen" ${v.engine==='qwen'?'selected':''}>Qwen3-TTS</option></select></div><div class="field"><label>生成方式</label><select data-vfield="mode"><option value="clone" ${v.mode!=='design'?'selected':''}>参照音声</option><option value="design" ${v.mode==='design'?'selected':''}>Voice Design</option></select></div><div class="field wide"><label>音声モデル（Voice DesignのIrodoriモデルを選択可能）</label><select data-vfield="model">${(models[v.engine]||models.irodori).map(([m,n])=>`<option value="${esc(m)}" ${v.model===m?'selected':''}>${esc(n)}</option>`).join('')}</select></div><div class="field wide"><label>声の説明（Voice Design）</label><textarea rows="2" data-vfield="description">${esc(v.description||'')}</textarea></div><div class="field wide"><label>Qwen Base: 参照音声の書き起こし（任意）</label><textarea rows="2" data-vfield="refText">${esc(v.refText||'')}</textarea></div><div class="field wide"><label>参照音声（複数選択可 / Qwen Baseは先頭1本）</label><div class="buttonline">${state.assets.filter(a=>a.kind==='reference'||(v.refs?.includes(a.id)&&a.kind!=='reference')).map(a=>`<label class="pill" title="${a.kind==='reference'?'声の参照音声':'以前登録した台詞音声です。不要ならチェックを外してください'}"><input type="checkbox" data-ref="${esc(a.id)}" ${v.refs?.includes(a.id)?'checked':''}> ${esc(a.name.slice(0,22))}${a.kind==='reference'?'':' ⚠ 旧登録'}</label>`).join('')||'<span class="small muted">未登録</span>'}</div></div></div><section class="cast-reference-section"><div class="cast-reference-heading"><div class="cast-reference-kicker">VOICE SAMPLES</div><h4>${esc(v.name)} の参照音声</h4></div><div class="cast-reference-preview">${(v.refs||[]).map(id=>state.assets.find(a=>a.id===id)).filter(Boolean).map(a=>`<div class="ref-row"><span title="${esc(a.name)}">${esc(a.name)}</span>${button('▶ 試聴',`data-preview="${esc(a.id)}"`)}</div>`).join('')||'<span class="small muted">参照音声はまだありません</span>'}</div></section><div class="asset-actions cast-card-actions">${button('✦ Voice Designで新しい声を作成',`data-voice-design="${i}"`,'primary')}${button('＋ 参照音声を登録',`data-voice-upload="${i}"`)}${button('このキャストの全台詞へ設定を適用',`data-voice-apply="${i}"`)}${button('⇄ 別作品へコピー',`data-cast-copy="${i}"`)}${button('🗑 このキャストを削除',`data-cast-delete="${i}"`,'danger')}</div></article>`).join(''):'<div class="empty">キャストがありません。追加すると声を登録できます。</div>')+`<div class="ref-manage"><h3>参照音声の管理</h3><p class="muted small">キャストの参照音声だけを管理します。使用中の音声を削除するときは確認が必要です。</p>${state.assets.filter(a=>a.kind==='reference').map(a=>`<div class="ref-row"><span title="${esc(a.name)}">${esc(a.name)} · ${fnum(a.duration,1)}秒</span><div>${button('▶ 試聴',`data-preview="${esc(a.id)}"`)}${button('🗑 削除',`data-trash="${esc(a.id)}"`)}</div></div>`).join('')||'<p class="muted small">参照音声はまだありません。</p>'}</div>`;
 }else{
 key.textContent='AUDIO LIBRARY';title.textContent='音声ライブラリ';sub.textContent='音声をドラッグして右のマルチトラックへ配置できます。BGM・効果音・環境音など';act.innerHTML=button('↓ 音声をまとめて取り込む',`id="batchLibraryBtn"`,'primary')+button('＋ 音声ファイル',`id="uploadAudioBtn"`)+button(`🗑 選択削除 (${trashSelection.size})`,`id="trashSelectedBtn" ${trashSelection.size?'':'disabled'}`);
 $('editorContent').innerHTML=bulkDropZone('library')+(state.assets.filter(a=>a.kind!=='reference').length?state.assets.filter(a=>a.kind!=='reference').slice().reverse().map(a=>`<div class="asset-card asset-draggable" draggable="true" data-drag-asset="${esc(a.id)}" title="マルチトラックのBGM・効果音・環境音のレーンへドラッグできます"><label class="small muted" title="一括削除の対象を選択"><input type="checkbox" data-trash-select="${esc(a.id)}" ${trashSelection.has(a.id)?'checked':''}> 選択</label><span class="asset-type">${esc((a.kind||'audio').toUpperCase())}</span><div class="asset-meta"><b>${esc(a.name)}</b><span class="muted small">${fnum(a.duration,1)}s</span></div><div class="asset-actions">${button('▶ 試聴',`data-preview="${esc(a.id)}"`)}${a.kind==='voice'?button('選択中の台詞へ',`data-attach="${esc(a.id)}"`):''}${button('＋ 台詞',`data-addclip="${esc(a.id)}:dialogue"`)}${button('＋ BGM',`data-addclip="${esc(a.id)}:bgm"`)}${button('＋ 効果音',`data-addclip="${esc(a.id)}:sfx"`)}${button('＋ 環境音',`data-addclip="${esc(a.id)}:ambience"`)}${button('🗑 削除',`data-trash="${esc(a.id)}"`)}</div></div>`).join(''):'<div class="empty">音声素材はまだありません。WAV / MP3などを取り込めます。</div>')+`<button class="editor-bigaction" id="openTrashBtn">🗑 ゴミ箱を確認・復元</button>`;
 }
}
function timelineDuration(){return Math.max(40,...activeClips().map(c=>Number(c.start||0)+Number(c.duration||0)+7))}
function renderTimeline(){if(!state)return;syncClipSelection();const duration=timelineDuration(),width=Math.ceil(duration*pxPerSec),clips=activeClips();let html=`<div class="tl-ruler"><div class="tl-ruler-side">TIME</div><div class="tl-ruler-scale" data-ruler="1" style="width:${width}px">`;
 for(let t=0;t<duration;t+=5)html+=`<div class="tl-tick" style="left:${t*pxPerSec}px">${fmt(t)}</div>`;html+='</div></div>';
 for(const track of state.tracks){const these=clips.filter(c=>c.trackId===track.id);html+=`<div class="tl-row"><div class="tl-label"><b>${esc(track.name)}</b><small>${these.length} clips</small><button class="track-select-button" data-select-track-below="${esc(track.id)}" title="このトラックと下の全トラックのクリップを選択">以降を全選択</button></div><div class="tl-lane" data-drop-track="${esc(track.id)}" title="音声をここへドロップして配置" style="width:${width}px;--fiveSec:${5*pxPerSec}px;--oneSec:${pxPerSec}px" data-lane="${esc(track.id)}">`;
 for(const c of these){const a=asset(c.assetId);if(!a)continue;const w=Math.max(14,Number(c.duration||0)*pxPerSec),left=Math.max(0,Number(c.start||0)*pxPerSec);let label=c.lineId?`${lineById(c.lineId)?.speaker||'台詞'} · ${(lineById(c.lineId)?.text||'').slice(0,12)}`:a.name;html+=`<div class="tl-clip ${esc(track.kind)} ${selectedClips.has(c.id)?'selected':''}" data-clip="${esc(c.id)}" role="button" tabindex="0" aria-pressed="${selectedClips.has(c.id)}" title="${esc(a.name)}" style="left:${left}px;width:${w}px"><div class="clip-bars"></div><span class="clip-text">${esc(label)}</span><div class="clip-handle" data-resize="1"></div></div>`}html+='</div></div>'}
 html+='<div id="playhead" class="tl-playhead" style="left:'+((108+currentTime*pxPerSec))+'px"></div>';$('timeline').innerHTML=html;
 $('timelineInfo').textContent=`${clips.length} clips · ${fmt(duration)} · ${state.tracks.length} tracks`;$('zoomNumber').textContent=`${pxPerSec} px/s`;paintClipSelection();updatePlayhead()}
function updatePlayhead(){const ph=$('playhead');if(ph)ph.style.left=(108+currentTime*pxPerSec)+'px';$('timeDisplay').textContent=fmt(currentTime)}
const models={irodori:[['Aratako/Irodori-TTS-v4.1-Small','Irodori v4.1 Small'],['Aratako/Irodori-TTS-v4-Large','Irodori v4 Large (FP32)'],['Aratako/Irodori-TTS-v4-Large-Quantized/int8-weight-only','Irodori Large INT8 (RTX 4070向け)'],['Aratako/Irodori-TTS-v4-Large-Quantized/int4-weight-only','Irodori Large INT4'],['phasefield-audio/Irodori-TTS-v4.1-Anime','Irodori v4.1 Anime'],['phasefield-audio/Irodori-TTS-v4.1-Anime/int8-weight-only','Irodori Anime INT8']],qwen:[['Qwen/Qwen3-TTS-12Hz-1.7B-Base','Qwen3 Base 1.7B'],['Qwen/Qwen3-TTS-12Hz-0.6B-Base','Qwen3 Base 0.6B'],['Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign','Qwen3 Voice Design 1.7B']]};
function modelSelect(engine,value){return `<select id="lineModel"><option value="">モデルを選択</option>${(models[engine]||models.irodori).map(([id,n])=>`<option value="${esc(id)}" ${value===id?'selected':''}>${esc(n)}</option>`).join('')}<option value="custom" ${value&&!models[engine]?.some(x=>x[0]===value)?'selected':''}>その他（直接入力）</option></select><input id="modelCustom" placeholder="Hugging Face モデルID / 量子化モデルのサブフォルダ" style="${value&&!models[engine]?.some(x=>x[0]===value)?'':'display:none;'}margin-top:6px" value="${value&&!models[engine]?.some(x=>x[0]===value)?esc(value):''}">`}
function getLineTtsSource(l){
  const v=state.voices.find(v=>v.name===l.speaker);
  if(l.ttsSource==='line')return 'line';
  if(l.ttsSource==='cast')return v?'cast':'line';
  if(!v)return 'line';
  if((l.engine||'irodori')!=='irodori'||(l.model&&l.model!=='Aratako/Irodori-TTS-v4.1-Small')||(l.mode&&l.mode!=='clone'))return 'line';
  return 'cast';
}
function displayLineTts(l){
  const v=state.voices.find(v=>v.name===l.speaker);
  const source=getLineTtsSource(l), c=source==='cast'?v:l;
  const engine=c?.engine||'irodori',mode=c?.mode||'clone';
  const model=engine==='qwen'&&mode==='design'?'Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign':(c?.model||'Aratako/Irodori-TTS-v4.1-Small');
  return {source,engine,mode,model};
}
function renderInspector(){if(!state)return;const inspector=$('inspector'),l=lineById(selectedLine),c=clipById(selectedClip);
 if(c){const a=asset(c.assetId);inspector.innerHTML=`<div class="inspector-grid"><div class="field wide"><label>選択中クリップ（${selectedClips.size}件選択・ドラッグでまとめて移動）</label><div style="font-weight:700">${esc(a?.name||'不明')}</div></div><div class="field"><label>開始（秒）</label><input type="number" id="clipStart" min="0" step="0.1" value="${fnum(c.start,2)}"></div><div class="field"><label>長さ（秒）</label><input type="number" id="clipDuration" min="0.01" step="0.1" value="${fnum(c.duration,2)}"></div><div class="field"><label>音量（%）</label><input type="number" id="clipGain" min="0" max="800" step="5" value="${Math.round((c.gain??1)*100)}"></div><div class="field"><label>フェードイン（秒）</label><input type="number" id="clipFadeIn" min="0" step="0.1" value="${fnum(c.fadeIn,1)}"></div><div class="field"><label>フェードアウト（秒）</label><input type="number" id="clipFadeOut" min="0" step="0.1" value="${fnum(c.fadeOut,1)}"></div><div class="field"><label>トラック</label><select id="clipTrack">${state.tracks.map(t=>`<option value="${esc(t.id)}" ${t.id===c.trackId?'selected':''}>${esc(t.name)}</option>`).join('')}</select></div><div class="field"><label>前をカット（秒）</label><input type="number" id="clipTrimStart" min="0" step="0.1" value="${fnum(c.trimStart,1)}"></div><div class="field"><label>末尾カット（秒）</label><input type="number" id="clipTrimEnd" min="0" step="0.1" value="${fnum(c.trimEnd,1)}"></div><div class="field wide"><label><input type="checkbox" id="clipLoop" ${c.loop?'checked':''}> ループ再生（BGM・環境音）</label></div>${c.trackId!=='dialogue'?`<div class="field wide"><label>台詞への追従</label><select id="clipAnchor"><option value="">なし（固定時刻）</option>${activeLines().map(x=>`<option value="${esc(x.id)}" ${c.anchorLineId===x.id?'selected':''}>${esc(x.speaker)}：${esc(x.text.slice(0,24))}</option>`).join('')}</select></div>`:''}<div class="field wide"><label><input type="checkbox" id="clipPinned" ${c.pinned?'checked':''}> 自動整列から除外（位置を固定）</label></div></div><div class="inspect-actions">${button('▶ この位置から再生','id="playClipBtn"')}${button('クリップ削除','id="deleteClipBtn"','danger')}${l?button('台詞・TTS設定','id="inspectLineBtn"'):''}</div>`;return;}
 if(l){const hasAudio=l.takes?.length>0, effective=displayLineTts(l),isOverride=effective.source==='line';inspector.innerHTML=`<div class="inspector-grid"><div class="field wide"><label>台詞 ${esc(l.speaker)}</label><div style="font-size:12px">${esc(l.text.slice(0,90)||'（空の台詞）')}</div></div><div class="field wide"><label>音声生成の設定元</label><select id="lineTtsSource"><option value="cast" ${!isOverride?'selected':''}>キャスト設定を使う（推奨）</option><option value="line" ${isOverride?'selected':''}>この台詞だけ個別設定</option></select></div>${!isOverride?`<div class="field wide"><label>実際に使用するモデル</label><div class="inspector-note">${esc(effective.model)}（キャスト・声で変更できます）</div></div>`:`<div class="field"><label>TTSエンジン</label><select id="lineEngine"><option value="irodori" ${l.engine!=='qwen'?'selected':''}>Irodori-TTS</option><option value="qwen" ${l.engine==='qwen'?'selected':''}>Qwen3-TTS</option></select></div><div class="field"><label>声の方式</label><select id="lineMode"><option value="clone" ${l.mode!=='design'?'selected':''}>参照音声</option><option value="design" ${l.mode==='design'?'selected':''}>Voice Design</option></select></div><div class="field wide"><label>モデル</label>${modelSelect(l.engine,l.model)}</div>`}<div class="field wide"><label>生成済みテイク</label><select id="lineTake"><option value="">未選択</option>${(l.takes||[]).map((t,i)=>`<option value="${esc(t.id)}" ${t.id===l.selectedTakeId?'selected':''}>テイク ${i+1} · ${esc(t.engine||'import')} · ${esc((t.createdAt||'').slice(0,16))}</option>`).join('')}</select></div></div><div class="inspect-actions">${button('▶ 音声生成',`id="generateLineBtn"`,'primary')}${button('＋ 既存音声を割当',`id="attachPickerBtn"`)}${button('✦ 演技指示を提案',`id="suggestCaptionBtn"`)}${hasAudio?button('▶ テイクを試聴',`id="previewTakeBtn"`)+button('↓ 選択テイクを保存',`id="downloadTakeBtn"`)+(l.selectedTakeId?button('🗑 選択テイクを削除',`id="deleteSelectedTakeBtn"`,'danger'):''):''}</div><p class="inspector-note" style="margin-top:10px">生成時は、キャスト設定の参照音声・声の説明を利用します。モデルと台詞の変更はテイク履歴に影響せず、再生成を促します。</p>`;return}
 inspector.innerHTML=`<div class="inspector-note">タイムラインのクリップか、左側の台詞を選択してください。<br>音声ライブラリからBGM・効果音を追加できます。</div><div class="inspect-actions">${button('＋ BGM / 効果音を取り込む','id="insUploadBtn"')}${button('＋ トラックを追加','id="addTrackBtn"')}</div>`;
}
function addClip(aid,track='sfx',atTime=currentTime){const a=asset(aid);if(!a)return;const t=state.tracks.find(x=>x.id===track);if(!t)return;const c={id:newid('clip'),sceneId:selectedScene,trackId:track,assetId:aid,lineId:null,start:Math.max(0,Number(atTime)||0),duration:a.duration,trimStart:0,trimEnd:0,loop:false,gain:1,fadeIn:0,fadeOut:0,pinned:true};state.clips.push(c);selectedClip=c.id;selectedLine=null;dirtyState();renderTimeline();renderInspector();toast(`${a.name} を${t.name}へ追加`)}
async function attachAsset(aid,lineid){if(!lineid){toast('先に台詞を選択してください',true);return}await saveNow();const data=await req(`/api/projects/${state.id}/lines/${lineid}/attach/${aid}`,{method:'POST'});state=data;clearEditHistory();selectedLine=lineid;selectedClip=state.clips.find(x=>x.lineId===lineid)?.id||null;renderAll();toast('音声を台詞へ紐付けました')}
function uploadAudio(kind='sfx',lineId=null,voiceName=null){uploadContext={kind,lineId,voiceName};$('audioPicker').value='';$('audioPicker').click()}

// Batch import is intentionally staged: no files are copied until the user reviews the mapping.
function stageAudioBatch(files,mode='assign',cast=''){
  const arr=[...files];const audios=arr.filter(f=>AUDIO_EXT.test(f.name));
  if(audios.length!==arr.length){toast(`音声以外の${arr.length-audios.length}件を除外しました`,true)}
  if(!audios.length)return toast('WAV / MP3 / FLAC / OGG / M4A / AAC / OPUSを指定してください',true);
  if(audios.length>250)return toast('一度に250ファイルまで取り込めます',true);
  bulkFiles=audios.map((file,i)=>({file,key:++bulkNextKey,original:i}));
  bulkMode=mode;bulkCast=cast;bulkKind=mode==='reference'?'reference':'voice';
  bulkSort='natural';bulkScene=selectedScene;bulkStart=(selectedLine&&lineById(selectedLine)?.sceneId===selectedScene)?selectedLine:'';
  bulkOnlyMissing=true;bulkBusy=false;
  sortBatchFiles();rebuildBatchAssignments();showAudioBatchModal();
}
function startBatchPicker(mode='assign',cast=''){
  if(!state)return;
  uploadContext={kind:mode,voiceName:cast};
  const el=$('bulkAudioPicker');el.value='';el.click();
}
function sortBatchFiles(){if(bulkSort==='natural')bulkFiles.sort((a,b)=>a.file.name.localeCompare(b.file.name,'ja',{numeric:true,sensitivity:'base'}));else if(bulkSort==='chosen')bulkFiles.sort((a,b)=>a.original-b.original)}
function batchLines(){return state.lines.filter(l=>l.sceneId===bulkScene)}
function fileMatchesLineId(file,l){
  // Only match complete, stable IDs; numeric filename guesses are unsafe.
  const stem=file.name.replace(AUDIO_EXT,'');
  return stem===l.id || stem.includes(l.id);
}
function rebuildBatchAssignments(){
  bulkTargets={};if(bulkMode!=='assign')return;
  const all=batchLines();
  const startAt=bulkStart?all.findIndex(l=>l.id===bulkStart):-1;
  const subset=startAt>=0?all.slice(startAt):all;
  const targets=subset.filter(l=>!bulkOnlyMissing || !l.selectedTakeId);
  const available=new Set(targets.map(l=>l.id));
  const consumed=new Set();
  for(const f of bulkFiles){const matches=targets.filter(l=>available.has(l.id)&&fileMatchesLineId(f.file,l));if(matches.length===1){bulkTargets[f.key]=matches[0].id;consumed.add(matches[0].id)}}
  let i=0;
  for(const f of bulkFiles){if(bulkTargets[f.key])continue;while(i<targets.length&&consumed.has(targets[i].id))i++;if(i<targets.length){bulkTargets[f.key]=targets[i].id;consumed.add(targets[i].id);i++}else bulkTargets[f.key]=''}
}
function batchMappingErrors(){
  if(bulkMode!=='assign')return [];
  const assigned=bulkFiles.map(f=>bulkTargets[f.key]).filter(Boolean);
  if(new Set(assigned).size!==assigned.length)return ['同じ台詞に複数ファイルが割り当てられています'];
  const allowed=new Set(batchLines().map(l=>l.id));
  if(assigned.some(x=>!allowed.has(x)))return ['現在のシーン以外の台詞が指定されています'];
  return [];
}
function showAudioBatchModal(){
  if(!bulkFiles.length)return;
  const all=batchLines();const assigned=bulkMode==='assign'?bulkFiles.filter(f=>bulkTargets[f.key]).length:0;
  const castOptions=state.voices.map(v=>`<option value="${esc(v.name)}" ${v.name===bulkCast?'selected':''}>${esc(v.name)}</option>`).join('');
  const lineOptions=all.map((l,i)=>`<option value="${esc(l.id)}">${i+1}. ${esc(l.speaker)}：${esc(l.text.slice(0,38)||'(空白)')} ${l.selectedTakeId?'[音声あり]':''}</option>`).join('');
  const errors=batchMappingErrors();const unmapped=bulkFiles.length-assigned;
  const rows=bulkFiles.map((f,i)=>`<div class="batch-row"><div class="batch-file"><span class="batch-seq">${i+1}</span><span title="${esc(f.file.name)}">${esc(f.file.name)}</span><small>${(f.file.size/1024/1024).toFixed(1)} MB</small></div><div class="batch-controls">${bulkMode==='assign'?`<select class="batch-target" data-batch-target="${f.key}"><option value="">割り当てなし（素材のみ）</option>${all.map((l,j)=>`<option value="${esc(l.id)}" ${bulkTargets[f.key]===l.id?'selected':''}>${j+1}. ${esc(l.speaker)}：${esc(l.text.slice(0,28))} ${l.selectedTakeId?'[音声あり]':''}</option>`).join('')}</select>`:bulkMode==='reference'?`<span class="muted small">参照音声として登録</span>`:`<span class="muted small">素材として保存</span>`}<button title="順番を上へ" data-batch-move="${f.key}" data-dir="-1" ${i===0?'disabled':''}>↑</button><button title="順番を下へ" data-batch-move="${f.key}" data-dir="1" ${i===bulkFiles.length-1?'disabled':''}>↓</button><button title="このファイルを除外" data-batch-remove="${f.key}">×</button></div></div>`).join('');
  modal(`<h2>音声をまとめて取り込む</h2><p>ドラッグ＆ドロップ／複数選択で追加した音声を、保存前に確認できます。元の音声ファイルは変更しません。</p>
    <div class="batch-top-grid"><div class="field"><label>取り込み方法</label><select id="batchMode"><option value="assign" ${bulkMode==='assign'?'selected':''}>台詞へ順番に割り当てる</option><option value="library" ${bulkMode==='library'?'selected':''}>音声ライブラリに保存する</option><option value="reference" ${bulkMode==='reference'?'selected':''}>キャストの参照音声にする</option></select></div>
    <div class="field"><label>ファイル順</label><select id="batchSort"><option value="natural" ${bulkSort==='natural'?'selected':''}>ファイル名順（1, 2, 10）</option><option value="chosen" ${bulkSort==='chosen'?'selected':''}>選択された順</option><option value="manual" ${bulkSort==='manual'?'selected':''}>手動で並べ替え中</option></select></div></div>
    ${bulkMode==='assign'?`<div class="batch-top-grid"><div class="field"><label>割り当てるシーン</label><select id="batchScene">${state.scenes.map(sc=>`<option value="${esc(sc.id)}" ${sc.id===bulkScene?'selected':''}>${esc(sc.name)}</option>`).join('')}</select></div><div class="field"><label>開始台詞</label><select id="batchStart"><option value="">シーンの先頭から</option>${lineOptions}</select></div></div><label class="batch-check"><input id="batchOnlyMissing" type="checkbox" ${bulkOnlyMissing?'checked':''}> 音声未割り当ての台詞だけに順番で割り当てる（推奨）</label><div class="batch-notice">台詞IDを含むファイル名はIDで照合します。それ以外は表示中のファイル順に割り当てます。既存テイクは削除せず、新しいテイクとして追加します。</div>`:''}
    ${bulkMode==='library'?`<div class="field"><label>素材の種類</label><select id="batchKind">${[['voice','台詞音声'],['bgm','BGM'],['sfx','効果音'],['ambience','環境音'],['other','その他']].map(([v,label])=>`<option value="${v}" ${v===bulkKind?'selected':''}>${label}</option>`).join('')}</select></div>`:''}
    ${bulkMode==='reference'?`<div class="field"><label>登録するキャスト</label><select id="batchCast"><option value="">キャストを選択</option>${castOptions}</select></div>`:''}
    <div class="batch-preview-header"><b>${bulkFiles.length} ファイル</b><span class="muted small">${bulkMode==='assign'?`割り当て ${assigned}件 · 素材のみ ${unmapped}件`:'取り込み先を確認してください'}</span></div>
    <div class="batch-rows">${rows}</div>
    ${errors.length?`<p class="batch-error">${errors.map(esc).join('、')}</p>`:''}
    <p id="batchStatus" class="muted small"></p>
    <div class="modal-actions">${button('キャンセル','data-close="1"')}${button('取り込む',`id="batchCommitBtn" ${errors.length||(!bulkCast&&bulkMode==='reference')?'disabled':''}`,'primary')}</div>`);
  if($('batchStart'))$('batchStart').value=bulkStart;
  if($('batchSort').value==='manual')$('batchSort').querySelector('[value="manual"]').disabled=true;
  $('batchCommitBtn')?.addEventListener('click',submitAudioBatch);
}
async function submitAudioBatch(){
  if(bulkBusy)return;
  if(!bulkFiles.length||batchMappingErrors().length)return toast('割り当ての重複を修正してください',true);
  if(bulkMode==='reference'&&!bulkCast)return toast('キャストを選択してください',true);
  bulkBusy=true;const btn=$('batchCommitBtn');btn.disabled=true;const msg=$('batchStatus');msg.textContent=`${bulkFiles.length}件の音声をコピーして検証中…`;
  try{
    await saveNow();
    const form=new FormData();for(const f of bulkFiles)form.append('files',f.file,f.file.name);
    form.append('kind',bulkMode==='reference'?'reference':bulkMode==='assign'?'voice':bulkKind);
    form.append('lineIds',JSON.stringify(bulkFiles.map(f=>bulkMode==='assign'?bulkTargets[f.key]||null:null)));
    form.append('castName',bulkMode==='reference'?bulkCast:'');
    const result=await req(`/api/projects/${state.id}/assets/batch`,{method:'POST',body:form});
    state=result.project;clearEditHistory();if(bulkMode==='assign')selectedScene=bulkScene;audioCache.clear();closeModal();renderAll();
    toast(`取り込み完了 ${result.summary.imported}件${result.summary.assigned?' · 台詞へ'+result.summary.assigned+'件割当':''}`);
    bulkFiles=[];
  }catch(e){toast('一括取り込みに失敗: '+e.message,true);if(msg)msg.textContent='取り込み失敗: '+e.message}
  finally{bulkBusy=false;if(btn?.isConnected)btn.disabled=false}
}
function handleBatchModalChange(e){
  const el=e.target;if(!el.closest('#modalBody')||!bulkFiles.length)return;
  if(el.id==='batchMode'){bulkMode=el.value;bulkKind=bulkMode==='reference'?'reference':'voice';rebuildBatchAssignments();showAudioBatchModal()}
  else if(el.id==='batchSort'){bulkSort=el.value;sortBatchFiles();rebuildBatchAssignments();showAudioBatchModal()}
  else if(el.id==='batchScene'){bulkScene=el.value;bulkStart='';rebuildBatchAssignments();showAudioBatchModal()}
  else if(el.id==='batchStart'){bulkStart=el.value;rebuildBatchAssignments();showAudioBatchModal()}
  else if(el.id==='batchOnlyMissing'){bulkOnlyMissing=el.checked;rebuildBatchAssignments();showAudioBatchModal()}
  else if(el.id==='batchKind'){bulkKind=el.value}
  else if(el.id==='batchCast'){bulkCast=el.value;showAudioBatchModal()}
  else if(el.dataset.batchTarget){bulkTargets[el.dataset.batchTarget]=el.value;const errors=batchMappingErrors();const btn=$('batchCommitBtn');btn.disabled=errors.length>0;const header=document.querySelector('.batch-preview-header span');if(header)header.textContent=`割り当て ${bulkFiles.filter(f=>bulkTargets[f.key]).length}件 · 素材のみ ${bulkFiles.filter(f=>!bulkTargets[f.key]).length}件`;}
}
function handleBatchModalClick(e){
  const move=e.target.closest('[data-batch-move]');const remove=e.target.closest('[data-batch-remove]');if(!move&&!remove)return false;
  if(bulkBusy)return true;
  const key=Number((move||remove).dataset.batchMove||(move||remove).dataset.batchRemove),i=bulkFiles.findIndex(f=>f.key===key);
  if(i<0)return true;
  if(remove){bulkFiles.splice(i,1);delete bulkTargets[key]}
  else {const j=i+Number(move.dataset.dir);if(j>=0&&j<bulkFiles.length)[bulkFiles[i],bulkFiles[j]]=[bulkFiles[j],bulkFiles[i]];bulkSort='manual';rebuildBatchAssignments()}
  if(!bulkFiles.length){closeModal();return true}showAudioBatchModal();return true;
}
// HTML5 DnD adds new clips; existing pointer-based clip move/resize stays intact.
const DRAG_ASSET_TYPE='application/x-voicedrama-asset';
let highlightedLane=null;
function laneFromTarget(target){return target?.closest?.('.tl-lane[data-drop-track]')||null}
function highlightLane(lane){if(highlightedLane!==lane){highlightedLane?.classList.remove('timeline-drop-target');highlightedLane=lane;if(lane)lane.classList.add('timeline-drop-target')}}
function laneTime(lane,clientX){return Math.max(0,Math.round((clientX-lane.getBoundingClientRect().left)/pxPerSec*20)/20)}
function onLibraryDragStart(e){
 const card=e.target.closest?.('.asset-draggable[data-drag-asset]');if(!card||!e.dataTransfer)return;
 const entry=asset(card.dataset.dragAsset);if(!entry||entry.kind==='reference'){e.preventDefault();return}
 e.dataTransfer.setData(DRAG_ASSET_TYPE,entry.id);e.dataTransfer.effectAllowed='copy';
}
function onLaneDragOver(e){
 const lane=laneFromTarget(e.target),types=[...e.dataTransfer?.types||[]];
 if(lane&&(types.includes(DRAG_ASSET_TYPE)||types.includes('Files'))){e.preventDefault();e.dataTransfer.dropEffect='copy';highlightLane(lane)}
 else highlightLane(null);
}
async function uploadToLane(files,trackId,atTime){
 const candidates=[...files].filter(f=>AUDIO_EXT.test(f.name));
 if(candidates.length!==files.length||!candidates.length)return toast('WAV / MP3等の音声ファイルだけドロップしてください',true);
 if(candidates.length>20)return toast('トラックへの同時ドロップは20ファイルまでです',true);
 const track=state.tracks.find(t=>t.id===trackId);if(!track||track.kind==='dialogue')return toast('BGM・効果音・環境音トラックへドロップしてください',true);
 try{
  await saveNow();let cursor=atTime;
  for(const file of candidates){
   const fd=new FormData();fd.append('file',file);fd.append('kind',['bgm','ambience','sfx'].includes(track.kind)?track.kind:'sfx');
   const response=await req(`/api/projects/${state.id}/assets`,{method:'POST',body:fd});
   state=response.project;clearEditHistory();
   addClip(response.asset.id,trackId,cursor);cursor+=response.asset.duration;
   await saveNow();
  }
  renderAll();toast(`${candidates.length}件の音声を${track.name}へ配置しました`);
 }catch(e){toast('音声をトラックへ配置できません: '+e.message,true)}
}
function onTimelineDrop(e){
 const lane=laneFromTarget(e.target);if(!lane||!e.dataTransfer)return;
 const aid=e.dataTransfer.getData(DRAG_ASSET_TYPE),files=e.dataTransfer.files;
 if(!aid&&!files?.length)return;
 e.preventDefault();e.stopPropagation();highlightLane(null);$('editorContent').classList.remove('file-drag-active');
 const track=state.tracks.find(t=>t.id===lane.dataset.dropTrack);
 if(!track||track.kind==='dialogue')return toast('BGM・効果音・環境音のトラックを指定してください',true);
 const seconds=laneTime(lane,e.clientX);
 if(aid){const a=asset(aid);if(!a||a.kind==='reference')return toast('この音声はドラッグ配置できません',true);addClip(aid,track.id,seconds)}
 else uploadToLane(files,track.id,seconds);
}
function handleAudioFileDrop(e){
  if(!e.dataTransfer?.files?.length)return;
  e.preventDefault();$('editorContent').classList.remove('file-drag-active');
  const castCard=e.target.closest('.cast-card');const cast=castCard?state.voices[Number(castCard.dataset.voice)]?.name:'';
  const mode=cast?'reference':e.target.closest('[data-audio-drop]')?.dataset.audioDrop||(view==='voices'?'reference':view==='assets'?'library':'assign');
  stageAudioBatch(e.dataTransfer.files,mode,cast||'');
}

async function handleUpload(files){if(!files?.length)return;try{await saveNow();for(const file of files){const fd=new FormData();fd.append('file',file);fd.append('kind',uploadContext.kind);const data=await req(`/api/projects/${state.id}/assets`,{method:'POST',body:fd});state=data.project;clearEditHistory();
 if(uploadContext.lineId)await attachAsset(data.asset.id,uploadContext.lineId);
 if(uploadContext.voiceName){let v=state.voices.find(x=>x.name===uploadContext.voiceName);if(v){v.refs=v.refs||[];v.refs.push(data.asset.id);dirtyState();await saveNow()}}
 else if(!uploadContext.lineId&&uploadContext.kind!=='reference'&&uploadContext.kind!=='voice')addClip(data.asset.id,uploadContext.kind);
 toast(`登録しました: ${file.name}`)}renderAll()}catch(e){toast(e.message,true)}}
function openClearScript(){
 const lines=state.lines.length,scenes=state.scenes.length,clips=state.clips.length;
 modal(`<h2>シナリオをすべて削除</h2><p>現在のプロジェクトの台本を空の状態へ戻します。</p>
  <p><b>削除対象：</b>台詞 ${lines}件 ／ シーン ${scenes}件 ／ タイムライン配置 ${clips}件</p>
  <p>キャラクター設定・声の参照音声・生成済みの音声素材・BGM・効果音ファイルは削除しません。</p>
  <p>変更前のプロジェクト情報は自動的にバックアップします。</p>
  <div class="modal-actions">${button('キャンセル','data-close="1"')}${button('すべて削除する','id="confirmClearScriptBtn"','danger')}</div>`);
}
async function clearEntireScript(){
 const btn=$('confirmClearScriptBtn');if(btn){btn.disabled=true;btn.textContent='削除中…'}
 try{
  await saveNow();
  const result=await req(`/api/projects/${state.id}/script/clear`,json('POST',{expectedRevision:state.revision}));
  stop();state=result.project;clearEditHistory();selectedScene=state.scenes[0]?.id||null;selectedLine=null;selectedClip=null;
  dirty=false;renderAll();$('saveState').textContent='保存済み';
  const info=result.summary;
  modal(`<h2>シナリオを削除しました</h2><p>台詞 ${info.removedLines}件、シーン ${info.removedScenes}件、配置 ${info.removedClips}件をクリアしました。</p>
   <p>音声素材・キャスト設定は残っています。正しいExcelを再度読み込んでください。</p>
   <p class="small muted">バックアップ: workspace / このプロジェクト / backups / ${esc(info.backup)}</p>
   <div class="modal-actions">${button('閉じる','data-close="1"','primary')}</div>`);
 }catch(e){toast('シナリオを削除できません: '+e.message,true);if(btn){btn.disabled=false;btn.textContent='すべて削除する'}}
}
async function downloadSceneSelectedTakes(){
 if(!selectedScene)return toast('シーンが選択されていません',true);
 const count=activeLines().filter(l=>l.selectedTakeId&&l.takes?.some(t=>t.id===l.selectedTakeId)).length;
 if(!count)return toast('このシーンに選択済みの音声テイクがありません',true);
 try{
  await saveNow();
  const link=document.createElement('a');
  link.href=`/api/projects/${state.id}/scenes/${selectedScene}/selected-takes.zip`;
  link.download='selected_scene_voices.zip';
  document.body.appendChild(link);
  link.click();
  link.remove();
 }catch(e){toast('音声ZIPの保存に失敗: '+e.message,true)}
}
function chooseExcelImport(){
 excelImportMode='merge';
 modal(`<h2>Excelシナリオの取り込み方法</h2><p>読み込む前に、更新方法を選択してください。</p>
  <label class="import-mode-choice"><input type="radio" name="excelMode" value="merge" checked><span><b>追加・更新（従来の方法）</b><small>Excelにない台詞も残します。</small></span></label>
  <label class="import-mode-choice"><input type="radio" name="excelMode" value="replace"><span><b>シナリオをすべて置き換える</b><small>Excelにない台詞とシーンを削除します。既存の音声素材・キャスト設定は保持し、変更前の台本をバックアップします。</small></span></label>
  <div class="modal-actions">${button('キャンセル','data-close="1"')}${button('Excelファイルを選択','id="chooseExcelFileBtn"','primary')}</div>`);
}
async function importExcel(file){
 if(!file)return;
 const mode=excelImportMode;
 if(mode==='replace' && !confirm(`「${file.name}」の内容でシナリオを全件置き換えますか？\n\nExcelに含まれない台詞・シーンと、それらに関連するタイムライン配置を取り除きます。\nキャスト設定・音声ファイルは残し、変更前のプロジェクト情報をバックアップします。`))return;
 try{
   await saveNow();
   const form=new FormData();form.append('file',file);form.append('mode',mode);form.append('expectedRevision',String(state.revision));
   const response=await req(`/api/projects/${state.id}/excel/import`,{method:'POST',body:form});
   stop();state=response.project;clearEditHistory();selectedScene=state.scenes[0]?.id||null;selectedLine=null;selectedClip=null;renderAll();
   const info=response.summary;
   const replaced=info.mode==='replace';
   modal(`<h2>Excel取り込み完了</h2><p>${replaced?'全件置き換え':'追加・更新'} / 追加 ${info.added}件 / 更新 ${info.updated}件 / 既存一致 ${info.matched}件</p>
     ${replaced?`<p>削除された台詞 ${info.removed}件・シーン ${info.removedScenes}件・クリップ ${info.removedClips}件</p><p>変更前の保存先: <code>workspace / プロジェクト / backups / ${esc(info.backup)}</code></p>`:''}
     <p>音声ファイル・キャストの参照音声は削除していません。台詞の変更により「要再生成」となる場合があります。</p>
     ${info.warnings?.length?`<div class="empty" style="text-align:left">${info.warnings.map(esc).join('<hr>')}</div>`:''}
     <div class="modal-actions">${button('閉じる','data-close="1"','primary')}</div>`);
 }catch(e){toast('Excel取り込みに失敗: '+e.message,true)}
}
function addVoice(){const name=prompt('キャラクター名を入力','新しいキャラクター');if(!name?.trim())return;if(state.voices.some(v=>v.name===name.trim())){toast('同じ名前のキャストが登録済み',true);return}state.voices.push({name:name.trim(),engine:'irodori',model:'Aratako/Irodori-TTS-v4.1-Small',mode:'clone',description:'',refs:[],refText:'',language:'Japanese'});dirtyState();renderAll()}
function openCastDelete(index){
 const voice=state.voices[index];if(!voice)return;
 const count=state.lines.filter(line=>line.speaker===voice.name).length;
 if(count){
  modal(`<h2>キャストを削除できません</h2><p>「${esc(voice.name)}」は${count}件の台詞で使用されています。先に「シナリオ」で該当する台詞の話者を変更してください。</p><div class="modal-actions">${button('閉じる','data-close="1"','primary')}</div>`);
  return;
 }
 modal(`<h2>キャストを削除</h2><p>「${esc(voice.name)}」のキャスト設定を削除しますか？</p><p>台詞・生成済み音声・参照音声のファイルは削除しません。参照音声はライブラリに残ります。</p><div class="modal-actions">${button('キャンセル','data-close="1"')}${button('削除する','id="confirmCastDeleteBtn"','danger')}</div>`);
 $('confirmCastDeleteBtn').onclick=()=>deleteCast(voice.name);
}
async function deleteCast(name){
 const btn=$('confirmCastDeleteBtn');if(btn)btn.disabled=true;
 try{
  await saveNow();
  const updated=await req(`/api/projects/${state.id}/casts/delete`,json('POST',{name,expectedRevision:state.revision}));
  state=updated;clearEditHistory();closeModal();renderAll();$('saveState').textContent='保存済み';
  toast(`キャスト「${name}」を削除しました（音声素材は保持）`);
 }catch(e){toast('キャストを削除できません: '+e.message,true);if(btn)btn.disabled=false}
}
function applyVoice(index){const v=state.voices[index];if(!v)return;let num=0;for(const l of state.lines.filter(l=>l.speaker===v.name)){l.ttsSource='cast';l.status=l.selectedTakeId?'stale':'missing';num++}dirtyState();renderAll();toast(`${v.name} の ${num}件をキャスト設定に切り替えました（${v.model}）`)}
async function designCharacterVoice(index){const voice=state.voices[index];if(!voice)return;const sampleText=prompt('作りたい声で読み上げるサンプル台詞を入力してください','こんにちは！ 今日はどんな素敵なことが起こるのか、楽しみですね。');if(!sampleText?.trim())return;
 try{await saveNow();const job=await req(`/api/projects/${state.id}/voices/design`,json('POST',{speaker:voice.name,sampleText:sampleText.trim()}));showJob(job.id)}catch(e){toast(e.message,true)}
}
async function generate(ids){
 if(!ids.length)return toast('対象台詞がありません',true);
 try{
  await saveNow();
  const preview=await req(`/api/projects/${state.id}/generate/preview`,json('POST',{lineIds:ids}));
  if(!preview.total)return toast('読み上げる台詞がありません',true);
  modal(`<h2>音声生成の確認</h2><p>${preview.total}件の台詞を生成します。モデル設定を確認してください。</p>
   <div class="field">${preview.groups.map(g=>`<div class="ref-row"><b>${esc(g.model)}</b><span>${g.count} 件</span></div>`).join('')}</div>
   <p class="small muted">キャスト設定を参照している台詞は「キャスト・声」のモデルで生成します。</p>
   <div class="modal-actions">${button('キャンセル','data-close="1"')}${button('生成を開始','id="confirmGenerationBtn"','primary')}</div>`);
  $('confirmGenerationBtn').onclick=async()=>{
   $('confirmGenerationBtn').disabled=true;
   try{const job=await req(`/api/projects/${state.id}/generate`,json('POST',{lineIds:ids}));showJob(job.id)}
   catch(e){toast('生成を開始できません: '+e.message,true);$('confirmGenerationBtn').disabled=false}
  };
 }catch(e){toast('生成前の設定確認に失敗: '+e.message,true)}
}
function showJob(jid){clearInterval(jobTimer);modal(`<h2>音声生成中</h2><p id="jobMessage">TTSモデルを読み込んでいます…</p><div class="progress"><div id="jobProgress" style="width:0%"></div></div><div id="jobStats" class="small muted" style="margin-top:10px">処理待ち</div><div class="modal-actions">${button('停止を要求',`id="cancelJobBtn"`)}${button('閉じる','data-close="1"')}</div>`);$('cancelJobBtn').onclick=async()=>{await req(`/api/jobs/${jid}/cancel`,{method:'POST'});toast('停止要求を送信しました')};
 const check=async()=>{try{const j=await req('/api/jobs/'+jid);if($('jobMessage')){$('jobMessage').textContent=j.message||j.status;$('jobStats').textContent=`${j.completed||0} / ${j.total||0} 件　${j.errors?.length||0} エラー`;$('jobProgress').style.width=((j.total?j.completed/j.total:0)*100)+'%'}if(j.status!=='running'){clearInterval(jobTimer);await saveNow();state=await req(`/api/projects/${state.id}`);clearEditHistory();renderAll();if(j.referenceAssetId && $('jobStats')){const actions=$('jobStats').closest('.modal')?.querySelector('.modal-actions');if(actions&&!$('previewDesignedReferenceBtn')){const b=document.createElement('button');b.id='previewDesignedReferenceBtn';b.className='btn primary';b.textContent='▶ 生成した参照音声を試聴';b.onclick=()=>playPreview(j.referenceAssetId);actions.prepend(b)}}if(j.errors?.length)toast(`生成完了。${j.errors.length}件でエラー`,true);else if(j.status==='error')toast('生成エラー: '+j.message,true);else toast('生成処理が終了しました');if($('jobMessage'))$('jobMessage').textContent=j.status==='error'?'失敗: '+j.message:`終了: ${j.completed}件処理`;}}catch(e){clearInterval(jobTimer);toast(e.message,true)}};jobTimer=setInterval(check,1500);check()}
async function renderAudio(){try{await saveNow();modal(`<h2>完成音声を書き出す</h2><p>すべてのトラックを同時にミックスし、フェードやループを反映します。</p><div class="field"><label>出力範囲</label><select id="exportScope"><option value="scene">現在のシーン (${esc(sceneName(selectedScene))})</option><option value="all">全シーン（順番に連結）</option></select></div><div class="field"><label>出力形式</label><select id="exportFormat"><option value="wav">WAV / PCM16</option><option value="mp3">MP3 / 192kbps（FFmpeg必要）</option></select></div><div class="modal-actions">${button('キャンセル','data-close="1"')}${button('書き出す','id="renderConfirmBtn"','primary')}</div>`);
 $('renderConfirmBtn').onclick=async()=>{const format=$('exportFormat').value,sceneId=$('exportScope').value==='scene'?selectedScene:null;$('renderConfirmBtn').disabled=true;$('renderConfirmBtn').textContent='処理中…';try{const result=await req(`/api/projects/${state.id}/render`,json('POST',{sceneId,format}));closeModal();const a=document.createElement('a');a.href=result.url;a.download=result.file;document.body.append(a);a.click();a.remove();toast(`書き出しました · ${fmt(result.info.duration)} · ${result.info.clips}クリップ`)}catch(e){toast(e.message,true);$('renderConfirmBtn').disabled=false;$('renderConfirmBtn').textContent='再試行'}};
 }catch(e){toast(e.message,true)}}
async function trashSelectedAssets(){
 const ids=[...trashSelection].filter(id=>state.assets.some(a=>a.id===id&&a.kind!=='reference'));
 if(!ids.length)return toast('削除する音声を選択してください',true);
 if(!confirm(`${ids.length}件の未使用の音声をゴミ箱へ移動しますか？\n使用中の音声が含まれる場合は削除せず、操作を中止します。`))return;
 try{
   await saveNow();
   const result=await req(`/api/projects/${state.id}/assets/bulk/trash`,json('POST',{assetIds:ids}));
   trashSelection.clear();state=result.project;clearEditHistory();audioCache.clear();renderAll();
   const failed=result.errors?.length||0;
   toast(`ゴミ箱へ${result.trashed.length}件移動${failed?'（失敗 '+failed+'件）':''}`,!!failed);
 }catch(e){toast('一括削除できません: '+e.message,true)}
}
async function trashAsset(aid){
 const assetInfo=state.assets.find(a=>a.id===aid);if(!assetInfo)return;
 try{
   await saveNow();
   const usage=await req(`/api/projects/${state.id}/assets/${aid}/usage`);
   const used=usage.casts.length||usage.lines.length||usage.clips.length;
   const details=[usage.casts.length?`キャスト参照 ${usage.casts.join('、')}`:'',usage.lines.length?`台詞 ${usage.lines.length}件`:'',usage.clips.length?`タイムライン ${usage.clips.length}件`:''].filter(Boolean).join(' / ');
   const message=used?`「${assetInfo.name}」は現在使用中です（${details}）。\n\n削除すると、これらの紐付けが解除されます。\nファイル自体はゴミ箱に保存されます。続行しますか？`:`「${assetInfo.name}」をゴミ箱へ移動しますか？\n元ファイルはゴミ箱から復元できます。`;
   if(!confirm(message))return;
   const response=await req(`/api/projects/${state.id}/assets/${aid}?detach=${used?'true':'false'}`,{method:'DELETE'});
   state=response.project;clearEditHistory();trashSelection.delete(aid);audioCache.delete(aid);selectedClip=null;renderAll();toast('ゴミ箱へ移動しました');
 }catch(e){toast('削除できません: '+e.message,true)}
}
async function openTrash(){
 try{
   await saveNow();const entries=await req(`/api/projects/${state.id}/trash/assets`);
   modal(`<h2>音声のゴミ箱</h2><p>ゴミ箱に移動した音声は、元のライブラリへ復元できます。解除された台詞・キャストへの紐付けは自動復元されません。</p>${entries.length?entries.slice().reverse().map(e=>`<div class="ref-row"><span>${esc(e.asset.name)} <small class="muted">${esc(e.trashedAt||'')}</small></span>${button('復元',`data-restore="${esc(e.asset.id)}"`,'primary')}</div>`).join(''):'<p class="muted">ゴミ箱は空です。</p>'}<div class="modal-actions">${button('閉じる','data-close="1"')}</div>`);
 }catch(e){toast('ゴミ箱の取得に失敗: '+e.message,true)}
}
async function restoreAsset(aid){
 try{const result=await req(`/api/projects/${state.id}/trash/assets/${aid}/restore`,{method:'POST'});state=result.project;clearEditHistory();renderAll();await openTrash();toast('音声をライブラリへ復元しました')}catch(e){toast('復元できません: '+e.message,true)}
}
async function openCastCopy(presetName=''){
  try{
    await saveNow();
    const otherProjects=(await req('/api/projects')).filter(p=>p.id!==state.id);
    if(!state.voices.length){toast('コピーするキャラクターを先に登録してください',true);return}
    castCopySelection=new Set(presetName?[presetName]:[]);
    modal(`<h2>キャスト設定を別のプロジェクトへコピー</h2>
      <p>声の説明・TTSモデル・言語・参照音声をコピーします。台詞や生成済みテイクはコピーしません。参照音声はコピー先に独立保存されます。</p>
      <div class="field"><label>コピー先プロジェクト</label>
       <select id="castCopyTarget">${otherProjects.map(p=>`<option value="${esc(p.id)}">${esc(p.title)}</option>`).join('')}<option value="__new__">＋ 新しいプロジェクトを作成</option></select></div>
      <div class="field" id="castCopyNewField" style="display:${otherProjects.length?'none':'block'}"><label>新しいプロジェクトのタイトル</label><input id="castCopyNewTitle" placeholder="作品タイトル"></div>
      <div class="field"><label>同名キャラクターがコピー先にいる場合</label><select id="castCopyConflict"><option value="skip">スキップ（上書きしない）</option><option value="rename">名前に「コピー」を付けて追加</option></select></div>
      <div class="field"><label>コピーするキャスト <span id="castCopyCount" class="muted small"></span></label>
        <div class="buttonline">${button('すべて選択','id="castCopyAllBtn"')}${button('選択解除','id="castCopyNoneBtn"')}</div>
        <div class="cast-copy-list">${state.voices.map((v,i)=>`<label class="cast-copy-item"><input type="checkbox" data-copy-cast="${i}" ${castCopySelection.has(v.name)?'checked':''}><span><b>${esc(v.name)}</b><small>${esc(v.engine||'irodori')} · 参照音声 ${(v.refs||[]).length}件</small></span></label>`).join('')}</div>
      </div><div class="modal-actions">${button('キャンセル','data-close="1"')}${button('選択キャストをコピー','id="castCopyConfirmBtn"','primary')}</div>`);
    updateCastCopyModal();
  }catch(e){toast(e.message,true)}
}
function updateCastCopyModal(){
  const select=$('castCopyTarget');if(!select)return;
  $('castCopyNewField').style.display=select.value==='__new__'?'block':'none';
  const label=$('castCopyCount');if(label)label.textContent=`${castCopySelection.size} / ${state.voices.length} 件選択`;
  const btn=$('castCopyConfirmBtn');if(btn)btn.disabled=!castCopySelection.size;
}
async function commitCastCopy(){
  if(!castCopySelection.size)return toast('コピーするキャラクターを選択してください',true);
  const btn=$('castCopyConfirmBtn');if(btn)btn.disabled=true;
  let created=null;
  try{
    await saveNow();
    let pid=$('castCopyTarget').value;
    if(pid==='__new__'){
      const title=$('castCopyNewTitle').value.trim();
      if(!title){throw new Error('新しいプロジェクト名を入力してください')}
      created=await req('/api/projects',json('POST',{title}));pid=created.id;
    }
    const target=await req(`/api/projects/${pid}`);
    const result=await req(`/api/projects/${state.id}/casts/copy`,json('POST',{
      targetProjectId:pid,castNames:[...castCopySelection],conflict:$('castCopyConflict').value,
      expectedSourceRevision:state.revision,expectedTargetRevision:target.revision
    }));
    closeModal();
    const summary=result.summary;
    toast(`${summary.copied}キャスト・参照音声${summary.referencesCopied}件をコピーしました${summary.skipped.length?`（同名${summary.skipped.length}件スキップ）`:''}`);
    if(created || (summary.copied>0 && confirm('コピー先のプロジェクトを開きますか？'))){
      await switchProject(pid);setView('voices');
    }
  }catch(e){toast('コピーできません: '+e.message,true);if(btn)btn.disabled=false;
    if(created)toast('コピー用プロジェクトが作成されましたが、キャストのコピーは完了していません',true);
  }
}
async function openProjects(){try{await saveNow();const list=await req('/api/projects');modal(`<h2>プロジェクトを選択</h2><p>プロジェクトごとに台本・音声素材・編集状態を分けて保存します。</p>${list.map(x=>`<button class="project-choice" data-open="${esc(x.id)}">${esc(x.title)}<small>${esc(x.updatedAt||'')}</small></button>`).join('')}<div class="field" style="margin-top:20px"><label>新規プロジェクト</label><input id="newProjectTitle" placeholder="作品タイトルを入力"></div><div class="modal-actions">${button('キャンセル','data-close="1"')}${button('新規作成','id="createProjectBtn"','primary')}</div>`)}catch(e){toast(e.message,true)}}
async function openSettings(){
 try{
  const common=await req('/api/settings/global');
  const project=state.settings||{};
  modal(`<h2>TTS・実行環境の設定</h2>
    <p>PC共通の設定です。すべてのプロジェクトで同じIrodori・Qwen環境を使用します。</p>
    <div class="field"><label>Irodori-TTSのフォルダ（infer.pyが入っている場所）</label>
      <input id="settingIrodori" value="${esc(common.irodori_dir||'')}"></div>
    <div class="field"><label>Irodori用Python実行ファイル（空欄ならIrodoriフォルダ内の.venv）</label>
      <input id="settingIrodoriPython" value="${esc(common.irodori_python||'')}"></div>
    <div class="field"><label>Qwen3-TTS用Python実行ファイル（仮想環境）</label>
      <input id="settingQwen" value="${esc(common.qwen_python||'')}"></div>
    <div class="field"><label>このプロジェクトだけ：台詞の標準の間（秒）</label>
      <input id="settingGap" type="number" min="0" step="0.05" value="${esc(project.defaultGap??0.65)}"></div>
    <p class="small muted">共通設定はworkspace/global_settings.jsonに保存します。既存作品の設定は初回だけ自動で引き継ぎます。</p>
    <div class="modal-actions">${button('キャンセル','data-close="1"')}${button('設定を保存','id="saveSettingsBtn"','primary')}</div>`);
  $('saveSettingsBtn').onclick=async()=>{
    const btn=$('saveSettingsBtn');btn.disabled=true;
    const settings={irodori_dir:$('settingIrodori').value.trim(),
      irodori_python:$('settingIrodoriPython').value.trim(),
      qwen_python:$('settingQwen').value.trim(),expectedRevision:common.revision};
    const gap=Number($('settingGap').value);
    if(!Number.isFinite(gap)||gap<0){toast('台詞の間は0以上の秒数で指定してください',true);btn.disabled=false;return}
    try{
      await saveNow();
      await req('/api/settings/global',json('PUT',settings));
      if((state.settings?.defaultGap??0.65)!==gap){
        state.settings={...state.settings,defaultGap:gap};dirtyState();await saveNow();
      }
      closeModal();toast('共通TTS設定を保存しました（全プロジェクトに適用）');
    }catch(e){toast('設定の保存に失敗: '+e.message,true);btn.disabled=false}
  };
 }catch(e){toast('共通TTS設定を開けません: '+e.message,true)}
}
async function playPreview(aid){try{stop();await ensureContext();const b=await decodeAsset(aid),source=ctx.createBufferSource();source.buffer=b;source.connect(ctx.destination);source.start();nodes.push(source)}catch(e){toast('試聴できません: '+e.message,true)}}
async function ensureContext(){if(!ctx)ctx=new (window.AudioContext||window.webkitAudioContext)();await ctx.resume()}
async function decodeAsset(id){if(audioCache.has(id))return audioCache.get(id);const promise=(async()=>{const r=await fetch(`/api/projects/${state.id}/assets/${id}`);if(!r.ok)throw new Error('音声を読み込めません');return ctx.decodeAudioData(await r.arrayBuffer())})();audioCache.set(id,promise);try{return await promise}catch(e){audioCache.delete(id);throw e}}
async function play(){if(playing){pause();return}const clips=activeClips().filter(c=>c.start+c.duration>currentTime);if(!clips.length){toast('再生するクリップがありません',true);return}try{await ensureContext();const buffers=await Promise.all(clips.map(c=>decodeAsset(c.assetId)));nodes=[];playStart=ctx.currentTime-currentTime;
 clips.forEach((c,i)=>{const b=buffers[i];if(!b)return;const begin=Number(c.start||0),len=Math.max(0,Number(c.duration||0));const offset=Math.max(0,currentTime-begin);if(offset>=len)return;const lead=Math.max(0,begin-currentTime);const when=ctx.currentTime+lead;const end=Math.max(0.01,b.duration-Number(c.trimEnd||0));const start=Math.max(0,Math.min(end-.001,Number(c.trimStart||0)));const playable=Math.max(.001,end-start);let source=ctx.createBufferSource();source.buffer=b;source.loop=!!c.loop;source.loopStart=start;source.loopEnd=end;let gain=ctx.createGain();const volume=Math.max(0,Math.min(8,Number(c.gain??1)));source.connect(gain);gain.connect(ctx.destination);
  const offsetInside=start+(c.loop?(offset%playable):offset);if(!c.loop && offsetInside>=end)return;
  const playLength=Math.min(len-offset,c.loop?len:end-offsetInside);if(playLength<=0)return;
  const fi=Number(c.fadeIn||0),fo=Number(c.fadeOut||0);gain.gain.setValueAtTime(volume,when);if(fi>0){let k=Math.min(1,offset/fi);gain.gain.setValueAtTime(volume*k,when);if(offset<fi)gain.gain.linearRampToValueAtTime(volume,when+Math.min(fi-offset,playLength))}
  if(fo>0){const fadeBegin=Math.max(0,len-fo);if(offset>=fadeBegin)gain.gain.setValueAtTime(Math.min(volume,volume*Math.max(0,(len-offset)/fo)),when);const whenFade=when+Math.max(0,fadeBegin-offset);if(whenFade<when+playLength){gain.gain.setValueAtTime(volume,whenFade);gain.gain.linearRampToValueAtTime(0,when+playLength)}}
  source.start(when,offsetInside);source.stop(when+playLength);nodes.push(source)});
  playing=true;$('playBtn').textContent='❚❚';animationId=requestAnimationFrame(tick);
 }catch(e){stop();toast('再生エラー: '+e.message,true)}}
function tick(){if(!playing)return;currentTime=Math.max(0,ctx.currentTime-playStart);updatePlayhead();if(currentTime>=Math.max(0,...activeClips().map(c=>c.start+c.duration))+0.15){stopNodes();currentTime=0;updatePlayhead();return}animationId=requestAnimationFrame(tick)}
function pause(){if(!playing)return;currentTime=Math.max(0,ctx.currentTime-playStart);stopNodes();updatePlayhead()}
function stopNodes(){nodes.forEach(n=>{try{n.stop()}catch{}});nodes=[];playing=false;cancelAnimationFrame(animationId);$('playBtn').textContent='▶'}
function stop(){stopNodes();currentTime=0;updatePlayhead()}
let drag=null;
function dragStart(e){
 if(e.button!==0)return;
 const elem=e.target.closest('.tl-clip');
 if(!elem){if(e.target.closest('.tl-lane')){selectedClip=null;selectedLine=null;selectedClips.clear();clipSelectionAnchor=null;paintClipSelection();renderInspector()}return}
 const c=clipById(elem.dataset.clip);if(!c)return;
 const resize=!!e.target.closest('[data-resize]');e.preventDefault();
 if(!pointerSelectClip(c,e,resize))return;
 const moving=resize?[c]:activeClips().filter(x=>selectedClips.has(x.id));
 const lineIds=new Set(moving.map(x=>x.lineId).filter(Boolean));
 // Followers join the group exactly once, even when explicitly selected.
 const followers=resize?[]:activeClips().filter(x=>!selectedClips.has(x.id)&&lineIds.has(x.anchorLineId));
 const originals=new Map([...moving,...followers].map(x=>[x.id,{start:Number(x.start)||0,pinned:x.pinned,anchorOffset:x.anchorOffset}]));
 drag={id:c.id,pointerId:e.pointerId,element:elem,x:e.clientX,duration:Number(c.duration)||0,resize,originals,movingIds:new Set(moving.map(x=>x.id)),used:false};
 elem.setPointerCapture?.(e.pointerId);
}
function dragMove(e){
 if(!drag||e.pointerId!==drag.pointerId)return;
 const c=clipById(drag.id);if(!c)return;
 const raw=(e.clientX-drag.x)/pxPerSec;
 if(!drag.used&&Math.abs(e.clientX-drag.x)<3)return;
 drag.used=true;
 if(drag.resize)c.duration=Math.max(.08,Math.round((drag.duration+raw)*20)/20);
 else{
  const minStart=Math.min(...[...drag.originals.values()].map(x=>x.start));
  const dt=Math.max(-minStart,Math.round(raw*20)/20);
  for(const [id,original] of drag.originals){const item=clipById(id);if(!item)continue;item.start=original.start+dt;if(drag.movingIds.has(id))item.pinned=true}
  // Independently moved anchored audio must keep its new offset on reflow.
  for(const id of drag.movingIds){const item=clipById(id);if(!item?.anchorLineId)continue;const anchor=activeClips().find(x=>x.lineId===item.anchorLineId&&x.trackId==='dialogue');if(anchor)item.anchorOffset=item.start-anchor.start}
 }
 const elements=new Map([...document.querySelectorAll('.tl-clip')].map(el=>[el.dataset.clip,el]));
 const clips=new Map(activeClips().map(item=>[item.id,item]));
 for(const id of drag.resize?[drag.id]:drag.originals.keys()){
  const item=clips.get(id),el=elements.get(id);
  if(item&&el){el.style.left=item.start*pxPerSec+'px';el.style.width=Math.max(14,item.duration*pxPerSec)+'px'}
 }
}
function dragEnd(e){
 if(!drag||(e&&e.pointerId!==drag.pointerId))return;
 const current=drag;drag=null;suppressClipClick=true;setTimeout(()=>suppressClipClick=false,0);
 if(current.element.hasPointerCapture?.(current.pointerId))current.element.releasePointerCapture(current.pointerId);
 if(e?.type==='pointercancel'){
  for(const [id,original] of current.originals){const item=clipById(id);if(item)Object.assign(item,original)}
  if(current.resize){const c=clipById(current.id);if(c)c.duration=current.duration}
  renderTimeline();renderInspector();return;
 }
 if(current.used){dirtyState();renderInspector();renderTimeline()}
}
function handleGlobalClick(e){const target=e.target;
 if(handleBatchModalClick(e))return;
 if(target.closest('[data-restore]')){restoreAsset(target.closest('[data-restore]').dataset.restore);return}
 if(target.closest('[data-trash]')){trashAsset(target.closest('[data-trash]').dataset.trash);return}
 if(target.id==='openTrashBtn'){openTrash();return}
 if(target.id==='trashSelectedBtn'){trashSelectedAssets();return}
 if(target.id==='createProjectBtn'){
   const title=$('newProjectTitle')?.value?.trim()||'無題の作品';target.disabled=true;
   createProject(title).then(()=>closeModal()).catch(err=>{toast('プロジェクトを作成できません: '+err.message,true);target.disabled=false});
   return;
 }
 if(target.closest('[data-close]'))return closeModal();if(target.id==='modalBackdrop')return closeModal();const opening=target.closest('[data-open]');if(opening){closeModal();switchProject(opening.dataset.open).catch(er=>toast(er.message,true));return}
 const deleteScene=target.closest('[data-delete-scene]');if(deleteScene)return openSceneDelete(deleteScene.dataset.deleteScene);
 if(target.id==='confirmDeleteSceneBtn')return confirmSceneDelete(target.dataset.confirmScene);
 const scene=target.closest('[data-scene]');if(scene)return selectScene(scene.dataset.scene);
 const article=target.closest('.script-line');if(article){const lid=article.dataset.id;const del=target.closest('[data-delete]');if(del)return removeLine(lid);const move=target.closest('[data-move]');if(move)return moveLine(lid,Number(move.dataset.move));const action=target.closest('[data-action]');if(action?.dataset.action==='addBelow')return addLine(lid);if(lid!==selectedLine)selectLine(lid)}
 const vc=target.closest('[data-voice-upload]');if(vc){const v=state.voices[Number(vc.dataset.voiceUpload)];if(v)uploadAudio('reference',null,v.name);return}
 const vd=target.closest('[data-voice-design]');if(vd)return designCharacterVoice(Number(vd.dataset.voiceDesign));
 const va=target.closest('[data-voice-apply]');if(va)return applyVoice(Number(va.dataset.voiceApply));
 const castDelete=target.closest('[data-cast-delete]');if(castDelete)return openCastDelete(Number(castDelete.dataset.castDelete));
 const preview=target.closest('[data-preview]');if(preview)return playPreview(preview.dataset.preview);
 const attach=target.closest('[data-attach]');if(attach)return attachAsset(attach.dataset.attach,selectedLine).catch(er=>toast(er.message,true));
 const add=target.closest('[data-addclip]');if(add){const [aid,track]=add.dataset.addclip.split(':');return addClip(aid,track)}
 const trackSelection=target.closest('[data-select-track-below]');if(trackSelection)return selectTrackAndBelow(trackSelection.dataset.selectTrackBelow);
 const cl=target.closest('.tl-clip');if(cl){if(!suppressClipClick&&e.detail===0)selectClip(cl.dataset.clip);return}
 if(target.id==='downloadSceneTakesZipBtn')return downloadSceneSelectedTakes();
 if(target.id==='importExcelBtn')chooseExcelImport();
 if(target.id==='openClearScriptBtn'){openClearScript();return}
 if(target.id==='confirmClearScriptBtn'){clearEntireScript();return}
  if(target.id==='chooseExcelFileBtn'){excelImportMode=document.querySelector('input[name="excelMode"]:checked')?.value||'merge';closeModal();$('excelPicker').click();return}
 if(target.id==='batchAssignBtn')startBatchPicker('assign');
 if(target.id==='batchReferencesBtn')startBatchPicker('reference');
 if(target.id==='openCastCopyBtn')return openCastCopy();
 const castToCopy=target.closest('[data-cast-copy]');if(castToCopy){const v=state.voices[Number(castToCopy.dataset.castCopy)];if(v)return openCastCopy(v.name)}
 if(target.id==='castCopyAllBtn'){castCopySelection=new Set(state.voices.map(v=>v.name));document.querySelectorAll('[data-copy-cast]').forEach(x=>x.checked=true);updateCastCopyModal();return}
 if(target.id==='castCopyNoneBtn'){castCopySelection.clear();document.querySelectorAll('[data-copy-cast]').forEach(x=>x.checked=false);updateCastCopyModal();return}
 if(target.id==='castCopyConfirmBtn')return commitCastCopy();
 if(target.id==='batchLibraryBtn')startBatchPicker('library');
 const drop=target.closest('[data-audio-drop]');if(drop)startBatchPicker(drop.dataset.audioDrop);
 if(target.id==='addLineTop'||target.id==='addLineBottom')addLine();
 if(target.id==='addVoiceBtn')addVoice();
 if(target.id==='generateMissingBtn')generate(activeLines().filter(x=>x.status!=='ready'&&x.text.trim()).map(x=>x.id));
 if(target.id==='uploadAudioBtn')uploadAudio('sfx');
 if(target.id==='insUploadBtn')uploadAudio('sfx');
 if(target.id==='attachPickerBtn')uploadAudio('voice',selectedLine);
 if(target.id==='generateLineBtn')generate([selectedLine]);
 if(target.id==='suggestCaptionBtn')suggestCaption();
 if(target.id==='previewTakeBtn'){const l=lineById(selectedLine);const t=l.takes.find(t=>t.id===l.selectedTakeId);if(t)playPreview(t.assetId)}
 if(target.id==='deleteSelectedTakeBtn')return removeSelectedTake();
 if(target.id==='downloadTakeBtn'){const l=lineById(selectedLine);const t=l?.takes.find(t=>t.id===l.selectedTakeId);if(t)location.href=`/api/projects/${state.id}/assets/${t.assetId}/download`;return}
 if(target.id==='inspectLineBtn'){selectedClip=null;renderInspector()}
 if(target.id==='addTrackBtn'){const name=prompt('トラック名','追加トラック');if(!name)return;const id=newid('track');state.tracks.push({id,name,kind:'sfx'});dirtyState();renderTimeline();renderInspector()}
 if(target.id==='playClipBtn'){currentTime=clipById(selectedClip)?.start||0;play()}
 if(target.id==='deleteClipBtn'){const c=clipById(selectedClip);if(!c)return;state.clips=state.clips.filter(x=>x.id!==c.id);selectedClip=null;dirtyState();renderTimeline();renderInspector()}
}
function handleChange(e){if(historyRestoring)return;handleBatchModalChange(e);const target=e.target;const id=target.id;
 if(id==='castCopyTarget'){updateCastCopyModal();return}
 if(target.dataset.copyCast!==undefined){const v=state.voices[Number(target.dataset.copyCast)];if(v){if(target.checked)castCopySelection.add(v.name);else castCopySelection.delete(v.name);updateCastCopyModal()}return}
 const field=target.dataset.lineField;if(field){const l=lineById(target.closest('.script-line').dataset.id);if(!l)return;l[field]=field==='after'?Math.max(0,Number(target.value)||0):target.value;if(field!=='outputFilename')l.status=l.selectedTakeId?'stale':'missing';if(field==='after')reflow();dirtyState();if(field==='speaker'||field==='after'){renderSidebar();renderTimeline();renderInspector()}return}
 if(target.dataset.trashSelect){if(target.checked)trashSelection.add(target.dataset.trashSelect);else trashSelection.delete(target.dataset.trashSelect);const btn=$('trashSelectedBtn');if(btn){btn.disabled=!trashSelection.size;btn.textContent=`🗑 選択削除 (${trashSelection.size})`}return}
 const vField=target.dataset.vfield;const card=target.closest('[data-voice]');if(vField&&card){const v=state.voices[Number(card.dataset.voice)];if(!v)return;const old=v.name;v[vField]=target.value;if(vField==='name'){state.lines.filter(l=>l.speaker===old).forEach(l=>l.speaker=v.name)}if(vField==='engine')v.model=(models[v.engine]||models.irodori)[0][0];if(vField!=='name')state.lines.filter(l=>l.speaker===v.name&&getLineTtsSource(l)==='cast').forEach(l=>l.status=l.selectedTakeId?'stale':'missing');dirtyState();if(vField==='engine')renderEditor();renderInspector();return}
 if(target.dataset.ref&&card){const v=state.voices[Number(card.dataset.voice)];v.refs=v.refs||[];if(target.checked&&!v.refs.includes(target.dataset.ref))v.refs.push(target.dataset.ref);if(!target.checked)v.refs=v.refs.filter(x=>x!==target.dataset.ref);state.lines.filter(l=>l.speaker===v.name&&getLineTtsSource(l)==='cast').forEach(l=>l.status=l.selectedTakeId?'stale':'missing');dirtyState();return}
 const c=clipById(selectedClip),l=lineById(selectedLine);
 const cv={clipStart:['start',Number],clipDuration:['duration',Number],clipGain:['gain',v=>Number(v)/100],clipFadeIn:['fadeIn',Number],clipFadeOut:['fadeOut',Number],clipTrack:['trackId',String],clipTrimStart:['trimStart',Number],clipTrimEnd:['trimEnd',Number],clipLoop:['loop',null],clipPinned:['pinned',null]};
 if(c&&id==='clipAnchor'){c.anchorLineId=target.value||null;const ref=state.clips.find(x=>x.lineId===target.value&&x.trackId==='dialogue');c.anchorOffset=ref?c.start-ref.start:0;dirtyState();renderTimeline();renderInspector();return}
 if(c&&cv[id]){let [prop,fn]=cv[id];c[prop]=fn?fn(target.value):target.checked;if(['start','duration','fadeIn','fadeOut','trimStart','trimEnd','gain'].includes(prop))c[prop]=Math.max(0,c[prop]);if(prop==='trackId'&&c.trackId!=='dialogue')c.pinned=true;dirtyState();renderTimeline();renderInspector();return}
 if(!l)return;
 if(id==='lineTtsSource'){
   l.ttsSource=target.value==='line'?'line':'cast';
   if(l.ttsSource==='line'){
     const v=state.voices.find(v=>v.name===l.speaker);
     if(v){l.engine=v.engine||'irodori';l.mode=v.mode||'clone';l.model=v.model||'Aratako/Irodori-TTS-v4.1-Small';l.language=v.language||'Japanese'}
   }
   l.status=l.selectedTakeId?'stale':'missing';dirtyState();renderInspector();return;
 }
 if(id==='lineEngine'){l.engine=target.value;l.model=(models[l.engine]||[])[0]?.[0]||'';l.status=l.selectedTakeId?'stale':'missing';selectedClip=null;dirtyState();renderInspector()}
 else if(id==='lineMode'){l.mode=target.value;if(l.engine==='qwen')l.model=l.mode==='design'?models.qwen[2][0]:models.qwen[0][0];l.status=l.selectedTakeId?'stale':'missing';dirtyState();renderInspector()}
 else if(id==='lineModel'){if(target.value==='custom'){$('modelCustom').style.display='block';$('modelCustom').focus()}else{l.model=target.value;l.status=l.selectedTakeId?'stale':'missing';dirtyState();renderInspector()}}
 else if(id==='modelCustom'){l.model=target.value;l.status=l.selectedTakeId?'stale':'missing';dirtyState()}
 else if(id==='lineTake'){l.selectedTakeId=target.value||null;let t=l.takes.find(t=>t.id===l.selectedTakeId);if(t){let a=asset(t.assetId),c=state.clips.find(x=>x.lineId===l.id&&x.trackId==='dialogue');if(a&&c){c.assetId=a.id;c.duration=a.duration;reflow()}}dirtyState();renderAll()}
}
async function suggestCaption(){if(!selectedLine)return;const model=prompt('Ollamaのモデル名','gemma3:4b');if(!model)return;try{await saveNow();toast('Ollamaで演技指示を解析しています');const r=await req(`/api/projects/${state.id}/caption/suggest`,json('POST',{lineId:selectedLine,model}));const l=lineById(selectedLine);l.caption=r.caption;l.status=l.selectedTakeId?'stale':'missing';dirtyState();renderEditor();renderInspector();toast('演技指示の候補を入力しました（必要なら修正してください）')}catch(e){toast(e.message,true)}}
function setView(v){view=v;renderSidebar();renderEditor()}
// Panel sizing is a browser-local UI preference, independent of project data.
// Users can resize the side navigation, script editor, timeline and inspector.
const PANEL_SIZE_KEY='vds.layout.v1';
const PANEL_LIMITS={sidebar:160,editor:300,workbench:320,timeline:170,inspector:120,gutter:8};
let preferredPanelSizes=null;
function clampPanelSize(value,min,max){return Math.min(Math.max(Number(value)||min,min),Math.max(min,max))}
function horizontalPanelsActive(){return window.matchMedia('(min-width: 951px)').matches}
function measuredPanelSizes(){
 return {sidebar:document.querySelector('.sidebar').getBoundingClientRect().width,
  editor:document.querySelector('.editor-panel').getBoundingClientRect().width,
  inspector:document.querySelector('.workbench-foot').getBoundingClientRect().height};
}
function applyPanelSizes(sizes){
 const layout=document.querySelector('.main-layout');
 if(horizontalPanelsActive()){
  const maxWidth=layout.getBoundingClientRect().width;
  const remaining=maxWidth-2*PANEL_LIMITS.gutter;
  const sidebar=clampPanelSize(sizes.sidebar,PANEL_LIMITS.sidebar,remaining-PANEL_LIMITS.editor-PANEL_LIMITS.workbench);
  const editor=clampPanelSize(sizes.editor,PANEL_LIMITS.editor,remaining-sidebar-PANEL_LIMITS.workbench);
  layout.style.setProperty('--vds-sidebar-width',Math.round(sidebar)+'px');
  layout.style.setProperty('--vds-editor-width',Math.round(editor)+'px');
 }
 const workbench=document.querySelector('.workbench');
 const header=workbench.querySelector('.workbench-head').getBoundingClientRect().height;
 const available=workbench.getBoundingClientRect().height-header-PANEL_LIMITS.gutter-PANEL_LIMITS.timeline;
 workbench.style.setProperty('--vds-inspector-height',Math.round(clampPanelSize(sizes.inspector,PANEL_LIMITS.inspector,available))+'px');
}
function savePanelSizes(){
 preferredPanelSizes=measuredPanelSizes();
 try{window.localStorage.setItem(PANEL_SIZE_KEY,JSON.stringify(preferredPanelSizes))}catch(_){/* private mode */}
}
function resetPanelSizes(){
 preferredPanelSizes=null;
 const layout=document.querySelector('.main-layout');
 layout.style.removeProperty('--vds-sidebar-width');
 layout.style.removeProperty('--vds-editor-width');
 document.querySelector('.workbench').style.removeProperty('--vds-inspector-height');
 try{window.localStorage.removeItem(PANEL_SIZE_KEY)}catch(_){/* private mode */}
}
function installPanelResizers(){
 try{
  const saved=JSON.parse(window.localStorage.getItem(PANEL_SIZE_KEY)||'null');
  if(saved && ['sidebar','editor','inspector'].every(k=>Number.isFinite(saved[k])&&saved[k]>0))preferredPanelSizes=saved;
 }catch(_){/* localStorage disabled or old data */}
 if(preferredPanelSizes)applyPanelSizes(preferredPanelSizes);
 const layout=document.querySelector('.main-layout');
 const workbench=document.querySelector('.workbench');
 const handlers=[['resizeSidebar','sidebar'],['resizeEditor','editor'],['resizeInspector','inspector']];
 for(const [id,part] of handlers){
  const grip=$(id);
  let pointerId=null;
  function changeAt(x,y){
   if(part!=='inspector'&&!horizontalPanelsActive())return;
   const sizes={...measuredPanelSizes()};
   if(part==='sidebar')sizes.sidebar=x-layout.getBoundingClientRect().left;
   if(part==='editor')sizes.editor=x-document.querySelector('.editor-panel').getBoundingClientRect().left;
   if(part==='inspector')sizes.inspector=workbench.getBoundingClientRect().bottom-y;
   applyPanelSizes(sizes);
  }
  grip.addEventListener('pointerdown',e=>{
   if(e.button!==0 || (part!=='inspector'&&!horizontalPanelsActive()))return;
   e.preventDefault();e.stopPropagation();pointerId=e.pointerId;
   grip.setPointerCapture(pointerId);grip.classList.add('is-resizing');
   document.body.classList.add('vds-resizing',part==='inspector'?'vds-resizing-rows':'vds-resizing-columns');
  });
  grip.addEventListener('pointermove',e=>{if(pointerId===e.pointerId)changeAt(e.clientX,e.clientY)});
  function endDrag(e){
   if(pointerId!==e.pointerId)return;
   pointerId=null;grip.classList.remove('is-resizing');
   document.body.classList.remove('vds-resizing','vds-resizing-columns','vds-resizing-rows');
   if(grip.hasPointerCapture(e.pointerId))grip.releasePointerCapture(e.pointerId);
   savePanelSizes();
  }
  grip.addEventListener('pointerup',endDrag);
  grip.addEventListener('pointercancel',endDrag);
  grip.addEventListener('dblclick',resetPanelSizes);
  grip.addEventListener('keydown',e=>{
   const horizontal=part!=='inspector';
   const direction=horizontal?(e.key==='ArrowRight'?1:e.key==='ArrowLeft'?-1:0):
    (e.key==='ArrowUp'?1:e.key==='ArrowDown'?-1:0);
   if(!direction || (horizontal&&!horizontalPanelsActive()))return;
   e.preventDefault();const sizes=measuredPanelSizes();sizes[part]+=24*direction;
   applyPanelSizes(sizes);savePanelSizes();
  });
 }
 window.addEventListener('resize',()=>{if(preferredPanelSizes)applyPanelSizes(preferredPanelSizes)});
}
function wire(){
 $('undoBtn').onclick=undoEdit;$('redoBtn').onclick=redoEdit;historyButtons();
 $('projectButton').onclick=openProjects;$('settingsBtn').onclick=openSettings;$('exportExcelBtn').onclick=async()=>{try{await saveNow();location.href=`/api/projects/${state.id}/excel/export`}catch(e){toast(e.message,true)}};
 $('renderBtn').onclick=renderAudio;$('addSceneBtn').onclick=addScene;
 $('navScript').onclick=()=>setView('script');$('navVoices').onclick=()=>setView('voices');$('navAssets').onclick=()=>setView('assets');
 $('rewindBtn').onclick=stop;$('playBtn').onclick=play;$('stopBtn').onclick=stop;
 $('reflowBtn').onclick=()=>{reflow();dirtyState();renderTimeline();toast('台詞を順番どおりに再配置しました')};
 $('zoom').oninput=e=>{pxPerSec=Number(e.target.value);renderTimeline()};
 $('selectAllClips').onclick=()=>selectClipGroup(activeClips());
 $('selectTrackClips').onclick=selectTrackClips;
 $('selectFollowingClips').onclick=selectFollowingClips;
 $('clearClipSelection').onclick=()=>{selectedClip=null;selectedLine=null;selectedClips.clear();clipSelectionAnchor=null;paintClipSelection();renderInspector()};
 $('excelPicker').onchange=e=>{importExcel(e.target.files[0]);e.target.value=''};
 $('audioPicker').onchange=e=>{handleUpload([...e.target.files]);e.target.value=''};
 $('bulkAudioPicker').onchange=e=>{const files=[...e.target.files];stageAudioBatch(files,uploadContext.kind,uploadContext.voiceName||'');e.target.value=''};
 document.addEventListener('click',handleGlobalClick);
 document.addEventListener('dragover',e=>{onLaneDragOver(e);if([...e.dataTransfer?.types||[]].includes('Files')){e.preventDefault();e.dataTransfer.dropEffect='copy';$('editorContent').classList.add('file-drag-active')}});
 document.addEventListener('dragleave',e=>{if(!e.relatedTarget)$('editorContent').classList.remove('file-drag-active')});
 document.addEventListener('drop',e=>{if(laneFromTarget(e.target))return onTimelineDrop(e);highlightLane(null);if(e.dataTransfer?.files?.length)handleAudioFileDrop(e)});
 document.addEventListener('dragstart',onLibraryDragStart);document.addEventListener('dragend',()=>highlightLane(null));

 document.addEventListener('change',handleChange);
 document.addEventListener('input',e=>{if(historyRestoring)return;const t=e.target;if(t.dataset.lineField==='text'||t.dataset.lineField==='caption'){const l=lineById(t.closest('.script-line').dataset.id);if(l){l[t.dataset.lineField]=t.value;l.status=l.selectedTakeId?'stale':'missing';const badge=t.closest('.script-line')?.querySelector('.line-status');if(badge){badge.className='line-status '+l.status;badge.textContent=l.status==='stale'?'要再生成':'未生成'}dirtyState(`typing:${l.id}:${t.dataset.lineField}`)}}else if(t.id==='modelCustom'){const l=lineById(selectedLine);if(l){l.model=t.value;dirtyState()}}});
 $('timeline').addEventListener('pointerdown',dragStart);
 document.addEventListener('pointermove',dragMove);
 document.addEventListener('pointerup',dragEnd);
 document.addEventListener('pointercancel',dragEnd);
 $('timeline').addEventListener('click',e=>{if(e.target.closest('[data-ruler]')){const b=e.target.closest('[data-ruler]').getBoundingClientRect();stopNodes();currentTime=Math.max(0,(e.clientX-b.left)/pxPerSec);updatePlayhead()}});
 document.addEventListener('keydown',e=>{
  const focus=document.activeElement;
  const timelineFocused=focus?.closest?.('#timeline');
  if(timelineFocused&&(e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='a'){e.preventDefault();selectClipGroup(activeClips());return}
  if(timelineFocused&&e.key==='Escape'){e.preventDefault();$('clearClipSelection').click();return}
  if(timelineFocused&&(e.key==='Enter'||e.key===' ')&&focus.matches('.tl-clip')){e.preventDefault();pointerSelectClip(clipById(focus.dataset.clip),e,false);return}
  if((e.ctrlKey||e.metaKey)&&!e.altKey&&!e.repeat&&!document.querySelector('.modal-backdrop:not(.hidden)')){
    const key=e.key.toLowerCase(),undo=key==='z'&&!e.shiftKey,redo=(key==='z'&&e.shiftKey)||key==='y';
    if((undo&&undoHistory.length)||(redo&&redoHistory.length)){
      e.preventDefault();if(undo)undoEdit();else redoEdit();restoreEditingFocus(focus);return;
    }
  }
  const writing=['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName);if(e.ctrlKey&&e.key.toLowerCase()==='s'){e.preventDefault();saveNow().then(()=>toast('保存しました')).catch(err=>toast(err.message,true))}if(e.code==='Space'&&!writing&&!document.querySelector('.modal-backdrop:not(.hidden)')){e.preventDefault();play()}});
 window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''} });
}
installPanelResizers();
wire();ensureProject().catch(e=>{toast('初期化に失敗しました: '+e.message,true);$('saveState').textContent='起動エラー'});
