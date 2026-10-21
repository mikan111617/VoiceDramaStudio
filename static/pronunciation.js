/* Pronunciation dictionary shared by VoiceDrama and game voice.
   The same editor runs on both screens, and the substitution mirrors
   speech_dict.py: the longest word wins, each place is replaced once, and an
   ASCII word is never replaced inside another ASCII word. */
'use strict';
(function(global){
 const API='/api/pronunciation-dictionary';
 const DEFAULT_LABEL='通常';
 const LIST_LIMIT=300;
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const escapeRegExp=text=>text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');

 // {surface: reading}: the line's chosen pattern, otherwise the word's default.
 function readings(dictionary,choices){
  const result={},chosen=choices&&typeof choices==='object'?choices:{};
  for(const [surface,entry] of Object.entries(dictionary||{})){
   let index=chosen[surface];
   if(!Number.isInteger(index)||index<0||index>=entry.readings.length)index=entry.default||0;
   result[surface]=entry.readings[index].reading;
  }
  return result;
 }
 // Spoken text plus the words actually replaced (a shorter word inside a
 // longer replaced word is not listed, because it is not read separately).
 function scan(text,dictionary,choices){
  const source=String(text??''),map=readings(dictionary,choices);
  const tokens=Object.keys(map).sort((a,b)=>b.length-a.length||(a<b?-1:a>b?1:0));
  const matched=[];
  if(!tokens.length)return {spoken:source,matched};
  const spoken=source.replace(new RegExp(tokens.map(escapeRegExp).join('|'),'g'),(key,offset)=>{
   if(/^[\x00-\x7F]+$/.test(key)&&/[A-Za-z0-9]/.test(key)){
    if(offset>0&&/[A-Za-z0-9]/.test(source[offset-1]))return key;
    const end=offset+key.length;
    if(end<source.length&&/[A-Za-z0-9]/.test(source[end]))return key;
   }
   if(!matched.includes(key))matched.push(key);
   return map[key];
  });
  return {spoken,matched};
 }
 function apply(text,dictionary,choices){return scan(text,dictionary,choices).spoken}
 // Words of this text that have more than one reading pattern.
 function choosable(text,dictionary){
  return scan(text,dictionary).matched.filter(surface=>dictionary[surface].readings.length>1);
 }
 // Select one pattern per word; '' means "follow the dictionary default".
 function choiceSelect(surface,entry,choices,attrs=''){
  const chosen=choices?.[surface];
  const valid=Number.isInteger(chosen)&&chosen>=0&&chosen<entry.readings.length;
  const fallback=entry.readings[entry.default||0];
  return `<select ${attrs}><option value="" ${valid?'':'selected'}>既定に従う（${esc(fallback.label)}：${esc(fallback.reading)}）</option>${
   entry.readings.map((p,i)=>`<option value="${i}" ${valid&&chosen===i?'selected':''}>${esc(p.label)}：${esc(p.reading)}</option>`).join('')}</select>`;
 }
 function withChoice(choices,surface,value){
  const next={...(choices||{})};
  if(value===''||value===null||value===undefined)delete next[surface];else next[surface]=Number(value);
  return next;
 }

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
 function fetchDictionary(){return call(API)}

 function conflictList(list,title){
  if(!list?.length)return '';
  return `<div class="pd-conflicts"><b>${esc(title)}（${list.length}語）</b><ul>${list.map(item=>`<li><b>${esc(item.surface)}</b>：${
   item.readings.map(r=>esc(r.reading)+(r.sources?.length?`（${esc(r.sources.join('・'))}）`:'')).join(' / ')}</li>`).join('')}</ul></div>`;
 }

 // Editor for the shared dictionary. Every save sends the server revision;
 // a change made from another screen is rejected and must be reloaded.
 function createEditor(root,{toast=()=>{},onSaved=()=>{},onLoad=()=>{},classes={}}={}){
  const cls={button:'',primary:'',danger:'',...classes};
  let data=null,loadError='',conflict=false,filter='',report=null,busy=false;
  const drafts=new Map();
  const btn=(label,attrs='',kind='button',type='button')=>`<button type="${type}" class="${esc(cls[kind]||cls.button)}" ${attrs}>${label}</button>`;

  function migrationHtml(){
   const m=data.migration;if(!m)return '';
   const projects=m.projects||[],conflicts=m.conflicts||[],unreadable=m.unreadable||[];
   if(!projects.length&&!unreadable.length)return '';
   return `<details class="pd-migration" ${conflicts.length?'open':''}><summary>ゲーム音声プロジェクトの辞書の統合結果（${projects.length}件${conflicts.length?` · 読みが異なった語 ${conflicts.length}語`:''}）</summary>
    <p>統合元：${projects.map(p=>`${esc(p.title)}（${p.entries}語）`).join('、')||'なし'}。統合は一度だけ行いました（${esc(String(m.completedAt||'').slice(0,16).replace('T',' '))}）。元のプロジェクトの辞書は削除せず、読み取り専用の記録として残しています。</p>
    ${conflictList(conflicts,'同じ語で読みが異なったため、両方を読みパターンとして残した語')}
    ${unreadable.length?`<p>読み込めなかったため統合していないプロジェクト：${unreadable.map(u=>esc(u.id)).join('、')}</p>`:''}</details>`;
  }
  function reportHtml(){
   if(!report)return '';
   return `<div class="pd-report" role="status"><div><b>取り込み結果</b>：${report.mode==='replace'?'辞書全体を置き換え':'追加・統合'} · ファイル ${report.imported}語 · 新しい語 ${report.added}語 · 辞書の合計 ${report.total}語</div>
    ${conflictList(report.conflicts,'読みが異なったため、両方の読みを残した語')}${btn('閉じる','data-pd-action="close-report"')}</div>`;
  }
  function entryHtml(surface,entry,draftSurface){
   const many=entry.readings.length>1;
   return `<form class="pd-entry" data-pd-entry="${esc(surface)}">
    <div class="pd-entry-head"><label>語<input name="surface" maxlength="100" required value="${esc(draftSurface??surface)}"></label><span class="pd-entry-count">読み ${entry.readings.length}件</span></div>
    <div class="pd-patterns">${entry.readings.map((p,i)=>`<div class="pd-pattern">
     <label class="pd-default" title="台詞で読みを選んでいないときに使う読み"><input type="radio" name="pd-default" value="${i}" ${i===(entry.default||0)?'checked':''}> 既定</label>
     <label>名前<input name="label" maxlength="40" value="${esc(p.label)}" placeholder="${i?'別読み':DEFAULT_LABEL}"></label>
     <label>読み<input name="reading" maxlength="120" required value="${esc(p.reading)}"></label>
     ${many?btn('×',`data-pd-remove-pattern="${i}" title="この読みパターンを削除" aria-label="この読みパターンを削除"`):''}</div>`).join('')}</div>
    <div class="pd-entry-actions">${btn('＋ 読みパターンを追加','data-pd-add-pattern')}${btn('この単語を保存','','primary','submit')}${btn('この単語を削除','data-pd-delete','danger')}</div></form>`;
  }
  function renderList(){
   const list=root.querySelector('[data-pd-list]');if(!list||!data)return;
   const q=filter.trim().toLowerCase();
   // Newest words first; the stored and exported order is unchanged.
   const entries=Object.entries(data.dictionary).reverse().filter(([surface,entry])=>!q||surface.toLowerCase().includes(q)||
     entry.readings.some(p=>p.reading.toLowerCase().includes(q)||p.label.toLowerCase().includes(q)));
   list.innerHTML=entries.slice(0,LIST_LIMIT).map(([surface,entry])=>{const draft=drafts.get(surface);return entryHtml(surface,draft?.entry||entry,draft?.surface)}).join('')||
    `<p class="pd-empty">${q?'絞り込みに一致する語がありません。':'読み方の登録はありません。'}</p>`;
   if(entries.length>LIST_LIMIT)list.insertAdjacentHTML('beforeend',`<p class="pd-empty">ほか ${entries.length-LIST_LIMIT}語あります。絞り込みで表示してください。</p>`);
  }
  function render(){
   if(!data){
    root.innerHTML=`<div class="pd-editor"><p class="${loadError?'pd-error':'pd-empty'}" role="${loadError?'alert':'status'}">${esc(loadError||'読み方辞書を読み込み中…')}</p>${loadError?btn('↻ 再読み込み','data-pd-action="reload"'):''}</div>`;
    return;
   }
   const entries=Object.values(data.dictionary),mode=root.querySelector('[data-pd-import-mode]')?.value||'merge';
   root.innerHTML=`<div class="pd-editor">
    <p class="pd-help">ボイスドラマとゲーム音声で共通の辞書です。音声生成のときだけ、TTSへ渡す文字列を読みに置き換えます（台本・キャプション・Excel・KSの内容は変更しません）。長い語を優先し、1か所を1回だけ置き換えます。</p>
    ${conflict?`<div class="pd-conflict" role="alert">別の画面で辞書が変更されたため、保存しませんでした。再読み込みしてから編集し直してください。${btn('↻ 再読み込み','data-pd-action="reload"','primary')}</div>`:''}
    <div class="pd-toolbar"><span class="pd-count">${entries.length}語 · 複数の読みがある語 ${entries.filter(e=>e.readings.length>1).length}語</span>
     <label class="pd-mode">取り込み方法<select data-pd-import-mode><option value="merge" ${mode==='merge'?'selected':''}>追加・統合（既存の読みを残す）</option><option value="replace" ${mode==='replace'?'selected':''}>辞書全体を置き換える</option></select></label>
     <span class="pd-toolbar-buttons">${btn('↑ 辞書JSONを取り込む','data-pd-action="import"')}${btn('↓ 辞書JSONを書き出す','data-pd-action="export"')}${btn('↻ 再読み込み','data-pd-action="reload"')}</span>
     <input type="file" accept=".json,application/json" hidden data-pd-file></div>
    ${reportHtml()}${migrationHtml()}
    <form class="pd-add" data-pd-add><label>語<input name="surface" maxlength="100" required placeholder="例：舞黒館"></label><label>読み<input name="reading" maxlength="120" required placeholder="例：まいくろかん"></label><label>名前（任意）<input name="label" maxlength="40" placeholder="${DEFAULT_LABEL}"></label>${btn('＋ 語を追加','','primary','submit')}</form>
    <p class="pd-note">別の読みは、登録した語の「＋ 読みパターンを追加」で増やせます（1語につき10個まで）。台詞ごとの読みは、台詞を選んだときの右側の「選択中の台詞」で切り替えます。</p>
    <label class="pd-filter">絞り込み<input type="search" data-pd-filter value="${esc(filter)}" placeholder="語・読みで絞り込み"></label>
    <div class="pd-list" data-pd-list></div></div>`;
   renderList();
  }
  function readCard(form){
   const rows=[...form.querySelectorAll('.pd-pattern')];
   const checked=form.querySelector('input[name="pd-default"]:checked');
   return {surface:form.elements.namedItem('surface').value,
    entry:{readings:rows.map(row=>({label:row.querySelector('[name="label"]').value,reading:row.querySelector('[name="reading"]').value})),
     default:checked?Number(checked.value):0}};
  }
  // Keep unsaved typing in other words when the list is redrawn.
  function captureDrafts(){
   if(!data)return;
   for(const form of root.querySelectorAll('[data-pd-entry]')){
    const key=form.dataset.pdEntry,saved=data.dictionary[key];
    if(!saved){drafts.delete(key);continue}
    const draft=readCard(form);
    if(draft.surface===key&&JSON.stringify(draft.entry)===JSON.stringify(saved))drafts.delete(key);else drafts.set(key,draft);
   }
  }
  async function load(){
   try{data=await fetchDictionary();loadError='';conflict=false;drafts.clear();onLoad(data)}
   catch(e){if(!data)loadError='読み方辞書を読み込めません: '+e.message;toast('読み方辞書を読み込めません: '+e.message,true)}
   render();return data;
  }
  // saved: words whose card is being committed, so their typing is not a draft.
  async function save(next,done,saved=[]){
   if(busy||!data)return false;
   busy=true;captureDrafts();
   const pending=new Map(saved.filter(key=>drafts.has(key)).map(key=>[key,drafts.get(key)]));
   for(const key of saved)drafts.delete(key);
   try{
    data=await call(API,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({dictionary:next,expectedRevision:data.revision})});
    conflict=false;render();toast(done);onSaved(data);return true;
   }catch(e){
    for(const [key,draft] of pending)drafts.set(key,draft);
    if(e.status===409){conflict=true;render()}
    toast('読み方辞書を保存できません: '+e.message,true);return false;
   }finally{busy=false}
  }
  function cleanEntry(entry){
   return {readings:entry.readings.map((p,i)=>({label:p.label.trim()||(i?`読み${i+1}`:DEFAULT_LABEL),reading:p.reading.trim()})),default:entry.default};
  }
  async function saveCard(form){
   const key=form.dataset.pdEntry,{surface,entry}=readCard(form),name=surface.trim(),clean=cleanEntry(entry);
   if(!name)return toast('語を入力してください',true);
   if(clean.readings.some(p=>!p.reading))return toast('読みを入力してください',true);
   if(name!==key&&Object.hasOwn(data.dictionary,name))return toast(`「${name}」はすでに登録されています`,true);
   const next={};
   for(const [surfaceKey,value] of Object.entries(data.dictionary))next[surfaceKey===key?name:surfaceKey]=surfaceKey===key?clean:value;
   await save(next,`「${name}」の読みを保存しました`,[key]);
  }
  async function addEntry(form){
   const name=form.elements.namedItem('surface').value.trim(),reading=form.elements.namedItem('reading').value.trim();
   const label=form.elements.namedItem('label').value.trim()||DEFAULT_LABEL;
   if(!name||!reading)return toast('語と読みを入力してください',true);
   if(Object.hasOwn(data.dictionary,name))return toast(`「${name}」はすでに登録されています。一覧の「＋ 読みパターンを追加」で読みを増やせます`,true);
   if(await save({...data.dictionary,[name]:{readings:[{label,reading}],default:0}},`「${name}」を追加しました`))root.querySelector('[data-pd-add]')?.reset();
  }
  async function removeEntry(form){
   const key=form.dataset.pdEntry;
   if(!confirm(`「${key}」を共通の読み方辞書から削除しますか？\nボイスドラマとゲーム音声の両方で、この語の読みの置き換えがなくなります。`))return;
   const next={...data.dictionary};delete next[key];
   await save(next,`「${key}」を削除しました`,[key]);
  }
  function editPatterns(form,change){
   captureDrafts();
   const key=form.dataset.pdEntry,draft=drafts.get(key)||{surface:key,entry:structuredClone(data.dictionary[key])};
   change(draft.entry);drafts.set(key,draft);renderList();
  }
  function addPattern(form){
   editPatterns(form,entry=>{
    if(entry.readings.length>=10)return toast('読みパターンは1語につき10個までです',true);
    entry.readings.push({label:'',reading:''});
   });
  }
  function removePattern(form,index){
   const count=form.querySelectorAll('.pd-pattern').length;
   if(index<count-1&&!confirm('台詞ごとの読みの選択は番号で保存しています。後ろの読みパターンを選んでいる台詞は、別の読みになることがあります（該当する台詞は「要再生成」になります）。この読みパターンを削除しますか？'))return;
   editPatterns(form,entry=>{
    entry.readings.splice(index,1);
    if(entry.default===index)entry.default=0;else if(entry.default>index)entry.default-=1;
   });
  }
  async function importFile(file){
   if(!file||!data)return;
   const mode=root.querySelector('[data-pd-import-mode]')?.value==='replace'?'replace':'merge';
   if(mode==='replace'&&!confirm('共通の読み方辞書を、ファイルの内容で置き換えます。\nファイルにない語は削除され、ボイスドラマとゲーム音声の両方に影響します。続行しますか？'))return;
   const form=new FormData();form.append('file',file);
   busy=true;captureDrafts();
   try{
    data=await call(`${API}/import?expectedRevision=${encodeURIComponent(data.revision)}&mode=${mode}`,{method:'POST',body:form});
    report=data.summary||null;conflict=false;render();
    toast(`読み方辞書を取り込みました（${report?.imported??0}語）`);onSaved(data);
   }catch(e){
    if(e.status===409){conflict=true;render()}
    toast('読み方辞書を取り込めません: '+e.message,true);
   }finally{busy=false}
  }
  function exportFile(){
   const link=document.createElement('a');link.href=`${API}/export`;link.download='pronunciation_dictionary.json';
   document.body.appendChild(link);link.click();link.remove();
  }
  root.addEventListener('click',e=>{
   const action=e.target.closest('[data-pd-action]')?.dataset.pdAction;
   if(action==='reload')return load();
   if(action==='import')return root.querySelector('[data-pd-file]')?.click();
   if(action==='export')return exportFile();
   if(action==='close-report'){report=null;captureDrafts();render();return}
   const form=e.target.closest('[data-pd-entry]');if(!form)return;
   if(e.target.closest('[data-pd-add-pattern]'))return addPattern(form);
   const remove=e.target.closest('[data-pd-remove-pattern]');if(remove)return removePattern(form,Number(remove.dataset.pdRemovePattern));
   if(e.target.closest('[data-pd-delete]'))return removeEntry(form);
  });
  root.addEventListener('submit',e=>{
   const form=e.target.closest('form');if(!form||!root.contains(form))return;
   e.preventDefault();
   if(form.matches('[data-pd-add]'))return addEntry(form);
   if(form.matches('[data-pd-entry]'))return saveCard(form);
  });
  root.addEventListener('input',e=>{if(e.target.matches('[data-pd-filter]')){captureDrafts();filter=e.target.value;renderList()}});
  root.addEventListener('change',e=>{if(e.target.matches('[data-pd-file]')){const file=e.target.files[0];e.target.value='';importFile(file)}});
  render();
  return {load,get data(){return data}};
 }

 global.PronunciationDictionary={readings,scan,apply,choosable,choiceSelect,withChoice,fetchDictionary,createEditor};
})(globalThis);
