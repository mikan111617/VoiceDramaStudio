'use strict';
/* Entirely separate game-voice state from the VoiceDrama Studio timeline editor. */
let game=null,scenarios=[],castProjects=[],scenarioKey='',focusedVoice='',marked=new Set(),pollTimer=null;
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
function renderAll(){if(!game)return;$('exportFormat').value=game.format||'wav';$('lastIssued').textContent=String(game.lastIssued||0);const speakerInfo=$('speakerFilterInfo');if(speakerInfo){const hasSheet=!!(game.speakerFilterEnabled||Object.keys(game.characterSheet||{}).length);speakerInfo.textContent=hasSheet?'キャラクターシートの登録者のみ音声化（TIPS・未登録話者は除外）':'キャラクターシート未登録：TIPSのみ自動除外。キャラクターシートの登録を推奨します。'}const selected=$('projectList');if(selected.querySelector(`option[value="${game.id}"]`))selected.value=game.id;renderScenarios();renderSource();renderVoiceRows();renderInspector();renderBindings()}
function renderScenarios(){const list=$('scenarioList');list.innerHTML=game.scenarios.map(sc=>`<button class="scenario-btn ${scenarioKey===sc.key?'active':''}" data-scenario="${esc(sc.key)}"><span title="${esc(sc.filename)}">${esc(sc.key)}</span><span>${sc.count}件</span></button>`).join('');}
function showScenario(key){scenarioKey=key;focusedVoice='';marked.clear();renderAll()}
function renderSource(){let scen=game.scenarios.find(s=>s.key===scenarioKey);$('scenarioTitle').textContent=scen?.filename||'シナリオを選んでください';$('scenarioMeta').textContent=scen?`${scen.format.toUpperCase()} • ${voicesOf().length} 台詞${Object.keys(scen.ignoredSpeakers||{}).length?' • 音声化対象外: '+Object.entries(scen.ignoredSpeakers).map(([name,count])=>name+' '+count+'件').join('、'):''} • 原文は読み取り専用`:'KS・ボイスリストJSONの両形式に対応';const viewer=$('sourceViewer');if(!scen){viewer.innerHTML='<div class="empty">左の「シナリオ .ks」か「ボイスリスト JSON」から取り込んでください。</div>';return}
const rows=voicesOf();if(scen.format==='voice-list'){viewer.innerHTML=rows.map((v,i)=>`<div class="source-line voiced ${v.voiceId===focusedVoice?'selected':''}" data-from-voice="${esc(v.voiceId)}"><span class="number">${i+1}</span><span>${esc(v.speaker)} / ${esc(v.sourceId)}: ${esc(v.text)}</span></div>`).join('');return}
const map=new Map(rows.map(v=>[v.sourceLine,v]));viewer.innerHTML=scen.content.split(/\r?\n/).map((line,index)=>{const n=index+1,v=map.get(n);return `<div class="source-line ${v?'voiced':''} ${v?.voiceId===focusedVoice?'selected':''}" ${v?`data-from-voice="${esc(v.voiceId)}"`:''}><span class="number">${n}</span><span>${v?'<b class="voice-id">'+esc(v.voiceId)+'</b>  ':''}${esc(line)||'&nbsp;'}</span></div>`}).join('');}
function renderVoiceRows(){const query=$('voiceSearch').value.toLowerCase().trim();const rows=voicesOf().filter(v=>!query||[v.voiceId,v.sourceId,v.speaker,v.text,v.ttsText].join(' ').toLowerCase().includes(query));const wrap=$('voiceList');$('voiceCount').textContent=`${rows.length}/${voicesOf().length}`;wrap.innerHTML=rows.map(v=>`<div class="voice-row ${focusedVoice===v.voiceId?'selected':''}" data-voice="${esc(v.voiceId)}"><input type="checkbox" data-mark="${esc(v.voiceId)}" ${marked.has(v.voiceId)?'checked':''} aria-label="${esc(v.voiceId)}を生成対象に選ぶ"><span class="voice-id">${esc(v.voiceId)}</span><span class="voice-speaker" title="${esc(v.speaker)}">${esc(v.speaker)}</span><span class="voice-text" title="${esc(v.text)}">${esc(v.text)}</span><span class="voice-status ${v.stale?'stale':v.audio?'ready':''}">${displayStatus(v)}</span></div>`).join('')||'<div class="empty">該当する台詞がありません。</div>';$('selectAll').checked=rows.length>0&&rows.every(v=>marked.has(v.voiceId))}
function focusVoice(id,scrollSource=false){focusedVoice=id;renderVoiceRows();renderSource();renderInspector();if(scrollSource)$('sourceViewer').querySelector(`[data-from-voice="${CSS.escape(id)}"]`)?.scrollIntoView({behavior:'smooth',block:'center'});}
function renderInspector(){const wrap=$('inspector');const v=selectedVoice();if(!v){wrap.innerHTML='<div class="empty">台詞をクリックすると、演技指示を編集したり生成音声を試聴できます。</div>';return}const profile=game.characterSheet[v.speaker]||game.referenceCharacters?.[v.speaker];wrap.innerHTML=`<h2 class="voice-inspector-title">${esc(v.speaker)}</h2><div class="keypair"><span class="tag">${esc(v.voiceId)}</span><span class="muted">${esc(v.scenario)} ${v.sourceLine?'· line '+v.sourceLine:''}</span></div>${v.sourceId?`<div class="note">ゲーム内ID: <b>${esc(v.sourceId)}</b></div>`:''}<div class="field"><label>元の台詞（シナリオ上の文章）</label><div class="note">${esc(v.text)}</div></div><div class="field"><label>読み上げテキスト（ゲーム原文は変更しません）</label><textarea id="ttsText">${esc(v.ttsText)}</textarea></div><div class="field"><label>演技指示・キャプション（任意）</label><textarea id="caption">${esc(v.caption||'')}</textarea></div><button class="primary" id="saveLine">この台詞の設定を保存</button>${v.audio?`<div class="field"><label>生成済み音声 ${v.stale?'（編集後・旧テイク）':''}</label><audio class="audio" controls preload="none" src="/api/game/projects/${game.id}/audio/${encodeURIComponent(v.voiceId)}"></audio></div>`:''}${profile?`<div class="note"><strong>${esc(profile.display_name||v.speaker)}</strong>（${esc(profile.reading||'')}）<p>${esc(profile.baseline_tone||'')}</p>${(profile.acting_notes||[]).slice(0,4).map(n=>`<p>・${esc(n)}</p>`).join('')}</div>`:''}`;}
function renderBindings(){const names=[...new Set(game.voices.map(v=>v.speaker))].sort();const options=[{value:'',label:'キャストを選択'}];for(const p of castProjects)for(const cast of p.casts)options.push({value:p.id+'|'+encodeURIComponent(cast.name),label:p.title+' / '+cast.name+' ('+(cast.model?.split('/').at(-1)||cast.engine)+')'});$('castBindings').innerHTML=names.map(speaker=>{const binding=game.bindings?.[speaker],selected=binding?binding.projectId+'|'+encodeURIComponent(binding.castName):'';return `<div class="binding"><label>${esc(speaker)} <span class="muted">${esc((game.characterSheet?.[speaker]||game.referenceCharacters?.[speaker])?.display_name||'')}</span></label><select data-bind="${esc(speaker)}">${options.map(o=>`<option value="${esc(o.value)}" ${o.value===selected?'selected':''}>${esc(o.label)}</option>`).join('')}</select></div>`}).join('')||'<div class="helper">シナリオを取り込むとキャラクター一覧が表示されます。</div>'}
async function autoBindCasts(){
 const bindings={...game.bindings};let found=0;
 for(const speaker of [...new Set(game.voices.map(v=>v.speaker))]){
  if(bindings[speaker]?.castName)continue;
  const profile=game.characterSheet?.[speaker]||game.referenceCharacters?.[speaker]||{};
  const targets=[speaker,profile.display_name,...(profile.aliases||[])].filter(Boolean).map(v=>v.toLowerCase());
  const matches=[];
  for(const p of castProjects)for(const c of p.casts){
   if(targets.includes(c.name.toLowerCase()))matches.push({projectId:p.id,castName:c.name});
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
 const form=new FormData();const revision=game.revision;let url=`/api/game/projects/${game.id}/`;if(kind==='ks'){url+=`import-ks?expectedRevision=${revision}`;for(const file of files)form.append('files',file)}else{const endpoint={json:'voice-list',state:'voice-id-state',sheet:'character-sheet',story:'story-context'}[kind];url+=`${endpoint}?expectedRevision=${revision}`;form.append('file',files[0])}busy('取り込み中…');try{const result=await request(url,{method:'POST',body:form});game=result;if(kind==='ks')scenarioKey=files.at(-1).name.replace(/\.ks$/i,'');if(kind==='json'){const added=game.scenarios.at(-1);scenarioKey=added?.key||''}focusedVoice='';marked.clear();renderAll();busy('保存済み');if(kind==='ks')clearImportError();const ignored=(kind==='ks'||kind==='sheet')?game.scenarios.flatMap(sc=>Object.entries(sc.ignoredSpeakers||{}).map(([name,n])=>`${sc.key}: ${name} ${n}件`)):[];message(`${files.length}ファイルを取り込みました${ignored.length?'（音声化対象外: '+ignored.slice(0,6).join('、')+(ignored.length>6?' ほか':'')+'）':''}`)}catch(e){busy('取り込み失敗');if(kind==='ks')showImportError(e.message);else message(e.message,true)}finally{picker.value=''}}
async function startGeneration(){if(!game)return;let ids=[...marked].filter(id=>game.voices.some(v=>v.voiceId===id));if(!ids.length)return message('台詞の左側のチェックを選んでください',true);const steps=Number($('numSteps').value);if(!Number.isInteger(steps)||steps<1||steps>80)return message('ステップ数は1〜80です',true);if(!confirm(`${ids.length}件の音声を生成します。\n選択したキャストのモデル・参照音声が使用されます。`))return;try{const job=await request(`/api/game/projects/${game.id}/generate`,json('POST',{voiceIds:ids,numSteps:steps}));message('ゲーム用音声の生成を開始しました');$('generateSelected').disabled=true;poll(job.id)}catch(e){message('生成を開始できません: '+e.message,true)}}
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
function downloadZip(){if(!game||!game.scenarios.length)return message('シナリオがありません',true);const link=document.createElement('a');link.href=`/api/game/projects/${game.id}/export.zip`;link.download='game_voice_export.zip';document.body.appendChild(link);link.click();link.remove();message('ゲーム用ZIPを準備しています。未生成音声は missing_voices.json に記録されます。')}
function wire(){$('newProject').onclick=()=>createNew();$('projectList').onchange=e=>openGame(e.target.value);for(const [button,picker,kind] of [['importKs','ksPicker','ks'],['importJson','jsonPicker','json'],['importState','statePicker','state'],['importSheet','sheetPicker','sheet'],['importStory','storyPicker','story']]){$(button).onclick=()=>(game?$(picker).click():message('プロジェクトを作成してください',true));$(picker).onchange=()=>uploadFiles($(picker),kind)}$('scenarioList').onclick=e=>{const b=e.target.closest('[data-scenario]');if(b)showScenario(b.dataset.scenario)};$('voiceList').onclick=e=>{if(e.target.matches('[data-mark]')){e.target.checked?marked.add(e.target.dataset.mark):marked.delete(e.target.dataset.mark);renderVoiceRows();return}const row=e.target.closest('[data-voice]');if(row)focusVoice(row.dataset.voice,true)};$('sourceViewer').onclick=e=>{const row=e.target.closest('[data-from-voice]');if(row)focusVoice(row.dataset.fromVoice)};$('selectAll').onchange=e=>{for(const v of voicesOf()){if(e.target.checked)marked.add(v.voiceId);else marked.delete(v.voiceId)}renderVoiceRows()};$('voiceSearch').oninput=renderVoiceRows;$('inspector').onclick=e=>{if(e.target.id==='saveLine')saveVoice()};$('autoBindCasts').onclick=autoBindCasts;$('castBindings').onchange=e=>{if(e.target.matches('[data-bind]'))setBinding(e.target.dataset.bind,e.target.value)};$('exportFormat').onchange=async e=>{if(await update({format:e.target.value}))renderAll()};$('analyzeSelected').onclick=startAnalysis;$('generateSelected').onclick=startGeneration;$('exportZip').onclick=downloadZip;$('exportCaptionsScene').onclick=()=>exportCaptionCsv('scene');$('exportCaptionsAll').onclick=()=>exportCaptionCsv('all');$('importCaptions').onclick=()=>game?$('captionPicker').click():message('ゲーム音声プロジェクトを作成してください',true);$('captionPicker').onchange=importCaptionCsv;$('copyCaptionPrompt').onclick=copyCaptionPrompt;}
wire();refreshGames().catch(e=>message(e.message,true));
