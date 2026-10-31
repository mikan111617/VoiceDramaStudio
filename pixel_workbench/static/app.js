'use strict';
const $ = q => document.querySelector(q);
let uiFrameSource=null, uiAIJobId=null;
let mode='text', currentJob=null, pollTimer=null, uploadedPreview=null, allJobs=[], allAssets=[], allSequences=[], currentEstimate=null, savedPrompt=null, ollamaModels=[], pastedResultFile=null, pastedResultUrl=null, importBusy=false, currentSequenceId='';
const modeNames={text:'新規生成',redraw:'参考画像から描き直す',convert:'元画像をドット絵化',animate:'アニメーション',vfx:'エフェクト専用'};
const modelChoices={pixellab:[['standard','Standard（Pixen / 通常変換）'],['pro','Pro（高品質モデル）']],openai:[['gpt-image-2','GPT Image 2'],['gpt-image-2.5-flare','GPT Image 2.5 Flare'],['gpt-image-2.5-sunburst','GPT Image 2.5 Sunburst']],chatgpt:[['browser','ChatGPT Plus（コピー＆貼り付け）']]};
const categoryLabels={character:'キャラクター',item:'アイテム',effect:'エフェクト',background:'背景',sound:'効果音・音声',ui:'UI・ウィンドウ枠',other:'その他'};
function toast(message,error=false){const b=$('#toast');b.textContent=message;b.className=error?'show error':'show';clearTimeout(toast.timer);toast.timer=setTimeout(()=>b.className='',6500)}
async function api(url, options={}){const r=await fetch(url,options);let d;try{d=await r.json()}catch{throw Error('サーバーからの応答形式が不正です')}if(!r.ok){throw Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail||d))}return d}
function postJSON(url,d,method='POST'){return api(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(d)})}
function safe(s){return String(s??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]))}
function usd(x){return x==null?'—':'$'+Number(x).toFixed(4)}
function providerName(p){return p==='openai'?'OpenAI API':p==='chatgpt'?'ChatGPT Plus（手動）':'PixelLab API'}
function getProvider(){return $('#provider').value}
function getModel(){return $('#model').value}
function currentCategory(){return $('#assetCategory')?.value||'other'}
function opts(){
 const options={provider:getProvider(),model:getModel(),mode,size:Number($('#size').value),frames:Number($('#frames').value),quality:$('#quality').value,gpt_size:$('#gptSize').value,reservation_usd:Number($('#reservation').value),category:currentCategory()};
 if((mode==='text'||mode==='redraw')&&getProvider()==='pixellab'){
  options.width=Number($('#imageWidth').value);
  options.height=Number($('#imageHeight').value);
 }
 return options;
}
function autoCategoryForMode(m){return m==='vfx'?'effect':m==='animate'?'character':m==='convert'?'character':'other'}
const assetWorkflowRules={
 character:['text','redraw','convert','animate'],
 item:['text','redraw','convert'],
 background:['text','redraw','convert'],
 ui:['text','redraw','convert'],
 effect:['text','convert','vfx'],
 other:['text','redraw','convert']
};
const workflowHelp={
 text:'文章から新しいドット絵を作成します。参照画像は不要です。',
 redraw:'参照画像の人物・衣装・色・特徴を使い、新しい構図とピクセル表現で描き直します。単純な縮小・変換ではありません。PixelLab Proを使用します。',
 convert:'参照画像の元の構図とポーズをできるだけ保ち、ドット絵に変換します。「描き直し」と違い元画像の形を優先します。',
 animate:'すでにあるドット絵を1コマ目として、動きを生成します。キャラクター制作の次の工程です。',
 vfx:'落雷・爆発・魔法陣など、動くエフェクトを新しく作ります。参照画像は任意です。'
};
function updateWorkflowPicker(){
 const category=currentCategory(),allowed=assetWorkflowRules[category]||assetWorkflowRules.other;
 document.querySelectorAll('.workflow-card').forEach(button=>{
  const available=allowed.includes(button.dataset.mode);
  button.classList.toggle('hidden',!available);
  button.disabled=!available;
  const selected=button.dataset.mode===mode;
  button.classList.toggle('current',selected);
  button.setAttribute('aria-pressed',String(selected));
 });
 document.querySelectorAll('[data-cat]').forEach(button=>button.classList.toggle('hidden',button.dataset.cat!==category));
 const summary=$('#categorySummary');
 if(summary)summary.textContent='制作する素材：'+(categoryLabels[category]||'その他')+'。生成結果はこの分類へ自動保存します。';
 const explain=$('#workflowExplanation');if(explain)explain.textContent=workflowHelp[mode]||'';
 const title=$('#workflowTitle');if(title)title.textContent='② '+(categoryLabels[category]||'素材')+'をどう作りますか？';
}
function setAssetType(category){
 if(!assetWorkflowRules[category])category='other';
 $('#assetCategory').value=category;
 document.querySelectorAll('[data-asset-type]').forEach(b=>{
  const selected=b.dataset.assetType===category;
  b.classList.toggle('selected',selected);b.setAttribute('aria-pressed',String(selected));
 });
 if(!(assetWorkflowRules[category]||[]).includes(mode))mode=category==='effect'?'vfx':'text';
 if(mode==='redraw'&&getProvider()!=='pixellab'){$('#provider').value='pixellab';setProvider();}
 if(mode==='redraw')$('#model').value='pro';
 if(category==='ui'&&mode==='text'&&getProvider()==='pixellab')$('#model').value='pro';
 updateWorkflowPicker();refreshUI();
}
const purposeTemplates={
 map:{label:'マップユニット',mode:'text',width:16,height:16,category:'character',prompt:'16×16ピクセルのGBA風SRPGマップユニット。正面向きの全身キャラ。大きな色の塊と明快な輪郭、少ない色数、単純な顔、背景なし。原寸16×16で識別できるデザイン。'},
 portrait:{label:'顔グラフィック',mode:'text',width:80,height:80,category:'character',prompt:'80×80ピクセルのGBA時代のSRPG用顔グラフィック。胸から上、表情が読みやすい、明瞭な輪郭と制限色数。テキストなし。'},
 battle:{label:'戦闘キャラ',mode:'text',width:96,height:96,category:'character',prompt:'96×96ピクセルのGBA風SRPG戦闘用の全身キャラドット絵。シルエットが明瞭、ゲーム画面で見やすい立ち姿。背景なし。'},
 item:{label:'アイテム',mode:'text',width:32,height:32,category:'item',prompt:'32×32ピクセルのゲーム用アイテムアイコン。1つだけを中央に配置、レトロなピクセルアート、輪郭は鮮明、透過背景。'},
 window:{label:'横長UI・会話枠',mode:'text',width:240,height:64,category:'ui',prompt:'240×64ピクセルのレトロSRPG/ノベルゲーム向け横長メッセージウィンドウ。読みやすい暗い内側、装飾された細い縁、文字なし・キャラクターなし。枠の外側は透明。'},
 nine:{label:'9-slice用の枠原画',mode:'text',width:64,height:64,category:'ui',prompt:'64×64ピクセルのピクセルアートUIフレームの原画。四隅の模様は固定、上下左右の辺は繰り返しても自然につながり、中央は単色に近い。文字なし。9-sliceで分割しやすい単純な枠。'},
 animate:{label:'アニメ生成',mode:'animate',category:'character',prompt:'元画像のキャラクターがその場で自然に足踏みする。最初と最後がつながる動き。'},
 vfx:{label:'魔法・エフェクト',mode:'vfx',category:'effect',prompt:'青白い稲妻が地面に落ち、火花が散って消える。キャラクターなし、透明背景のドット絵エフェクト。'}
};
function applyPurpose(name){
 const p=purposeTemplates[name];if(!p)return;
 if(getProvider()!=='pixellab'){$('#provider').value='pixellab';setProvider()}
 setMode(p.mode);
 if(p.width){$('#imageWidth').value=p.width;$('#imageHeight').value=p.height}
 $('#assetCategory').value=p.category;
 if(p.category==='ui')$('#model').value='pro';
 else if(p.mode==='text')$('#model').value='standard';
 $('#description').value=p.prompt;
 $('#purposeSummary').textContent=p.label+'向けの初期設定を反映しました。文章・寸法は自由に編集できます。'+(name==='nine'?' ※ 9-sliceへの切り出しは未実装です。':'');
 document.querySelectorAll('[data-purpose]').forEach(b=>{const selected=b.dataset.purpose===name;b.classList.toggle('selected',selected);b.setAttribute('aria-pressed',String(selected))});
 refreshUI();
}
async function updateAnimationHelp(){
 if(mode!=='animate')return;
 const max=Number($('#size').value),count=Number($('#frames').value),file=$('#referenceImage').files[0];
 let w=max,h=max,source='正方形として計算';
 if(file){
  try{const bitmap=await createImageBitmap(file);const scale=Math.min(1,max/Math.max(bitmap.width,bitmap.height));w=Math.max(1,Math.round(bitmap.width*scale));h=Math.max(1,Math.round(bitmap.height*scale));bitmap.close();source='参照画像の縦横比から概算';}catch(e){source='画像の寸法を確認できません'}
 }
 const cap=Math.min(16,Math.floor(524288/(w*h))),possible=[4,6,8,10,12,14,16].filter(n=>n<=cap);
 const message='元画像を含む総'+count+'コマ。'+source+'：'+w+'×'+h+'pxの場合、選べる総コマ数は'+(possible.length?possible.join(' / ')+'コマ':'なし')+'。';
 const label=$('#animationHelp');label.textContent=message+(count>cap?' ⚠ この組み合わせは上限を超えます。サイズか枚数を下げてください。':'');
 label.classList.toggle('limit-warning',count>cap);
}
function setProvider(){
 const service=getProvider(), previous=getModel();$('#model').replaceChildren();
 for(const [id,name] of modelChoices[service]){const option=document.createElement('option');option.value=id;option.textContent=name;$('#model').append(option)}
 if(modelChoices[service].some(v=>v[0]===previous))$('#model').value=previous;
 if(service!=='pixellab' && (mode==='animate'||mode==='vfx'||mode==='redraw'))setMode('text');
 refreshUI();
}
function setMode(m){
 const allowed=assetWorkflowRules[currentCategory()]||assetWorkflowRules.other;
 if(!allowed.includes(m))return toast('この素材では選べない制作方法です',true);
 if(m==='redraw'){
  if(getProvider()!=='pixellab'){$('#provider').value='pixellab';setProvider();}
  $('#model').value='pro';
 }
 mode=m;
 document.querySelectorAll('.tab').forEach(b=>{const selected=b.dataset.mode===m;b.classList.toggle('current',selected);b.setAttribute('aria-selected',String(selected))});
 updateWorkflowPicker();refreshUI();
}
function refreshUI(){
 const service=getProvider(), m=getModel(), browser=service==='chatgpt', gpt=service==='openai', pro=m==='pro';
 if(mode==='redraw' && (service!=='pixellab'||m!=='pro')){$('#provider').value='pixellab';setProvider();$('#model').value='pro';return refreshUI();}
 updateWorkflowPicker();
 document.querySelectorAll('[data-mode="animate"],[data-mode="vfx"]').forEach(b=>{b.disabled=(gpt||browser)||!(assetWorkflowRules[currentCategory()]||[]).includes(b.dataset.mode)});

 $('#vfxPanel').classList.toggle('hidden',mode!=='vfx');
 if(service==='pixellab')for(const option of $('#model').options){
 const ui=mode==='text'&&currentCategory()==='ui';
 option.textContent=mode==='vfx'?(option.value==='standard'?'Lite（VFX・低消費）':'Pro（VFX・高品質）'):ui?(option.value==='standard'?'Standard（汎用画像生成）':'Pro（UI専用生成API）'):(option.value==='standard'?'Standard（Pixen / 通常変換）':'Pro（高品質モデル）');
}
 $('#imageField').classList.toggle('hidden',mode==='text');
 $('#framesField').classList.toggle('hidden',mode!=='animate');
 $('#presets').classList.toggle('hidden',mode!=='text');
 $('#vfxPresets').classList.toggle('hidden',mode!=='vfx');
 $('#fidelityField').classList.toggle('hidden',mode!=='convert');
 $('#browserPanel').classList.toggle('hidden',!browser);
 $('#reservationField').classList.toggle('hidden',browser);
 $('#gptSizeField').classList.toggle('hidden',!gpt);
 $('#qualityField').classList.toggle('hidden',!gpt);
 $('#sizeField').classList.toggle('hidden',gpt||browser||mode==='vfx'||mode==='text'||mode==='redraw'||(mode==='convert'&&pro));
 $('#dimensionsField').classList.toggle('hidden',service!=='pixellab'||!(mode==='text'||mode==='redraw'));
 $('#advancedProviderSettings').open = $('#advancedProviderSettings').open || false;
 $('#description').closest('.field').classList.toggle('hidden',false);
 $('#transparent').closest('.toggle').classList.toggle('hidden',browser||mode==='vfx'||(mode==='convert'&&!gpt));
 $('#size').querySelector('[value="512"]').disabled=mode==='animate';
 if(mode==='animate' && Number($('#size').value)>256)$('#size').value='128';
 $('#promptLabel').textContent=mode==='redraw'?'描き直したい姿・衣装・ドット絵の特徴':mode==='vfx'?'どんなエフェクトを動かしますか？':mode==='animate'?'動作の説明':mode==='convert'?'追加の変換指示（省略可）':'生成したい画像の説明';
 $('#description').placeholder=mode==='redraw'?'例：青いマント、茶髪、青い宝石付きの杖を保ち、GBA風の正面立ちドット絵として新しく描き直す。大胆な形の簡略化、明確な輪郭、透明背景。':mode==='vfx'?'例：上空から青白い稲妻が3本落ち、地面で火花が散って消える。8フレーム程度、背景透明。':mode==='animate'?'例：立っているキャラクターが本を開く。自然なループ動作。':mode==='convert'?'例：元の顔つきや頭身を保ち、緻密な16bitドット絵にしてください（任意）':'例：温かい雰囲気の本を持つ女性、全身立ち絵、繊細なドット絵';
 $('#imageLabel').textContent=mode==='redraw'?'デザインの参考にする画像（必須）':mode==='vfx'?'キャラクター画像（任意・512px以下）':mode==='animate'?'動かすドット絵': '変換元の画像';
 $('#modeGuide').textContent=mode==='redraw'?'参考画像から描き直す｜参考画像の特徴をもとにPixelLab Proで新しいドット絵を作ります。ポーズや構図は文章で指定できます。':mode==='text'?'新規生成｜文章から静止画を1枚作成。PixelLabなら幅と高さを指定でき、横長UI枠も作れます。':mode==='convert'?'画像から変換｜元画像が必要です。Standardはサイズと忠実度を指定、Proはモデル側が出力寸法を決めます。':mode==='animate'?'アニメ生成｜最初の画像を1枚用意し、総コマ数4〜16枚（偶数）を指定します。最初の1枚は元画像を維持します。':'エフェクト生成｜落雷・炎・斬撃などを生成。コマ数とサイズはVFXサービス側で決定されます。';
 $('#providerGuide').textContent=browser?'ChatGPT Plusは画面内で直接生成しません。指示をコピーし、画像を貼り付けてください。':gpt?'OpenAI画像APIは静止画・変換のみ。GPT用サイズは専用項目で選びます。':mode==='redraw'?'PixelLab Proの参照画像付き新規生成APIを使用。元画像の構図を固定せず、特徴を抽出して描き直します。費用の事前額が不明な場合は予算予約を利用します。':mode==='vfx'?'VFXはPixelLab専用。生成コマ数は選択できず、実際の結果を確認してください。':mode==='text'&&currentCategory()==='ui'?(m==='pro'?'UI専用のPixelLab Pro APIを使用します。枠・ボタン・HPバーなどの素材を作成。料金は送信前に確認し、予約額は請求上限ではありません。':'汎用のPixen生成でUIを作ります。UI専用モデルを使うならProに変更してください。'):'PixelLab APIを利用。新規生成なら幅・高さ16〜512pxを別々に指定できます。';
 updateAnimationHelp();
 $('#sizeHint').textContent=mode==='convert'?'元の縦横比を保ち、指定値を長辺の目安として使用':mode==='animate'?'入力の長辺を最大256pxに調整':'PixelLab新規生成では正方形';
 $('#fidelityHint').textContent=browser?'ChatGPTに渡すプロンプトに忠実度を反映します':gpt?'GPTへの編集指示に忠実度を反映（元画像通りを保証するものではありません）':pro?'Proは細かな忠実度パラメータ非対応（文章で調整）':'StandardではFaithful modeを調整。追加の文章指示はAPIに送信されません';
 $('#generatedLabel').textContent=mode==='vfx'?'VFXアニメーション（GIF）':browser?'ChatGPTで生成した画像（貼り付け取り込み）':gpt?'GPT生成画像':mode==='text'&&currentCategory()==='ui'?'PixelLab UI枠素材':'PixelLab生成画像';
 $('#generate').classList.toggle('hidden',browser);
 $('#demo').closest('.toggle').classList.toggle('hidden',browser);
 if(browser){$('#browserPreparedPrompt').value=browserPrompt();$('#connectionStatus').textContent='● ブラウザ利用・APIキー不要';$('#connectionStatus').classList.add('connected')}
 else if(service==='openai'){$('#connectionStatus').textContent=$('#openaiStatus').textContent.includes('接続済み')?'● OpenAI接続':'● OpenAI未接続'}
 const isTooLarge=service==='pixellab' && m==='standard' && mode==='text' && Math.max(Number($('#imageWidth').value),Number($('#imageHeight').value))>256;
 if(isTooLarge && $('#transparent').checked){$('#transparent').checked=false;toast('Pixen標準の512pxでは透過背景をオフにしました')}
 updateEstimate();
}
async function updateEstimate(){
 if(mode==='vfx'&&getProvider()==='pixellab'){
  const f=$('#referenceImage').files[0];let maxDim=0;
  if(f){try{const b=await createImageBitmap(f);maxDim=Math.max(b.width,b.height);b.close();}catch(e){}}
  const pro=getModel()==='pro';const quoted=pro?(maxDim>256?28:maxDim>128?14:maxDim?8:6):(maxDim>256?5:maxDim>128?3:1);
  $('#vfxQuote').textContent=`${pro?'Pro':'Lite'} / 見込み ${quoted} 生成枠（上振れ目安 最大 ${quoted*2} 枠）${maxDim?' / 参照画像あり':''}。送信後に公式見積もりを記録。`;
 }
 if(getProvider()==='chatgpt'){currentEstimate={estimate_usd:0,budget_reservation_usd:0};$('#estimatePrice').textContent='API料金なし';$('#estimateNotice').textContent='ChatGPT側の利用枠は別管理';return;}
 try{const d=await postJSON('/pixel/api/estimate',opts());currentEstimate=d;$('#estimatePrice').textContent=$('#demo').checked?'$0.0000':d.estimate_usd==null?'要確認':usd(d.estimate_usd);$('#estimateNotice').textContent=$('#demo').checked?'デモ・API送信なし':d.estimate_usd==null?`予算予約 ${usd(d.budget_reservation_usd)}（請求上限ではない）`:'参考額・変動あり';}catch(e){$('#estimatePrice').textContent='—';currentEstimate=null;$('#estimateNotice').textContent=e.message}
}
async function status(){try{const s=await api('/pixel/api/status');allJobs=s.jobs;if(getProvider()!=='chatgpt'){$('#connectionStatus').textContent=getProvider()==='openai'?(s.has_openai_key?'● OpenAI接続':'● OpenAI未接続'):(s.has_key?'● PixelLab接続':'● PixelLab未接続');$('#connectionStatus').classList.toggle('connected',getProvider()==='openai'?s.has_openai_key:s.has_key);}$('#openaiStatus').textContent=s.has_openai_key?'● GPT接続済み':'○ GPT未接続';$('#monthSpend').textContent=usd(s.stats.usd_confirmed);$('#budgetReserved').textContent=usd(s.stats.budget_reserved);$('#budget').value=Number(s.stats.budget_usd).toFixed(2);renderHistory(s.jobs);return s}catch(e){toast(e.message,true)}}
async function fetchBalance(silent=false){try{const b=await api('/pixel/api/balance');$('#providerCredits').textContent=usd(b.credits?.usd);const gen=b.subscription?.generations, tot=b.subscription?.total;$('#providerGenerations').textContent=gen==null?'—':Number(gen).toLocaleString('ja-JP')+(tot==null?'':' / '+Number(tot).toLocaleString('ja-JP'));$('#subscriptionInfo').textContent=b.subscription?.plan||b.subscription?.status||'PixelLab残高';if(!silent)toast('PixelLab残高を取得しました')}catch(e){if(!silent)toast('残高取得：'+e.message,true)}}
function jobFiles(job){return (job.files||[]).filter(f=>f!=='reference.png')}
function setCompareOptions(job){const sel=$('#compareSelect'),before=sel.value;sel.replaceChildren();const src=document.createElement('option');src.value='reference';src.textContent='今回の元画像';sel.append(src);const empty=document.createElement('option');empty.value='none';empty.textContent='比較しない';sel.append(empty);for(const j of allJobs.filter(x=>x.status==='completed'&&x.id!==job.id&&jobFiles(x).some(f=>f.endsWith('.png')))){const opt=document.createElement('option');opt.value=j.id;opt.textContent=`${providerName(j.provider)} ${j.model||'standard'} / ${j.created_at.slice(0,16).replace('T',' ')} / ${modeNames[j.mode]}`;sel.append(opt)}sel.value=[...sel.options].some(x=>x.value===before)?before:(job.files||[]).includes('reference.png')?'reference':'none';}
function updateCompare(job){const choice=$('#compareSelect').value;let url=null,label='元画像';if(choice==='reference'&&(job.files||[]).includes('reference.png')){url=`/pixel/api/jobs/${encodeURIComponent(job.id)}/files/reference.png`;}else if(choice!=='reference'&&choice!=='none'){const other=allJobs.find(j=>j.id===choice&&j.status==='completed');const f=other?jobFiles(other).find(x=>x.endsWith('.png')):null;if(f){url=`/pixel/api/jobs/${encodeURIComponent(other.id)}/files/${encodeURIComponent(f)}`;label=`比較：${providerName(other.provider)} ${other.model||'standard'}`}}$('#sourceWrap').classList.toggle('hidden',!url);if(url){$('#sourcePreview').src=url;$('#leftCompareLabel').textContent=label;}}
function displayResult(job){if(!job)return;currentJob=job.id;syncUIAIPreview(job);const firstImage=jobFiles(job).find(f=>f.endsWith('.png'));for(const id of ['useResultAsReference','copyResultImage'])$('#'+id).disabled=job.status!=='completed'||!firstImage;$('#reuseJobSettings').disabled=job.status!=='completed';const canvas=$('#previewCanvas'),badge=$('#jobBadge'),links=$('#downloads');$('#feedback').classList.toggle('hidden',job.status!=='completed');$('#compareAI').classList.toggle('hidden',job.status!=='completed'||!(job.files||[]).includes('reference.png'));$('#rateGood').classList.toggle('selected',job.rating==='good');$('#rateBad').classList.toggle('selected',job.rating==='bad');badge.textContent={queued:'待機中',processing:'生成中',completed:'完了',failed:'失敗',unknown:'結果/費用要確認'}[job.status]||job.status;links.replaceChildren();setCompareOptions(job);updateCompare(job);const list=jobFiles(job),candidate=list.find(f=>f==='effect.gif')||list.find(f=>f==='preview.gif')||list.find(f=>f.endsWith('.png'));if(job.status==='completed'&&candidate){const url=`/pixel/api/jobs/${encodeURIComponent(job.id)}/files/${encodeURIComponent(candidate)}`;canvas.innerHTML=`<img src="${url}" alt="生成結果">`;for(const name of list){const a=document.createElement('a');a.href=`/pixel/api/jobs/${encodeURIComponent(job.id)}/files/${encodeURIComponent(name)}`;a.download=name;a.textContent='↓ '+name;links.append(a)}}else if(['processing','queued'].includes(job.status)){canvas.innerHTML='<div class="empty-result"><div class="pixel-symbol">◌</div><h3>生成中です</h3><p>別作業しながら待てます。完了後に自動更新します。</p></div>'}else{canvas.innerHTML=`<div class="empty-result"><div class="pixel-symbol">▦</div><h3>${job.status==='unknown'?'結果・請求確認が必要です':'生成できませんでした'}</h3><p>${safe(job.error||'生成結果を確認できません。')}</p></div>`}let cost='未確定';if(job.provider==='chatgpt')cost='ブラウザ側で生成・本ツールからのAPI料金なし';else if(job.demo)cost='デモ（課金なし）';else if(job.usage_status==='confirmed')cost=`判明USD ${usd(job.usd_charged)} / PixelLab生成枠 ${job.generations_used??'不明'}`;else cost=`請求額不明（予約 ${usd(job.reservation_usd)}）`;$('#resultMeta').textContent=`${providerName(job.provider)} / ${job.model||'standard'} / ${modeNames[job.mode]} / ${job.provider==='openai'?job.gpt_size:job.provider==='chatgpt'?'外部生成':job.width&&job.height?job.width+'×'+job.height+'px':job.size+'px'} / ${job.created_at?.slice(0,19).replace('T',' ')} UTC / カテゴリー ${categoryLabels[job.category]||job.category||'—'} / ${cost}`;}
function renderHistory(jobs){$('#historyCount').textContent=jobs.length+' ITEMS';const box=$('#historyList');box.replaceChildren();if(!jobs.length){box.innerHTML='<p class="history-empty">まだ生成履歴がありません。</p>';return}for(const j of jobs){const item=document.createElement('button');item.type='button';item.className='history-item';const thumbnail=jobFiles(j).find(f=>f.endsWith('.png'));const pic=thumbnail?`<img class="history-thumb" src="/pixel/api/jobs/${encodeURIComponent(j.id)}/files/${encodeURIComponent(thumbnail)}" alt="">`:'<span class="history-thumb history-placeholder">▦</span>';const desc=j.asset_name||j.prompt||'参照画像から変換';const state={completed:'完了',processing:'生成中',queued:'待機中',failed:'失敗',unknown:'要確認'}[j.status]||j.status;item.innerHTML=`${pic}<div class="history-main"><b>${safe(desc)}</b><small>${safe(providerName(j.provider))} · ${safe(j.model||'standard')} · ${safe(modeNames[j.mode])} · ${safe(categoryLabels[j.category]||j.category||'—')} · ${safe(j.created_at.slice(0,10))} · ${state}</small></div><div class="history-pricing"><strong>${j.provider==='chatgpt'?'取込':j.demo?'DEMO':j.estimate_usd==null?'不明':usd(j.estimate_usd)}</strong><span>${j.provider==='chatgpt'?'外部利用':j.demo?'課金なし':j.usage_status==='confirmed'?'利用量確認':'見積/請求確認'}</span></div>`;item.addEventListener('click',()=>{displayResult(j);$('#create').scrollIntoView({behavior:'smooth'})});box.append(item)}}
function fileInputUpdate(){const el=$('#referenceImage');if(uploadedPreview)URL.revokeObjectURL(uploadedPreview);if(el.files?.length){const f=el.files[0];$('#uploadName').textContent=f.name;uploadedPreview=URL.createObjectURL(f);$('#referencePreview').src=uploadedPreview;$('#referencePreview').classList.remove('hidden');$('#sourceWrap').classList.remove('hidden');$('#sourcePreview').src=uploadedPreview;}else{$('#uploadName').textContent='クリックして画像を選択';$('#referencePreview').classList.add('hidden')}}
async function generate(){const demo=$('#demo').checked, settings=opts(), prompt=$('#description').value.trim(),file=$('#referenceImage').files[0];if(settings.width!==undefined&&(!Number.isInteger(settings.width)||!Number.isInteger(settings.height)||settings.width<16||settings.height<16||settings.width>512||settings.height>512))return toast('幅・高さは16〜512の整数で入力してください',true);if((mode==='text'||mode==='redraw')&&!prompt)return toast('生成したい画像を説明してください',true);if((mode==='animate'||mode==='vfx')&&!prompt)return toast('動作を説明してください',true);if(mode!=='text'&&mode!=='vfx'&&!file)return toast('参考画像を選んでください',true);try{currentEstimate=await postJSON('/pixel/api/estimate',settings)}catch(e){return toast('料金の再確認：'+e.message,true)}if(!demo){const price=currentEstimate.estimate_usd==null?`事前請求額は不明 / 仮予約 ${usd(currentEstimate.budget_reservation_usd)}（請求上限ではありません）`:`参考料金 ${usd(currentEstimate.estimate_usd)}（実額は変動あり）`;const service=settings.provider==='openai'?'OpenAI API（ChatGPT Plusとは別料金）':'PixelLab';if(!confirm(`${service}にリクエストを送信します。\n${price}${mode==='vfx'?'\n'+$('#vfxQuote').textContent:''}\n\n元画像を選んだ場合は外部APIへ送信されます。続行しますか？`))return}const data=new FormData();for(const [key,value] of Object.entries({...settings,prompt,transparent:$('#transparent').checked,demo,fidelity:$('#fidelity').value,category:currentCategory(),asset_name:$('#assetName').value.trim()})){data.set(key,String(value))}if(file&&mode!=='text')data.set('image',file);const button=$('#generate');button.disabled=true;try{const result=await api('/pixel/api/jobs',{method:'POST',body:data});toast(demo?'デモを開始しました':'APIで生成を開始しました');await refreshAll();const job=allJobs.find(j=>j.id===result.id);$('#compareSelect').value='reference';if(job)displayResult(job);watch(result.id);return result.id}catch(e){toast(e.message,true);return null}finally{button.disabled=false}}
function watch(jobId){clearInterval(pollTimer);pollTimer=setInterval(async()=>{try{const job=await api('/pixel/api/jobs/'+encodeURIComponent(jobId));if(job.id===currentJob)displayResult(job);if(['completed','failed','unknown'].includes(job.status)){clearInterval(pollTimer);await refreshAll();if(job.provider==='pixellab'&&!job.demo)await fetchBalance(true);toast(job.status==='completed'?'生成が完了しました':'結果と料金を確認してください',job.status!=='completed')}}catch(e){clearInterval(pollTimer);toast(e.message,true)}},2000)}
async function compareWithOllama(){const model=$('#ollamaModel').value;if(!model)return toast('まずOllamaのVisionモデルを選択してください',true);const job=allJobs.find(x=>x.id===currentJob);if(!job||!job.files?.includes('reference.png'))return toast('元画像がある履歴を選んでください',true);const btn=$('#compareWithOllama');btn.disabled=true;try{const d=await postJSON('/pixel/api/ollama/compare',{model,job_id:job.id});$('#ollamaOutput').value=d.text;$('#ollamaOutputLabel').textContent='元画像との比較・改善点';$('#ollamaOutputWrap').classList.remove('hidden');$('#applyOllama').classList.add('hidden');$('#ollamaPanel').scrollIntoView({behavior:'smooth'});toast('比較結果を取得しました。')}catch(e){toast(e.message,true)}finally{btn.disabled=false}}
async function refreshOllama(){const el=$('#ollamaStatus'),sel=$('#ollamaModel'),old=sel.value;el.textContent='ローカルOllamaを探しています…';try{const d=await api('/pixel/api/ollama/models');sel.replaceChildren();ollamaModels=d.models||[];if(!d.connected||!ollamaModels.length){const o=document.createElement('option');o.value='';o.textContent=d.connected?'インストール済みモデルなし':'未接続';sel.append(o);el.textContent=d.connected?'Ollamaは起動していますがモデルがありません。':'Ollama未接続。';return;}for(const m of ollamaModels){const o=document.createElement('option');o.value=m;o.textContent=m;sel.append(o)}if(ollamaModels.includes(old))sel.value=old;else{const vision=ollamaModels.find(x=>/llava|gemma|qwen.*vl|vision|moondream|minicpm/i.test(x));if(vision)sel.value=vision}el.textContent=`接続済み：${ollamaModels.length}モデル。画像解析はVisionモデルを選んでください。`;}catch(e){el.textContent='Ollamaを確認できません：'+e.message}}
async function ollamaAssist(task){const model=$('#ollamaModel').value,file=$('#referenceImage').files[0];if(!model)return toast('Ollamaを起動し、モデルを選択してください',true);const useImage=task==='describe'||($('#ollamaWithImage').checked&&mode!=='text'&&!!file);if(task==='describe'&&!file)return toast('画像の特徴抽出には元画像を選択してください',true);const data=new FormData();data.set('model',model);data.set('task',task);data.set('prompt',$('#description').value.trim());data.set('mode',mode);data.set('fidelity',$('#fidelity').value);if(useImage&&file)data.set('image',file);const btn=task==='describe'?$('#describeImage'):$('#improvePrompt');btn.disabled=true;$('#ollamaStatus').textContent='ローカルLLMが処理中です。';try{const d=await api('/pixel/api/ollama/assist',{method:'POST',body:data});$('#ollamaOutput').value=d.text;$('#ollamaOutputLabel').textContent=task==='describe'?'元画像から抽出した特徴':'改善後のプロンプト';$('#ollamaOutputWrap').classList.remove('hidden');$('#applyOllama').classList.toggle('hidden',task==='describe');$('#ollamaStatus').textContent='完了しました。';toast('Ollamaの提案を取得しました')}catch(e){$('#ollamaStatus').textContent='処理エラー：'+e.message;toast(e.message,true)}finally{btn.disabled=false}}
function browserPrompt(){const prompt=$('#description').value.trim(),fidelity=$('#fidelity').value;if(mode==='text')return 'Please generate a clean, readable pixel-art image with crisp, consistent pixel clusters. '+prompt;const intro=fidelity==='creative'?'Reinterpret the attached reference image as coherent game-ready pixel art, retaining its distinctive identity.':fidelity==='balanced'?'Convert the attached reference image into pixel art while preserving its principal character and visual details.':'Convert the attached reference image into high-quality pixel art. Preserve the exact character identity, facial features, original age impression, body proportions, pose, hair silhouette, outfit, and small accessories. Do not redesign or make the subject chibi.';return intro+' Produce clean, readable pixel clusters. '+prompt+'\nNote: I will attach the reference image manually.'}
async function launchChatGPT(){if(mode==='text'&&!$('#description').value.trim())return toast('生成する画像の指示を入力してください',true);window.open('https://chatgpt.com/','_blank','noopener,noreferrer');const prompt=browserPrompt();try{await navigator.clipboard.writeText(prompt);toast('指示をコピーしました。')}catch{toast('ブラウザを開きました。必要なら手動でコピーしてください。',true)}}
function isImageFile(file){return !!file && ['image/png','image/jpeg','image/webp','image/gif'].includes(file.type) && file.size>0 && file.size<=12*1024*1024}
function setReferenceFile(file){if(!isImageFile(file))return toast('参照画像は12MB以下のPNG・JPEG・WebP・GIFを選んでください',true);const transfer=new DataTransfer();transfer.items.add(file);$('#referenceImage').files=transfer.files;if(mode==='text')setMode('redraw');fileInputUpdate();updateEstimate();toast('参照画像に設定しました。')}
function clipboardFile(file){if(!isImageFile(file))return toast('12MB以下のPNG・JPEG・WebP・GIF画像を貼り付けてください',true);pastedResultFile=file;if(pastedResultUrl)URL.revokeObjectURL(pastedResultUrl);pastedResultUrl=URL.createObjectURL(file);$('#chatgptImportPreview').src=pastedResultUrl;$('#chatgptImportPreview').classList.remove('hidden');$('#importStatus').textContent=`画像を受け取りました（${(file.size/1024).toFixed(0)}KB）。履歴に取り込み中…`;importChatGPT()}
async function clipboardReadButton(){try{if(!navigator.clipboard?.read)throw new Error('ブラウザのクリップボード読み取りに非対応です。Ctrl+Vをお試しください。');const items=await navigator.clipboard.read();for(const item of items){const type=item.types.find(x=>['image/png','image/jpeg','image/webp'].includes(x));if(type){const blob=await item.getType(type);clipboardFile(new File([blob],'chatgpt-paste.'+type.split('/')[1],{type}));return}}throw new Error('クリップボードに画像がありません。')}catch(e){toast('画像の貼り付け：'+e.message,true)}}
async function importChatGPT(){const file=pastedResultFile||$('#importResult').files[0];if(!file)return toast('画像を貼り付けるか、ファイルを選択してください',true);if(importBusy)return;importBusy=true;const data=new FormData();data.set('result_image',file);data.set('prompt',$('#description').value.trim());data.set('mode',mode);data.set('category', currentCategory());data.set('asset_name', $('#assetName').value.trim());const reference=$('#referenceImage').files[0];if(reference&&mode==='convert')data.set('reference',reference);const button=$('#importChatGPT');if(button)button.disabled=true;try{const result=await api('/pixel/api/import-chatgpt',{method:'POST',body:data});await refreshAll();const job=allJobs.find(j=>j.id===result.id);if(job){displayResult(job);$('#compareSelect').value=reference?'reference':'none';updateCompare(job)}$('#importStatus').textContent='取り込み完了。';pastedResultFile=null;$('#importResult').value='';if(pastedResultUrl){URL.revokeObjectURL(pastedResultUrl);pastedResultUrl=null}$('#chatgptImportPreview').classList.add('hidden');toast('画像を取り込みました。')}catch(e){$('#importStatus').textContent='取り込み失敗：'+e.message;toast(e.message,true)}finally{if(button)button.disabled=false;importBusy=false}}
async function jobImageFile(job, preferReference=false){const filename=preferReference&&job.files?.includes('reference.png')?'reference.png':jobFiles(job).find(f=>f.endsWith('.png'));if(!filename)throw Error('利用できる画像がありません');const url=`/pixel/api/jobs/${encodeURIComponent(job.id)}/files/${encodeURIComponent(filename)}`;const r=await fetch(url);if(!r.ok)throw Error('画像を取得できませんでした');const blob=await r.blob();return new File([blob],filename,{type:'image/png'})}
async function useResultAsReference(){const job=allJobs.find(j=>j.id===currentJob);if(!job)return;try{const file=await jobImageFile(job);setReferenceFile(file);$('#create').scrollIntoView({behavior:'smooth'});toast('生成画像を参照画像にセットしました。')}catch(e){toast(e.message,true)}}
async function reuseJobSettings(){const job=allJobs.find(j=>j.id===currentJob);if(!job)return;try{$('#provider').value=job.provider||'pixellab';setProvider();if([...$('#model').options].some(x=>x.value===job.model))$('#model').value=job.model;setMode(job.mode||'text');if(job.size&&[...$('#size').options].some(x=>+x.value===job.size))$('#size').value=job.size;if(job.frames)$('#frames').value=job.frames;if(job.width)$('#imageWidth').value=job.width;if(job.height)$('#imageHeight').value=job.height;if(job.fidelity&&[...$('#fidelity').options].some(x=>x.value===job.fidelity))$('#fidelity').value=job.fidelity;if(job.quality)$('#quality').value=job.quality;if(job.gpt_size)$('#gptSize').value=job.gpt_size;if(typeof job.transparent==='boolean')$('#transparent').checked=job.transparent;$('#description').value=job.prompt||'';$('#assetCategory').value=job.category||autoCategoryForMode(job.mode||'text');$('#assetName').value=job.asset_name||'';if(job.files?.includes('reference.png')){const file=await jobImageFile(job,true);setReferenceFile(file)}refreshUI();$('#create').scrollIntoView({behavior:'smooth'});toast('前回の設定と参照画像を再利用しました。')}catch(e){toast(e.message,true)}}
async function copyResultImage(){const job=allJobs.find(j=>j.id===currentJob);if(!job)return;try{const file=await jobImageFile(job);if(!navigator.clipboard?.write || typeof ClipboardItem==='undefined')throw Error('画像コピーに対応していない環境です。');const bitmap=await createImageBitmap(file);const canvas=document.createElement('canvas');canvas.width=bitmap.width;canvas.height=bitmap.height;canvas.getContext('2d').drawImage(bitmap,0,0);bitmap.close();const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!blob)throw Error('コピー用の画像を作成できませんでした');await navigator.clipboard.write([new ClipboardItem({'image/png':blob})]);toast('画像をクリップボードにコピーしました。')}catch(e){toast(e.message,true)}}
function handleImagePaste(ev){const items=[...(ev.clipboardData?.items||[])];const item=items.find(x=>x.kind==='file'&&x.type.startsWith('image/'));if(!item)return;const file=item.getAsFile();if(!file)return;if(getProvider()==='chatgpt'){ev.preventDefault();clipboardFile(file)}else if(mode==='convert'||mode==='animate'||mode==='vfx'){ev.preventDefault();setReferenceFile(file)}}
function bindFileDrop(el,handler){if(!el)return;el.addEventListener('dragover',ev=>{if(![...ev.dataTransfer.types].includes('Files'))return;ev.preventDefault();el.classList.add('drag-active')});el.addEventListener('dragleave',()=>el.classList.remove('drag-active'));el.addEventListener('drop',ev=>{el.classList.remove('drag-active');if(!ev.dataTransfer.files.length)return;ev.preventDefault();handler(ev.dataTransfer.files[0])})}
async function loadAssets(filter=''){try{const d=await api('/pixel/api/assets'+(filter?`?category=${encodeURIComponent(filter)}`:''));allAssets=d.assets||[];renderAssets();renderClipAssetOptions();return d}catch(e){toast(e.message,true)}}
function assetPrimaryUrl(asset){const file=asset.primary_file || (asset.files||[])[0];return file?`/pixel/api/assets/${encodeURIComponent(asset.id)}/files/${encodeURIComponent(file)}`:null}
function renderAssets(){const box=$('#assetList');$('#assetCount').textContent=(allAssets.length||0)+' ASSETS';if(!box)return;box.replaceChildren();if(!allAssets.length){box.innerHTML='<p class="history-empty">まだアセットがありません。</p>';return}for(const a of allAssets){const item=document.createElement('div');item.className='history-item asset-item';let thumb='<span class="history-thumb history-placeholder">♪</span>';if(a.kind!=='audio'){const url=assetPrimaryUrl(a);if(url)thumb=`<img class="history-thumb" src="${url}" alt="">`;}item.innerHTML=`${thumb}<div class="history-main"><b>${safe(a.name)}</b><small>${safe(categoryLabels[a.category]||a.category)} · ${safe(a.kind)} · ${safe((a.created_at||'').slice(0,10))}</small></div><div class="history-pricing"><strong>${safe(a.source||'')}</strong><span>${safe((a.files||[]).join(', '))}</span></div>`;item.addEventListener('click',()=>{if($('#clipAsset'))$('#clipAsset').value=a.id;$('#sequence').scrollIntoView({behavior:'smooth'})});box.append(item)}}
function renderClipAssetOptions(){const sel=$('#clipAsset');if(!sel)return;const prev=sel.value;sel.replaceChildren();const def=document.createElement('option');def.value='';def.textContent='アセットを選択';sel.append(def);for(const a of allAssets){const opt=document.createElement('option');opt.value=a.id;opt.textContent=`${a.name} / ${categoryLabels[a.category]||a.category}`;sel.append(opt)}if([...sel.options].some(x=>x.value===prev))sel.value=prev}
async function importAsset(){const file=$('#importFile').files[0];if(!file)return toast('取り込むファイルを選択してください',true);const data=new FormData();data.set('file',file);data.set('category',$('#importCategory').value);data.set('name',$('#importName').value.trim());try{await api('/pixel/api/assets/import',{method:'POST',body:data});$('#importFile').value='';$('#importName').value='';toast('アセットを取り込みました');await loadAssets($('#assetFilter').value)}catch(e){toast(e.message,true)}}
async function loadSequences(){try{const d=await api('/pixel/api/sequences');allSequences=d.sequences||[];renderSequenceOptions();$('#sequenceCount').textContent=(allSequences.length||0)+' SEQUENCES';if(currentSequenceId&&!allSequences.find(s=>s.id===currentSequenceId))currentSequenceId='';if(currentSequenceId)await openSequence(currentSequenceId);return d}catch(e){toast(e.message,true)}}
function renderSequenceOptions(){const sel=$('#sequenceSelect');if(!sel)return;const prev=currentSequenceId||sel.value;sel.replaceChildren();const def=document.createElement('option');def.value='';def.textContent='未選択';sel.append(def);for(const s of allSequences){const opt=document.createElement('option');opt.value=s.id;opt.textContent=`${s.name} (${s.clip_count||0})`;sel.append(opt)}if([...sel.options].some(x=>x.value===prev))sel.value=prev}
async function createSequence(){const name=$('#sequenceName').value.trim();if(!name)return toast('シーケンス名を入力してください',true);try{const seq=await postJSON('/pixel/api/sequences',{name,fps:Number($('#sequenceFps').value),width:Number($('#sequenceWidth').value),height:Number($('#sequenceHeight').value)});currentSequenceId=seq.id;$('#sequenceSelect').value=seq.id;await loadSequences();await openSequence(seq.id);toast('シーケンスを作成しました')}catch(e){toast(e.message,true)}}
async function saveSequence(){if(!currentSequenceId)return toast('保存するシーケンスを選択してください',true);try{const seq=await postJSON('/pixel/api/sequences/'+encodeURIComponent(currentSequenceId),{name:$('#sequenceName').value.trim()||'sequence',fps:Number($('#sequenceFps').value),width:Number($('#sequenceWidth').value),height:Number($('#sequenceHeight').value)},'PUT');await loadSequences();await openSequence(seq.id);toast('シーケンス設定を保存しました')}catch(e){toast(e.message,true)}}
async function deleteSequence(){if(!currentSequenceId)return toast('削除するシーケンスを選択してください',true);if(!confirm('このシーケンスを削除しますか？'))return;try{await api('/pixel/api/sequences/'+encodeURIComponent(currentSequenceId),{method:'DELETE'});currentSequenceId='';$('#timelineView').innerHTML='<p class="history-empty">シーケンスを削除しました。</p>';$('#clipList').innerHTML='<p class="history-empty">クリップはまだありません。</p>';$('#sequenceMeta').textContent='未選択';await loadSequences();toast('削除しました')}catch(e){toast(e.message,true)}}
async function openSequence(id){if(!id){currentSequenceId='';$('#sequenceMeta').textContent='未選択';$('#timelineView').innerHTML='<p class="history-empty">シーケンスを作成すると、ここにレーン別タイムラインが表示されます。</p>';$('#clipList').innerHTML='<p class="history-empty">クリップはまだありません。</p>';return}try{const seq=await api('/pixel/api/sequences/'+encodeURIComponent(id));currentSequenceId=seq.id;$('#sequenceSelect').value=seq.id;$('#sequenceName').value=seq.name||'';$('#sequenceFps').value=seq.fps||12;$('#sequenceWidth').value=seq.width||240;$('#sequenceHeight').value=seq.height||160;$('#sequenceMeta').textContent=`${seq.width}×${seq.height} / ${seq.fps}fps / ${seq.clips?.length||0} clips`;renderTimeline(seq);renderClipList(seq)}catch(e){toast(e.message,true)}}
function renderTimeline(seq){const box=$('#timelineView');const clips=seq.clips||[];if(!clips.length){box.innerHTML='<p class="history-empty">クリップはまだありません。</p>';return}const tracks=['background','item','character','effect','sound'];const maxFrame=Math.max(...clips.map(c=>c.start+c.duration),1);const scale=Math.max(12, Math.min(28, Math.floor(720/maxFrame)));let html='';for(const track of tracks){const row=clips.filter(c=>c.track===track);let bars='';for(const c of row){bars+=`<div class="timeline-clip timeline-${safe(track)}" style="left:${c.start*scale}px;width:${Math.max(24,c.duration*scale)}px" title="${safe(c.asset_name)} / ${c.start} - ${c.start+c.duration}">${safe(c.label||c.asset_name)}</div>`;}html+=`<div class="timeline-row"><div class="timeline-label">${safe(categoryLabels[track]||track)}</div><div class="timeline-lane" style="width:${maxFrame*scale+20}px">${bars}</div></div>`}box.innerHTML=`<div class="timeline-ruler">全長 ${maxFrame} フレーム</div>${html}`}
function renderClipList(seq){const box=$('#clipList');const clips=seq.clips||[];box.replaceChildren();if(!clips.length){box.innerHTML='<p class="history-empty">クリップはまだありません。</p>';return}for(const c of clips){const item=document.createElement('div');item.className='history-item';item.innerHTML=`<span class="history-thumb history-placeholder">${safe((categoryLabels[c.track]||c.track).charAt(0))}</span><div class="history-main"><b>${safe(c.label||c.asset_name)}</b><small>${safe(c.track)} · frame ${c.start} → ${c.start+c.duration} · x:${c.x} y:${c.y} · vol:${c.volume}</small></div><div class="history-pricing"><button type="button" class="quiet-btn" data-remove="${c.id}">削除</button></div>`;item.querySelector('[data-remove]').addEventListener('click',()=>removeClip(c.id));box.append(item)}}
async function addClip(){if(!currentSequenceId)return toast('先にシーケンスを作成または選択してください',true);const assetId=$('#clipAsset').value;if(!assetId)return toast('アセットを選択してください',true);try{await postJSON('/pixel/api/sequences/'+encodeURIComponent(currentSequenceId)+'/clips',{asset_id:assetId,track:$('#clipTrack').value,start:Number($('#clipStart').value),duration:Number($('#clipDuration').value),volume:Number($('#clipVolume').value),x:Number($('#clipX').value),y:Number($('#clipY').value),label:$('#clipLabel').value.trim()});await loadSequences();await openSequence(currentSequenceId);toast('タイムラインに追加しました')}catch(e){toast(e.message,true)}}
async function removeClip(id){if(!currentSequenceId)return;try{await api(`/pixel/api/sequences/${encodeURIComponent(currentSequenceId)}/clips/${encodeURIComponent(id)}`,{method:'DELETE'});await loadSequences();await openSequence(currentSequenceId);toast('クリップを削除しました')}catch(e){toast(e.message,true)}}
async function exportSequence(){if(!currentSequenceId)return toast('シーケンスを選択してください',true);try{const d=await api('/pixel/api/sequences/'+encodeURIComponent(currentSequenceId)+'/export');$('#ollamaOutput').value=JSON.stringify(d,null,2);$('#ollamaOutputLabel').textContent='シーケンスJSON';$('#ollamaOutputWrap').classList.remove('hidden');$('#applyOllama').classList.add('hidden');$('#sequence').scrollIntoView({behavior:'smooth'});toast('JSONを表示しました')}catch(e){toast(e.message,true)}}
async function refreshAll(){const s=await status();await Promise.all([loadAssets($('#assetFilter')?.value||''), loadSequences()]);return s}
function attachEvents(){
 document.querySelectorAll('.tab').forEach(el=>el.addEventListener('click',()=>{if(!el.disabled)setMode(el.dataset.mode)}));
 $('#provider').addEventListener('change',setProvider);$('#model').addEventListener('change',refreshUI);
 for(const id of ['size','frames','fidelity','quality','gptSize','reservation','demo','transparent','assetCategory','imageWidth','imageHeight'])$('#'+id).addEventListener('change',refreshUI);
 for(const id of ['imageWidth','imageHeight'])$('#'+id).addEventListener('input',refreshUI);
 document.querySelectorAll('[data-purpose]').forEach(el=>el.addEventListener('click',()=>applyPurpose(el.dataset.purpose)));
 document.querySelectorAll('[data-dims]').forEach(el=>el.addEventListener('click',()=>{const [w,h]=el.dataset.dims.split('x').map(Number);$('#imageWidth').value=w;$('#imageHeight').value=h;refreshUI()}));
 $('#description').addEventListener('input',()=>{if(getProvider()==='chatgpt')$('#browserPreparedPrompt').value=browserPrompt()});
 $('#referenceImage').addEventListener('change',()=>{fileInputUpdate();updateEstimate();updateAnimationHelp()});$('#compareSelect').addEventListener('change',()=>{const job=allJobs.find(j=>j.id===currentJob);if(job)updateCompare(job)});
 document.querySelectorAll('[data-text]').forEach(b=>b.addEventListener('click',()=>{$('#description').value=b.dataset.text;if(getProvider()==='chatgpt')$('#browserPreparedPrompt').value=browserPrompt()}));
 $('#generate').addEventListener('click',generate);attachUIFrameEvents();$('#refreshBalance').addEventListener('click',()=>fetchBalance());
 for(const [provider,input,save,clear] of [['pixellab','apiToken','saveToken','clearToken'],['openai','openaiToken','saveOpenAI','clearOpenAI']]){$('#'+save).addEventListener('click',async()=>{const token=$('#'+input).value.trim();if(!token)return toast('APIキーを入力してください',true);try{await postJSON('/pixel/api/session',{token,provider});$('#'+input).value='';await status();if(provider==='pixellab')await fetchBalance(true);toast((provider==='openai'?'OpenAI':'PixelLab')+'キーをメモリに設定しました')}catch(e){toast(e.message,true)}});$('#'+clear).addEventListener('click',async()=>{try{await api('/pixel/api/session?provider='+provider,{method:'DELETE'});await status();if(provider==='pixellab'){$('#providerCredits').textContent='—';$('#providerGenerations').textContent='—'}toast('接続情報を解除しました')}catch(e){toast(e.message,true)}})}
 $('#saveBudget').addEventListener('click',async()=>{try{await postJSON('/pixel/api/settings',{budget_usd:Number($('#budget').value)});await status();toast('月間予算を保存しました')}catch(e){toast(e.message,true)}});
 $('#newWindow').addEventListener('click',()=>window.open('/pixel/','_blank','noopener'));$('#refreshOllama').addEventListener('click',refreshOllama);$('#improvePrompt').addEventListener('click',()=>ollamaAssist('refine'));$('#describeImage').addEventListener('click',()=>ollamaAssist('describe'));$('#compareWithOllama').addEventListener('click',compareWithOllama);$('#applyOllama').addEventListener('click',()=>{savedPrompt=$('#description').value;$('#description').value=$('#ollamaOutput').value.slice(0,2000);if(getProvider()==='chatgpt')$('#browserPreparedPrompt').value=browserPrompt();$('#undoPrompt').classList.remove('hidden');toast('プロンプトに反映しました。')});$('#undoPrompt').addEventListener('click',()=>{if(savedPrompt!==null){$('#description').value=savedPrompt;if(getProvider()==='chatgpt')$('#browserPreparedPrompt').value=browserPrompt();savedPrompt=null;$('#undoPrompt').classList.add('hidden');toast('変更前の指示に戻しました')}});$('#launchChatGPT').addEventListener('click',launchChatGPT);if($('#importChatGPT'))$('#importChatGPT').addEventListener('click',importChatGPT);$('#pasteChatGPT').addEventListener('click',clipboardReadButton);$('#copyBrowserPrompt').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(browserPrompt());toast('プロンプトをコピーしました')}catch(e){toast('コピー失敗：'+e.message,true)}});$('#importResult').addEventListener('change',()=>{if($('#importResult').files[0])clipboardFile($('#importResult').files[0])});document.addEventListener('paste',handleImagePaste);bindFileDrop($('#chatgptDropzone'),file=>{if(getProvider()!=='chatgpt'){$('#provider').value='chatgpt';setProvider()}clipboardFile(file)});bindFileDrop(document.querySelector('#imageField .dropzone'),setReferenceFile);$('#useResultAsReference').addEventListener('click',useResultAsReference);$('#reuseJobSettings').addEventListener('click',reuseJobSettings);$('#copyResultImage').addEventListener('click',copyResultImage);$('#toggleAccount').addEventListener('click',()=>{const collapsed=$('#account').classList.toggle('collapsed');$('#toggleAccount').textContent=collapsed?'接続・料金を表示 ▾':'接続・料金を閉じる ▴';$('#toggleAccount').setAttribute('aria-expanded',String(!collapsed))});for(const [id,rating] of [['rateGood','good'],['rateBad','bad']])$('#'+id).addEventListener('click',async()=>{if(!currentJob)return;try{await postJSON(`/pixel/api/jobs/${encodeURIComponent(currentJob)}/rating`,{rating});const s=await status();const j=s.jobs.find(x=>x.id===currentJob);if(j)displayResult(j);toast('評価をローカルに保存しました')}catch(e){toast(e.message,true)}});
 if($('#assetFilter'))$('#assetFilter').addEventListener('change',()=>loadAssets($('#assetFilter').value));
 if($('#importAssetBtn'))$('#importAssetBtn').addEventListener('click',importAsset);
 if($('#sequenceSelect'))$('#sequenceSelect').addEventListener('change',()=>openSequence($('#sequenceSelect').value));
 if($('#createSequenceBtn'))$('#createSequenceBtn').addEventListener('click',createSequence);
 if($('#saveSequenceBtn'))$('#saveSequenceBtn').addEventListener('click',saveSequence);
 if($('#deleteSequenceBtn'))$('#deleteSequenceBtn').addEventListener('click',deleteSequence);
 if($('#addClipBtn'))$('#addClipBtn').addEventListener('click',addClip);
 if($('#exportSequenceBtn'))$('#exportSequenceBtn').addEventListener('click',exportSequence);
}
async function init(){attachEvents();setProvider();const s=await status();if(s?.has_key)await fetchBalance(true);await Promise.all([refreshOllama(), loadAssets(), loadSequences()]);const last=allJobs[0];if(last){displayResult(last);if(['processing','queued'].includes(last.status))watch(last.id)}}
init();

// v0.7: battle player, frame-accurate sound cues, editable timeline, exports.
let battleSequence = null;
let selectedClipId = null;
let battlePlaying = false;
let battleTicker = 0;
let battleStarted = 0;
let battleStartFrame = 0;
let battleFrame = 0;
let battleDrawTicket = 0;
let battleFrameRequested = -1;
let soundInstances = new Map();
let dragStart = null;

function battleLastFrame(){ return Math.max(0, ...(battleSequence?.clips || []).map(c => Number(c.start) + Number(c.duration) - 1)); }
function battleLength(){ return battleLastFrame()+1; }
function battleAssetUrl(asset){const f=asset?.primary_file||(asset?.files||[])[0];return f?`/pixel/api/assets/${encodeURIComponent(asset.id)}/files/${encodeURIComponent(f)}`:null;}
function stopBattleSounds(){for(const a of soundInstances.values()){try{a.pause();a.currentTime=0;}catch(_){}}soundInstances.clear();}
function haltBattle(reset=false){battlePlaying=false;cancelAnimationFrame(battleTicker);stopBattleSounds();$('#seqPlay').textContent='▶ 再生';if(reset)seekBattle(0);}
function cueSounds(frame, resume=false){
 if(!battleSequence)return;
 for(const clip of battleSequence.clips||[]){
  if(clip.track!=='sound')continue;
  const start=Number(clip.start),end=start+Number(clip.duration);
  if(frame<start||frame>=end||soundInstances.has(clip.id)||(!resume&&frame!==start))continue;
  const asset=allAssets.find(a=>a.id===clip.asset_id),url=battleAssetUrl(asset);
  if(!url)continue;
  const snd=new Audio(url);snd.volume=Math.max(0,Math.min(1,Number(clip.volume)||0));snd.preload='auto';
  const offset=(frame-start)/battleSequence.fps;
  snd.addEventListener('loadedmetadata',()=>{if(offset>0 && snd.duration>offset)try{snd.currentTime=offset;}catch(_){}},{once:true});
  soundInstances.set(clip.id,snd);
  snd.play().catch(()=>toast('効果音を再生できません。ファイル形式またはブラウザの音声許可を確認してください。',true));
 }
 for(const [id,snd] of soundInstances){const clip=(battleSequence.clips||[]).find(c=>c.id===id);if(!clip||frame>=Number(clip.start)+Number(clip.duration)){snd.pause();soundInstances.delete(id);}}
}
function tickBattle(ts){
 if(!battlePlaying||!battleSequence)return;
 const target=Math.floor((ts-battleStarted)/1000*battleSequence.fps)+battleStartFrame;
 const previous=battleFrame;
 if(target>=battleLength()){haltBattle();seekBattle(battleLastFrame());return;}
 if(target!==previous){
   for(let f=previous+1;f<=target;f++)cueSounds(f);
   seekBattle(target,false);
 }
 battleTicker=requestAnimationFrame(tickBattle);
}
function toggleBattle(){
 if(!battleSequence)return toast('先にシーケンスを選択してください。',true);
 if(battlePlaying){haltBattle();return;}
 battlePlaying=true;$('#seqPlay').textContent='❚❚ 一時停止';battleStartFrame=battleFrame;
 if(battleStartFrame>=battleLastFrame()&&battleLastFrame()>0){seekBattle(0);battleStartFrame=0;}
 cueSounds(battleStartFrame,true);
 battleStarted=performance.now();battleTicker=requestAnimationFrame(tickBattle);
}
function seekBattle(frame, stop=true){
 if(!battleSequence)return;
 if(stop&&battlePlaying)haltBattle();
 battleFrame=Math.max(0,Math.min(battleLastFrame(),Math.round(frame)||0));
 $('#seqScrub').max=String(battleLastFrame());$('#seqScrub').value=String(battleFrame);
 $('#seqFrameLabel').textContent=`${battleFrame}f / ${battleLastFrame()}f`;
 if(battleFrameRequested===battleFrame)return;
 battleFrameRequested=battleFrame;
 const ticket=++battleDrawTicket;
 const url=`/pixel/api/sequences/${encodeURIComponent(battleSequence.id)}/preview/${battleFrame}`;
 fetch(url).then(async response=>{
   if(!response.ok)throw Error((await response.json()).detail||'フレームの表示に失敗');
   return response.blob();
 }).then(createImageBitmap).then(bitmap=>{
   if(ticket!==battleDrawTicket){bitmap.close();return;}
   const canvas=$('#battlePreview');if(canvas.width!==bitmap.width||canvas.height!==bitmap.height){canvas.width=bitmap.width;canvas.height=bitmap.height;}
   const ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);ctx.imageSmoothingEnabled=false;
   ctx.drawImage(bitmap,0,0);bitmap.close();
 }).catch(err=>{if(ticket===battleDrawTicket)$('#exportStatus').textContent='プレビュー: '+err.message;});
}
function resetBattleView(seq){
 haltBattle();battleSequence=seq;selectedClipId=null;$('#updateClipBtn').classList.add('hidden');$('#cancelClipEditBtn').classList.add('hidden');$('#addClipBtn').classList.remove('hidden');
 battleFrameRequested=-1;
 $('#seqScrub').max=String(battleLastFrame());
 seekBattle(0);
}
async function openSequence(id){
 if(!id){currentSequenceId='';battleSequence=null;haltBattle();$('#sequenceMeta').textContent='未選択';$('#timelineView').innerHTML='<p class="history-empty">シーケンスを作成するとタイムラインが表示されます。</p>';$('#clipList').innerHTML='<p class="history-empty">クリップはまだありません。</p>';return;}
 try{
  const seq=await api('/pixel/api/sequences/'+encodeURIComponent(id));currentSequenceId=seq.id;
  $('#sequenceSelect').value=seq.id;$('#sequenceName').value=seq.name||'';
  $('#sequenceFps').value=seq.fps||12;$('#sequenceWidth').value=seq.width||240;$('#sequenceHeight').value=seq.height||160;
  $('#sequenceMeta').textContent=`${seq.width}×${seq.height} / ${seq.fps}fps / ${seq.clips?.length||0} clips`;
  resetBattleView(seq);renderTimeline(seq);renderClipList(seq);
 }catch(e){toast(e.message,true)}
}
function selectBattleClip(id, scrub=true){
 if(!battleSequence)return;
 const clip=(battleSequence.clips||[]).find(c=>c.id===id);if(!clip)return;
 selectedClipId=id;
 $('#clipAsset').value=clip.asset_id;
 $('#clipTrack').value=clip.track;$('#clipStart').value=clip.start;$('#clipDuration').value=clip.duration;
 $('#clipVolume').value=clip.volume;$('#clipX').value=clip.x;$('#clipY').value=clip.y;$('#clipLabel').value=clip.label||'';
 $('#updateClipBtn').classList.remove('hidden');$('#cancelClipEditBtn').classList.remove('hidden');$('#addClipBtn').classList.add('hidden');
 for(const node of document.querySelectorAll('[data-clip-id]'))node.classList.toggle('clip-selected',node.dataset.clipId===id);
 if(scrub)seekBattle(clip.start);
}
function clearBattleSelection(){selectedClipId=null;$('#updateClipBtn').classList.add('hidden');$('#cancelClipEditBtn').classList.add('hidden');$('#addClipBtn').classList.remove('hidden');document.querySelectorAll('[data-clip-id]').forEach(n=>n.classList.remove('clip-selected'));}
async function saveSelectedClip(){
 if(!selectedClipId||!currentSequenceId)return;
 const data={track:$('#clipTrack').value,start:Number($('#clipStart').value),duration:Number($('#clipDuration').value),volume:Number($('#clipVolume').value),x:Number($('#clipX').value),y:Number($('#clipY').value),label:$('#clipLabel').value.trim()};
 try{await postJSON(`/pixel/api/sequences/${encodeURIComponent(currentSequenceId)}/clips/${encodeURIComponent(selectedClipId)}`,data,'PUT');const id=selectedClipId;await loadSequences();selectBattleClip(id,false);toast('クリップを更新しました。')}catch(e){toast(e.message,true)}
}
function renderTimeline(seq){
 const box=$('#timelineView'),clips=seq.clips||[],tracks=['background','item','character','effect','sound'];
 const maxFrame=Math.max(24,...clips.map(c=>c.start+c.duration));const scale=Math.max(7,Math.min(24,Math.floor(900/maxFrame)));
 const ruler=document.createElement('div');ruler.className='timeline-ruler';ruler.textContent=`0 ─ ${maxFrame} フレーム　／　クリップを左右にドラッグして位置変更。右端をドラッグで長さ変更。`;box.replaceChildren(ruler);
 for(const track of tracks){
   const row=document.createElement('div');row.className='timeline-row';
   const label=document.createElement('div');label.className='timeline-label';label.textContent=categoryLabels[track];
   const lane=document.createElement('div');lane.className='timeline-lane';lane.style.width=`${maxFrame*scale+20}px`;lane.dataset.track=track;
   lane.addEventListener('click',event=>{if(event.target===lane)seekBattle(Math.floor(event.offsetX/scale));});
   for(const c of clips.filter(c=>c.track===track)){
      const clip=document.createElement('div');clip.className=`timeline-clip timeline-${track}`;clip.dataset.clipId=c.id;clip.title=`${c.asset_name} / ${c.start}–${c.start+c.duration}f`;
      clip.style.left=`${c.start*scale}px`;clip.style.width=`${Math.max(24,c.duration*scale)}px`;
      clip.textContent=c.label||c.asset_name;
      clip.addEventListener('click',ev=>{ev.stopPropagation();selectBattleClip(c.id);});
      clip.addEventListener('pointerdown',ev=>{
        if(ev.button!==0)return;ev.stopPropagation();selectBattleClip(c.id,false);
        dragStart={id:c.id,x:ev.clientX,start:c.start,duration:c.duration,resize:ev.offsetX>clip.clientWidth-11,scale};
        clip.setPointerCapture(ev.pointerId);
      });
      clip.addEventListener('pointermove',ev=>{
        if(!dragStart||dragStart.id!==c.id)return;
        const delta=Math.round((ev.clientX-dragStart.x)/scale);
        const start=dragStart.resize?dragStart.start:Math.max(0,dragStart.start+delta);
        const duration=dragStart.resize?Math.max(1,dragStart.duration+delta):dragStart.duration;
        clip.style.left=`${start*scale}px`;clip.style.width=`${Math.max(24,duration*scale)}px`;
        $('#clipStart').value=start;$('#clipDuration').value=duration;
      });
      clip.addEventListener('pointerup',ev=>{if(!dragStart||dragStart.id!==c.id)return;dragStart=null;clip.releasePointerCapture(ev.pointerId);saveSelectedClip();});
      clip.addEventListener('pointercancel',()=>{dragStart=null;});
      lane.append(clip);
   }
   row.append(label,lane);box.append(row);
 }
}
function renderClipList(seq){
 const box=$('#clipList'),clips=seq.clips||[];box.replaceChildren();
 if(!clips.length){box.innerHTML='<p class="history-empty">クリップはまだありません。先に素材をレーンへ追加してください。</p>';return;}
 for(const c of clips){const item=document.createElement('div');item.className='history-item';item.dataset.clipId=c.id;
   item.innerHTML=`<span class="history-thumb history-placeholder">${safe(categoryLabels[c.track]?.charAt(0)||'?')}</span><div class="history-main"><b>${safe(c.label||c.asset_name)}</b><small>${safe(categoryLabels[c.track]||c.track)} · ${c.start}〜${c.start+c.duration}f · X:${c.x} Y:${c.y} · 音量:${c.volume}</small></div><div class="history-pricing"><button type="button" class="quiet-btn" data-remove="${c.id}">削除</button></div>`;
   item.addEventListener('click',()=>selectBattleClip(c.id));item.querySelector('[data-remove]').addEventListener('click',ev=>{ev.stopPropagation();removeClip(c.id);});box.append(item);
 }
}
async function loadAssets(){
 try{const d=await api('/pixel/api/assets');allAssets=d.assets||[];renderAssets();renderClipAssetOptions();return d;}catch(e){toast(e.message,true)}
}
function renderAssets(){
 const filter=$('#assetFilter')?.value||'';const items=allAssets.filter(a=>!filter||a.category===filter),box=$('#assetList');
 $('#assetCount').textContent=allAssets.length+' ASSETS';box.replaceChildren();if(!items.length){box.innerHTML='<p class="history-empty">該当アセットがありません。</p>';return;}
 for(const a of items){const item=document.createElement('div');item.className='history-item asset-item';
  const url=battleAssetUrl(a);const pic=a.kind==='audio'?'<span class="history-thumb history-placeholder">♪</span>':url?`<img class="history-thumb" src="${url}" alt="">`:'<span class="history-thumb history-placeholder">▦</span>';
  item.innerHTML=`${pic}<div class="history-main"><b>${safe(a.name)}</b><small>${safe(categoryLabels[a.category]||a.category)} · ${safe(a.kind)} · ${(a.files||[]).length}ファイル</small></div>`;
  item.addEventListener('click',()=>{const option=[...$('#clipAsset').options].find(o=>o.value===a.id);if(option){$('#clipAsset').value=a.id;$('#clipTrack').value=a.kind==='audio'?'sound':(a.category==='other'||a.category==='ui')?'item':a.category;$('#sequence').scrollIntoView({behavior:'smooth'});}});box.append(item);
 }
}
async function importAsset(){
 const file=$('#importFile').files[0];if(!file)return toast('取り込むファイルを選択してください',true);
 const data=new FormData();data.set('file',file);data.set('category',$('#importCategory').value);data.set('name',$('#importName').value.trim());data.set('columns',$('#importColumns').value);data.set('rows',$('#importRows').value);
 try{await api('/pixel/api/assets/import',{method:'POST',body:data});$('#importFile').value='';$('#importName').value='';await loadAssets();toast('アセットを取り込みました。')}catch(e){toast(e.message,true)}
}
async function downloadBattle(kind){
 if(!currentSequenceId)return toast('先にシーケンスを選択してください。',true);
 if((kind==='gif'||kind==='mp4')&&battleLength()>600)return toast('書き出しは600フレーム以内です。',true);
 const button=$('#export'+({json:'Json',zip:'Zip',gif:'Gif',mp4:'Mp4'}[kind])+'Btn');
 button.disabled=true;const previous=button.textContent;button.textContent='処理中…';
 $('#exportStatus').textContent=kind==='mp4'?'MP4と効果音を合成しています。アプリを閉じずにお待ちください。':'書き出しています…';
 try{
  const response=await fetch(`/pixel/api/sequences/${encodeURIComponent(currentSequenceId)}/download/${kind}`);
  if(!response.ok){let error;try{error=(await response.json()).detail}catch(_){error='書き出しに失敗しました。'}throw Error(error)}
  const blob=await response.blob(),url=URL.createObjectURL(blob),anchor=document.createElement('a');
  anchor.href=url;anchor.download=`${(battleSequence?.name||'battle').replace(/[\\/:*?"<>|]/g,'_')}.${kind}`;
  document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),4000);
  $('#exportStatus').textContent=`${kind.toUpperCase()}を書き出しました（${(blob.size/1024).toFixed(0)} KB）。`;
  toast('書き出しが完了しました。');
 }catch(e){$('#exportStatus').textContent=e.message;toast(e.message,true)}finally{button.disabled=false;button.textContent=previous;}
}
function installBattleControls(){
 $('#seqPlay').addEventListener('click',toggleBattle);
 $('#seqStop').addEventListener('click',()=>haltBattle(true));
 $('#seqScrub').addEventListener('input',ev=>seekBattle(Number(ev.target.value)));
 $('#updateClipBtn').addEventListener('click',saveSelectedClip);
 $('#cancelClipEditBtn').addEventListener('click',clearBattleSelection);
 for(const kind of ['json','zip','gif','mp4'])$('#export'+({json:'Json',zip:'Zip',gif:'Gif',mp4:'Mp4'}[kind])+'Btn').addEventListener('click',()=>downloadBattle(kind));
 $('#assetFilter').addEventListener('change',renderAssets);
 $('#clipAsset').addEventListener('change',()=>{const a=allAssets.find(a=>a.id===$('#clipAsset').value);if(a)$('#clipTrack').value=a.kind==='audio'?'sound':['background','item','character','effect'].includes(a.category)?a.category:'item';});
 window.addEventListener('beforeunload',stopBattleSounds);
}
installBattleControls();


// Local 9-slice UI frame builder. No external API call until the user explicitly
// chooses a separate generation action in the workbench.
function makeUIFrameTemplate(theme){
 const colors={
  navy:['#101d34','#273f60','#d3aa61','#816c56'],
  brown:['#2b201b','#51392a','#efd09a','#a88661'],
  green:['#142b29','#28574a','#c7dfd6','#73998d']
 }[theme]||['#101d34','#273f60','#d3aa61','#816c56'];
 const canvas=document.createElement('canvas');canvas.width=64;canvas.height=64;
 const ctx=canvas.getContext('2d');ctx.imageSmoothingEnabled=false;
 ctx.fillStyle=colors[0];ctx.fillRect(0,0,64,64);
 ctx.fillStyle=colors[1];ctx.fillRect(3,3,58,58);
 ctx.fillStyle=colors[0];ctx.fillRect(6,6,52,52);
 ctx.fillStyle=colors[2];
 ctx.fillRect(0,0,64,2);ctx.fillRect(0,62,64,2);ctx.fillRect(0,0,2,64);ctx.fillRect(62,0,2,64);
 ctx.fillStyle=colors[3];
 ctx.fillRect(3,3,58,1);ctx.fillRect(3,60,58,1);ctx.fillRect(3,3,1,58);ctx.fillRect(60,3,1,58);
 for(const [x,y] of [[1,1],[55,1],[1,55],[55,55]]){
  ctx.fillStyle=colors[2];ctx.fillRect(x,y,8,8);
  ctx.fillStyle=colors[0];ctx.fillRect(x+2,y+2,4,4);
 }
 return canvas;
}
function tileUIArea(ctx,source,sx,sy,sw,sh,dx,dy,dw,dh){
 if(sw<=0||sh<=0||dw<=0||dh<=0)return;
 for(let y=0;y<dh;y+=sh)for(let x=0;x<dw;x+=sw){
  const cw=Math.min(sw,dw-x),ch=Math.min(sh,dh-y);
  ctx.drawImage(source,sx,sy,cw,ch,dx+x,dy+y,cw,ch);
 }
}
function renderUIFrame(){
 const state=$('#uiFrameStatus'),canvas=$('#uiFrameCanvas');
 if(!canvas)return;
 const theme=$('#uiTheme').value;
 const source=theme==='custom'?uiFrameSource:makeUIFrameTemplate(theme);
 const width=Number($('#uiTargetWidth').value),height=Number($('#uiTargetHeight').value),corner=Number($('#uiCornerSize').value);
 const valid=source&&[width,height,corner].every(Number.isInteger)&&width>=32&&width<=2048&&height>=24&&height<=1024&&corner>=1&&corner<=256&&corner*2<Math.min(source.width,source.height,width,height);
 $('#uiDownload').disabled=!valid;$('#uiRegister').disabled=!valid;
 if(!valid){
  state.textContent=source?'寸法を確認してください。角は原画と出力の幅・高さの半分未満にしてください。':'画像が未読み込みです。サンプルを選ぶか原画を読み込んでください。';
  state.classList.add('limit-warning');return;
 }
 state.classList.remove('limit-warning');
 canvas.width=width;canvas.height=height;
 const ctx=canvas.getContext('2d');ctx.imageSmoothingEnabled=false;
 const sw=source.width,sh=source.height,c=corner,midW=width-2*c,midH=height-2*c;
 // Center, four edges, four corners. Tile instead of blurring or stretching.
 tileUIArea(ctx,source,c,c,sw-2*c,sh-2*c,c,c,midW,midH);
 tileUIArea(ctx,source,c,0,sw-2*c,c,c,0,midW,c);
 tileUIArea(ctx,source,c,sh-c,sw-2*c,c,c,height-c,midW,c);
 tileUIArea(ctx,source,0,c,c,sh-2*c,0,c,c,midH);
 tileUIArea(ctx,source,sw-c,c,c,sh-2*c,width-c,c,c,midH);
 ctx.drawImage(source,0,0,c,c,0,0,c,c);
 ctx.drawImage(source,sw-c,0,c,c,width-c,0,c,c);
 ctx.drawImage(source,0,sh-c,c,c,0,height-c,c,c);
 ctx.drawImage(source,sw-c,sh-c,c,c,width-c,height-c,c,c);
 $('#uiFrameSize').textContent=width+' × '+height+' px';
 state.textContent='原画 '+sw+'×'+sh+'px / 四隅 '+c+'px / 出力 '+width+'×'+height+'px。繰り返し部分の継ぎ目を確認してください。';
}
async function setUIFrameSource(file){
 if(!file||!['image/png','image/webp'].includes(file.type)||file.size>12*1024*1024){
  toast('12MB以下のPNGまたはWebPを選んでください',true);return;
 }
 try{
  const bitmap=await createImageBitmap(file);
  if(bitmap.width>2048||bitmap.height>2048||bitmap.width<3||bitmap.height<3){bitmap.close();throw Error('原画は3〜2048pxの範囲で指定してください');}
  if(uiFrameSource&&typeof uiFrameSource.close==='function')uiFrameSource.close();
  uiFrameSource=bitmap;$('#uiTheme').value='custom';renderUIFrame();toast('枠の原画を読み込みました');
 }catch(e){toast('原画の読み込み：'+e.message,true)}
}
function uiPNGBlob(){
 return new Promise((resolve,reject)=>$('#uiFrameCanvas').toBlob(blob=>blob?resolve(blob):reject(Error('PNGを作成できませんでした')),'image/png'));
}
function uiFrameFilename(){
 const raw=($('#uiFrameName').value||'ui_frame').trim().replace(/[\\/:*?"<>|]/g,'_').slice(0,90);
 return (raw||'ui_frame')+'.png';
}
function attachUIFrameEvents(){
 if(!$('#uiFrameCanvas'))return;
 attachUIAIGenerationEvents();
 $('#uiTheme').addEventListener('change',renderUIFrame);
 $('#uiSourceFile').addEventListener('change',()=>{if($('#uiSourceFile').files[0])setUIFrameSource($('#uiSourceFile').files[0])});
 $('#uiFramePreset').addEventListener('change',()=>{
  if($('#uiFramePreset').value!=='custom'){
   const [w,h]=$('#uiFramePreset').value.split('x').map(Number);
   $('#uiTargetWidth').value=w;$('#uiTargetHeight').value=h;
  }
  renderUIFrame();
 });
 for(const id of ['uiTargetWidth','uiTargetHeight','uiCornerSize']){
  $('#'+id).addEventListener('input',()=>{
   if(id!=='uiCornerSize')$('#uiFramePreset').value='custom';
   renderUIFrame();
  });
 }
 $('#uiUseCurrent').addEventListener('click',async()=>{
  const job=allJobs.find(j=>j.id===currentJob);
  if(!job||job.status!=='completed')return toast('先に生成済みの画像をプレビューから選んでください',true);
  try{await setUIFrameSource(await jobImageFile(job));$('#ui-frame').scrollIntoView({behavior:'smooth'})}
  catch(e){toast(e.message,true)}
 });
 $('#uiDownload').addEventListener('click',async()=>{
  try{
   const blob=await uiPNGBlob(),url=URL.createObjectURL(blob),link=document.createElement('a');
   link.href=url;link.download=uiFrameFilename();document.body.append(link);link.click();link.remove();
   setTimeout(()=>URL.revokeObjectURL(url),1000);
   toast('UI枠のPNGを保存しました');
  }catch(e){toast(e.message,true)}
 });
 $('#uiRegister').addEventListener('click',async()=>{
  const button=$('#uiRegister');button.disabled=true;
  try{
   const blob=await uiPNGBlob(),form=new FormData();
   form.set('file',new File([blob],uiFrameFilename(),{type:'image/png'}));
   form.set('name',($('#uiFrameName').value||'UI枠').trim());
   form.set('category','ui');form.set('columns','1');form.set('rows','1');
   await api('/pixel/api/assets/import',{method:'POST',body:form});
   await loadAssets($('#assetFilter')?.value||'');
   toast('UI枠をアセットに登録しました');
  }catch(e){toast('UI枠の登録：'+e.message,true)}
  finally{button.disabled=false}
 });
 renderUIFrame();
}


// AI-based UI generation runs through the existing estimate and job endpoints.
// Manual preview/export below are independent of the 9-slice builder.
const uiAIExamples={
 dialog:{width:240,height:64,prompt:'濃紺と金色のアールデコ風メッセージウィンドウ。装飾された細い外枠と文字の読みやすい暗い内側。文字、人物、アイコンなし。背景透明。ゲーム画面下部に重ねる横長UI素材。'},
 novel:{width:320,height:80,prompt:'クラシックなノベルゲーム用会話ウィンドウ。繊細な金色の縁取り、内側は文字の視認性を優先した暗い色。透過背景、文字・人物なし。'},
 name:{width:160,height:40,prompt:'ファンタジーRPGのキャラクター名を表示する小さな装飾枠。中央は空白、読みやすいプレート、金属の細い縁取り。文字なし、背景透明。'},
 status:{width:256,height:128,prompt:'GBA風ファンタジーSRPGのステータスウィンドウ。整理されたパネルの枠、濃紺と金色、控えめな装飾。文字・数字・顔画像なし、背景透明。'},
 button:{width:128,height:32,prompt:'レトロRPGのメニューボタン。明瞭なピクセル輪郭、軽い立体感、青と金の装飾。中央に文字を置ける空白。文字なし、背景透明。'},
 hp:{width:160,height:32,prompt:'レトロSRPG用のHPゲージの装飾フレーム。横長で中央にHPバーを重ねられる領域、細い金属枠。文字・数字なし、背景透明。'}
};
function syncUIAIPreview(job){
 if(!uiAIJobId||job.id!==uiAIJobId||!$('#uiAIResult'))return;
 const state=$('#uiAIState'),status=$('#uiAIStatus'),preview=$('#uiAIResult'),link=$('#uiAIDownload'),empty=$('#uiAIEmpty');
 state.textContent={queued:'受付済み',processing:'生成中',completed:'完成',failed:'失敗',unknown:'結果・請求要確認'}[job.status]||job.status;
 const image=jobFiles(job).find(x=>x.endsWith('.png'));
 const completed=job.status==='completed'&&Boolean(image);
 preview.classList.toggle('hidden',!completed);empty.classList.toggle('hidden',completed);
 link.classList.toggle('hidden',!completed);
 if(completed){
  const url='/pixel/api/jobs/'+encodeURIComponent(job.id)+'/files/'+encodeURIComponent(image);
  preview.src=url;link.href=url;link.download=image;
  status.textContent=job.demo?'デモ画像を作成しました（API利用なし）。':'生成が完了しました。PNGを保存するか、生成履歴から画像を確認できます。';
 }else if(job.status==='failed'||job.status==='unknown'){
  status.textContent='生成結果を確認してください：'+(job.error||'詳細は生成履歴から確認できます。');
 }else status.textContent='画像を生成しています。完了するとここに表示されます。';
}
function attachUIAIGenerationEvents(){
 const preset=$('#uiAIPreset');if(!preset)return;
 preset.addEventListener('change',()=>{
  const data=uiAIExamples[preset.value];
  if(data){$('#uiAIPrompt').value=data.prompt;$('#uiAIWidth').value=data.width;$('#uiAIHeight').value=data.height;}
 });
 for(const btn of document.querySelectorAll('[data-ui-size]'))btn.addEventListener('click',()=>{
  const pair=btn.dataset.uiSize.split('x').map(Number);
  $('#uiAIWidth').value=pair[0];$('#uiAIHeight').value=pair[1];
 });
 $('#uiAIGoHistory').addEventListener('click',()=>$('#history').scrollIntoView({behavior:'smooth'}));
 $('#uiAIGenerate').addEventListener('click',async()=>{
  const button=$('#uiAIGenerate'),width=Number($('#uiAIWidth').value),height=Number($('#uiAIHeight').value);
  const prompt=$('#uiAIPrompt').value.trim(),colors=$('#uiAIColors').value.trim();
  if(!prompt)return toast('生成するUIの説明を入力してください',true);
  if(!Number.isInteger(width)||!Number.isInteger(height)||width<16||height<16||width>512||height>512)
   return toast('UI画像の幅・高さは16〜512pxの整数で指定してください',true);
  button.disabled=true;
  try{
   if(getProvider()!=='pixellab'){$('#provider').value='pixellab';setProvider();}
   setMode('text');
   $('#model').value='pro';
   $('#assetCategory').value='ui';
   $('#imageWidth').value=width;$('#imageHeight').value=height;
   $('#description').value=prompt+(colors?'\n配色: '+colors:'');
   $('#transparent').checked=$('#uiAITransparent').checked;
   $('#demo').checked=$('#uiAIDemo').checked;
   $('#assetName').value='UI_'+preset.selectedOptions[0].textContent;
   refreshUI();
   // A fresh quote for this model and image size must be loaded first.
   await updateEstimate();
   const id=await generate();
   if(id){
    uiAIJobId=id;
    const item=allJobs.find(j=>j.id===id);
    if(item)syncUIAIPreview(item);
   }
  }catch(e){toast('UIの生成：'+e.message,true)}
  finally{button.disabled=false}
 });
}
