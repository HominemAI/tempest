'use strict';
const $=id=>document.getElementById(id);
let deltaIndexPromise=null,eventDetailSequence=0;
function loadDeltaIndex(){
 if(!deltaIndexPromise)deltaIndexPromise=fetch('/api/state-index?kind=delta').then(async response=>{
  if(!response.ok)throw Error('Delta index unavailable');const data=await response.json();
  if(data.dimensions?.length!==23)throw Error('Invalid delta index');return data;
 }).catch(e=>{deltaIndexPromise=null;throw e;});
 return deltaIndexPromise;
}
async function inspectEventTurn(turn){
 const sequence=++eventDetailSequence;
 $('event-turn-details').hidden=false;$('event-turn-title').textContent=`Turn ${turn.turn} · ${turn.event}`;
 $('event-turn-phrase').textContent='Loading delta descriptions…';$('event-turn-source').textContent='';
 const numbers=$('event-turn-numbers');numbers.replaceChildren();
 turn.dimensions.forEach((name,i)=>{
  if(turn.event_delta[i]===0&&turn.net_delta[i]===0)return;
  const line=document.createElement('span'),label=document.createElement('strong');line.className='event-delta-line';label.textContent=name+': ';line.append(label);
  for(const [kind,value] of [['event',turn.event_delta[i]],['net',turn.net_delta[i]]]){
   const span=document.createElement('span');span.className='delta-ink '+stateColor(i,value);span.textContent=`${kind} ${value>0?'+':''}${value.toFixed(2)}`;span.title=stateColorMeaning(i,value);line.append(span);if(kind==='event')line.append(' · ');
  }
  numbers.append(line);
 });
 if(!numbers.children.length)numbers.textContent='No changes.';
 if(window.TEMPEST_HOSTED&&typeof turn.delta_text==='string'){
  $('event-turn-phrase').textContent=turn.delta_text;
  $('event-turn-source').textContent='Stored server description · model-category changes before multipliers, recovery, and caps.';
  return;
 }
 try{
  const index=await loadDeltaIndex();if(sequence!==eventDetailSequence)return;
  const phrases=[];
  turn.categories.forEach((category,i)=>{
   if(category===0)return;
   const options=index.dimensions[i].anchors.find(anchor=>anchor.value===category)?.options;
   if(!options?.length)throw Error('Missing delta anchor for '+index.dimensions[i].name);
   // Stable phrase selection: reopening a turn never randomly rewrites it.
   const key=`${turn.session}|${turn.turn}|${turn.seed}|${index.dimensions[i].id}|${category}`;
   let hash=2166136261;for(const char of key)hash=Math.imul(hash^char.charCodeAt(0),16777619)>>>0;
   phrases.push(options[hash%options.length].text.trim());
  });
  $('event-turn-phrase').textContent=phrases.length?phrases.join(' '):'No changes.';
  $('event-turn-source').textContent=`Delta index ${index.version} · Describes model categories before multipliers, recovery, and caps—not the final net change.`;
 }catch(e){if(sequence===eventDetailSequence)$('event-turn-phrase').textContent=e.message+'. Reopen this event to retry.';}
}
let lastAnimatedEventAt=null;
function updateEventMotion(requestStarted){
 const now=performance.now();
 // The next completion time is unknown; use the observed arrival interval.
 const interval=lastAnimatedEventAt===null?Math.max(1000,now-requestStarted):Math.max(1000,now-lastAnimatedEventAt);
 $('live-bars').style.setProperty('--event-motion-duration',Math.round(interval)+'ms');
 lastAnimatedEventAt=now;
}
let historySession=crypto.randomUUID();
const HISTORY_PENDING='tempest-unsaved-turns-v1';
let pendingTurns=[];
if(!window.TEMPEST_HOSTED)try{const stored=JSON.parse(localStorage.getItem(HISTORY_PENDING)||'[]');if(Array.isArray(stored))pendingTurns=stored;}catch(e){$('history-status').textContent='Pending history could not be read from browser storage.';}
let historyTurns=[];
function appendEventLog(turn,prepend=true){
 const key=turn.session+':'+turn.turn;
 if([...$('live-history').children].some(row=>row.dataset.turnKey===key))return;
 const item=document.createElement('li'),button=document.createElement('button');item.dataset.turnKey=key;item.value=turn.turn;button.type='button';button.className='event-log-link';button.textContent=turn.event;button.title=turn.timestamp||turn.session;button.onclick=()=>inspectEventTurn(turn);item.append(button);
 if(prepend)$('live-history').prepend(item);else $('live-history').append(item);
}
async function restorePersonalityLog(){
 const id=$('live-start').value==='saved'?loadedPersonality?.id:null;if(!id)return;
 try{await flushHistory();const response=await fetch('/api/inference/history?personality='+encodeURIComponent(id));if(!response.ok)throw Error('Could not load personality log');const data=await response.json();if(loadedPersonality?.id!==id||$('live-start').value!=='saved')return;data.turns.forEach(turn=>appendEventLog(turn,false));$('history-status').textContent='Personality log restored (latest 500 turns). New turns save automatically.';}
 catch(e){$('history-status').textContent=e.message;}
}
async function flushHistory(){
 for(const turn of [...pendingTurns]){
  const response=await fetch('/api/inference/history',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(turn)});
  if(!response.ok)throw Error('History save failed. Press Play to retry before continuing.');
  pendingTurns=pendingTurns.filter(t=>!(t.session===turn.session&&t.turn===turn.turn));
  localStorage.setItem(HISTORY_PENDING,JSON.stringify(pendingTurns));
 }
 $('history-status').textContent='All completed turns saved locally.';
}
async function refreshHistory(){
 try{const response=await fetch('/api/inference/history');if(!response.ok)throw Error('History unavailable');const data=await response.json(),select=$('history-session'),selected=select.value;select.replaceChildren(new Option('Choose a session',''));data.sessions.forEach(s=>select.add(new Option(`${s.updated} · ${s.turns} turns · ${s.id.slice(0,8)}`,s.id)));select.value=selected;}
 catch(e){$('history-status').textContent=e.message;}
}
function showHistoryTurn(){
 const turn=historyTurns[Number($('history-turn').value)],rows=$('history-values');rows.replaceChildren();if(!turn)return;
 $('history-event').textContent=turn.event;
 liveNames.forEach((name,i)=>{const row=document.createElement('tr');[name,turn.before[i],turn.model_input[i],turn.categories[i],turn.event_delta[i],turn.after[i],turn.baselines_after.temporary[i]].forEach(v=>{const cell=document.createElement('td');cell.textContent=typeof v==='number'?Number(v.toFixed(3)):v;row.append(cell);});rows.append(row);});
}
$('history-refresh').onclick=refreshHistory;
$('history-session').onchange=async()=>{try{const response=await fetch('/api/inference/history?session='+encodeURIComponent($('history-session').value));if(!response.ok)throw Error('Could not load session');const data=await response.json();historyTurns=data.turns||[];const select=$('history-turn');select.replaceChildren();historyTurns.forEach((t,i)=>select.add(new Option('Turn '+t.turn,String(i))));showHistoryTurn();}catch(e){$('history-status').textContent=e.message;}};
$('history-turn').onchange=showHistoryTurn;
$('history-export').onclick=()=>{if(!historyTurns.length)return;const url=URL.createObjectURL(new Blob([JSON.stringify(historyTurns,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=`tempest-session-${historyTurns[0].session}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
// Live mode is session-local. One in-flight call; pausing discards its result.
const liveNames=['Self-preservation','Threat vigilance','Mobilization','Alarm','Aversion','Anger / resistance','Frustration','Agency','Exploration','Reward pursuit','Pleasure','Contentment','Affiliation','Trust / openness','Social withdrawal','Care','Separation distress','Grief / sadness','Recognition seeking','Shame / exposure sensitivity','Guilt / repair motivation','Restoration','Play'];
const liveEmoji=['🛡️','👀','⚡','🚨','🤢','😠','😤','🎯','🧭','🏆','😊','😌','🤝','👐','🚪','💗','💔','😢','🌟','😳','🩹','😴','🎲'];
// Display convention only: +1 prefers higher, -1 prefers lower, 0 is context-dependent.
// This never changes model inputs, effects, or simulation settings.
const stateDesirability=[0,0,0,-1,0,-1,-1,1,0,0,1,1,0,0,0,0,-1,-1,0,-1,0,0,0];
function stateColor(index,value){return value===0?'neutral':stateDesirability[index]===0?(value>0?'context-positive':'context-negative'):value*stateDesirability[index]>0?'desirable':'undesirable';}
function stateColorMeaning(index,value){const color=stateColor(index,value);return color==='neutral'?'at zero':color.startsWith('context-')?'context-dependent; no desirability assigned':color==='desirable'?'more desirable direction (display convention)':'less desirable direction (display convention)';}
let liveRunning=false,liveBusy=false,liveTimer=null,liveGeneration=0,liveState=null,liveCount=0,liveSeed=42,liveBaseline=null;
const recoveryRates=Array(23).fill(.25),eventMultipliers=Array(23).fill(1);
let pressureState=null;
const pressureConfig=TempestPressure.defaults(),savedVector=Array(23).fill(0);
let loadedPersonality=null,loadedSnapshot=null;
function simulationSettings(){return {version:1,seed:Number($('live-seed').value),recovery:[...recoveryRates],multipliers:[...eventMultipliers],effects:[...categoryEffects],constitution:structuredClone(pressureConfig),pressureEnabled:$('pressure-enabled').checked,longEnabled:$('long-enabled').checked,relationshipsEnabled:$('relationships-enabled').checked,relationshipVersion:relationshipData?.version||null};}
function restoreSettings(settings){
 const s=settings||{seed:42,recovery:Array(23).fill(.25),multipliers:Array(23).fill(1),effects:[...defaultCategoryEffects],constitution:TempestPressure.defaults(),pressureEnabled:false,longEnabled:false,relationshipsEnabled:false};
 if(!validVector(s.recovery,23,0,1)||!validVector(s.multipliers,23,-1,2)||!validVector(s.effects,9,-10,10)||!TempestPressure.specs.every(([k,,,min,max])=>validVector(s.constitution?.[k],23,min,max))||!Number.isInteger(s.seed)||s.seed<0||s.seed>=2**32)throw Error('Invalid saved simulation settings.');
 if(s.relationshipsEnabled&&s.relationshipVersion!==relationshipData?.version)throw Error('The saved relationship version is not available.');
 recoveryInputs.forEach((input,i)=>{input.value=s.recovery[i];input.oninput();});multiplierInputs.forEach((input,i)=>{input.value=s.multipliers[i];input.oninput();});categoryInputs.forEach(({number},i)=>{number.value=s.effects[i];number.oninput();});
 for(const key of Object.keys(pressureInputs))pressureInputs[key].forEach(input=>{input.value=s.constitution[key][pressureInputs[key].indexOf(input)];input.oninput();});
 $('live-seed').value=s.seed;$('pressure-enabled').checked=Boolean(s.pressureEnabled);$('long-enabled').checked=Boolean(s.longEnabled);$('relationships-enabled').checked=Boolean(s.relationshipsEnabled);
}
function previewPersonality(){
 liveBaseline=[...savedVector];liveState=loadedSnapshot?[...loadedSnapshot.current]:[...savedVector];pressureState=loadedSnapshot?structuredClone(loadedSnapshot.pressure):TempestPressure.create(liveBaseline);
 drawLive(liveState,Array(23).fill(0));drawPressure();
}
let relationshipData=null,relationshipError=null;
function renderRelationships(){const query=$('relationship-filter').value.trim().toLowerCase();$('relationship-rows').replaceChildren();for(const link of relationshipData?.links||[]){if(!`${liveNames[link.source]} ${liveNames[link.target]}`.toLowerCase().includes(query))continue;const row=document.createElement('tr');[liveNames[link.source],link.direction>0?'Increase':'Decrease',liveNames[link.target],(link.multiplier>0?'+':'')+link.multiplier.toFixed(3)+'×'].forEach(value=>{const td=document.createElement('td');td.textContent=value;row.append(td);});$('relationship-rows').append(row);}}
$('relationship-filter').oninput=renderRelationships;
const relationshipsReady=fetch('/assets/relationship-multipliers.json').then(async response=>{if(!response.ok)throw Error('Coefficient file unavailable');const data=await response.json();if(data.version!=='relationship-draft-1'||!Array.isArray(data.links)||data.links.length!==89||!data.links.every(l=>Number.isInteger(l.source)&&l.source>=0&&l.source<23&&Number.isInteger(l.target)&&l.target>=0&&l.target<23&&l.source!==l.target&&[1,-1].includes(l.direction)&&Number.isFinite(l.multiplier)))throw Error('Invalid coefficient file');relationshipData=data;$('relationships-enabled').disabled=false;$('relationships-status').textContent=`${data.links.length} candidate directional links · ${data.version}. Changes apply next turn; requires mid-term shifts.`;renderRelationships();}).catch(e=>{relationshipError=e.message;$('relationships-status').textContent='Relationships unavailable: '+e.message+'. Reload to retry; base simulation still works.';});
const defaultCategoryEffects=[-2,-1,-.5,-.25,0,.25,.5,1,2],categoryEffects=[...defaultCategoryEffects];
const categoryNames=['Extreme decrease','Large decrease','Moderate decrease','Small decrease','No change','Small increase','Moderate increase','Large increase','Extreme increase'];
const categoryInputs=categoryEffects.map((value,i)=>{const box=document.createElement('div'),label=document.createElement('label'),range=document.createElement('input'),number=document.createElement('input');box.className='category-control';label.htmlFor='category-effect-'+i;label.textContent=(i>4?'+':'')+(i-4)+' · '+categoryNames[i];range.type='range';number.type='number';number.id=label.htmlFor;for(const input of [range,number]){input.min=-10;input.max=10;input.step=.05;input.value=value;input.setAttribute('aria-label',categoryNames[i]+' effect in state units');}const sync=input=>{if(input.value.trim()===''||!Number.isFinite(Number(input.value))||Number(input.value)<-10||Number(input.value)>10){input.setCustomValidity('Enter a value between −10 and +10.');return;}input.setCustomValidity('');categoryEffects[i]=Number(input.value);for(const other of [range,number])if(other!==input){other.value=input.value;other.setCustomValidity('');}};range.oninput=()=>sync(range);number.oninput=()=>sync(number);box.append(label,range,number);$('category-controls').append(box);return {range,number};});
$('category-reset').onclick=()=>categoryInputs.forEach(({range,number},i)=>{categoryEffects[i]=defaultCategoryEffects[i];for(const input of [range,number]){input.value=categoryEffects[i];input.setCustomValidity('');}});
function makeSliders(target,values,min,max,step,format){return liveNames.map((name,i)=>{const row=document.createElement('div'),label=document.createElement('label'),input=document.createElement('input'),output=document.createElement('output');row.className='slider-row';input.id=target+'-'+i;input.type='range';input.min=min;input.max=max;input.step=step;input.value=values[i];label.htmlFor=input.id;label.textContent=liveEmoji[i];label.title=name;input.setAttribute('aria-label',name+(target==='recovery-sliders'?' baseline recovery':' event multiplier'));input.title=name;output.htmlFor=input.id;output.textContent=format(values[i]);input.setAttribute('aria-valuetext',format(values[i]));input.oninput=()=>{values[i]=Number(input.value);output.textContent=format(values[i]);input.setAttribute('aria-valuetext',format(values[i]));};row.append(label,input,output);$(target).append(row);return input;});}
const recoveryInputs=makeSliders('recovery-sliders',recoveryRates,0,1,.01,v=>Math.round(v*100)+'%');
const multiplierInputs=makeSliders('multiplier-sliders',eventMultipliers,-1,2,.05,v=>v.toFixed(2)+'×');
const chartScroll=document.querySelector('.live-scroll'),sliderScroll=document.querySelector('.regulation-scroll');
for(const [source,target] of [[chartScroll,sliderScroll],[sliderScroll,chartScroll]])source.addEventListener('scroll',()=>{if(Math.abs(target.scrollLeft-source.scrollLeft)>1)target.scrollLeft=source.scrollLeft;},{passive:true});
function setAllRecovery(value){$('recovery-master').value=value;$('recovery-master-value').textContent=Math.round(value*100)+'%';$('recovery-master').setAttribute('aria-valuetext',Math.round(value*100)+'%');recoveryInputs.forEach(input=>{input.value=value;input.oninput();});}
$('recovery-master').oninput=()=>setAllRecovery(Number($('recovery-master').value));
$('recovery-reset').onclick=()=>setAllRecovery(.25);
recoveryInputs.forEach(input=>{const update=input.oninput;input.oninput=()=>{update();const same=recoveryRates.every(v=>v===recoveryRates[0]);$('recovery-master-value').textContent=same?Math.round(recoveryRates[0]*100)+'%':'Mixed';if(same)$('recovery-master').value=recoveryRates[0];$('recovery-master').setAttribute('aria-valuetext',same?Math.round(recoveryRates[0]*100)+'%':'Mixed individual values; move to set all');};});
$('multiplier-reset').onclick=()=>multiplierInputs.forEach(input=>{input.value=1;input.oninput();});
function recoverState(state,baseline,rates){return state.map((v,i)=>v+(baseline[i]-v)*rates[i]);}
function applyLiveDeltas(state,deltas,multipliers){const sum=state.map((v,i)=>v+deltas[i]*multipliers[i]);return {state:sum.map(v=>Math.max(-10,Math.min(10,Math.round(v*1e8)/1e8))),clipped:sum.filter(v=>v< -10||v>10).length};}
const pressureInputs={};
for(const [key,title,,min,max,step,help] of TempestPressure.specs){
 const section=document.createElement('details'),summary=document.createElement('summary'),p=document.createElement('p'),row=document.createElement('div'),master=document.createElement('input'),label=document.createElement('label');
 summary.textContent=title;p.textContent=help;row.id='pressure-'+key;row.className='state-sliders';master.type='range';master.min=min;master.max=max;master.step=step;master.value=pressureConfig[key][0];master.setAttribute('aria-label','Set all '+title.toLowerCase());label.textContent='Set all states ';label.append(master);section.append(summary,p,label,row);$('pressure-controls').append(section);
 const format=v=>key==='sensitivity'?v.toFixed(2)+'×':(v*100).toFixed(key==='longRate'?1:0)+'%';
 pressureInputs[key]=makeSliders(row.id,pressureConfig[key],min,max,step,format);
 pressureInputs[key].forEach((input,i)=>input.setAttribute('aria-label',liveNames[i]+' '+title.toLowerCase()));
 master.oninput=()=>pressureInputs[key].forEach(input=>{input.value=master.value;input.oninput();});
}
const validVector=(v,n,min,max)=>Array.isArray(v)&&v.length===n&&v.every(x=>Number.isFinite(x)&&x>=min&&x<=max);
const statTooltip=document.createElement('div');statTooltip.id='live-stat-tooltip';statTooltip.className='live-stat-tooltip';statTooltip.setAttribute('role','tooltip');statTooltip.hidden=true;document.body.append(statTooltip);
let tooltipTarget=null;
function hideStatTooltip(){statTooltip.hidden=true;if(tooltipTarget)tooltipTarget.removeAttribute('aria-describedby');tooltipTarget=null;}
function showStatTooltip(button,name){hideStatTooltip();tooltipTarget=button;statTooltip.textContent=name;statTooltip.hidden=false;button.setAttribute('aria-describedby',statTooltip.id);const r=button.getBoundingClientRect(),t=statTooltip.getBoundingClientRect();statTooltip.style.left=Math.max(8,Math.min(innerWidth-t.width-8,r.left+r.width/2-t.width/2))+'px';statTooltip.style.top=(r.top-t.height-8<8?r.bottom+8:r.top-t.height-8)+'px';$('live-inspect').textContent=button.dataset.detail||name;}
document.addEventListener('keydown',e=>{if(e.key==='Escape')hideStatTooltip();});window.addEventListener('scroll',hideStatTooltip,true);window.addEventListener('resize',hideStatTooltip);
const barNodes=liveNames.map((name,i)=>{const col=document.createElement('div'),bar=document.createElement('div'),button=document.createElement('button'),value=document.createElement('span');col.className='live-column';bar.className='live-bar unchanged';button.className='live-emoji';button.textContent=liveEmoji[i];button.type='button';button.setAttribute('aria-label',name);value.className='live-value';value.textContent='0.0';col.append(bar,button,value);$('live-bars').append(col);const inspect=()=>showStatTooltip(button,name);button.onmouseenter=inspect;button.onfocus=inspect;button.onclick=inspect;button.onmouseleave=hideStatTooltip;button.onblur=hideStatTooltip;return {bar,button,value};});
const baselineMarkers=barNodes.map(n=>{const marker=document.createElement('span');marker.className='live-baseline';marker.hidden=true;n.bar.parentElement.append(marker);return marker;});
const shiftMarkers=barNodes.map(n=>['temporary','adapted'].map(kind=>{const mark=document.createElement('span');mark.className='live-baseline '+kind;mark.hidden=true;n.bar.parentElement.append(mark);return mark;}));
function drawPressure(){
 $('pressure-values').replaceChildren();
 $('relationship-diagnostics').replaceChildren();
 if(pressureState)liveNames.forEach((name,i)=>{const row=document.createElement('tr');[name,pressureState.directShift?.[i]||0,pressureState.unionShift?.[i]||0,pressureState.relatedAdjustment?.[i]||0].forEach(value=>{const td=document.createElement('td');td.textContent=typeof value==='number'?value.toFixed(4):value;row.append(td);});$('relationship-diagnostics').append(row);});
 liveNames.forEach((name,i)=>{const markers=shiftMarkers[i];markers.forEach((mark,k)=>{mark.hidden=!pressureState;if(pressureState)mark.style.top=(50-pressureState[k?'adapted':'temporary'][i]*5)+'%';});if(!pressureState)return;const tr=document.createElement('tr');[name,liveState[i],pressureState.pressure[i],pressureState.intensity[i],pressureState.original[i],pressureState.temporary[i],pressureState.adapted[i],pressureState.duration[i]].forEach((v,j)=>{const td=document.createElement(j?'td':'th');td.textContent=typeof v==='number'?v.toFixed(j===7?0:2):v;tr.append(td);});$('pressure-values').append(tr);
 barNodes[i].button.dataset.detail+=` · pressure ${pressureState.pressure[i].toFixed(2)} · temporary ${pressureState.temporary[i].toFixed(2)} · adapted ${pressureState.adapted[i].toFixed(2)}`;barNodes[i].button.setAttribute('aria-label',barNodes[i].button.dataset.detail);
 });
}
const changeLabels=barNodes.map(n=>{const label=document.createElement('span');label.className='live-change';label.hidden=true;n.bar.parentElement.append(label);return label;});
function drawChangeLabels(state,delta){state.forEach((v,i)=>{const label=changeLabels[i],d=delta[i];label.hidden=liveCount===0;label.textContent=(d>0?'+':d<0?'−':'±')+Math.abs(d).toFixed(2);});}
const baselineLegend=document.createElement('span');baselineLegend.className='baseline-legend';baselineLegend.textContent=' · Red: starting baseline · Purple: shifted baseline';document.querySelector('.live-legend').append(baselineLegend);
function drawLive(state,delta,nodes=barNodes,baseline=liveBaseline,markers=baselineMarkers){if(nodes===barNodes)drawChangeLabels(state,delta);state.forEach((v,i)=>{const n=nodes[i],h=Math.abs(v)*5;n.bar.style.height=(v===0?.45:h)+'%';n.bar.style.top=(v>0?50-h:50)+'%';n.bar.className='live-bar '+stateColor(i,v);n.value.className='live-value'+(v>0?' above':'');n.value.textContent=(v>0?'+':'')+v.toFixed(2);const marker=markers[i];if(marker){marker.hidden=baseline===null;if(baseline!==null)marker.style.top=(50-baseline[i]*5)+'%';}n.button.dataset.detail=`${liveNames[i]}: ${v.toFixed(2)} · ${stateColorMeaning(i,v)} · latest change ${delta[i]>0?'+':''}${delta[i].toFixed(2)}`+(baseline===null?'':` · starting level ${baseline[i].toFixed(2)}`);n.button.setAttribute('aria-label',n.button.dataset.detail);if(tooltipTarget===n.button)$('live-inspect').textContent=n.button.dataset.detail;});}
function liveControls(){ $('random-event-once').disabled=liveBusy||liveRunning; $('manual-event-submit').disabled=liveBusy; $('live-toggle').textContent=liveRunning?'Pause':'Play';$('live-toggle').setAttribute('aria-pressed',String(liveRunning));$('live-toggle').disabled=liveBusy&&!liveRunning;for(const id of ['live-start','live-seed','random-seed'])$(id).disabled=liveRunning||liveCount>0||liveBusy; }
function pauseLive(message='Paused. State is retained.'){lastAnimatedEventAt=null;liveRunning=false;liveGeneration++;clearTimeout(liveTimer);$('live-status').textContent=message;liveControls();}
function resetLive(){historySession=crypto.randomUUID();pauseLive('Session reset. Start with a fresh personality.');liveState=null;liveBaseline=null;pressureState=null;liveCount=0;$('live-count').textContent='0';$('live-event-text').textContent='Start to watch the personality respond.';$('live-history').replaceChildren();$('event-turn-details').hidden=true;drawLive(Array(23).fill(0),Array(23).fill(0));drawPressure();$('live-inspect').textContent='Hover or focus an emoji for its state name, value, and latest change.';liveControls();$('live-toggle').textContent='Play';if($('live-start').value==='saved'&&loadedPersonality){previewPersonality();restorePersonalityLog();}}
async function liveTick(manualEvent=null,singleTurn=false){
 if(!liveRunning||liveBusy)return;liveBusy=true;const generation=liveGeneration,started=performance.now(),rates=[...recoveryRates],multipliers=[...eventMultipliers],effects=[...categoryEffects],previous=liveState?[...liveState]:null;liveControls();
 const constitution=structuredClone(pressureConfig),pressureEnabled=$('pressure-enabled').checked,longEnabled=$('long-enabled').checked;
 const links=$('relationships-enabled').checked&&pressureEnabled?(relationshipData?.links||[]):[];
 try{
  await flushHistory();if(generation!==liveGeneration)return;
  const session=historySession,source=$('live-start').value,personalityId=$('live-start').value==='saved'?loadedPersonality?.id:null,pressureBefore=pressureState?structuredClone(pressureState):null;
  const payload={...(manualEvent===null?{random_event:true}:{event:manualEvent}),seed:liveSeed,steps:[0.25,0.5,1,2]};
  if(previous)payload.state=recoverState(previous,pressureState?(pressureEnabled?pressureState.temporary:pressureState.adapted):liveBaseline,rates);else if($('live-start').value==='neutral')payload.state=Array(23).fill(0);else if($('live-start').value==='saved')payload.state=[...savedVector];
  const response=await fetch('/api/inference',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),r=await response.json();
  if(generation!==liveGeneration)return;
  if(!response.ok)throw Error(r.error||'Local inference failed.');
  if(r.starting_vector?.length!==23||!r.starting_vector.every(Number.isFinite)||r.categories?.length!==23||!r.categories.every(v=>Number.isInteger(v)&&v>=-4&&v<=4))throw Error('Unexpected model state values.');
  if(liveBaseline===null)liveBaseline=[...r.starting_vector];
  if(pressureState===null)pressureState=TempestPressure.create(liveBaseline);
  pressureState=TempestPressure.advance(pressureState,r.starting_vector,r.categories.map((c,i)=>effects[c+4]*multipliers[i]),constitution,pressureEnabled,longEnabled,links);
  const applied=applyLiveDeltas(r.starting_vector,r.categories.map(c=>effects[c+4]),multipliers),prior=previous||r.starting_vector;
  updateEventMotion(started);
  liveState=applied.state;liveCount++;$('live-count').textContent=String(liveCount);$('live-event-text').textContent=r.event;drawLive(liveState,liveState.map((v,i)=>Math.round((v-prior[i])*1e8)/1e8));
  // Bar movement includes recovery and multipliers; its indicator shows only
  // the configured effect size of the model's chosen discrete category.
  drawChangeLabels(liveState,r.categories.map(c=>effects[c+4]));
  drawPressure();
  r.categories.forEach((c,i)=>{const recovery=r.starting_vector[i]-prior[i],baseDelta=effects[c+4];barNodes[i].button.dataset.detail+=` · category ${c>0?'+':''}${c} → ${baseDelta.toFixed(2)} × ${multipliers[i].toFixed(2)} · recovery ${recovery.toFixed(2)}`;barNodes[i].button.setAttribute('aria-label',barNodes[i].button.dataset.detail);if(tooltipTarget===barNodes[i].button)$('live-inspect').textContent=barNodes[i].button.dataset.detail;});

  const turn={session,personality_id:personalityId,turn:liveCount,timestamp:new Date().toISOString(),event:r.event,model:r.model||null,seed:liveSeed,source,event_source:manualEvent===null?'random':'manual',dimensions:liveNames,before:[...prior],model_input:[...r.starting_vector],categories:[...r.categories],event_delta:r.categories.map((c,i)=>effects[c+4]*multipliers[i]),net_delta:liveState.map((v,i)=>v-prior[i]),after:[...liveState],baselines_before:pressureBefore,baselines_after:structuredClone(pressureState),settings:{recovery:rates,multipliers,category_effects:effects,constitution,pressureEnabled,longEnabled,relationship_version:links.length?relationshipData.version:null}};
  appendEventLog(turn);
  pendingTurns.push(turn);localStorage.setItem(HISTORY_PENDING,JSON.stringify(pendingTurns));await flushHistory();
  if(generation!==liveGeneration)return;
  $('live-status').textContent=`Running · ${liveCount} events · ${Math.round(performance.now()-started)} ms last request · ${applied.clipped} states clamped`;
 }catch(e){if(generation===liveGeneration)pauseLive(`Paused: ${e.message} Press Play to retry.`);}
 finally{liveBusy=false;if((manualEvent!==null||singleTurn)&&generation===liveGeneration&&liveRunning)pauseLive(manualEvent!==null?'Manual event applied. Press Play to resume automatic events.':'Random event applied. Paused.');liveControls();if(liveRunning&&generation===liveGeneration)liveTimer=setTimeout(liveTick,Math.max(0,1000-(performance.now()-started)));}
}
$('live-toggle').onclick=()=>{if(liveRunning){pauseLive();return;}if(liveBusy)return;const seed=Number($('live-seed').value);if(!Number.isInteger(seed)||seed<0||seed>=2**32){$('live-status').textContent='Seed must be an integer between 0 and 4294967295.';return;}liveSeed=seed;liveRunning=true;liveGeneration++;$('live-status').textContent=liveCount?'Resuming…':'Warming local model…';liveTick();};
$('live-reset').onclick=resetLive;
$('random-seed').onclick=()=>{$('live-seed').value=crypto.getRandomValues(new Uint32Array(1))[0];$('live-status').textContent='Random seed selected. Press Play to start.';};
document.addEventListener('visibilitychange',()=>{if(document.hidden&&liveRunning)pauseLive('Paused because this tab became hidden. Press Play to continue.');});
window.addEventListener('pagehide',()=>pauseLive());
let savedSearchSequence=0,savedSearchTimer,savedLoadSequence=0;
async function searchSavedPersonalities(){
 const seq=++savedSearchSequence,select=$('saved-personality-select');$('saved-personality-load').disabled=true;
 try{const params=new URLSearchParams({kind:'profile',q:$('saved-personality-search').value.trim(),offset:'0'});const response=await fetch('/api/personalities/list?'+params),data=await response.json();if(seq!==savedSearchSequence)return;if(!response.ok)throw Error(data.error||'Could not load personalities');select.replaceChildren(new Option(data.rows.length?'Choose a saved personality':'No matching personalities',''));for(const row of data.rows)select.add(new Option(`${row.name} · ${row.id}`,row.id));$('saved-personality-status').textContent=`${data.total} matches; showing ${data.rows.length}. Refine the search to find more. Loading copies baseline values only.`;}
 catch(e){if(seq===savedSearchSequence){select.replaceChildren(new Option('Search unavailable',''));$('saved-personality-status').textContent=e.message+' — change the search to retry.';}}
}
$('saved-personality-search').oninput=()=>{savedSearchSequence++;clearTimeout(savedSearchTimer);savedSearchTimer=setTimeout(searchSavedPersonalities,250);};
$('saved-personality-select').onchange=()=>{$('saved-personality-load').disabled=!$('saved-personality-select').value;};
$('saved-personality-load').onclick=async()=>{
 const id=$('saved-personality-select').value;if(!id)return;const seq=++savedLoadSequence;$('saved-personality-load').disabled=true;
 try{const response=await fetch('/api/personalities/record?id='+encodeURIComponent(id)),record=await response.json();if(seq!==savedLoadSequence)return;if(!response.ok)throw Error(record.error||'Could not load personality');const vector=record.body?.state_values;
  if(record.kind!=='profile'||!validVector(vector,23,-10,10))throw Error('This personality does not contain a valid 23-state baseline');
  const snapshot=record.body.simulation_snapshot||null;
  if(snapshot&&(!validVector(snapshot.current,23,-10,10)||!['original','adapted','temporary'].every(k=>validVector(snapshot.pressure?.[k],23,-10,10))||!['pressure','duration','intensity'].every(k=>validVector(snapshot.pressure?.[k],23,-1e9,1e9))))throw Error('Invalid saved simulation state.');
  await relationshipsReady;if(seq!==savedLoadSequence)return;restoreSettings(record.body.simulation_settings);
  loadedPersonality=record;loadedSnapshot=snapshot;savedVector.splice(0,23,...vector);const savedOption=$('live-start').querySelector('[value=saved]');savedOption.disabled=false;savedOption.textContent=record.name;$('live-start').value='saved';resetLive();$('save-simulation').disabled=false;
  $('saved-personality-status').textContent=`Loaded ${record.name}. Graph shows its current state and baselines. ${record.body.simulation_settings?'Saved settings restored.':'No saved settings yet; using defaults.'}`;
 }
 catch(e){$('saved-personality-status').textContent=e.message;}
 finally{if(seq===savedLoadSequence)$('saved-personality-load').disabled=!$('saved-personality-select').value;}
};
$('save-simulation').onclick=async()=>{
 if(!loadedPersonality)return;
 pauseLive('Paused to save state and settings.');$('save-simulation').disabled=true;
 try{const body=structuredClone(loadedPersonality.body);body.simulation_settings=simulationSettings();body.simulation_snapshot={current:[...(liveState||savedVector)],pressure:structuredClone(pressureState||TempestPressure.create(savedVector))};
 const response=await fetch('/api/personalities/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:loadedPersonality.id,revision:loadedPersonality.revision,name:loadedPersonality.name,body})}),data=await response.json();if(!response.ok)throw Error(data.error||'Save failed');loadedPersonality=data;loadedSnapshot=structuredClone(body.simulation_snapshot);$('saved-personality-status').textContent=`Saved ${data.name}: current state, shifted baselines, and all simulation settings. Original baseline unchanged.`;
 }catch(e){$('saved-personality-status').textContent=e.message;}finally{$('save-simulation').disabled=false;}
};
$('live-start').onchange=()=>{resetLive();$('save-simulation').disabled=$('live-start').value!=='saved';};
if(!window.TEMPEST_HOSTED){searchSavedPersonalities();refreshHistory();}
drawLive(Array(23).fill(0),Array(23).fill(0));

function selectEventTab(log){
 for(const [id,selected] of [['current-event-tab',!log],['event-log-tab',log]]){$(id).setAttribute('aria-selected',String(selected));$(id).tabIndex=selected?0:-1;}
 $('current-event-panel').hidden=log;$('event-log-panel').hidden=!log;
}
$('current-event-tab').onclick=()=>selectEventTab(false);$('event-log-tab').onclick=()=>selectEventTab(true);
for(const id of ['current-event-tab','event-log-tab'])$(id).onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const log=e.key==='End'||(e.key!=='Home'&&id==='current-event-tab');selectEventTab(log);$(log?'event-log-tab':'current-event-tab').focus();}};
$('manual-event-form').onsubmit=async e=>{
 e.preventDefault();if(liveBusy)return;const event=$('manual-event').value.trim();if(!event)return;
 const seed=Number($('live-seed').value);if(!Number.isInteger(seed)||seed<0||seed>=2**32){$('live-status').textContent='Enter a valid seed.';return;}
 pauseLive('Applying manual event…');liveSeed=seed;liveRunning=true;liveGeneration++;selectEventTab(false);await liveTick(event);
};

$('random-event-once').onclick=async()=>{
 if(liveRunning||liveBusy)return;
 const seed=Number($('live-seed').value);if(!Number.isInteger(seed)||seed<0||seed>=2**32){$('live-status').textContent='Enter a valid seed.';return;}
 liveSeed=seed;liveRunning=true;liveGeneration++;$('live-status').textContent='Applying one random event…';await liveTick(null,true);
};
if(window.TEMPEST_HOSTED)window.TempestHosted.mount();
