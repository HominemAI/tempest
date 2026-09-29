'use strict';
// One theme owner for every route. Loaded synchronously before the body paints.
(()=>{
 const key='tempest-theme',root=document.documentElement;
 const read=k=>{try{return localStorage.getItem(k)}catch{return null}};
 const valid=v=>v==='light'||v==='dark';
 let chosen=read(key);
 if(!valid(chosen))chosen=[read('tempest-inference-theme'),read('tempest-comparison-theme')].find(valid);
 const media=matchMedia('(prefers-color-scheme: dark)');
 function updateButton(){const button=document.getElementById('tempest-theme');if(!button)return;const dark=root.dataset.theme==='dark';button.textContent=dark?'Light mode':'Dark mode';button.setAttribute('aria-label',dark?'Switch to light mode':'Switch to dark mode');button.setAttribute('aria-pressed',String(dark));}
 function set(value,persist=true){const theme=valid(value)?value:(media.matches?'dark':'light');root.dataset.theme=theme;root.dataset.tempestTheme=theme;if(persist)try{localStorage.setItem(key,theme)}catch{}updateButton();dispatchEvent(new CustomEvent('tempest:theme',{detail:{theme}}));}
 set(chosen,valid(chosen));
 window.TempestTheme={set,toggle:()=>set(root.dataset.theme==='dark'?'light':'dark')};
 document.addEventListener('DOMContentLoaded',()=>{updateButton();const button=document.getElementById('tempest-theme');if(button)button.onclick=window.TempestTheme.toggle;});
 addEventListener('storage',event=>{if(event.key===key)set(event.newValue,false)});
 media.addEventListener('change',()=>{if(!valid(read(key)))set(null,false)});
})();
