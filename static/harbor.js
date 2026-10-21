'use strict';
/* Shared, local artwork and presentation-only focus mode. */
(()=>{
 const canvas=document.querySelector('.harbor-art');
 const masthead=document.querySelector('.harbor-masthead');
 const toolbar=document.querySelector('.topbar,.bar');
 const button=document.getElementById('harborFocus');
 if(!canvas||!masthead||!toolbar||!button)return;
 const context=canvas.getContext('2d');
    function paint(){
      const width=Math.max(320,Math.round(canvas.clientWidth)),height=Math.round(canvas.clientHeight);
      const pixel=2,dpr=Math.min(window.devicePixelRatio||1,2);
      canvas.width=width*dpr;canvas.height=height*dpr;context.scale(dpr,dpr);context.imageSmoothingEnabled=false;
      const rect=(x,y,w,h,color,alpha=1)=>{context.globalAlpha=alpha;context.fillStyle=color;context.fillRect(Math.round(x/pixel)*pixel,Math.round(y/pixel)*pixel,Math.max(pixel,Math.round(w/pixel)*pixel),Math.max(pixel,Math.round(h/pixel)*pixel))};
      const dot=(x,y,color,alpha=1)=>rect(x,y,pixel,pixel,color,alpha);
      const ellipse=(cx,cy,rx,ry,color)=>{for(let y=-ry;y<=ry;y+=pixel)for(let x=-rx;x<=rx;x+=pixel)if(x*x/(rx*rx)+y*y/(ry*ry)<=1)dot(cx+x,cy+y,color)};
      const polygon=(points,color)=>{
        const minX=Math.min(...points.map(p=>p[0])),maxX=Math.max(...points.map(p=>p[0])),minY=Math.min(...points.map(p=>p[1])),maxY=Math.max(...points.map(p=>p[1]));
        for(let y=minY;y<=maxY;y+=pixel)for(let x=minX;x<=maxX;x+=pixel){let inside=false;for(let i=0,j=points.length-1;i<points.length;j=i++){const a=points[i],b=points[j];if((a[1]>y)!==(b[1]>y)&&x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0])inside=!inside}if(inside)dot(x,y,color)}
      };
      const horizon=height-44,quay=horizon-7;
      rect(0,0,width,height,'#d6eef8');
      // An airy seaside mural drawn on a fine pixel grid.
      ellipse(width*.62,35,16,16,'#fae6b7');ellipse(width*.62,35,11,11,'#fff3d4');
      for(const [x,y,s] of [[width*.37,27,1],[width*.74,43,.8],[width*.97,34,.7]]){ellipse(x,y,22*s,6*s,'#f2fbfc');ellipse(x+14*s,y-5*s,14*s,9*s,'#f2fbfc');ellipse(x+29*s,y,19*s,6*s,'#f2fbfc')}
      polygon([[width*.2,horizon],[width*.32,horizon-22],[width*.45,horizon-14],[width*.6,horizon-34],[width*.8,horizon-8],[width,horizon-20],[width,horizon]],'#b8dbe3');
      rect(0,horizon,width,44,'#83c4de');rect(0,horizon+12,width,32,'#70b7d4');
      // Colorful waterfront facades, shaded shutters and small balconies.
      const townX=width*.37,step=Math.max(24,Math.min(34,width*.034));
      const walls=['#f8e9cb','#f0c5b4','#eaf5eb','#e6ebcf','#faf0d6','#e4edf4','#f1cdbc'];
      for(let house=0;house<7;house++){
        const x=townX+house*step,w=step-3,h=28+(house%3)*9,y=quay-h;
        rect(x+2,y,w,h,walls[house]);rect(x+w-5,y,5,h,'#94afb7',.35);
        polygon([[x,y],[x+w/2,y-12],[x+w+3,y]],house%2?'#c88977':'#929eb1');
        rect(x+w/2-1,y-5,4,4,'#fff8e9');
        for(let floor=0;floor<Math.max(1,Math.floor(h/14));floor++)for(let windowIndex=0;windowIndex<2;windowIndex++){
          const wx=x+6+windowIndex*(w/2-2),wy=y+5+floor*14;
          rect(wx-2,wy-1,7,9,'#fcf9e9');rect(wx,wy,3,6,'#55869d');rect(wx-3,wy,2,7,'#6f9c99');
          if((floor+house)%3===0){rect(wx-3,wy+8,9,2,'#d6977e');rect(wx-2,wy+6,7,2,'#84a38b');dot(wx+2,wy+6,'#d69597')}
        }
        rect(x+w/2-3,quay-10,6,10,'#648b9c');
      }
      rect(townX-12,quay,width*.31,7,'#e6cfac');rect(townX-12,quay+7,width*.31,3,'#8caeb8');
      for(let x=townX-8;x<townX+width*.29;x+=18){rect(x,quay-4,2,4,'#d2b28c');rect(x,quay+1,9,2,'#bfaa8b',.35)}
      // A small lighthouse is a visual anchor at the edge of the harbor.
      const lighthouseX=width*.89;
      polygon([[lighthouseX-42,horizon+6],[lighthouseX-13,horizon-3],[lighthouseX+29,horizon],[lighthouseX+49,horizon+10]],'#cabd9e');
      rect(lighthouseX-9,horizon-49,18,50,'#fff8e5');rect(lighthouseX+5,horizon-49,4,50,'#c0d5d9');
      rect(lighthouseX-9,horizon-30,18,9,'#5796b4');rect(lighthouseX-11,horizon-50,22,4,'#bd9777');
      rect(lighthouseX-8,horizon-61,16,11,'#f1e8cc');rect(lighthouseX-5,horizon-60,10,8,'#4e8199');
      polygon([[lighthouseX-13,horizon-61],[lighthouseX,horizon-70],[lighthouseX+14,horizon-61]],'#cb8e7b');
      rect(lighthouseX-3,horizon-12,6,12,'#7ba1b0');
      // White sails and elongated highlights suggest open water.
      function boat(x,y,scale){
        rect(x,y-30*scale,2,32*scale,'#718fa0');
        polygon([[x-2,y-29*scale],[x-2,y-4*scale],[x-20*scale,y-4*scale]],'#fffaf0');
        polygon([[x+3,y-24*scale],[x+3,y-5*scale],[x+18*scale,y-5*scale]],'#e8f2f0');
        polygon([[x-24*scale,y],[x+24*scale,y],[x+16*scale,y+6*scale],[x-17*scale,y+6*scale]],'#416e8b');
        rect(x-26*scale,y+10*scale,54*scale,2,'#c6e9ec',.9);
      }
      boat(width*.2,horizon+18,.85);boat(width*.75,horizon+24,.65);
      for(let band=0;band<3;band++)for(let x=12+band*19;x<width;x+=53){rect(x,horizon+8+band*12,14+(x%19),2,'#d0edf1',.65);rect(x+9,horizon+10+band*12,8,2,'#5b9fbf',.45)}
      // Repeating wave curves provide an art-poster border to the pixel scene.
      for(let x=0;x<width;x+=2){const wave=Math.round(Math.sin(x/25)*2);dot(x,height-7+wave,'#daeef0',.9);dot(x,height-3-wave,'#e5cc9d',.8)}
      context.globalAlpha=1;
    }

 let layoutFrame=0;
 function updateChrome(){
  cancelAnimationFrame(layoutFrame);
  layoutFrame=requestAnimationFrame(()=>{
   const height=masthead.getBoundingClientRect().height+toolbar.getBoundingClientRect().height;
   document.documentElement.style.setProperty('--harbor-chrome-height',Math.ceil(height)+'px');
   if(context)paint();
   window.dispatchEvent(new Event('harborlayoutchange'));
  });
 }
 const focusKey='vds.harbor.focus.'+(location.pathname==='/game'?'game':'drama');
 function showFocus(on){
  document.body.classList.toggle('harbor-focus',on);
  button.setAttribute('aria-pressed',String(on));
  button.textContent=on?'⤡ 通常の表示に戻す':'⤢ 編集エリアを広げる';
  updateChrome();
 }
 try{showFocus(localStorage.getItem(focusKey)==='true')}catch(_){showFocus(false)}
 button.addEventListener('click',()=>{
  const on=!document.body.classList.contains('harbor-focus');showFocus(on);
  try{localStorage.setItem(focusKey,String(on))}catch(_){}
 });
 if(typeof ResizeObserver==='function'){
  const observer=new ResizeObserver(updateChrome);observer.observe(masthead);observer.observe(toolbar);
 }else window.addEventListener('resize',updateChrome);
 updateChrome();
})();
