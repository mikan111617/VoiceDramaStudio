'use strict';
// Standalone client-side UI frame composition. No PixelLab or OpenAI API request is made here.
(() => {
 const q=id=>document.getElementById(id);
 const history=q('history'),create=q('create');
 if(!create)return;
 const markup=`<section id="ui-frame" class="history-wrap pixel-ui-frame" aria-labelledby="uiFrameTitle">
  <div class="section-heading"><div><span class="section-kicker">UI FRAME BUILDER</span><h2 id="uiFrameTitle">UI・メッセージウィンドウ枠を作る</h2></div><span class="small-counter">追加API料金なし・ローカル処理</span></div>
  <p class="pixel-frame-intro">横長の会話ウィンドウ、名前枠、ステータス枠を作成できます。テンプレートを選ぶか、PixelLabで生成した枠画像を使い、<b>9-slice</b>（四隅を変えず、辺と中央を繰り返す方式）で仕上げます。文字はゲーム側で重ねてください。</p>
  <div class="pixel-builder-grid">
    <div class="card pixel-builder-settings">
      <h3><span class="pixel-step-number">1</span>用途とデザインを選ぶ</h3>
      <div class="field"><label for="uiFramePreset">完成する枠の種類</label><select id="uiFramePreset"><option value="dialog">GBA風 会話枠：320 × 80</option><option value="name">名前表示枠：160 × 40</option><option value="status">ステータス枠：160 × 120</option><option value="novel">ノベルゲーム枠：960 × 180</option><option value="custom">自由サイズ</option></select></div>
      <div class="field"><label for="uiFrameTheme">枠の元デザイン</label><select id="uiFrameTheme"><option value="navy">濃紺 × 金</option><option value="brown">ブラウン × クリーム</option><option value="green">深緑 × 明るい縁</option><option value="custom">手持ちの画像から作る</option></select></div>
      <div class="field"><label for="uiFrameSource">自分の枠画像を使う（PNG・WebP）</label><input id="uiFrameSource" type="file" accept="image/png,image/webp"><small>四隅・辺・中央が描かれた小さな枠画像を指定します。キャラクター画像には向きません。</small></div>
      <button type="button" class="quiet-btn" id="useCurrentAsFrame">直近の生成結果を枠素材として読み込む</button>
      <h3><span class="pixel-step-number">2</span>横幅・高さを設定する</h3>
      <div class="pixel-dimension-grid">
        <div class="field"><label for="uiFrameWidth">横幅（px）</label><input id="uiFrameWidth" type="number" min="32" max="2048" value="320"></div>
        <div class="field"><label for="uiFrameHeight">高さ（px）</label><input id="uiFrameHeight" type="number" min="24" max="1024" value="80"></div>
        <div class="field"><label for="uiFrameCorner">四隅の幅（px）</label><input id="uiFrameCorner" type="number" min="2" max="128" value="8"></div>
      </div>
      <p id="uiFrameHelp" role="status" aria-live="polite" class="pixel-frame-help">テンプレートは8pxの四隅を固定します。自分の枠画像の場合は、枠画像の角に合わせて数値を変更してください。</p>
      <div class="pixel-frame-actions"><button type="button" id="downloadUiFrame" class="small-btn">↓ 透明PNGで保存</button><button type="button" id="registerUiFrame" class="quiet-btn">アセット管理へ登録</button><button type="button" id="resetUiFrame" class="quiet-btn">初期化</button></div>
    </div>
    <div class="card pixel-builder-preview">
      <div class="result-head"><div><span class="section-kicker">LIVE PREVIEW</span><h3><span class="pixel-step-number">3</span>プレビューと書き出し</h3></div><span id="uiFrameDimensions" class="result-state">320 × 80 px</span></div>
      <div class="pixel-frame-canvas-wrap"><canvas id="uiFrameCanvas" width="320" height="80" aria-label="ウィンドウ枠のプレビュー"></canvas></div>
      <p class="pixel-frame-help">生成料金はかかりません。ピクセル補間をオフにして描画し、指定サイズの透過PNGを保存します。登録した素材は「アセット管理」のUIカテゴリーに表示されます。</p>
      <details class="pixel-detail"><summary>9-slice素材をきれいに作るコツ</summary><p>小さな正方形の「空のウィンドウ枠」を元画像にしてください。角の装飾を四隅へ収め、上下左右の辺はつなげても破綻しない模様にします。読み込んだ画像は上辺・中央・下辺などをタイル状に繰り返します。画面全体の文字やアイコンが描かれた画像は適していません。</p></details>
    </div>
  </div>
 </section>`;
 (history||create.nextElementSibling)?.insertAdjacentHTML('beforebegin',markup);
 const presets={dialog:[320,80],name:[160,40],status:[160,120],novel:[960,180]};
 const colors={
   navy:['#162c43','#e3c37a','#5a829a','rgba(7,22,41,0.89)'],
   brown:['#594031','#ead1a2','#a17c5c','rgba(52,31,24,0.88)'],
   green:['#1f4036','#bcd9a5','#638d78','rgba(12,38,29,0.9)']
 };
 let imported=null, originalFileName='', lastValid=false;
 function message(s,bad=false){q('uiFrameHelp').textContent=s;q('uiFrameHelp').classList.toggle('pixel-error',bad);}
 function makeTemplate(name){
   const t=document.createElement('canvas');t.width=t.height=24;
   const x=t.getContext('2d');x.imageSmoothingEnabled=false;
   const [base,light,inner,fill]=colors[name]||colors.navy;
   x.fillStyle=base;x.fillRect(0,0,24,24);
   x.fillStyle=light;x.fillRect(1,1,22,2);x.fillRect(1,21,22,2);x.fillRect(1,1,2,22);x.fillRect(21,1,2,22);
   x.fillStyle=inner;x.fillRect(3,3,18,1);x.fillRect(3,20,18,1);x.fillRect(3,3,1,18);x.fillRect(20,3,1,18);
   x.fillStyle=fill;x.fillRect(4,4,16,16);
   // Bright corner accents remain anchored after 9-slice composition.
   x.fillStyle=light;for(const [px,py] of [[3,3],[19,3],[3,19],[19,19]])x.fillRect(px,py,2,2);
   return t;
 }
 // Repeat, do not blur or stretch, the 9 segments of a frame.
 function tile(ctx,image,sx,sy,sw,sh,dx,dy,dw,dh){
   if(!sw||!sh||!dw||!dh)return;
   for(let y=0;y<dh;y+=sh)for(let x=0;x<dw;x+=sw){
     const tw=Math.min(sw,dw-x),th=Math.min(sh,dh-y);
     ctx.drawImage(image,sx,sy,tw,th,dx+x,dy+y,tw,th);
   }
 }
 function drawNine(ctx,src,width,height,corner){
   const sw=src.width,sh=src.height,midW=sw-corner*2,midH=sh-corner*2;
   if(midW<1||midH<1||width<=corner*2||height<=corner*2)throw Error('角の幅が大きすぎます。元画像と出力サイズの半分未満にしてください。');
   const xs=[0,corner,sw-corner],ys=[0,corner,sh-corner];
   const ws=[corner,midW,corner],hs=[corner,midH,corner];
   const dx=[0,corner,width-corner],dy=[0,corner,height-corner];
   const dw=[corner,width-2*corner,corner],dh=[corner,height-2*corner,corner];
   for(let iy=0;iy<3;iy++)for(let ix=0;ix<3;ix++)tile(ctx,src,xs[ix],ys[iy],ws[ix],hs[iy],dx[ix],dy[iy],dw[ix],dh[iy]);
 }
 function draw(){
   const width=Number(q('uiFrameWidth').value),height=Number(q('uiFrameHeight').value),corner=Number(q('uiFrameCorner').value);
   const canvas=q('uiFrameCanvas'),ctx=canvas.getContext('2d');
   lastValid=false;
   if(!Number.isInteger(width)||width<32||width>2048||!Number.isInteger(height)||height<24||height>1024||!Number.isInteger(corner)||corner<2||corner>128){message('横幅32〜2048px、高さ24〜1024px、四隅2〜128pxを指定してください。',true);return;}
   const type=q('uiFrameTheme').value;
   if(type==='custom'&&!imported){message('「手持ちの画像から作る」を選んだ場合は、PNGかWebPの枠画像を読み込んでください。',true);return;}
   const source=type==='custom'?imported:makeTemplate(type);
   canvas.width=width;canvas.height=height;ctx.imageSmoothingEnabled=false;
   try{
     drawNine(ctx,source,width,height,corner);
     q('uiFrameDimensions').textContent=`${width} × ${height} px`;
     message(type==='custom'?`読み込み: ${originalFileName}（${source.width}×${source.height}px） / 四隅 ${corner}px。PNGの背景の透過度も引き継ぎます。`:`テンプレートを使用中。横 ${width}px × 縦 ${height}px、角 ${corner}pxの9-slice枠です。`);
     lastValid=true;
   }catch(e){ctx.clearRect(0,0,width,height);message(e.message,true);}
 }
 async function loadBlob(blob,name){
   if(!blob||!['image/png','image/webp'].includes(blob.type))throw Error('PNGまたはWebP画像を指定してください。');
   const bitmap=await createImageBitmap(blob);
   if(bitmap.width>4096||bitmap.height>4096){bitmap.close();throw Error('枠画像は4096px以下にしてください。');}
   if(imported?.close)imported.close();
   imported=bitmap;originalFileName=name;q('uiFrameTheme').value='custom';draw();
 }
 function outputBlob(){return new Promise((resolve,reject)=>{if(!lastValid)return reject(Error('設定を確認してください。'));q('uiFrameCanvas').toBlob(blob=>blob?resolve(blob):reject(Error('PNGを書き出せませんでした。')),'image/png');});}
 q('uiFramePreset').addEventListener('change',()=>{
   const v=presets[q('uiFramePreset').value];if(v){q('uiFrameWidth').value=v[0];q('uiFrameHeight').value=v[1];}draw();
 });
 q('uiFrameTheme').addEventListener('change',draw);
 for(const id of ['uiFrameWidth','uiFrameHeight','uiFrameCorner'])q(id).addEventListener('input',draw);
 q('uiFrameSource').addEventListener('change',async()=>{
   const f=q('uiFrameSource').files?.[0];if(!f)return;
   try{await loadBlob(f,f.name);}catch(e){message(e.message,true);}
 });
 q('useCurrentAsFrame').addEventListener('click',async()=>{
   try{
     if(typeof allJobs==='undefined')throw Error('生成履歴を読み込めません。');
     const job=allJobs.find(j=>j.status==='completed'&&Array.isArray(j.files)&&j.files.some(f=>/^image_.*\.png$/.test(f)));
     if(!job)throw Error('使用可能な画像がありません。先に枠画像を生成するか、PNGを指定してください。');
     const file=job.files.find(f=>/^image_.*\.png$/.test(f));
     const response=await fetch(`/pixel/api/jobs/${encodeURIComponent(job.id)}/files/${encodeURIComponent(file)}`);
     if(!response.ok)throw Error('画像を取得できませんでした。');
     await loadBlob(await response.blob(),file);
   }catch(e){message(e.message,true);}
 });
 q('downloadUiFrame').addEventListener('click',async()=>{
   try{
     const blob=await outputBlob(),url=URL.createObjectURL(blob),a=document.createElement('a');
     a.href=url;a.download=`ui-frame-${q('uiFrameWidth').value}x${q('uiFrameHeight').value}.png`;document.body.append(a);a.click();a.remove();
     setTimeout(()=>URL.revokeObjectURL(url),1000);
     message('PNGを保存しました。出力は指定したピクセルサイズです。');
   }catch(e){message(e.message,true);}
 });
 q('registerUiFrame').addEventListener('click',async()=>{
   const button=q('registerUiFrame');button.disabled=true;
   try{
     const blob=await outputBlob(),form=new FormData();
     form.set('file',new File([blob],'ui-frame.png',{type:'image/png'}));
     form.set('category','ui');form.set('name',`UI枠 ${q('uiFrameWidth').value}×${q('uiFrameHeight').value}`);
     form.set('columns','1');form.set('rows','1');
     if(typeof api!=='function')throw Error('アセット機能に接続できません。');
     await api('/pixel/api/assets/import',{method:'POST',body:form});
     if(typeof loadAssets==='function')await loadAssets();
     message('アセット管理の「UI・ウィンドウ枠」に保存しました。');
   }catch(e){message(e.message,true);}finally{button.disabled=false;}
 });
 q('resetUiFrame').addEventListener('click',()=>{
   q('uiFramePreset').value='dialog';q('uiFrameTheme').value='navy';
   q('uiFrameWidth').value=320;q('uiFrameHeight').value=80;q('uiFrameCorner').value=8;
   q('uiFrameSource').value='';if(imported?.close)imported.close();imported=null;originalFileName='';draw();
 });
 draw();
})();
