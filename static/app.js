/* VoiceDrama Studio: script-first editor, layered Web Audio preview and local project API. */
'use strict';
let state=null, selectedScene=null,selectedLine=null,selectedClip=null, view='script';
let selectedClips=new Set(),clipSelectionAnchor=null,suppressClipClick=false;
let dirty=false,saveTimer=null,saving=null,pxPerSec=38,currentTime=0,playing=false,playStart=0,ctx=null,nodes=[],audioCache=new Map(),animationId=0;
let uploadContext={kind:'sfx',lineId:null,voiceName:null};
let jobTimer=null,playbackRequest=0;
let bulkFiles=[],bulkMode='assign',bulkKind='voice',bulkCast='',bulkSort='natural',bulkScene='',bulkStart='',bulkOnlyMissing=true,bulkTargets={};
let bulkNextKey=0,bulkBusy=false;
let trashSelection=new Set();
const LIBRARY_TABS=[['voice','セリフ'],['bgm','BGM'],['sfx','効果音'],['ambience','環境音'],['other','その他']];
let libraryTab='voice',libraryQueries={};
const SHAREABLE_LIBRARY_KINDS=new Set(['bgm','sfx','ambience']);
let librarySource='project',sharedAssets=[],sharedAssetsError='';
let excelImportMode='merge';
let castCopySelection=new Set();
// Shared with game voice; applied to the TTS input only, never to l.text.
let pronunciation=null,pronunciationEditor=null;
const PD=()=>globalThis.PronunciationDictionary;
// Characters shared by every work; a project's own casts are the fallback.
let characterRegistry=null,characterEditor=null;
const CH=()=>globalThis.CharacterRegistry;
function voiceFor(speaker){
 const link=state?.characterLinks?.[speaker],local=state?.voices.find(v=>v.name===speaker)||null;
 if(link!=='project'){
  const found=(link&&CH()?.byId(characterRegistry,link))||CH()?.match(characterRegistry,[speaker]);
  if(found)return found;
 }
 return local;
}
function voiceLabel(v){
 if(!v)return '未設定（標準の声）';
 const shared=!!CH()?.byId(characterRegistry,v.id);
 return `${shared?'共通キャラクター':'この作品のキャスト'}「${v.name}」 · ${String(v.model||'Aratako/Irodori-TTS-v4.1-Small').split('/').pop()} · 参照音声${(v.refs||[]).length}件${shared&&!(v.refs||[]).length&&v.mode!=='design'?' ⚠ 参照音声なし':''}`;
}
// A project cast whose speaker uses a shared character shows that
// character's references: they are what generation actually uses.
function sharedVoiceHtml(v,c){
 if(!c)return '';
 const refs=(c.refs||[]).map(id=>(characterRegistry?.assets||[]).find(a=>a.id===id)).filter(Boolean);
 return `<section class="cast-reference-section cast-shared-voice"><div class="cast-reference-heading"><div class="cast-reference-kicker">VOICE IN USE</div><h4>共通キャラクター「${esc(c.name)}」の参照音声（生成に使う声）</h4></div><p class="small muted">話者「${esc(v.name)}」は、共通キャラクター「${esc(c.name)}」の設定と参照音声で生成します。下の項目はこの作品だけのキャストの設定で、今は使われていません（「この作品の話者と声」で「この作品だけのキャスト」を選ぶと使われます）。共通キャラクターの参照音声は、上の共通キャラクター一覧で編集できます。</p>${!refs.length&&c.mode!=='design'?`<p class="cast-shared-warning">⚠ 共通キャラクター「${esc(c.name)}」に参照音声がありません。このままでは参照音声なしの声で生成されます。</p>`:''}<div class="cast-reference-preview">${refs.map(a=>`<div class="ref-row"><span title="${esc(a.name)}">${esc(a.name)}</span>${button('▶ 試聴',`data-preview-character="${esc(a.id)}"`)}</div>`).join('')}</div></section>`;
}
// Local edit history only: never roll back audio files, uploads or server-side
// destructive actions. Keep asset metadata and the server revision authoritative.
const HISTORY_LIMIT=80;
const HISTORY_FIELDS=['title','scenes','lines','clips','tracks','voices','settings','characterLinks'];
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
async function switchProject(pid){stop();if(state&&dirty)await saveNow();state=await req('/api/projects/'+pid);selectedScene=state.scenes[0]?.id||null;selectedLine=null;selectedClip=null;currentTime=0;audioCache.clear();trashSelection.clear();libraryTab='voice';libraryQueries={};librarySource='project';sharedAssets=[];view='script';clearEditHistory();renderAll();$('saveState').textContent='保存済み';loadPronunciation();loadCharacters();resumeGeneration()}
async function createProject(title){const s=await req('/api/projects',json('POST',{title}));await switchProject(s.id);toast('プロジェクトを作成しました')}
async function ensureProject(){const list=await req('/api/projects');if(!list.length){await createProject('新しいボイスドラマ')}else await switchProject(list[0].id)}
function getVoices(){return [...new Set([...state.voices.map(v=>v.name),...state.lines.map(l=>l.speaker),...(characterRegistry?.characters||[]).map(c=>c.name)].filter(Boolean))]}
// The scene list is always visible: choosing a scene opens its script from
// any screen (cast, library, dictionary), not only the timeline behind it.
function selectScene(id){if(id===selectedScene&&view==='script')return;if(id!==selectedScene){stop();selectedScene=id;selectedLine=null;selectedClip=null;currentTime=0}view='script';renderAll()}
function selectLine(id){
 // A script-line click always switches the inspector to line/TTS controls,
 // including when its clip is selected and selectedLine is already this ID.
 selectedLine=id;
 selectedClip=null;
 selectedClips.clear();
 clipSelectionAnchor=null;
 document.querySelectorAll('.script-line').forEach(x=>x.classList.toggle('active',x.dataset.id===id));
 renderTimeline();
 renderInspector();
}

function dialogueClipForLine(id){
 // A line may have multiple takes or linked sound effects; seek only to its
 // dialogue clip, never to a BGM or effect anchored to the same line.
 return activeClips().filter(c=>c.lineId===id&&c.trackId==='dialogue')
   .sort((a,b)=>Number(a.start)-Number(b.start))[0]||null;
}
function revealTimelineTime(time){
 const shell=document.querySelector('.timeline-shell');
 if(!shell)return;
 const contentWidth=Math.max(0,shell.clientWidth-TIMELINE_LABEL_WIDTH);
 if(!contentWidth)return;
 // Place the target about one third across the visible time area, with room
 // for the dialogue and following clips.
 const x=TIMELINE_LABEL_WIDTH+time*pxPerSec;
 shell.scrollLeft=Math.max(0,x-TIMELINE_LABEL_WIDTH-contentWidth*.3);
}
function jumpToScriptLine(id,autoplay=false){
 const line=lineById(id);
 if(!line||line.sceneId!==selectedScene)return false;
 selectLine(id);
 const clip=dialogueClipForLine(id);
 if(!clip){
  toast('この台詞の音声はまだマルチトラックに配置されていません。',true);
  return false;
 }
 // Interrupt the current audio before moving the playhead. Also invalidate
 // any pending async audio loading from the previous playback position.
 stopNodes();
 currentTime=Math.max(0,Number(clip.start)||0);
 updatePlayhead();
 revealTimelineTime(currentTime);
 if(autoplay)play();
 return true;
}
function syncScriptToSelectedClip(){
 if(view!=='script')return;
 const panel=$('editorContent');if(!panel||typeof panel.querySelectorAll!=='function')return;
 let selected=null;
 panel.querySelectorAll('.script-line').forEach(el=>{
  const isSelected=!!selectedLine&&el.dataset.id===selectedLine;
  el.classList.toggle('active',isSelected);
  if(isSelected)selected=el;
 });
 if(!selected)return;
 // Scroll the script pane, not the document or the timeline.
 const viewport=panel.getBoundingClientRect(),item=selected.getBoundingClientRect();
 const top=panel.scrollTop+item.top-viewport.top+(item.height-viewport.height)/2;
 const reduced=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
 panel.scrollTo({top:Math.max(0,top),behavior:reduced?'auto':'smooth'});
}
function selectClip(id){selectedClips=new Set([id]);clipSelectionAnchor=id;selectedClip=id;selectedLine=clipById(id)?.lineId||null;renderTimeline();renderInspector();syncScriptToSelectedClip()}
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
 paintClipSelection();renderInspector();syncScriptToSelectedClip();
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
 selectedLine=clipById(selectedClip)?.lineId||null;paintClipSelection();renderInspector();syncScriptToSelectedClip();
 return selectedClips.has(c.id);
}
function addScene(){const name=prompt('新しいシーン名','シーン'+(state.scenes.length+1));if(!name)return;const id=newid('scene');state.scenes.push({id,name});dirtyState();selectScene(id)}
async function renameScene(id){
 const scene=state?.scenes.find(s=>s.id===id);if(!scene)return;
 const answer=prompt('シーン名を変更',scene.name);if(answer===null)return;
 const name=answer.trim();if(!name)return toast('シーン名を入力してください',true);
 if(name===scene.name)return;
 scene.name=name;dirtyState();renderAll();
 try{await saveNow();toast('シーン名を変更しました')}catch(e){toast(e.message,true)}
}
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
function renderSidebar(){const counts={};state.lines.forEach(l=>counts[l.sceneId]=(counts[l.sceneId]||0)+1);$('sceneList').innerHTML=state.scenes.map((s,i)=>`<div class="scene-row"><button class="scene-item ${selectedScene===s.id?'active':''}" data-scene="${esc(s.id)}" title="${esc(s.name)}"><span class="scene-mark"></span><span class="scene-title">${esc(s.name)}</span><span class="scene-count">${counts[s.id]||0}</span></button><button class="scene-rename-btn" data-rename-scene="${esc(s.id)}" title="シーン名を変更" aria-label="${esc(s.name)}の名前を変更">✎</button><button class="scene-delete-btn" data-delete-scene="${esc(s.id)}" title="このシーンを削除" aria-label="${esc(s.name)}を削除">×</button></div>`).join('');$('projectButton').innerHTML=`<span class="project-title">${esc(state.title)}</span><span class="project-chevron" aria-hidden="true">⌄</span>`;for(const v of ['script','voices','assets','pronunciation'])$('nav'+v[0].toUpperCase()+v.slice(1)).classList.toggle('active',view===v)}
function libraryDialogueMap(){
 const map=new Map(),add=(id,line)=>{if(!id||!line)return;const list=map.get(id)||[];if(!list.some(l=>l.id===line.id))list.push(line);map.set(id,list)};
 for(const line of state.lines)for(const take of line.takes||[])add(take.assetId,line);
 const lines=new Map(state.lines.map(l=>[l.id,l]));
 for(const clip of state.clips)add(clip.assetId,lines.get(clip.lineId));
 return map;
}
function libraryDisplayInfo(asset,dialogues){
 const lines=dialogues.get(asset.id)||[],texts=[...new Set(lines.map(l=>l.text?.trim()).filter(Boolean))];
 return {title:texts.length?texts.join(' / '):asset.name,
  context:[...new Set(lines.map(l=>[sceneName(l.sceneId),l.speaker].filter(Boolean).join(' · ')))].filter(Boolean).join(' / ')};
}
function libraryAssetKind(asset,dialogues){
 if(['reference','voice','bgm','sfx','ambience'].includes(asset.kind))return asset.kind;
 return dialogues.has(asset.id)?'voice':'other';
}
function librarySearchText(value){return String(value||'').normalize('NFKC').toLocaleLowerCase().trim()}
function filteredLibraryAssets(kind,query,dialogues=libraryDialogueMap()){
 const search=librarySearchText(query);
 return state.assets.filter(a=>{
  if(libraryAssetKind(a,dialogues)!==kind)return false;
  const text=kind==='voice'?libraryDisplayInfo(a,dialogues).title:a.name;
  return !search||librarySearchText(text).includes(search);
 }).slice().reverse();
}
function libraryAssetCard(a,dialogues){
 const kind=libraryAssetKind(a,dialogues),info=kind==='voice'?libraryDisplayInfo(a,dialogues):{title:a.name,context:''};
 const share=SHAREABLE_LIBRARY_KINDS.has(kind)?button('⇄ 共通ライブラリへ登録',`data-share-asset="${esc(a.id)}"`):'';
 return `<div class="asset-card asset-draggable" draggable="true" data-drag-asset="${esc(a.id)}" title="マルチトラックのレーンへドラッグできます"><label class="small muted" title="一括削除の対象を選択"><input type="checkbox" data-trash-select="${esc(a.id)}" ${trashSelection.has(a.id)?'checked':''}> 選択</label><span class="asset-type">${esc(LIBRARY_TABS.find(t=>t[0]===kind)?.[1]||'その他')}</span><div class="asset-meta"><b class="asset-display-title">${esc(info.title)}</b><span class="muted small">${fnum(a.duration,1)}s</span></div>${info.context?`<div class="asset-dialogue-context">${esc(info.context)}</div><div class="asset-file-name">${esc(a.name)}</div>`:''}<div class="asset-actions">${button('▶ 試聴',`data-preview="${esc(a.id)}"`)}${kind==='voice'?button('選択中の台詞の音声にする',`data-attach="${esc(a.id)}"`):''}${button('＋ 台詞トラックへ配置',`data-addclip="${esc(a.id)}:dialogue"`)}${button('＋ BGMトラックへ配置',`data-addclip="${esc(a.id)}:bgm"`)}${button('＋ 効果音トラックへ配置',`data-addclip="${esc(a.id)}:sfx"`)}${button('＋ 環境音トラックへ配置',`data-addclip="${esc(a.id)}:ambience"`)}${share}${button('🗑 ゴミ箱へ移動',`data-trash="${esc(a.id)}"`)}</div></div>`;
}
function sharedAssetCard(a){
 const already=state.assets.some(item=>item.sharedId===a.id);
 return `<div class="asset-card"><span class="asset-type">共通素材 · ${esc(LIBRARY_TABS.find(t=>t[0]===a.kind)?.[1]||'音声')}</span><div class="asset-meta"><b class="asset-display-title">${esc(a.name)}</b><span class="muted small">${fnum(a.duration,1)}s</span></div><div class="asset-actions">${button('▶ 試聴',`data-preview-shared="${esc(a.id)}"`)}${button(already?'登録済み':'＋ この作品へ取り込む',`data-import-shared="${esc(a.id)}" ${already?'disabled':''}`,already?'ghost':'primary')}</div></div>`;
}
async function loadSharedAssets(){
 try{
  const fetched=await req('/api/shared-assets');
  sharedAssets=Array.isArray(fetched)?fetched:[];
  sharedAssetsError='';
 }catch(e){
  sharedAssetsError=e.message||'共通ライブラリを読み込めませんでした';
  toast(sharedAssetsError,true);
 }
 if(view==='assets')updateLibraryResults();
}
function updateLibraryResults(){
 if(view!=='assets'||!$('libraryResults'))return;
 const isShared=librarySource==='shared'&&SHAREABLE_LIBRARY_KINDS.has(libraryTab);
 const dialogues=libraryDialogueMap(),counts={};
 for(const a of (isShared?sharedAssets:state.assets)){
  const kind=isShared?a.kind:libraryAssetKind(a,dialogues);
  counts[kind]=(counts[kind]||0)+1;
 }
 const search=librarySearchText(libraryQueries[libraryTab]);
 const results=isShared?sharedAssets.filter(a=>a.kind===libraryTab&&(!search||librarySearchText(a.name).includes(search))).slice().reverse():filteredLibraryAssets(libraryTab,libraryQueries[libraryTab],dialogues);
 for(const [kind] of LIBRARY_TABS){
  const tab=$('libraryTab-'+kind);
  tab.setAttribute('aria-selected',String(kind===libraryTab));
  tab.tabIndex=kind===libraryTab?0:-1;
  tab.querySelector('.library-tab-count').textContent=counts[kind]||0;
 }
 const sourceBar=$('librarySourceBar');
 if(sourceBar)sourceBar.hidden=!SHAREABLE_LIBRARY_KINDS.has(libraryTab);
 for(const source of ['project','shared']){
  const el=$('librarySource-'+source);
  if(el)el.setAttribute('aria-pressed',String(isShared?source==='shared':source==='project'));
 }
 const shareAll=$('shareAllLibraryBtn');
 if(shareAll)shareAll.hidden=isShared||!SHAREABLE_LIBRARY_KINDS.has(libraryTab);
 const dropzone=$('libraryDropzone');
 if(dropzone)dropzone.hidden=isShared;
 $('libraryResults').setAttribute('aria-labelledby','libraryTab-'+libraryTab);
 $('librarySearch').placeholder=libraryTab==='voice'?'セリフ本文を検索':'タイトルを検索';
 $('librarySearchLabel').textContent=libraryTab==='voice'?'セリフ内容で検索':'タイトルで検索';
 $('libraryResultCount').textContent=`${results.length} / ${counts[libraryTab]||0}件表示`;
 $('clearLibrarySearch').disabled=!libraryQueries[libraryTab];
 $('libraryResults').innerHTML=results.map(a=>isShared?sharedAssetCard(a):libraryAssetCard(a,dialogues)).join('')||
  `<div class="empty">${sharedAssetsError&&isShared?esc(sharedAssetsError):search?'検索に一致する音声がありません。':isShared?'共通素材はまだありません。作品内の音声を「共通ライブラリへ登録」してください。':'この種類の音声はまだありません。'}</div>`;
 const trash=$('openTrashBtn');if(trash)trash.hidden=isShared;
}
function selectLibraryTab(kind){
 if(!LIBRARY_TABS.some(t=>t[0]===kind))return;
 libraryTab=kind;
 if(!SHAREABLE_LIBRARY_KINDS.has(kind))librarySource='project';
 $('librarySearch').value=libraryQueries[kind]||'';
 updateLibraryResults();
}
function renderLibraryView(){
 $('editorContent').innerHTML=`<div class="library-tabs" role="tablist" aria-label="音声の種類">${LIBRARY_TABS.map(([kind,label])=>`<button type="button" id="libraryTab-${kind}" data-library-tab="${kind}" role="tab" aria-controls="libraryResults" aria-selected="${kind===libraryTab}">${esc(label)} <span class="library-tab-count">0</span></button>`).join('')}</div>
 <div id="librarySourceBar" class="library-source-bar"><span class="small muted">表示する素材</span><button type="button" id="librarySource-project" data-library-source="project">この作品</button><button type="button" id="librarySource-shared" data-library-source="shared">共通ライブラリ</button><button type="button" class="btn ghost mini" id="shareAllLibraryBtn">この作品の素材をすべて共通ライブラリへ登録</button></div>
 <div class="library-controls"><label for="librarySearch" id="librarySearchLabel">セリフ内容で検索</label><div class="library-search-row"><input id="librarySearch" type="search" value="${esc(libraryQueries[libraryTab]||'')}" placeholder="セリフ本文を検索"><button type="button" id="clearLibrarySearch" class="btn ghost">検索をクリア</button></div><div id="libraryResultCount" class="small muted" role="status" aria-live="polite"></div></div>
 <div id="libraryDropzone">${bulkDropZone('library')}</div><div id="libraryResults" role="tabpanel" aria-labelledby="libraryTab-${libraryTab}"></div><button class="editor-bigaction" id="openTrashBtn">🗑 ゴミ箱を確認・復元</button>`;
 updateLibraryResults();
 loadSharedAssets();
}
function renderAll(){if(!state)return;renderSidebar();renderEditor();renderTimeline();renderInspector()}
async function loadPronunciation(){
 try{pronunciation=await PD().fetchDictionary();renderInspector()}
 catch(e){toast('読み方辞書を読み込めません: '+e.message,true)}
}
// A dictionary or character change affects only lines whose spoken text or
// voice changes; the server compares each take's signature, so take the
// statuses from there.
async function refreshLineStatuses(){
 if(!state)return;
 try{
  await saveNow();
  const pid=state.id,fresh=await req('/api/projects/'+pid);
  if(state?.id!==pid)return;
  const statuses=new Map(fresh.lines.map(l=>[l.id,l.status]));
  for(const line of state.lines)if(statuses.has(line.id))line.status=statuses.get(line.id);
  renderInspector();
 }catch(e){toast('台詞の状態を更新できません: '+e.message,true)}
}
async function pronunciationSaved(data){
 pronunciation=data;
 await refreshLineStatuses();
}
async function loadCharacters(){
 try{characterRegistry=await CH().fetchRegistry();renderInspector();renderProjectCasts()}
 catch(e){toast('共通キャラクターを読み込めません: '+e.message,true)}
}
async function charactersSaved(data,info){
 characterRegistry=data;
 if(!state)return;
 // "Use in the source scenario" links this project's speakers on the server.
 if(info?.source?.kind==='drama'&&info.source.id===state.id&&info.summary?.applied){
  try{state=await req('/api/projects/'+state.id);clearEditHistory();renderAll();$('saveState').textContent='保存済み'}
  catch(e){toast(e.message,true)}
  return;
 }
 await refreshLineStatuses();
 renderProjectCasts();characterEditor?.refresh();
}
function projectCastsHtml(){
 // Casts made inside this project before shared characters existed.
 const sharedOf=v=>{const u=voiceFor(v.name);return u&&u!==v?u:null};
 return speakerLinksHtml()+(state.voices.length?`<section class="project-casts"><h3>この作品だけのキャスト</h3><p class="muted small">この作品だけの声の設定です。同じ名前の共通キャラクターがあると、そちらを使います。「⇧ このキャストを共通キャラクターにする」で全作品から使えるようにできます。</p><div class="buttonline">${button('⇧ この作品のキャストをまとめて共通キャラクターへ','id="promoteAllCastsBtn"')}${button('⇄ キャストを別作品へコピー','id="openCastCopyBtn"')}${button('↓ 参照音声をまとめて登録','id="batchReferencesBtn"')}</div></section>`+bulkDropZone('reference'):'')+(state.voices.length?state.voices.map((v,i)=>`<article class="cast-card" data-voice="${i}" title="ここに音声をドロップすると、このキャストの参照音声として登録できます"><header class="cast-card-heading"><div class="cast-identity"><span class="cast-number">CAST ${String(i+1).padStart(2,'0')}</span><h3>${esc(v.name)}</h3></div><span class="cast-reference-count">${sharedOf(v)?`共通キャラクター「${esc(sharedOf(v).name)}」を使用中 · 参照音声 ${(sharedOf(v).refs||[]).length}件`:`参照音声 ${(v.refs||[]).length}件`}</span></header>${sharedVoiceHtml(v,sharedOf(v))}<div class="inspector-grid cast-fields"><div class="field"><label>名前</label><input data-vfield="name" value="${esc(v.name)}"></div><div class="field"><label>言語</label><select data-vfield="language">${['Japanese','English','Chinese','Auto'].map(z=>`<option ${z===v.language?'selected':''}>${z}</option>`).join('')}</select></div><div class="field"><label>既定TTS</label><select data-vfield="engine"><option value="irodori" ${v.engine!=='qwen'?'selected':''}>Irodori-TTS</option><option value="qwen" ${v.engine==='qwen'?'selected':''}>Qwen3-TTS</option></select></div><div class="field"><label>生成方式</label><select data-vfield="mode"><option value="clone" ${v.mode!=='design'?'selected':''}>参照音声</option><option value="design" ${v.mode==='design'?'selected':''}>Voice Design</option></select></div><div class="field wide"><label>音声モデル（Voice DesignのIrodoriモデルを選択可能）</label><select data-vfield="model">${(models[v.engine]||models.irodori).map(([m,n])=>`<option value="${esc(m)}" ${v.model===m?'selected':''}>${esc(n)}</option>`).join('')}</select></div><div class="field wide"><label>声の説明（Voice Design）</label><textarea rows="2" data-vfield="description">${esc(v.description||'')}</textarea></div><div class="field wide"><label>Qwen Base: 参照音声の書き起こし（任意）</label><textarea rows="2" data-vfield="refText">${esc(v.refText||'')}</textarea></div><div class="field wide"><label>参照音声（複数選択可 / Qwen Baseは先頭1本）</label><div class="buttonline">${state.assets.filter(a=>a.kind==='reference'||(v.refs?.includes(a.id)&&a.kind!=='reference')).map(a=>`<label class="pill" title="${a.kind==='reference'?'声の参照音声':'以前登録した台詞音声です。不要ならチェックを外してください'}"><input type="checkbox" data-ref="${esc(a.id)}" ${v.refs?.includes(a.id)?'checked':''}> ${esc(a.name.slice(0,22))}${a.kind==='reference'?'':' ⚠ 旧登録'}</label>`).join('')||'<span class="small muted">未登録</span>'}</div></div></div><section class="cast-reference-section"><div class="cast-reference-heading"><div class="cast-reference-kicker">VOICE SAMPLES</div><h4>${esc(v.name)} の参照音声${sharedOf(v)?'（この作品だけのキャスト・今は未使用）':''}</h4></div><div class="cast-reference-preview">${(v.refs||[]).map(id=>state.assets.find(a=>a.id===id)).filter(Boolean).map(a=>`<div class="ref-row"><span title="${esc(a.name)}">${esc(a.name)}</span>${button('▶ 試聴',`data-preview="${esc(a.id)}"`)}</div>`).join('')||'<span class="small muted">参照音声はまだありません</span>'}</div></section><div class="asset-actions cast-card-actions">${button('✦ Voice Designで新しい声を作成',`data-voice-design="${i}"`,'primary')}${button('＋ 参照音声ファイルを登録',`data-voice-upload="${i}"`)}${button('⇧ このキャストを共通キャラクターにする',`data-cast-promote="${i}" title="このキャストを全作品で使える共通キャラクターにします"`)}${button('このキャストの全台詞へ設定を適用',`data-voice-apply="${i}"`)}${button('⇄ このキャストを別作品へコピー',`data-cast-copy="${i}"`)}${button('🗑 このキャストを削除',`data-cast-delete="${i}"`,'danger')}</div></article>`).join(''):'')+`<div class="ref-manage"><h3>参照音声の管理</h3><p class="muted small">キャストの参照音声だけを管理します。使用中の音声を削除するときは確認が必要です。</p>${state.assets.filter(a=>a.kind==='reference').map(a=>`<div class="ref-row"><span title="${esc(a.name)}">${esc(a.name)} · ${fnum(a.duration,1)}秒</span><div>${button('▶ 試聴',`data-preview="${esc(a.id)}"`)}${button('🗑 ゴミ箱へ移動',`data-trash="${esc(a.id)}"`)}</div></div>`).join('')||'<p class="muted small">参照音声はまだありません。</p>'}</div>`;
}
function renderCastsView(){
 if(!($('characterEditor')?.isConnected&&characterEditor)){
  $('editorContent').innerHTML='<div id="characterEditor"></div><div id="projectCasts"></div>';
  characterEditor=CH()?.createEditor($('characterEditor'),{toast,models,onSaved:charactersSaved,
   classes:{button:'btn ghost',primary:'btn primary',danger:'btn danger'},
   usage:c=>{const ids=new Map();let n=0;for(const l of state.lines){if(!ids.has(l.speaker))ids.set(l.speaker,voiceFor(l.speaker)?.id);if(ids.get(l.speaker)===c.id)n++}return n?`この作品の台詞 ${n}件`:''},
   actions:()=>[{label:'この作品の台詞へ設定を適用',title:'この作品でこのキャラクターを使う台詞の「声の設定元」を、キャラクター設定に戻します',run:applyCharacter},
    {label:'✦ この作品のこのキャラクターの台詞を生成',title:'この作品で、このキャラクターの声を使う台詞だけをまとめて生成します',run:c=>{const sp=speakersUsing(c);if(!sp.length)return toast(`この作品に「${c.name}」の声を使う台詞がありません`,true);openSceneGeneration({speakers:sp})}}],
   beforeImport:()=>saveNow(),
   onLoad:data=>{characterRegistry=data;renderProjectCasts();renderInspector()}})||null;
  characterEditor?.load();
 }
 renderProjectCasts();
}
function renderProjectCasts(){const box=$('projectCasts');if(box&&view==='voices')box.innerHTML=projectCastsHtml()}
function speakerLinksHtml(){
 const speakers=[...new Set(state.lines.map(l=>l.speaker).filter(Boolean))];
 if(!speakers.length)return '';
 const shared=characterRegistry?.characters||[];
 return `<section class="speaker-links"><h3>この作品の話者と声</h3><p class="muted small">「自動」は、名前か別名が一致する共通キャラクターを使い、なければこの作品だけのキャストを使います。話者ごとに別のキャラクターも選べます。</p>${speakers.map(sp=>{
  const link=state.characterLinks?.[sp]||'',auto=CH()?.match(characterRegistry,[sp]),local=state.voices.find(v=>v.name===sp),voice=voiceFor(sp);
  return `<div class="speaker-link-row"><b title="${esc(sp)}">${esc(sp)} <small class="muted">${state.lines.filter(l=>l.speaker===sp).length}件</small></b><select data-speaker-link="${esc(sp)}" aria-label="${esc(sp)}の声"><option value="" ${link?'':'selected'}>自動（${auto?'共通キャラクター「'+esc(auto.name)+'」':local?'この作品だけのキャスト':'一致なし'}）</option>${shared.map(c=>`<option value="${esc(c.id)}" ${link===c.id?'selected':''}>共通キャラクター：${esc(c.name)}</option>`).join('')}${local?`<option value="project" ${link==='project'?'selected':''}>この作品だけのキャスト「${esc(sp)}」</option>`:''}</select><span class="speaker-link-state">${voice?esc(voiceLabel(voice)):'未登録（標準の声）'+button('＋ 共通キャラクターに登録',`data-register-speaker="${esc(sp)}"`)}${button('✦ この話者の台詞を生成',`data-generate-speaker="${esc(sp)}" title="この話者の台詞を全シーンからまとめて生成します（対象は確認画面で選び直せます）"`)}</span></div>`;
 }).join('')}</section>`;
}
function setSpeakerLink(speaker,value){
 const before=voiceFor(speaker),links={...(state.characterLinks||{})};
 if(value)links[speaker]=value;else delete links[speaker];
 if(Object.keys(links).length)state.characterLinks=links;else delete state.characterLinks;
 if(voiceFor(speaker)!==before)state.lines.filter(l=>l.speaker===speaker&&l.selectedTakeId&&getLineTtsSource(l)==='cast').forEach(l=>l.status='stale');
 dirtyState();renderProjectCasts();characterEditor?.refresh();renderInspector();
}
function applyCharacter(c){
 let num=0;
 for(const l of state.lines.filter(l=>voiceFor(l.speaker)?.id===c.id)){l.ttsSource='cast';l.status=l.selectedTakeId?'stale':'missing';num++}
 dirtyState();renderAll();toast(`${c.name} の ${num}件をキャラクター設定に切り替えました`);
}
function renderPronunciationView(){
 if($('pronunciationEditor')?.isConnected&&pronunciationEditor)return;
 $('editorContent').innerHTML='<div id="pronunciationEditor"></div>';
 pronunciationEditor=PD()?.createEditor($('pronunciationEditor'),{toast,onSaved:pronunciationSaved,
  onLoad:data=>{pronunciation=data;renderInspector()},
  classes:{button:'btn ghost',primary:'btn primary',danger:'btn danger'}})||null;
 pronunciationEditor?.load();
}
function linePronunciationHtml(l){
 const pd=PD();if(!pd||!pronunciation)return '';
 const dictionary=pronunciation.dictionary||{},choices=l.pronunciationChoices||{};
 const words=pd.choosable(l.text,dictionary);
 return `<section class="pd-line" aria-label="読み方"><div class="pd-line-title">辞書適用後の読み上げ（音声生成のときだけ・台本は変更しません）</div><div class="pd-spoken">${esc(pd.apply(l.text,dictionary,choices)||'（空の台詞）')}</div>${words.map(w=>`<label class="pd-choice">「${esc(w)}」の読み${pd.choiceSelect(w,dictionary[w],choices,`data-pron-choice="${esc(w)}"`)}</label>`).join('')}</section>`;
}
function bulkDropZone(mode){return `<div class="batch-dropzone" data-audio-drop="${mode}"><div class="dropzone-icon">⇩</div><div><b>${mode==='assign'?'生成済みの台詞音声をまとめて割り当て':mode==='reference'?'キャストの参照音声をドロップ':'音声ファイルをまとめてライブラリへ追加'}</b><p>音声ファイルをここへドラッグ＆ドロップ · 複数ファイル対応 · 取り込み前に割り当てを確認</p></div></div>`}
function renderEditor(){if(!state)return;if(view!=='voices')characterEditor=null;const act=$('panelActions');const title=$('editorTitle');const sub=$('editorSub');const key=$('editorKicker');
 if(view==='script'){
  pronunciationEditor=null;
  key.textContent='台本・台詞';title.textContent=sceneName(selectedScene);sub.textContent=`${activeLines().length} セリフ · 編集すると音声の更新を検出`;
  const pendingCount=activeLines().filter(x=>x.status!=='ready'&&x.text.trim()).length;
  act.innerHTML=`<div class="action-group action-main">${button('＋ セリフを追加','id="addLineTop"')}${button(pendingCount?`✦ このシーンの未生成${pendingCount}件を生成`:'✦ このシーンの未生成を生成', `id="generateMissingBtn" ${pendingCount?'':'disabled'} title="このシーンの未生成・要再生成の台詞を生成します"`,'primary')}${button('✦ 複数シーンをまとめて生成','id="generateScenesBtn" title="複数のシーンや全シーンの台詞をまとめて生成します"')}${button('✦ キャラクターを選んで生成','id="generateSpeakersBtn" title="選んだキャラクターの台詞だけを、全シーンからまとめて生成します"')}</div><div class="action-group">${button('↑ Excelの台本を取り込む','id="importExcelBtn"')}${button('↓ 一括取り込み用Excelひな形','id="downloadExcelTemplateBtn"')}${button('↓ 手持ちの音声ファイルを台詞に割り当て','id="batchAssignBtn"')}${button('↓ このシーンの台詞音声をZIPで保存','id="downloadSceneTakesZipBtn"')}${button('🗑 台本の台詞をすべて削除','id="openClearScriptBtn"','danger')}</div>`;
  $('editorContent').innerHTML=bulkDropZone('assign')+activeLines().map((l,i)=>`<article class="script-line ${selectedLine===l.id?'active':''}" data-id="${esc(l.id)}"><div class="line-top"><span class="line-number">${String(i+1).padStart(2,'0')}</span><input class="speaker-select" data-line-field="speaker" list="speakers" value="${esc(l.speaker)}" title="話者を入力"><span class="line-status ${esc(l.status)}">${{ready:'音声あり',stale:'要再生成',missing:'未生成'}[l.status]||'未生成'}</span><div class="line-actions"><button class="icon-btn" data-move="-1" title="上へ">↑</button><button class="icon-btn" data-move="1" title="下へ">↓</button><button class="icon-btn" data-delete="line" title="削除">×</button></div></div><textarea class="line-text" data-line-field="text" placeholder="ここに台詞を入力">${esc(l.text)}</textarea><div class="caption-box"><span class="caption-tag">演技指示</span><textarea data-line-field="caption" placeholder="例：声をひそめて、辺りを警戒しながら…">${esc(l.caption)}</textarea></div><div class="file-name-box"><label>生成音声のファイル名（任意） <input data-line-field="outputFilename" type="text" maxlength="120" value="${esc(l.outputFilename||'')}" placeholder="未指定なら自動命名 · 例: mahoru_001.wav" spellcheck="false"></label></div><div class="line-footer"><label>次の台詞まで <input class="gap-input" type="number" min="0" max="60" step="0.1" data-line-field="after" value="${fnum(l.after,1)}"> 秒</label><span>${l.selectedTakeId?'<button data-action="preview" title="選択中のテイクを試聴">▶ 試聴</button>　':''}<button type="button" data-action="seek" title="対応する台詞クリップの開始位置へ再生ヘッドを移動">↪ タイムラインで位置を表示</button>　<button type="button" data-action="playFrom" title="この台詞の開始位置へ移動して再生">▶ ここから再生</button>　<button data-action="addBelow" title="この台詞のすぐ下に新しいセリフを追加">＋ この下にセリフを追加</button>　<button data-action="select" title="右側の「選択中の台詞・クリップ」で生成・試聴・テイク選択">テイク${l.takes?.length||0}件・生成 →</button></span></div></article>`).join('')+`<button class="editor-bigaction" id="addLineBottom">＋ セリフを追加</button><datalist id="speakers">${getVoices().map(n=>`<option value="${esc(n)}">`).join('')}</datalist>`;
 }else if(view==='voices'){
  key.textContent='キャスト・声';title.textContent='キャスト・声';sub.textContent='共通キャラクターは、すべての作品とゲーム音声で使えます。台詞音声は音声ライブラリへ';act.innerHTML=button('⇩ 他の作品・ゲームからキャラクターを取り込む','id="importCharactersBtn"')+button('＋ 共通キャラクターを追加','id="addCharacterBtn"','primary');
  pronunciationEditor=null;
  renderCastsView();
 }else if(view==='pronunciation'){
  key.textContent='読み方';title.textContent='読み方辞書';sub.textContent='ボイスドラマとゲーム音声で共通 · 音声生成のときだけ適用します';act.innerHTML='';
  renderPronunciationView();
 }else{
 pronunciationEditor=null;
 key.textContent='音声ライブラリ';title.textContent='音声ライブラリ';sub.textContent='音声をドラッグして右のマルチトラックへ配置できます。BGM・効果音・環境音など';act.innerHTML=button('↓ 音声ファイルをまとめて取り込む',`id="batchLibraryBtn"`,'primary')+button('＋ 音声ファイルを追加',`id="uploadAudioBtn"`)+button(`🗑 選択した${trashSelection.size}件をゴミ箱へ`,`id="trashSelectedBtn" ${trashSelection.size?'':'disabled'}`);
 renderLibraryView();
 }
}
const TIMELINE_ZOOM_KEY='vds.timeline.zoom.v1';
const TIMELINE_MIN_PX_PER_SEC=.001,TIMELINE_MAX_PX_PER_SEC=200,TIMELINE_LABEL_WIDTH=108;
function clampTimelineZoom(value){
 const number=Number(value);
 return Number.isFinite(number)?Math.max(TIMELINE_MIN_PX_PER_SEC,Math.min(TIMELINE_MAX_PX_PER_SEC,number)):38;
}
function timelineZoomToSlider(value){
 const px=clampTimelineZoom(value);
 return Math.round(100*Math.log(px/TIMELINE_MIN_PX_PER_SEC)/Math.log(TIMELINE_MAX_PX_PER_SEC/TIMELINE_MIN_PX_PER_SEC));
}
function timelineSliderToZoom(value){
 const fraction=Math.max(0,Math.min(100,Number(value)||0))/100;
 return TIMELINE_MIN_PX_PER_SEC*Math.pow(TIMELINE_MAX_PX_PER_SEC/TIMELINE_MIN_PX_PER_SEC,fraction);
}
function timelineTickStep(px=pxPerSec){
 const target=90/Math.max(px,TIMELINE_MIN_PX_PER_SEC);
 const options=[1,2,5,10,15,30,60,120,300,600,900,1800,3600,7200,14400,28800,86400,172800,604800,1209600];
 return options.find(n=>n>=target)||Math.ceil(target/86400)*86400;
}
function timelineTickName(seconds){
 if(seconds<60)return seconds+'秒';
 if(seconds%3600===0)return (seconds/3600)+'時間';
 if(seconds%60===0)return (seconds/60)+'分';
 return seconds+'秒';
}
function timelineTickLabel(seconds){
 const whole=Math.round(seconds),hours=Math.floor(whole/3600),mins=Math.floor(whole%3600/60),secs=whole%60;
 return hours?hours+':'+String(mins).padStart(2,'0')+':'+String(secs).padStart(2,'0'):
   String(mins).padStart(2,'0')+':'+String(secs).padStart(2,'0');
}
function timelineDuration(){return Math.max(40,...activeClips().map(c=>Number(c.start||0)+Number(c.duration||0)+7))}
function setTimelineZoom(next,fit=false){
 const shell=document.querySelector('.timeline-shell');
 const before=pxPerSec;
 const viewport=shell?.clientWidth||0;
 // Keep the same moment under the center of the viewport while zooming.
 const centerTime=Math.max(0,((shell?.scrollLeft||0)+viewport/2-TIMELINE_LABEL_WIDTH)/before);
 pxPerSec=clampTimelineZoom(next);
 renderTimeline();
 if(shell){
  if(fit)shell.scrollLeft=0;
  else shell.scrollLeft=Math.max(0,TIMELINE_LABEL_WIDTH+centerTime*pxPerSec-viewport/2);
 }
 try{localStorage.setItem(TIMELINE_ZOOM_KEY,String(pxPerSec))}catch(_){}
}
function fitTimeline(){
 if(!state)return;
 const shell=document.querySelector('.timeline-shell');
 if(!shell)return;
 const visible=Math.max(50,shell.clientWidth-TIMELINE_LABEL_WIDTH-20);
 setTimelineZoom(visible/timelineDuration(),true);
}
function restoreTimelineZoom(){
 try{
  const value=localStorage.getItem(TIMELINE_ZOOM_KEY);
  if(value!==null&&value!=='')pxPerSec=clampTimelineZoom(value);
 }catch(_){}
}
function renderTimeline(){
 if(!state)return;
 syncClipSelection();
 const duration=timelineDuration(),width=Math.ceil(duration*pxPerSec),clips=activeClips();
 const tick=timelineTickStep(),major=tick*pxPerSec,minor=major/5;
 let html=`<div class="tl-ruler"><div class="tl-ruler-side">TIME</div><div class="tl-ruler-scale" data-ruler="1" style="width:${width}px">`;
 for(let t=0;t<duration;t+=tick)html+=`<div class="tl-tick" style="left:${t*pxPerSec}px">${timelineTickLabel(t)}</div>`;
 html+='</div></div>';
 for(const track of state.tracks){
  const these=clips.filter(c=>c.trackId===track.id);
  html+=`<div class="tl-row"><div class="tl-label"><b>${esc(track.name)}</b><small>${these.length} clips</small><button class="track-select-button" data-select-track-below="${esc(track.id)}" title="このトラックと下の全トラックのクリップを選択">ここから下を全選択</button></div><div class="tl-lane" data-drop-track="${esc(track.id)}" title="音声をここへドロップして配置" style="width:${width}px;--fiveSec:${major}px;--oneSec:${minor}px" data-lane="${esc(track.id)}">`;
  for(const c of these){
   const a=asset(c.assetId);if(!a)continue;
   const w=Math.max(pxPerSec<4?2:pxPerSec<12?6:14,Number(c.duration||0)*pxPerSec),left=Math.max(0,Number(c.start||0)*pxPerSec);
   const label=c.lineId?`${lineById(c.lineId)?.speaker||'台詞'} · ${(lineById(c.lineId)?.text||'').slice(0,12)}`:a.name;
   html+=`<div class="tl-clip ${esc(track.kind)} ${selectedClips.has(c.id)?'selected':''}" data-clip="${esc(c.id)}" role="button" tabindex="0" aria-pressed="${selectedClips.has(c.id)}" title="${esc(a.name)}" style="left:${left}px;width:${w}px"><div class="clip-bars"></div><span class="clip-text">${esc(label)}</span>${w>=20?'<div class="clip-handle" data-resize="1"></div>':''}</div>`;
  }
  html+='</div></div>';
 }
 html+='<div id="playhead" class="tl-playhead" style="left:'+(TIMELINE_LABEL_WIDTH+currentTime*pxPerSec)+'px"></div>';
 $('timeline').innerHTML=html;
 $('timelineInfo').textContent=`${clips.length} clips · ${fmt(duration)} · ${state.tracks.length} tracks`;
 $('zoomNumber').textContent=`${Number(pxPerSec.toPrecision(3))} px/s`;
 $('zoomScale').textContent=`目盛: ${timelineTickName(tick)}`;
 $('zoom').value=timelineZoomToSlider(pxPerSec);
 paintClipSelection();
 updatePlayhead();
}
function updatePlayhead(){
 const ph=$('playhead');
 if(ph)ph.style.left=(TIMELINE_LABEL_WIDTH+currentTime*pxPerSec)+'px';
 $('timeDisplay').textContent=fmt(currentTime);
}
// Scroll the timeline content, not the clips themselves: keep their actual
// timestamps unchanged.  During playback the playhead stays near the right
// third of the visible range, while every lane scrolls together.
function followPlaybackPosition(){
 if(!playing)return;
 const shell=document.querySelector('.timeline-shell');
 const width=shell?.clientWidth||0;
 if(width<=TIMELINE_LABEL_WIDTH+60)return;
 const x=TIMELINE_LABEL_WIDTH+currentTime*pxPerSec;
 const relative=x-shell.scrollLeft;
 const left=TIMELINE_LABEL_WIDTH+12;
 const right=TIMELINE_LABEL_WIDTH+(width-TIMELINE_LABEL_WIDTH)*.72;
 if(relative<left||relative>right)
  shell.scrollLeft=Math.max(0,x-right);
}
const models={irodori:[['Aratako/Irodori-TTS-v4.1-Small','Irodori v4.1 Small'],['Aratako/Irodori-TTS-v4-Large','Irodori v4 Large (FP32)'],['Aratako/Irodori-TTS-v4-Large-Quantized/int8-weight-only','Irodori Large INT8 (RTX 4070向け)'],['Aratako/Irodori-TTS-v4-Large-Quantized/int4-weight-only','Irodori Large INT4'],['phasefield-audio/Irodori-TTS-v4.1-Anime','Irodori v4.1 Anime'],['phasefield-audio/Irodori-TTS-v4.1-Anime/int8-weight-only','Irodori Anime INT8']],qwen:[['Qwen/Qwen3-TTS-12Hz-1.7B-Base','Qwen3 Base 1.7B'],['Qwen/Qwen3-TTS-12Hz-0.6B-Base','Qwen3 Base 0.6B'],['Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign','Qwen3 Voice Design 1.7B']]};
function modelSelect(engine,value){return `<select id="lineModel"><option value="">モデルを選択</option>${(models[engine]||models.irodori).map(([id,n])=>`<option value="${esc(id)}" ${value===id?'selected':''}>${esc(n)}</option>`).join('')}<option value="custom" ${value&&!models[engine]?.some(x=>x[0]===value)?'selected':''}>その他（直接入力）</option></select><input id="modelCustom" placeholder="Hugging Face モデルID / 量子化モデルのサブフォルダ" style="${value&&!models[engine]?.some(x=>x[0]===value)?'':'display:none;'}margin-top:6px" value="${value&&!models[engine]?.some(x=>x[0]===value)?esc(value):''}">`}
function getLineTtsSource(l){
  const v=voiceFor(l.speaker);
  if(l.ttsSource==='line')return 'line';
  if(l.ttsSource==='cast')return v?'cast':'line';
  if(!v)return 'line';
  if((l.engine||'irodori')!=='irodori'||(l.model&&l.model!=='Aratako/Irodori-TTS-v4.1-Small')||(l.mode&&l.mode!=='clone'))return 'line';
  return 'cast';
}
function displayLineTts(l){
  const v=voiceFor(l.speaker);
  const source=getLineTtsSource(l), c=source==='cast'?v:l;
  const engine=c?.engine||'irodori',mode=c?.mode||'clone';
  const model=engine==='qwen'&&mode==='design'?'Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign':(c?.model||'Aratako/Irodori-TTS-v4.1-Small');
  return {source,engine,mode,model};
}
function renderInspector(){if(!state)return;const inspector=$('inspector'),l=lineById(selectedLine),c=clipById(selectedClip);
 if(c){const a=asset(c.assetId);inspector.innerHTML=`<div class="inspector-grid"><div class="field wide"><label>選択中クリップ（${selectedClips.size}件選択・ドラッグでまとめて移動）</label><div style="font-weight:700">${esc(a?.name||'不明')}</div></div><div class="field"><label>開始（秒）</label><input type="number" id="clipStart" min="0" step="0.1" value="${fnum(c.start,2)}"></div><div class="field"><label>長さ（秒）</label><input type="number" id="clipDuration" min="0.01" step="0.1" value="${fnum(c.duration,2)}"></div><div class="field"><label>音量（%）</label><input type="number" id="clipGain" min="0" max="800" step="5" value="${Math.round((c.gain??1)*100)}"></div><div class="field"><label>フェードイン（秒）</label><input type="number" id="clipFadeIn" min="0" step="0.1" value="${fnum(c.fadeIn,1)}"></div><div class="field"><label>フェードアウト（秒）</label><input type="number" id="clipFadeOut" min="0" step="0.1" value="${fnum(c.fadeOut,1)}"></div><div class="field"><label>トラック</label><select id="clipTrack">${state.tracks.map(t=>`<option value="${esc(t.id)}" ${t.id===c.trackId?'selected':''}>${esc(t.name)}</option>`).join('')}</select></div><div class="field"><label>前をカット（秒）</label><input type="number" id="clipTrimStart" min="0" step="0.1" value="${fnum(c.trimStart,1)}"></div><div class="field"><label>末尾カット（秒）</label><input type="number" id="clipTrimEnd" min="0" step="0.1" value="${fnum(c.trimEnd,1)}"></div><div class="field wide"><label><input type="checkbox" id="clipLoop" ${c.loop?'checked':''}> ループ再生（BGM・環境音）</label></div>${c.trackId!=='dialogue'?`<div class="field wide"><label>台詞への追従</label><select id="clipAnchor"><option value="">なし（固定時刻）</option>${activeLines().map(x=>`<option value="${esc(x.id)}" ${c.anchorLineId===x.id?'selected':''}>${esc(x.speaker)}：${esc(x.text.slice(0,24))}</option>`).join('')}</select></div>`:''}<div class="field wide"><label><input type="checkbox" id="clipPinned" ${c.pinned?'checked':''}> 自動整列から除外（位置を固定）</label></div></div><div class="inspect-actions">${button('▶ この位置から再生','id="playClipBtn"')}${button('このクリップを削除','id="deleteClipBtn"','danger')}${l?button('この台詞の生成設定を開く','id="inspectLineBtn"'):''}</div>`;return;}
 if(l){const hasAudio=l.takes?.length>0, effective=displayLineTts(l),isOverride=effective.source==='line',statusLabel={ready:'✓ 音声あり',stale:'要再生成',missing:'未生成'}[l.status]||'未生成';inspector.innerHTML=`<section class="take-panel" aria-label="この台詞の音声"><div class="take-head"><span class="take-title">音声 · ${esc(l.speaker||'話者未設定')}</span><span class="line-status ${esc(l.status)}">${statusLabel}</span></div>${hasAudio?`<div class="field"><label>使うテイク</label><select id="lineTake"><option value="">未選択</option>${(l.takes||[]).map((t,i)=>`<option value="${esc(t.id)}" ${t.id===l.selectedTakeId?'selected':''}>テイク ${i+1} · ${esc(t.engine||'import')} · ${esc((t.createdAt||'').slice(0,16))}</option>`).join('')}</select></div>`:'<p class="inspector-note">まだ音声がありません。</p>'}<div class="inspect-actions take-actions">${button('✦ この台詞を生成',`id="generateLineBtn"`,'primary')}${hasAudio?button('▶ 試聴',`id="previewTakeBtn"`)+button('↓ このテイクを音声ファイルで保存',`id="downloadTakeBtn"`):''}</div></section>${linePronunciationHtml(l)}<div class="inspector-grid"><div class="field wide"><label>台詞</label><div style="font-size:12px">${esc(l.text.slice(0,90)||'（空の台詞）')}</div></div><div class="field wide"><label>声の設定元</label><select id="lineTtsSource"><option value="cast" ${!isOverride?'selected':''}>キャスト設定を使う（推奨）</option><option value="line" ${isOverride?'selected':''}>この台詞だけ個別設定</option></select></div>${!isOverride?`<div class="field wide"><label>実際に使用するモデル</label><div class="inspector-note">${esc(effective.model)}（${esc(voiceLabel(voiceFor(l.speaker)).split(' · ')[0])}・「02 キャスト・声」で変更できます）</div></div>`:`<div class="field"><label>TTSエンジン</label><select id="lineEngine"><option value="irodori" ${l.engine!=='qwen'?'selected':''}>Irodori-TTS</option><option value="qwen" ${l.engine==='qwen'?'selected':''}>Qwen3-TTS</option></select></div><div class="field"><label>声の方式</label><select id="lineMode"><option value="clone" ${l.mode!=='design'?'selected':''}>参照音声</option><option value="design" ${l.mode==='design'?'selected':''}>Voice Design</option></select></div><div class="field wide"><label>モデル</label>${modelSelect(l.engine,l.model)}</div>`}</div><div class="inspect-actions">${button('＋ 手持ちの音声を割り当て',`id="attachPickerBtn"`)}${button('✦ 演技指示をAIで提案',`id="suggestCaptionBtn"`)}${hasAudio&&l.selectedTakeId?button('🗑 選択テイクを削除',`id="deleteSelectedTakeBtn"`,'danger'):''}</div><p class="inspector-note" style="margin-top:10px">生成時は、キャスト設定の参照音声・声の説明を利用します。モデルと台詞の変更はテイク履歴に影響せず、再生成を促します。</p>`;return}
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
  bulkMode=mode;bulkCast=cast;bulkKind=mode==='reference'?'reference':mode==='library'?libraryTab:'voice';
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
function downloadExcelTemplate(){
 // Works even before any lines exist; the server does not need a project ID.
 const link=document.createElement('a');
 link.href='/api/excel/template';
 link.download='voice_drama_template.xlsx';
 document.body.appendChild(link);
 link.click();
 link.remove();
}
function chooseExcelImport(){
 excelImportMode='merge';
 modal(`<h2>Excelシナリオの取り込み方法</h2><p>読み込む前に、更新方法を選択してください。</p>
  <div class="excel-template-guide"><b>初めてExcelで台本を作る場合</b><p>専用のひな形をダウンロードして、2行目から話者とセリフを入力してください。列名を覚える必要はありません。</p>${button('↓ 一括取り込み用Excelひな形をダウンロード','id="downloadExcelTemplateModalBtn"')}</div>
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
// One dialog per Voice Design session: results can be regenerated, compared
// and chosen without closing it. history: [{assetId,text}] in creation order.
let designSession=null;
async function designCharacterVoice(index){const voice=state.voices[index];if(!voice)return;const sampleText=prompt('作りたい声で読み上げるサンプル台詞を入力してください','こんにちは！ 今日はどんな素敵なことが起こるのか、楽しみですね。');if(!sampleText?.trim())return;
 designSession={speaker:voice.name,sampleText:sampleText.trim(),history:[],current:null};
 await startVoiceDesign(designSession);
}
async function startVoiceDesign(session){
 try{
  await saveNow();
  // Regenerating replaces the result in use; the first run adds a reference.
  const job=await req(`/api/projects/${state.id}/voices/design`,json('POST',{speaker:session.speaker,sampleText:session.sampleText,replaceAssetId:session.current}));
  showJob(job.id,session);
 }catch(e){toast(e.message,true);if(session===designSession)renderDesignPanel(session,false)}
}
function regenerateVoiceDesign(){
 const session=designSession,text=$('designSampleText')?.value.trim();
 if(!session)return;
 if(!text)return toast('読み上げるサンプル台詞を入力してください',true);
 session.sampleText=text;
 startVoiceDesign(session);
}
function useDesignCandidate(assetId){
 const session=designSession,voice=state.voices.find(v=>v.name===session?.speaker),chosen=session?.history.find(c=>c.assetId===assetId);
 if(!voice||!chosen)return;
 const results=new Set(session.history.map(c=>c.assetId));
 voice.refs=[...(voice.refs||[]).filter(id=>!results.has(id)),assetId];
 voice.refText=chosen.text;session.current=assetId;
 state.lines.filter(l=>l.speaker===voice.name&&getLineTtsSource(l)==='cast').forEach(l=>l.status=l.selectedTakeId?'stale':'missing');
 dirtyState();renderAll();renderDesignPanel(session,false);
 toast(`${session.history.indexOf(chosen)+1}回目の声を${voice.name}の参照音声にしました`);
}
function renderDesignPanel(session,running){
 const panel=$('designPanel');if(!panel||session!==designSession)return;
 const title=$('jobTitle');if(title)title.textContent=(running?'声をデザイン中：':'Voice Designの結果：')+session.speaker;
 panel.innerHTML=`<div class="field"><label for="designSampleText">サンプル台詞（変更してから再生成できます）</label><textarea id="designSampleText" rows="2" maxlength="500">${esc(session.sampleText)}</textarea></div>
  ${session.history.length?`<div class="design-candidates">${session.history.map((c,i)=>`<div class="ref-row"><span title="${esc(c.text)}">${i+1}回目「${esc(c.text.slice(0,24))}${c.text.length>24?'…':''}」${c.assetId===session.current?' <b>使用中</b>':''}</span><div>${button('▶ 試聴',`data-preview="${esc(c.assetId)}"`)}${c.assetId===session.current?'':button('この声を使う',`data-design-use="${esc(c.assetId)}" ${running?'disabled':''}`)}</div></div>`).join('')}</div>`:''}
  <p class="small muted">「✦ 再生成」は、使用中の結果をこのキャストの参照音声から外して新しい結果に置き換えます。外した音声は削除せず「参照音声の管理」に残し、ここの「この声を使う」で戻せます。</p>`;
 panel.querySelectorAll('[data-design-use]').forEach(b=>b.onclick=()=>useDesignCandidate(b.dataset.designUse));
 const actions=panel.closest('.modal')?.querySelector('.modal-actions');
 let again=$('redesignVoiceBtn');
 if(!again&&actions){again=document.createElement('button');again.id='redesignVoiceBtn';again.className='btn primary';again.textContent='✦ 再生成';again.onclick=regenerateVoiceDesign;actions.insertBefore(again,actions.lastElementChild)}
 if(again)again.disabled=running;
}
// Several scenes and/or characters at once: only what is missing, or every
// line again. speakers=null means every speaker.
function sceneGenerationTargets(sceneIds,scope,speakers=null){
 const order=new Map(state.scenes.map((s,i)=>[s.id,i])),chosen=new Set(sceneIds),who=speakers&&new Set(speakers);
 return state.lines.map((l,i)=>[l,i]).filter(([l])=>chosen.has(l.sceneId)&&l.text.trim()&&(!who||who.has(l.speaker))&&(scope==='all'||l.status!=='ready'))
  .sort((a,b)=>order.get(a[0].sceneId)-order.get(b[0].sceneId)||a[1]-b[1]).map(([l])=>l);
}
// Speakers whose lines are voiced by this shared character in this work.
function speakersUsing(character){
 return [...new Set(state.lines.map(l=>l.speaker).filter(Boolean))].filter(sp=>voiceFor(sp)?.id===character.id);
}
// preset.speakers: open with only these characters ticked and every scene.
// preset.pickSpeakers: every scene, no character ticked yet (choose one).
function openSceneGeneration(preset={}){
 const speakerNames=[...new Set(state.lines.map(l=>l.speaker).filter(Boolean))];
 const sceneStats=state.scenes.map(s=>{const lines=state.lines.filter(l=>l.sceneId===s.id&&l.text.trim());return {id:s.id,name:s.name,total:lines.length,pending:lines.filter(l=>l.status!=='ready').length}});
 const byCharacter=preset.speakers||preset.pickSpeakers;
 const pickedScenes=new Set(byCharacter?sceneStats.map(x=>x.id):sceneStats.filter(x=>x.pending).map(x=>x.id));
 if(!pickedScenes.size&&selectedScene)pickedScenes.add(selectedScene);
 const pickedSpeakers=new Set(preset.speakers||(preset.pickSpeakers?[]:speakerNames));
 const voiceOf=sp=>{const v=voiceFor(sp);return v?`${CH()?.byId(characterRegistry,v.id)?'共通キャラクター':'この作品のキャスト'}「${v.name}」の声`:'標準の声'};
 modal(`<h2>まとめて生成（シーン・キャラクターを選ぶ）</h2><p>選んだシーンの、選んだキャラクターの台詞をまとめて音声にします。40件ずつ順番に処理し、終わった分から台本とタイムラインに反映します。途中で停止でき、画面を再読み込みしても進捗に再接続できます。</p>
  <div class="gen-pick-head"><b class="small">シーン</b><span class="buttonline">${button('すべて選択','id="sceneGenAll"')}${button('未生成があるシーン','id="sceneGenPending"')}${button('選択解除','id="sceneGenNone"')}</span></div>
  <div class="scene-gen-list">${sceneStats.map(x=>`<label class="cast-copy-item"><input type="checkbox" data-scene-gen="${esc(x.id)}" ${pickedScenes.has(x.id)?'checked':''}><span><b>${esc(x.name)}</b><small>台詞 ${x.total}件 · 未生成・要再生成 ${x.pending}件</small></span></label>`).join('')}</div>
  <div class="gen-pick-head"><b class="small">キャラクター（話者）</b><span class="buttonline">${button('すべて選択','id="speakerGenAll"')}${button('選択解除','id="speakerGenNone"')}</span></div>
  <div class="scene-gen-list">${speakerNames.map(sp=>`<label class="cast-copy-item"><input type="checkbox" data-speaker-gen="${esc(sp)}" ${pickedSpeakers.has(sp)?'checked':''}><span><b>${esc(sp)}</b><small data-speaker-gen-count="${esc(sp)}"></small><small>${esc(voiceOf(sp))}</small></span></label>`).join('')||'<p class="small muted">台詞に話者がいません。</p>'}</div>
  <div class="scene-gen-scope"><b class="small">生成する台詞</b>
   <label class="batch-check"><input type="radio" name="sceneGenScope" value="pending" checked> 未生成・要再生成の台詞だけ</label>
   <label class="batch-check"><input type="radio" name="sceneGenScope" value="all"> 選んだ台詞をすべて（生成済みも作り直します。以前のテイクは残ります）</label></div>
  <p id="sceneGenSummary" class="small muted"></p>
  <div class="modal-actions">${button('キャンセル','data-close="1"')}${button('次へ（モデルを確認）','id="sceneGenNext"','primary')}</div>`);
 const boxes=kind=>[...document.querySelectorAll(`[data-${kind}-gen]`)];
 const scope=()=>document.querySelector('input[name="sceneGenScope"]:checked')?.value||'pending';
 const scenes=()=>boxes('scene').filter(b=>b.checked).map(b=>b.dataset.sceneGen);
 const speakers=()=>boxes('speaker').filter(b=>b.checked).map(b=>b.dataset.speakerGen);
 const targets=()=>sceneGenerationTargets(scenes(),scope(),speakers());
 const refresh=()=>{
  const inScenes=new Set(scenes());
  for(const el of document.querySelectorAll('[data-speaker-gen-count]')){
   const lines=state.lines.filter(l=>l.speaker===el.dataset.speakerGenCount&&inScenes.has(l.sceneId)&&l.text.trim());
   el.textContent=`選んだシーンの台詞 ${lines.length}件 · 未生成・要再生成 ${lines.filter(l=>l.status!=='ready').length}件`;
  }
  const n=targets().length;
  $('sceneGenSummary').textContent=n?`${scenes().length}シーン・${speakers().length}人の台詞から${n}件を生成します。`
   :!speakers().length?'生成するキャラクターを選んでください。':'生成する台詞がありません。シーン・キャラクター・「生成する台詞」を選び直してください。';
  $('sceneGenNext').disabled=!n;
 };
 const set=(kind,test)=>{boxes(kind).forEach(b=>{b.checked=test(b)});refresh()};
 const pending=id=>sceneStats.find(x=>x.id===id)?.pending>0;
 $('sceneGenAll').onclick=()=>set('scene',()=>true);
 $('sceneGenPending').onclick=()=>set('scene',b=>pending(b.dataset.sceneGen));
 $('sceneGenNone').onclick=()=>set('scene',()=>false);
 $('speakerGenAll').onclick=()=>set('speaker',()=>true);
 $('speakerGenNone').onclick=()=>set('speaker',()=>false);
 document.querySelectorAll('[data-scene-gen],[data-speaker-gen],input[name="sceneGenScope"]').forEach(el=>el.onchange=refresh);
 $('sceneGenNext').onclick=()=>generate(targets().map(l=>l.id));
 refresh();
}
// A long run attaches takes as it goes; follow it unless the user is editing.
async function refreshDuringJob(){
 if(!state||dirty||saving||playing)return;
 const focused=document.activeElement;
 if(focused&&/^(INPUT|TEXTAREA|SELECT)$/.test(focused.tagName)&&!focused.closest('.modal'))return;
 try{const fresh=await req(`/api/projects/${state.id}`);if(dirty||saving||fresh.id!==state.id)return;state=fresh;clearEditHistory();renderAll()}catch(_){}
}
async function resumeGeneration(){
 try{const job=await req(`/api/projects/${state.id}/generate/active`);if(job?.id){showJob(job.id);toast('実行中の音声生成に再接続しました')}}catch(_){}
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
function showJob(jid,design=null){if(playing)pause();else stopNodes();clearInterval(jobTimer);modal(`<h2 id="jobTitle">${design?'声をデザイン中：'+esc(design.speaker):'音声生成中'}</h2><p id="jobMessage">TTSモデルを読み込んでいます…</p><div class="progress"><div id="jobProgress" style="width:0%"></div></div><div id="jobStats" class="small muted" style="margin-top:10px">処理待ち</div>${design?'<div id="designPanel" class="design-panel"></div>':''}<div class="modal-actions">${button('■ 生成を停止',`id="cancelJobBtn"`)}${button('閉じる','data-close="1"')}</div>`);$('cancelJobBtn').onclick=async()=>{await req(`/api/jobs/${jid}/cancel`,{method:'POST'});toast('停止要求を送信しました')};
 if(design)renderDesignPanel(design,true);
 let seen=0;const check=async()=>{try{const j=await req('/api/jobs/'+jid);if(j.status==='running'&&!design&&j.completed!==seen){seen=j.completed;await refreshDuringJob()}if($('jobMessage')){$('jobMessage').textContent=j.message||j.status;$('jobStats').textContent=`${j.completed||0} / ${j.total||0} 件　${j.errors?.length||0} エラー`;$('jobProgress').style.width=((j.total?j.completed/j.total:0)*100)+'%'}if(j.status!=='running'){clearInterval(jobTimer);await saveNow();state=await req(`/api/projects/${state.id}`);clearEditHistory();renderAll();if(j.referenceAssetId && $('jobStats')){const actions=$('jobStats').closest('.modal')?.querySelector('.modal-actions');if(actions&&!$('previewDesignedReferenceBtn')){const b=document.createElement('button');b.id='previewDesignedReferenceBtn';b.className='btn primary';b.textContent='▶ 生成した参照音声を試聴';b.onclick=()=>playPreview(j.referenceAssetId);actions.prepend(b)}}if(design){if(j.referenceAssetId&&!design.history.some(c=>c.assetId===j.referenceAssetId)){design.history.push({assetId:j.referenceAssetId,text:design.sampleText});design.current=j.referenceAssetId}renderDesignPanel(design,false)}if(j.errors?.length)toast(`生成完了。${j.errors.length}件でエラー`,true);else if(j.status==='error')toast('生成エラー: '+j.message,true);else toast('生成処理が終了しました');if($('jobMessage'))$('jobMessage').textContent=j.status==='error'?'失敗: '+j.message:`${j.message||'終了'}（${j.completed}件処理）${j.errors?.length?'　最初のエラー: '+String(j.errors[0].error||'').slice(0,300):''}`;}}catch(e){clearInterval(jobTimer);toast(e.message,true)}};jobTimer=setInterval(check,1500);check()}
async function renderAudio(){
 try{
  await saveNow();
  let coverData=null,coverReadVersion=0,exporting=false;
  modal(`<h2>完成音声を書き出す</h2><p>すべてのトラックを同時にミックスし、フェードやループを反映します。</p>
   <div class="scene-gen-scope"><b class="small">出力範囲</b>
    <label class="batch-check"><input type="radio" name="exportScope" value="scene" checked> 表示中のシーンだけ（${esc(sceneName(selectedScene))}）</label>
    <label class="batch-check"><input type="radio" name="exportScope" value="join"> 複数のシーンをつなげて1本の完成音声にする</label></div>
   <div id="exportJoin" class="export-join" hidden>
    <div class="gen-pick-head"><b class="small">つなげるシーンと順番</b><span class="buttonline">${button('すべて選択','id="exportJoinAll"')}${button('音声があるシーン','id="exportJoinAudio"')}${button('選択解除','id="exportJoinNone"')}</span></div>
    <div class="export-join-bulk">
     <label>シーンの間の無音をまとめて設定 <input type="number" id="exportGapAll" min="0" max="60" step="0.5"> 秒</label>${button('すべての切れ目に適用','id="exportGapApply"')}
     <label>切り替わりの試聴：前後それぞれ <input type="number" id="exportPreviewSeconds" min="1" max="30" step="1"> 秒</label>
    </div>
    <div id="exportSceneList" class="scene-gen-list export-scene-list"></div>
    <p id="exportJoinSummary" class="small muted"></p>
   </div>
   <div class="field"><label for="exportFormat">出力形式</label><select id="exportFormat"><option value="wav">WAV / PCM16</option><option value="mp3">MP3 / 192kbps（FFmpeg必要）</option></select></div>
   <div class="field"><label for="exportTitle">作品タイトル</label><input type="text" id="exportTitle" maxlength="160" value="${esc(state.title||'')}" placeholder="ボイスドラマのタイトル"></div>
   <div class="field"><label for="exportArtist">制作名義・アーティスト（任意）</label><input type="text" id="exportArtist" maxlength="160" placeholder="例：制作サークル名"></div>
   <div class="field"><label for="exportCover">ジャケット画像（任意）</label><input type="file" id="exportCover" accept="image/jpeg,image/png,.jpg,.jpeg,.png">
    <p class="small muted" id="exportCoverHint">JPEG / PNG、5MB以下。画像はMP3に埋め込まれます。WAVには埋め込めません。</p>
    <div class="export-cover-preview" id="exportCoverPreview" hidden><img id="exportCoverImage" alt="選択したジャケット画像のプレビュー"><button type="button" class="btn ghost mini" id="exportCoverClear">画像を取り消す</button></div></div>
   <div class="field"><label>共通の保存先</label><p class="small muted"><code>VoiceDramaStudio/exports/</code> に指定したタイトルだけのファイル名で保存します（例: 作品名.mp3）。同名のファイルがある場合は上書きを確認します。</p>
   <button type="button" class="btn ghost" id="openExportFolderBtn">📁 保存先フォルダを開く</button></div>
   <div class="modal-actions">${button('キャンセル','data-close="1"')}${button('完成音声を書き出す','id="renderConfirmBtn"','primary')}</div>`);
  const confirm=$('renderConfirmBtn'),picker=$('exportCover'),preview=$('exportCoverPreview');
  // Joining scenes: which ones, their order, and the silence after each
  // scene. Saved with the project (settings.sceneJoin) for the next export.
  const sceneLength=id=>Math.max(0,...state.clips.filter(c=>c.sceneId===id).map(c=>Number(c.start||0)+Number(c.duration||0)));
  const saved=state.settings?.sceneJoin||{},known=new Set(state.scenes.map(sc=>sc.id));
  const joinOrder=[...(saved.order||[]).filter(id=>known.has(id))];
  for(const sc of state.scenes)if(!joinOrder.includes(sc.id))joinOrder.push(sc.id);
  const joinPicked=new Set(saved.picked?saved.picked.filter(id=>known.has(id)):joinOrder.filter(id=>sceneLength(id)>0));
  const gaps={...(saved.gaps||{})};let defaultGap=saved.defaultGap??1,previewSeconds=saved.previewSeconds??5;
  const clampGap=v=>Math.min(60,Math.max(0,Number(v)||0));
  const gapAfter=id=>gaps[id]??defaultGap;
  const scope=()=>document.querySelector('input[name="exportScope"]:checked')?.value||'scene';
  const joinIds=()=>joinOrder.filter(id=>joinPicked.has(id));
  const nextPicked=id=>{const ids=joinIds();return ids[ids.indexOf(id)+1]};
  const saveJoin=()=>{state.settings={...state.settings,sceneJoin:{order:[...joinOrder],picked:joinIds(),gaps:{...gaps},defaultGap,previewSeconds}};dirtyState('sceneJoin')};
  const summarize=()=>{
   const ids=joinIds(),total=ids.reduce((sum,id,i)=>sum+sceneLength(id)+(i<ids.length-1?gapAfter(id):0),0);
   const route=ids.map((id,i)=>i<ids.length-1?`${sceneName(id)} →（無音${gapAfter(id)}秒）→ `:sceneName(id)).join('');
   $('exportJoinSummary').textContent=ids.length?`${ids.length}シーンをこの順につなげます（合計 約${fmt(total)}）：${route}`:'つなげるシーンを選んでください。';
  };
  const renderJoin=()=>{
   $('exportJoin').hidden=scope()!=='join';
   $('exportGapAll').value=defaultGap;$('exportPreviewSeconds').value=previewSeconds;
   $('exportSceneList').innerHTML=joinOrder.map((id,i)=>{const len=sceneLength(id),next=joinPicked.has(id)&&nextPicked(id);return `<div class="export-scene-row"><label class="cast-copy-item"><input type="checkbox" data-export-scene="${esc(id)}" ${joinPicked.has(id)?'checked':''}><span><b>${joinPicked.has(id)?joinIds().indexOf(id)+1+'. ':''}${esc(sceneName(id))}</b><small>${len?'長さ '+fmt(len):'音声がありません'}</small></span></label><span class="export-scene-move">${button('↑',`data-export-move="${i}:-1" title="前へ" ${i?'':'disabled'}`)}${button('↓',`data-export-move="${i}:1" title="後ろへ" ${i<joinOrder.length-1?'':'disabled'}`)}</span></div>${next?`<div class="export-gap-row"><span>⏸ 次の「${esc(sceneName(next))}」まで無音</span><input type="number" min="0" max="60" step="0.5" data-export-gap="${esc(id)}" value="${gapAfter(id)}" aria-label="${esc(sceneName(id))}のあとの無音（秒）"><span>秒</span>${button('▶ 切り替わりを試聴',`data-export-preview="${esc(id)}" title="「${esc(sceneName(id))}」の終わり→無音→「${esc(sceneName(next))}」の始まりを、書き出しと同じ音で再生します"`)}</div>`:''}`}).join('');
   summarize();
  };
  // Audition one scene change, rendered by the same mixer as the export.
  let previewAudio=null,previewButton=null;
  const stopPreview=()=>{if(previewAudio){previewAudio.pause();URL.revokeObjectURL(previewAudio.src);previewAudio=null}if(previewButton?.isConnected)previewButton.textContent='▶ 切り替わりを試聴';previewButton=null};
  const previewTransition=async(button,id)=>{
   const wasPlaying=previewButton===button;stopPreview();if(wasPlaying)return;
   const next=nextPicked(id);if(!next)return;
   if(playing)pause();else stopNodes();
   previewButton=button;button.textContent='準備中…';
   try{
    await saveNow();
    const r=await fetch(`/api/projects/${state.id}/render/transition`,json('POST',{fromSceneId:id,toSceneId:next,gap:gapAfter(id),before:previewSeconds,after:previewSeconds}));
    if(!r.ok){let m=await r.text();try{m=JSON.parse(m).detail||m}catch(_){}throw new Error(m)}
    if(previewButton!==button)return;
    previewAudio=new Audio(URL.createObjectURL(await r.blob()));
    previewAudio.onended=stopPreview;await previewAudio.play();
    button.textContent='■ 試聴を止める';
    toast(`「${sceneName(id)}」の終わり → 無音${gapAfter(id)}秒 → 「${sceneName(next)}」の始まり を再生しています`);
   }catch(e){stopPreview();toast('切り替わりを試聴できません: '+e.message,true)}
  };
  document.querySelectorAll('input[name="exportScope"]').forEach(el=>el.onchange=renderJoin);
  $('exportSceneList').onchange=e=>{const id=e.target.dataset.exportScene;if(!id)return;stopPreview();if(e.target.checked)joinPicked.add(id);else joinPicked.delete(id);saveJoin();renderJoin()};
  $('exportSceneList').oninput=e=>{const id=e.target.dataset.exportGap;if(!id)return;gaps[id]=clampGap(e.target.value);saveJoin();summarize()};
  $('exportSceneList').onclick=e=>{
   const preview=e.target.closest('[data-export-preview]');if(preview)return previewTransition(preview,preview.dataset.exportPreview);
   const b=e.target.closest('[data-export-move]');if(!b)return;stopPreview();const [i,d]=b.dataset.exportMove.split(':').map(Number);[joinOrder[i],joinOrder[i+d]]=[joinOrder[i+d],joinOrder[i]];saveJoin();renderJoin();
  };
  $('exportGapApply').onclick=()=>{defaultGap=clampGap($('exportGapAll').value);for(const id of joinOrder)delete gaps[id];saveJoin();renderJoin();toast(`すべての切れ目の無音を${defaultGap}秒にしました`)};
  $('exportPreviewSeconds').onchange=()=>{previewSeconds=Math.min(30,Math.max(1,Math.round(Number($('exportPreviewSeconds').value)||5)));saveJoin();renderJoin()};
  const pickScenes=test=>{stopPreview();joinPicked.clear();joinOrder.filter(test).forEach(id=>joinPicked.add(id));saveJoin();renderJoin()};
  $('exportJoinAll').onclick=()=>pickScenes(()=>true);
  $('exportJoinAudio').onclick=()=>pickScenes(id=>sceneLength(id)>0);
  $('exportJoinNone').onclick=()=>pickScenes(()=>false);
  $('modalBackdrop').addEventListener('click',function stopOnClose(e){if(!$('exportJoin')?.isConnected||e.target.closest('[data-close]')||e.target.id==='modalBackdrop'){stopPreview();$('modalBackdrop').removeEventListener('click',stopOnClose)}});
  renderJoin();
  $('openExportFolderBtn').onclick=async()=>{
   try{await req('/api/exports/open-folder',{method:'POST'})}
   catch(e){toast('保存先フォルダを開けません: '+e.message,true)}
  };
  picker.addEventListener('change',()=>{
   const version=++coverReadVersion;
   coverData=null;preview.hidden=true;
   const file=picker.files?.[0];if(!file){if(!exporting)confirm.disabled=false;return;}
   if(!['image/jpeg','image/png'].includes(file.type)||file.size>5*1024*1024||file.size===0){
    toast('ジャケット画像は5MB以下のJPEGまたはPNGを選択してください',true);picker.value='';if(!exporting)confirm.disabled=false;return;
   }
   if($('exportFormat').value!=='mp3'){
    $('exportFormat').value='mp3';
    toast('ジャケット画像を埋め込むため、出力形式をMP3に切り替えました');
   }
   confirm.disabled=true;
   const reader=new FileReader();
   reader.onload=()=>{
    if(!picker.isConnected||version!==coverReadVersion)return;
    coverData=String(reader.result||'');
    $('exportCoverImage').src=coverData;preview.hidden=false;if(!exporting)confirm.disabled=false;
   };
   reader.onerror=()=>{if(!picker.isConnected||version!==coverReadVersion)return;picker.value='';if(!exporting)confirm.disabled=false;toast('画像を読み込めませんでした',true)};
   reader.readAsDataURL(file);
  });
  $('exportCoverClear').onclick=()=>{coverReadVersion++;picker.value='';coverData=null;preview.hidden=true;$('exportCoverImage').removeAttribute('src');if(!exporting)confirm.disabled=false};
  confirm.onclick=async()=>{
   if(exporting)return;
   const format=$('exportFormat').value,join=scope()==='join';
   const title=$('exportTitle').value.trim(),artist=$('exportArtist').value.trim();
   if(!title)return toast('作品タイトルを入力してください',true);
   if(join&&!joinIds().length)return toast('つなげるシーンを選んでください',true);
   if(coverData&&format!=='mp3')return toast('ジャケット画像を埋め込むにはMP3形式を選択してください',true);
   exporting=true;confirm.disabled=true;confirm.textContent='処理中…';
   try{
    const range=join?{sceneIds:joinIds(),sceneGaps:joinIds().slice(0,-1).map(gapAfter)}:{sceneId:selectedScene};
    stopPreview();
    const payload={...range,format,title,artist,coverData};
    const send=overwrite=>req(`/api/projects/${state.id}/render`,json('POST',{...payload,overwrite}));
    let result;
    try{result=await send(false)}
    catch(e){
     if(!e.message.includes('同名ファイルが既に存在します'))throw e;
     const filename=title.toLowerCase().endsWith('.'+format)?title:title+'.'+format;
     if(!window.confirm(`「${filename}」は保存先に既にあります。上書きしますか？`)){
      exporting=false;confirm.disabled=false;confirm.textContent='完成音声を書き出す';return;
     }
     result=await send(true);
    }
    closeModal();const a=document.createElement('a');a.href=result.url;a.download=result.file;
    document.body.append(a);a.click();a.remove();
    toast(`共通exportsに保存しました · ${join?result.info.scenes.length+'シーンをつなげて ':''}${fmt(result.info.duration)} · ${result.info.clips}クリップ`);
   }catch(e){toast(e.message,true);exporting=false;if(confirm.isConnected){confirm.disabled=false;confirm.textContent='再試行'}}
  };
 }catch(e){toast(e.message,true)}
}
async function shareLibraryAssets(ids){
 if(!ids.length)return toast('共有する音声がありません',true);
 try{
  await saveNow();
  const response=await req(`/api/projects/${state.id}/assets/share`,json('POST',{assetIds:ids}));
  sharedAssets=response.assets||[];
  toast(`共通ライブラリへ ${response.added}件登録${response.existing?' · 既存 '+response.existing+'件':''}`);
  updateLibraryResults();
 }catch(e){toast('共通素材へ登録できません: '+e.message,true)}
}
async function importSharedAudio(id){
 const buttonEl=[...document.querySelectorAll('[data-import-shared]')].find(el=>el.dataset.importShared===id);
 if(buttonEl)buttonEl.disabled=true;
 try{
  await saveNow();
  const result=await req(`/api/projects/${state.id}/assets/import-shared/${encodeURIComponent(id)}`,json('POST',{expectedRevision:state.revision}));
  state=result.project;clearEditHistory();renderTimeline();renderInspector();
  librarySource='project';updateLibraryResults();
  toast(result.alreadyImported?'この素材は登録済みです':'この作品の音声ライブラリに取り込みました。マルチトラックにドラッグして使えます');
 }catch(e){toast('共通素材の取り込みに失敗: '+e.message,true);if(buttonEl?.isConnected)buttonEl.disabled=false}
}
function playSharedPreview(id){return playRemotePreview('/api/shared-assets/'+encodeURIComponent(id)+'/audio')}
async function playRemotePreview(url){
 try{
  if(playing)pause();else stopNodes();
  await ensureContext();
  const r=await fetch(url);
  if(!r.ok)throw new Error('素材を読み込めません');
  const buffer=await ctx.decodeAudioData(await r.arrayBuffer());
  const source=ctx.createBufferSource();source.buffer=buffer;source.connect(ctx.destination);source.start();nodes.push(source);
 }catch(e){toast('試聴できません: '+e.message,true)}
}
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
      </div><div class="modal-actions">${button('キャンセル','data-close="1"')}${button('選んだキャストをコピー','id="castCopyConfirmBtn"','primary')}</div>`);
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
async function openProjects(){try{await saveNow();const list=await req('/api/projects');modal(`<h2>プロジェクトを選択</h2><p>プロジェクトごとに台本・音声素材・編集状態を分けて保存します。</p>${list.map(x=>`<button class="project-choice" data-open="${esc(x.id)}">${esc(x.title)}<small>${esc(x.updatedAt||'')}</small></button>`).join('')}<div class="field" style="margin-top:20px"><label>新規プロジェクト</label><input id="newProjectTitle" placeholder="作品タイトルを入力"></div><div class="modal-actions">${button('キャンセル','data-close="1"')}${button('この名前で新規作成','id="createProjectBtn"','primary')}</div>`)}catch(e){toast(e.message,true)}}
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
async function playPreview(aid){try{if(playing)pause();else stopNodes();await ensureContext();const b=await decodeAsset(aid),source=ctx.createBufferSource();source.buffer=b;source.connect(ctx.destination);source.start();nodes.push(source)}catch(e){toast('試聴できません: '+e.message,true)}}
async function ensureContext(){if(!ctx)ctx=new (window.AudioContext||window.webkitAudioContext)();await ctx.resume()}
async function decodeAsset(id){if(audioCache.has(id))return audioCache.get(id);const promise=(async()=>{const r=await fetch(`/api/projects/${state.id}/assets/${id}`);if(!r.ok)throw new Error('音声を読み込めません');return ctx.decodeAudioData(await r.arrayBuffer())})();audioCache.set(id,promise);try{return await promise}catch(e){audioCache.delete(id);throw e}}
async function play(){if(playing){pause();return}const clips=activeClips().filter(c=>c.start+c.duration>currentTime);if(!clips.length){toast('再生するクリップがありません',true);return}stopNodes();const requestId=++playbackRequest;try{await ensureContext();const buffers=await Promise.all(clips.map(c=>decodeAsset(c.assetId)));if(requestId!==playbackRequest)return;nodes=[];playStart=ctx.currentTime-currentTime;
 clips.forEach((c,i)=>{const b=buffers[i];if(!b)return;const begin=Number(c.start||0),len=Math.max(0,Number(c.duration||0));const offset=Math.max(0,currentTime-begin);if(offset>=len)return;const lead=Math.max(0,begin-currentTime);const when=ctx.currentTime+lead;const end=Math.max(0.01,b.duration-Number(c.trimEnd||0));const start=Math.max(0,Math.min(end-.001,Number(c.trimStart||0)));const playable=Math.max(.001,end-start);let source=ctx.createBufferSource();source.buffer=b;source.loop=!!c.loop;source.loopStart=start;source.loopEnd=end;let gain=ctx.createGain();const volume=Math.max(0,Math.min(8,Number(c.gain??1)));source.connect(gain);gain.connect(ctx.destination);
  const offsetInside=start+(c.loop?(offset%playable):offset);if(!c.loop && offsetInside>=end)return;
  const playLength=Math.min(len-offset,c.loop?len:end-offsetInside);if(playLength<=0)return;
  const fi=Number(c.fadeIn||0),fo=Number(c.fadeOut||0);gain.gain.setValueAtTime(volume,when);if(fi>0){let k=Math.min(1,offset/fi);gain.gain.setValueAtTime(volume*k,when);if(offset<fi)gain.gain.linearRampToValueAtTime(volume,when+Math.min(fi-offset,playLength))}
  if(fo>0){const fadeBegin=Math.max(0,len-fo);if(offset>=fadeBegin)gain.gain.setValueAtTime(Math.min(volume,volume*Math.max(0,(len-offset)/fo)),when);const whenFade=when+Math.max(0,fadeBegin-offset);if(whenFade<when+playLength){gain.gain.setValueAtTime(volume,whenFade);gain.gain.linearRampToValueAtTime(0,when+playLength)}}
  source.start(when,offsetInside);source.stop(when+playLength);nodes.push(source)});
  playing=true;$('playBtn').textContent='❚❚';updatePlayhead();followPlaybackPosition();animationId=requestAnimationFrame(tick);
 }catch(e){if(requestId!==playbackRequest)return;stopNodes();toast('再生エラー: '+e.message,true)}}
function tick(){
 if(!playing)return;
 currentTime=Math.max(0,ctx.currentTime-playStart);
 updatePlayhead();
 followPlaybackPosition();
 if(currentTime>=Math.max(0,...activeClips().map(c=>c.start+c.duration))+0.15){
  stopNodes();currentTime=0;updatePlayhead();return;
 }
 animationId=requestAnimationFrame(tick);
}
function pause(){if(!playing)return;currentTime=Math.max(0,ctx.currentTime-playStart);stopNodes();updatePlayhead()}
function stopNodes(){playbackRequest++;nodes.forEach(n=>{try{n.stop()}catch{}});nodes=[];playing=false;cancelAnimationFrame(animationId);$('playBtn').textContent='▶'}
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
  if(item&&el){el.style.left=item.start*pxPerSec+'px';el.style.width=Math.max(pxPerSec<4?2:pxPerSec<12?6:14,item.duration*pxPerSec)+'px'}
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
 const libraryButton=target.closest('[data-library-tab]');if(libraryButton){selectLibraryTab(libraryButton.dataset.libraryTab);return}
 const sourceChoice=target.closest('[data-library-source]');
 if(sourceChoice){
  if(!SHAREABLE_LIBRARY_KINDS.has(libraryTab))return;
  librarySource=sourceChoice.dataset.librarySource;
  updateLibraryResults();return;
 }
 if(target.id==='shareAllLibraryBtn')return shareLibraryAssets(state.assets.filter(a=>SHAREABLE_LIBRARY_KINDS.has(a.kind)).map(a=>a.id));
 const sharing=target.closest('[data-share-asset]');if(sharing)return shareLibraryAssets([sharing.dataset.shareAsset]);
 const importing=target.closest('[data-import-shared]');if(importing)return importSharedAudio(importing.dataset.importShared);
 const sharedPreview=target.closest('[data-preview-shared]');if(sharedPreview)return playSharedPreview(sharedPreview.dataset.previewShared);
 const characterPreview=target.closest('[data-preview-character]');if(characterPreview)return playRemotePreview('/api/characters/assets/'+encodeURIComponent(characterPreview.dataset.previewCharacter));
 if(target.id==='clearLibrarySearch'){libraryQueries[libraryTab]='';$('librarySearch').value='';updateLibraryResults();$('librarySearch').focus();return}
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
 const rename=target.closest('[data-rename-scene]');if(rename)return renameScene(rename.dataset.renameScene);
 if(target.id==='confirmDeleteSceneBtn')return confirmSceneDelete(target.dataset.confirmScene);
 const scene=target.closest('[data-scene]');if(scene)return selectScene(scene.dataset.scene);
 const article=target.closest('.script-line');if(article){const lid=article.dataset.id;const del=target.closest('[data-delete]');if(del)return removeLine(lid);const move=target.closest('[data-move]');if(move)return moveLine(lid,Number(move.dataset.move));const action=target.closest('[data-action]');if(action?.dataset.action==='addBelow')return addLine(lid);if(action?.dataset.action==='seek')return jumpToScriptLine(lid);if(action?.dataset.action==='playFrom')return jumpToScriptLine(lid,true);if(action?.dataset.action==='preview'){const l=lineById(lid),t=l?.takes?.find(t=>t.id===l.selectedTakeId);if(lid!==selectedLine||selectedClip!==null)selectLine(lid);if(t)playPreview(t.assetId);return}if(lid!==selectedLine||selectedClip!==null)selectLine(lid)}
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
 if(target.id==='downloadExcelTemplateBtn'||target.id==='downloadExcelTemplateModalBtn')return downloadExcelTemplate();
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
 if(target.id==='importCharactersBtn')return characterEditor?.openImport();
 if(target.id==='addCharacterBtn')return characterEditor?.focusAdd();
 if(target.id==='promoteAllCastsBtn')return characterEditor?.openImport({kind:'drama',id:state.id,keys:state.voices.map(v=>v.name)});
 const promote=target.closest('[data-cast-promote]');if(promote){const v=state.voices[Number(promote.dataset.castPromote)];if(v)return characterEditor?.openImport({kind:'drama',id:state.id,keys:[v.name]})}
 const register=target.closest('[data-register-speaker]');if(register)return characterEditor?.create({name:register.dataset.registerSpeaker});
 if(target.id==='generateScenesBtn')return openSceneGeneration();
 if(target.id==='generateSpeakersBtn')return openSceneGeneration({pickSpeakers:true});
 const speakerGen=target.closest('[data-generate-speaker]');if(speakerGen)return openSceneGeneration({speakers:[speakerGen.dataset.generateSpeaker]});
 if(target.id==='generateMissingBtn')generate(activeLines().filter(x=>x.status!=='ready'&&x.text.trim()).map(x=>x.id));
 if(target.id==='uploadAudioBtn')uploadAudio(libraryTab);
 if(target.id==='insUploadBtn')uploadAudio('sfx');
 if(target.id==='attachPickerBtn')uploadAudio('voice',selectedLine);
 if(target.id==='generateLineBtn')generate([selectedLine]);
 if(target.id==='suggestCaptionBtn')suggestCaption();
 if(target.id==='previewTakeBtn'){const l=lineById(selectedLine);const t=l.takes.find(t=>t.id===l.selectedTakeId);if(t)playPreview(t.assetId)}
 if(target.id==='deleteSelectedTakeBtn')return removeSelectedTake();
 if(target.id==='downloadTakeBtn'){const l=lineById(selectedLine);const t=l?.takes.find(t=>t.id===l.selectedTakeId);if(t)location.href=`/api/projects/${state.id}/assets/${t.assetId}/download`;return}
 if(target.id==='inspectLineBtn'){if(selectedLine)selectLine(selectedLine);return}
 if(target.id==='addTrackBtn'){const name=prompt('トラック名','追加トラック');if(!name)return;const id=newid('track');state.tracks.push({id,name,kind:'sfx'});dirtyState();renderTimeline();renderInspector()}
 if(target.id==='playClipBtn'){currentTime=clipById(selectedClip)?.start||0;play()}
 if(target.id==='deleteClipBtn'){const c=clipById(selectedClip);if(!c)return;state.clips=state.clips.filter(x=>x.id!==c.id);selectedClip=null;dirtyState();renderTimeline();renderInspector()}
}
function handleChange(e){if(historyRestoring)return;handleBatchModalChange(e);const target=e.target;const id=target.id;
 if(id==='castCopyTarget'){updateCastCopyModal();return}
 if(target.dataset.copyCast!==undefined){const v=state.voices[Number(target.dataset.copyCast)];if(v){if(target.checked)castCopySelection.add(v.name);else castCopySelection.delete(v.name);updateCastCopyModal()}return}
 const field=target.dataset.lineField;if(field){const l=lineById(target.closest('.script-line').dataset.id);if(!l)return;l[field]=field==='after'?Math.max(0,Number(target.value)||0):target.value;if(field!=='outputFilename')l.status=l.selectedTakeId?'stale':'missing';if(field==='after')reflow();dirtyState();if(field==='speaker'||field==='after'){renderSidebar();renderTimeline();renderInspector()}return}
 if(target.dataset.trashSelect){if(target.checked)trashSelection.add(target.dataset.trashSelect);else trashSelection.delete(target.dataset.trashSelect);const btn=$('trashSelectedBtn');if(btn){btn.disabled=!trashSelection.size;btn.textContent=`🗑 選択した${trashSelection.size}件をゴミ箱へ`}return}
 const vField=target.dataset.vfield;const card=target.closest('[data-voice]');if(vField&&card){const v=state.voices[Number(card.dataset.voice)];if(!v)return;const old=v.name;v[vField]=target.value;if(vField==='name'){state.lines.filter(l=>l.speaker===old).forEach(l=>l.speaker=v.name)}if(vField==='engine')v.model=(models[v.engine]||models.irodori)[0][0];if(vField!=='name')state.lines.filter(l=>l.speaker===v.name&&getLineTtsSource(l)==='cast').forEach(l=>l.status=l.selectedTakeId?'stale':'missing');dirtyState();if(vField==='engine')renderEditor();renderInspector();return}
 if(target.dataset.ref&&card){const v=state.voices[Number(card.dataset.voice)];v.refs=v.refs||[];if(target.checked&&!v.refs.includes(target.dataset.ref))v.refs.push(target.dataset.ref);if(!target.checked)v.refs=v.refs.filter(x=>x!==target.dataset.ref);state.lines.filter(l=>l.speaker===v.name&&getLineTtsSource(l)==='cast').forEach(l=>l.status=l.selectedTakeId?'stale':'missing');dirtyState();return}
 if(target.dataset.speakerLink!==undefined){setSpeakerLink(target.dataset.speakerLink,target.value);return}
 const c=clipById(selectedClip),l=lineById(selectedLine);
 const cv={clipStart:['start',Number],clipDuration:['duration',Number],clipGain:['gain',v=>Number(v)/100],clipFadeIn:['fadeIn',Number],clipFadeOut:['fadeOut',Number],clipTrack:['trackId',String],clipTrimStart:['trimStart',Number],clipTrimEnd:['trimEnd',Number],clipLoop:['loop',null],clipPinned:['pinned',null]};
 if(c&&id==='clipAnchor'){c.anchorLineId=target.value||null;const ref=state.clips.find(x=>x.lineId===target.value&&x.trackId==='dialogue');c.anchorOffset=ref?c.start-ref.start:0;dirtyState();renderTimeline();renderInspector();return}
 if(c&&cv[id]){let [prop,fn]=cv[id];c[prop]=fn?fn(target.value):target.checked;if(['start','duration','fadeIn','fadeOut','trimStart','trimEnd','gain'].includes(prop))c[prop]=Math.max(0,c[prop]);if(prop==='trackId'&&c.trackId!=='dialogue')c.pinned=true;dirtyState();renderTimeline();renderInspector();return}
 if(!l)return;
 if(target.dataset.pronChoice!==undefined){
   const pd=PD(),dictionary=pronunciation?.dictionary||{},before=pd.apply(l.text,dictionary,l.pronunciationChoices);
   const choices=pd.withChoice(l.pronunciationChoices,target.dataset.pronChoice,target.value);
   if(Object.keys(choices).length)l.pronunciationChoices=choices;else delete l.pronunciationChoices;
   // Only a changed spoken text needs a new take; the server re-checks on save.
   if(l.selectedTakeId&&pd.apply(l.text,dictionary,l.pronunciationChoices)!==before){
     l.status='stale';
     const badge=[...document.querySelectorAll('.script-line')].find(x=>x.dataset.id===l.id)?.querySelector('.line-status');
     if(badge){badge.className='line-status stale';badge.textContent='要再生成'}
   }
   dirtyState();renderInspector();return;
 }
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
 return {sidebar:document.querySelector('.sidebar').getBoundingClientRect().width||preferredPanelSizes?.sidebar||220,
  editor:document.querySelector('.editor-panel').getBoundingClientRect().width,
  inspector:document.querySelector('.workbench-foot').getBoundingClientRect().height};
}
function applyPanelSizes(sizes){
 const layout=document.querySelector('.main-layout');
 if(horizontalPanelsActive()){
  const maxWidth=layout.getBoundingClientRect().width;
  const focused=document.body.classList.contains('harbor-focus');
  const remaining=maxWidth-(focused?1:2)*PANEL_LIMITS.gutter;
  const sidebar=focused?0:clampPanelSize(sizes.sidebar,PANEL_LIMITS.sidebar,remaining-PANEL_LIMITS.editor-PANEL_LIMITS.workbench);
  const editor=clampPanelSize(sizes.editor,PANEL_LIMITS.editor,remaining-sidebar-PANEL_LIMITS.workbench);
  if(!focused)layout.style.setProperty('--vds-sidebar-width',Math.round(sidebar)+'px');
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
 if(!preferredPanelSizes)preferredPanelSizes=measuredPanelSizes();
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
 window.addEventListener('harborlayoutchange',()=>{if(preferredPanelSizes)applyPanelSizes(preferredPanelSizes)});
}
function wire(){
 $('editorContent').addEventListener('input',e=>{if(e.target.id==='librarySearch'){libraryQueries[libraryTab]=e.target.value;updateLibraryResults()}});
 $('editorContent').addEventListener('keydown',e=>{const tab=e.target.closest('[data-library-tab]');if(!tab)return;const index=LIBRARY_TABS.findIndex(t=>t[0]===tab.dataset.libraryTab);let next;if(e.key==='ArrowRight')next=(index+1)%LIBRARY_TABS.length;else if(e.key==='ArrowLeft')next=(index+LIBRARY_TABS.length-1)%LIBRARY_TABS.length;else if(e.key==='Home')next=0;else if(e.key==='End')next=LIBRARY_TABS.length-1;else return;e.preventDefault();selectLibraryTab(LIBRARY_TABS[next][0]);$('libraryTab-'+libraryTab).focus()});
 $('undoBtn').onclick=undoEdit;$('redoBtn').onclick=redoEdit;historyButtons();
 $('projectButton').onclick=openProjects;$('settingsBtn').onclick=openSettings;$('exportExcelBtn').onclick=async()=>{try{await saveNow();location.href=`/api/projects/${state.id}/excel/export`}catch(e){toast(e.message,true)}};
 $('renderBtn').onclick=renderAudio;$('addSceneBtn').onclick=addScene;
 $('navScript').onclick=()=>setView('script');$('navVoices').onclick=()=>setView('voices');$('navAssets').onclick=()=>setView('assets');$('navPronunciation').onclick=()=>setView('pronunciation');
 $('rewindBtn').onclick=stop;$('playBtn').onclick=play;$('stopBtn').onclick=()=>{if(playing)pause();else stopNodes()};
 $('reflowBtn').onclick=()=>{reflow();dirtyState();renderTimeline();toast('台詞を順番どおりに再配置しました')};
 $('zoom').oninput=e=>setTimelineZoom(timelineSliderToZoom(e.target.value));
 $('zoomOutBtn').onclick=()=>setTimelineZoom(pxPerSec/1.6);
 $('zoomInBtn').onclick=()=>setTimelineZoom(pxPerSec*1.6);
 $('zoomFitBtn').onclick=fitTimeline;
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
  const writing=['INPUT','TEXTAREA','SELECT','BUTTON','A'].includes(document.activeElement?.tagName);if(e.ctrlKey&&e.key.toLowerCase()==='s'){e.preventDefault();saveNow().then(()=>toast('保存しました')).catch(err=>toast(err.message,true))}if(e.code==='Space'&&!writing&&!document.querySelector('.modal-backdrop:not(.hidden)')){e.preventDefault();play()}});
 window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''} });
}
installPanelResizers();
restoreTimelineZoom();wire();ensureProject().catch(e=>{toast('初期化に失敗しました: '+e.message,true);$('saveState').textContent='起動エラー'});
