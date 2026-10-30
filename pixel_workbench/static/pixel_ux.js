'use strict';
// Explanations and workflow shortcuts layered on top of the existing generation UI.
// No API calls are made by this module.
(() => {
  const q = (id) => document.getElementById(id);
  const guide = {
    text: {
      name: '新規生成',
      detail: '文章から1枚のドット絵を作成します。キャラ・アイテム・背景・UI枠の元画像に向いています。',
      steps: '① サービスとモデル → ② 説明 → ③ サイズ → ④ 生成',
      note: 'PixelLab新規生成は現在正方形。横長の枠は下の「UI枠ビルダー」で組み立てられます。'
    },
    convert: {
      name: '画像から変換',
      detail: '元画像を選び、絵柄をドット絵に変換します。キャラの特徴を残したい場合に向いています。',
      steps: '① サービスとモデル → ② 元画像 → ③ 忠実度 → ④ 生成',
      note: 'Standardは長辺の出力サイズを指定できます。Proではサイズ・忠実度の指定方法が異なります。'
    },
    animate: {
      name: 'アニメーション',
      detail: '既存のドット絵を最初の絵として、動きの文章からアニメーションを生成します。',
      steps: '① PixelLab → ② 元画像 → ③ 動き → ④ フレーム数 → ⑤ 生成',
      note: '4・8・16はAPIへ送るframe_count指定です。返却される画像数はAPIの結果を確認してください。'
    },
    vfx: {
      name: '魔法・エフェクト',
      detail: 'キャラ不要で落雷・炎・斬撃などのVFXを作ります。元画像を指定して重ねることもできます。',
      steps: '① PixelLab → ② Lite / Pro → ③ 演出の説明 → ④ 生成',
      note: 'VFXのフレーム数・出力寸法はここで指定できません。実際のコマ数とFPSは生成結果に依存します。'
    }
  };
  const start = `<section class="pixel-quickstart" aria-labelledby="pixelQuickTitle">
    <div class="section-heading"><div><span class="section-kicker">START HERE</span><h2 id="pixelQuickTitle">まず、何を作りたいですか？</h2></div></div>
    <div class="pixel-quick-grid">
      <button type="button" data-pixel-workflow="text"><b>▦ キャラ・背景</b><span>文章から新しく作る</span></button>
      <button type="button" data-pixel-workflow="convert"><b>▧ 画像を変換</b><span>元画像からドット絵化</span></button>
      <button type="button" data-pixel-workflow="animate"><b>▷ キャラを動かす</b><span>4 / 8 / 16コマを指定</span></button>
      <button type="button" data-pixel-workflow="vfx"><b>⚡ 魔法・エフェクト</b><span>斬撃や魔法の効果</span></button>
      <button type="button" data-pixel-workflow="window"><b>▣ UI・会話枠</b><span>自由な横幅のPNG</span></button>
    </div>
    <p class="pixel-quick-note">PixelLabとOpenAIのAPI生成は別料金。下のUI枠ビルダーでの組み立て・保存はローカル処理で追加API料金なし。</p>
  </section>`;
  const main = document.querySelector('.main'), create = q('create');
  if (!main || !create) return;
  create.insertAdjacentHTML('beforebegin', start);
  const tabbar = create.querySelector('.tabbar');
  const g = document.createElement('div');g.id='modeGuide';g.className='pixel-mode-guide';g.setAttribute('aria-live','polite');
  tabbar?.insertAdjacentElement('afterend',g);
  const framesField = q('framesField');
  const frameGuide = document.createElement('div');frameGuide.id='animationLimit';frameGuide.className='pixel-animation-guide';frameGuide.setAttribute('role','status');
  framesField?.append(frameGuide);
  const help = document.createElement('p');help.className='pixel-size-note';help.textContent='※ マップユニットの16×16pxは、この生成画面の最小32×32pxとは異なります。縮小だけではドットの情報が失われるため、16×16に適した単純なデザインが必要です。';
  q('sizeField')?.append(help);
  const assetHelp=document.createElement('p');assetHelp.className='pixel-quick-note';assetHelp.textContent='「保存カテゴリー」は生成後に整理するための分類です。UI枠の元画像は「UI・ウィンドウ枠」を選んでください。';
  q('assetCategory')?.closest('.field')?.append(assetHelp);
  const steps = `<div class="pixel-steps"><span><b>1</b> 作る方法を選択</span><span><b>2</b> 画像・条件を指定</span><span><b>3</b> 生成と保存</span></div>`;
  g.insertAdjacentHTML('beforebegin',steps);
  function renderGuide(){
    const m=typeof mode==='string' ? mode : 'text';
    const v=guide[m]||guide.text;
    const service=q('provider')?.value||'pixellab';
    const unsupported=service!=='pixellab'&&(m==='animate'||m==='vfx');
    g.innerHTML=`<strong>${v.name}でできること</strong><p>${v.detail}</p><p class="pixel-guide-steps">${v.steps}</p><small>${v.note}</small>${unsupported?'<p>このモードはPixelLab APIのみ対応しています。</p>':''}`;
    const animation=m==='animate';
    if(framesField)frameGuide.classList.toggle('hidden',!animation);
    updateAnimationLimit();
  }
  let checkVersion=0;
  async function updateAnimationLimit(){
    const ticket=++checkVersion;
    if(typeof mode==='undefined'||mode!=='animate'||!q('frames'))return;
    const size=Number(q('size').value);
    const file=q('referenceImage')?.files?.[0];
    const opts=[...q('frames').options];
    if(!file){
      opts.forEach(o=>o.disabled=false);
      frameGuide.textContent='選択可能: 4・8・16フレーム。最長辺256pxまで。画像を選ぶと、この組み合わせで可能なコマ数を確認します。制限: 画像の幅 × 高さ × 指定フレーム数 ≤ 524,288。';
      return;
    }
    try{
      const bitmap=await createImageBitmap(file);
      const scale=Math.min(1,size/Math.max(bitmap.width,bitmap.height));
      const width=Math.max(1,Math.round(bitmap.width*scale)),height=Math.max(1,Math.round(bitmap.height*scale));
      bitmap.close();
      if(ticket!==checkVersion)return;
      const maxFrames=Math.floor(524288/(width*height));
      opts.forEach(o=>o.disabled=Number(o.value)>maxFrames);
      if(q('frames').selectedOptions[0]?.disabled){
        const best=opts.filter(o=>!o.disabled).pop();
        if(best){q('frames').value=best.value;if(typeof updateEstimate==='function')updateEstimate();}
      }
      frameGuide.textContent=`参照画像を約${width}×${height}pxに調整。指定可能: ${opts.filter(o=>!o.disabled).map(o=>o.value).join(' / ')||'なし'}フレーム（選択条件に基づく目安）。${maxFrames<4?'画像が大きすぎます。出力サイズを下げてください。':''}`;
    }catch{
      if(ticket===checkVersion)frameGuide.textContent='画像サイズを読み取れません。API送信前に対応形式をご確認ください。';
    }
  }
  const categories=q('assetCategory'),importCategories=q('importCategory'),filter=q('assetFilter');
  for(const el of [categories,importCategories,filter]){
    if(el && ![...el.options].some(o=>o.value==='ui')){
      const opt=new Option('UI・ウィンドウ枠','ui');
      el.insertBefore(opt,[...el.options].find(o=>o.value==='other')||null);
    }
  }
  document.querySelectorAll('[data-pixel-workflow]').forEach(b=>b.addEventListener('click',()=>{
    const target=b.dataset.pixelWorkflow;
    if(target==='window'){q('ui-frame')?.scrollIntoView({behavior:'smooth',block:'start'});return;}
    if(['animate','vfx'].includes(target)&&q('provider').value!=='pixellab'){
      q('provider').value='pixellab';q('provider').dispatchEvent(new Event('change'));
    }
    if(typeof setMode==='function')setMode(target);
    if(target==='vfx')q('assetCategory').value='effect';
    if(target==='animate')q('assetCategory').value='character';
    create.scrollIntoView({behavior:'smooth',block:'start'});
    renderGuide();
  }));
  document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click',renderGuide));
  for(const id of ['provider','model','size','frames','referenceImage']){
    q(id)?.addEventListener('change',renderGuide);
  }
  renderGuide();
})();
