'use strict';
const $=id=>document.getElementById(id);
async function api(url,options){
  const response=await fetch(url,options);
  const data=await response.json().catch(()=>({detail:'サーバーからの応答を読み取れません'}));
  if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data));
  return data;
}
const post=(url,body)=>api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
function msg(value,error=false){$('message').textContent=value;$('message').classList.toggle('ase-error',error);}
async function status(){
  try{
    const data=await api('/api/aseprite/status');
    $('status').textContent=data.available?'接続可能：'+data.executable:'未接続。Aseprite.exeを指定してください。';
    if(data.executable)$('exe').value=data.executable;
  }catch(error){$('status').textContent=error.message;}
}
async function list(){
  try{
    const data=await api('/api/aseprite/sprites'),gallery=$('gallery');
    gallery.replaceChildren();
    for(const sprite of data.sprites){
      const card=document.createElement('div');card.className='ase-item';
      const img=document.createElement('img');img.loading='lazy';img.alt=sprite.name;
      img.src='/api/aseprite/sprites/'+sprite.id+'/download/png?v='+Date.now();card.append(img);
      const title=document.createElement('b');title.textContent=sprite.name;card.append(title);
      const size=document.createElement('small');
      size.textContent=sprite.width+'×'+sprite.height+' · '+sprite.frames+'フレーム';card.append(size);
      const actions=document.createElement('div');actions.className='ase-downloads';
      for(const [format,label] of [['aseprite','Aseprite'],['png','PNG'],['sheet','シート'],['gif','GIF']]){
        const a=document.createElement('a');a.href='/api/aseprite/sprites/'+sprite.id+'/download/'+format;
        a.textContent=label;a.download=sprite.name+'.'+(format==='aseprite'?'aseprite':format==='gif'?'gif':'png');
        actions.append(a);
      }
      card.append(actions);gallery.append(card);
    }
    if(!data.sprites.length)gallery.textContent='スプライトはまだありません。';
  }catch(error){msg(error.message,true);}
}
async function run(fn){
  try{msg('処理を実行しています…');await fn();await list();msg('完了しました。');}
  catch(error){msg(error.message,true);}
}
$('refresh').onclick=()=>{status();list();};
$('connect').onclick=()=>run(async()=>{
  await post('/api/aseprite/config',{executable:$('exe').value.trim()});await status();
});
$('generate').onclick=()=>run(async()=>{
  await post('/api/aseprite/ai',{
    prompt:$('prompt').value,model:$('model').value,width:Number($('width').value),
    height:Number($('height').value),frames:Number($('frames').value)
  });
});
$('sample').onclick=()=>run(async()=>{
  const width=Number($('width').value),height=Number($('height').value),frames=Number($('frames').value);
  const shapes=[];
  for(let frame=0;frame<frames;frame++)shapes.push([
    {type:'rect',x:Math.floor(width/4)+frame%2,y:Math.floor(height/4),
     w:Math.max(2,Math.floor(width/2)),h:Math.max(2,Math.floor(height/2)),color:'#4378a8'},
    {type:'ellipse',x:Math.floor(width/2)-2,y:Math.floor(height/3),w:4,h:4,color:'#fff7b0'}
  ]);
  await post('/api/aseprite/sprites',{
    name:'サンプル',width,height,frames,layers:[{name:'サンプル',frames:shapes}]
  });
});
$('import').onclick=()=>run(async()=>{
  const file=$('upload').files?.[0];
  if(!file)throw Error('画像を選択してください');
  const form=new FormData();form.append('file',file);
  await api('/api/aseprite/import',{method:'POST',body:form});
});
status();list();
