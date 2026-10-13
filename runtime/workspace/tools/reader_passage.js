(()=>{
 const root=document.documentElement,ns='creative-brain-reader:',nebula=root.dataset.brainVisual==='nebula';
 const reduced=()=>matchMedia('(prefers-reduced-motion: reduce)').matches||document.querySelector('#motion')?.getAttribute('aria-pressed')==='true';
 const read=k=>{try{return JSON.parse(sessionStorage.getItem(ns+k)||'null')}catch{return null}};
 const save=(k,v)=>{try{sessionStorage.setItem(ns+k,JSON.stringify(v))}catch{}};
 const palette=read('palette');if(['violet','aurora','rose','amber'].includes(palette))root.dataset.readerPalette=palette;
 const travel=read('travel');save('travel',null);if(travel&&travel.to===location.pathname+location.search&&Date.now()-travel.time<12000&&!reduced()){
  root.dataset.passageTone=travel.tone;root.classList.add('passage-in');
 }
 const ready=()=>{
  setTimeout(()=>root.classList.remove('passage-in'),240);
  const select=document.querySelector('#nebula-palette');
  if(select){if(palette&&select.value!==palette){select.value=palette;select.dispatchEvent(new Event('change',{bubbles:true}));}
   select.addEventListener('change',()=>{save('palette',select.value);root.dataset.readerPalette=select.value;});
  }
  const back=read('reader');
  if(nebula&&back){const u=new URL(back.path,location.href),dir=location.pathname.slice(0,location.pathname.lastIndexOf('/')+1);if(u.origin===location.origin&&u.pathname.startsWith(dir)&&u.pathname.endsWith('.html')){const a=document.querySelector('.tools a');if(a)a.href=u.href;}}
  if(!nebula&&back&&back.path===location.pathname+location.search){requestAnimationFrame(()=>requestAnimationFrame(()=>scrollTo(0,back.y)));}
 };
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',ready,{once:true});else ready();
 let leaving=false;
 document.addEventListener('click',event=>{
  const a=event.target.closest('a[href]');if(!a||event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||a.download||a.target==='_blank')return;
  const url=new URL(a.href,location.href);if(url.origin!==location.origin||url.hash||url.protocol!==location.protocol)return;
  const into=/\/nebula\.html$/.test(url.pathname);if(!into&&!nebula)return;
  if(!into&&(!url.pathname.startsWith(location.pathname.slice(0,location.pathname.lastIndexOf('/')+1))||!url.pathname.endsWith('.html')))return;
  if(into&&root.dataset.brainVisual!=='home')save('reader',{path:location.pathname+location.search,y:scrollY});
  if(into&&root.dataset.brainVisual==='home')save('reader',null);
  if(reduced())return;
  event.preventDefault();if(leaving)return;leaving=true;
  const tone=into?'dark':'light';save('travel',{to:url.pathname+url.search,tone,time:Date.now()});root.dataset.passageTone=tone;root.classList.add('passage-out');
  setTimeout(()=>location.assign(url.href),150);
 });
 addEventListener('pageshow',()=>{leaving=false;root.classList.remove('passage-out');});
})();
