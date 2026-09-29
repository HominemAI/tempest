'use strict';
const $=id=>document.getElementById(id);let data,entries=[];
function render(){const query=$('search').value.toLowerCase().trim(),state=$('dimension').value,anchor=$('anchor').value;const matches=entries.filter(e=>(!state||e.state.id===state)&&(anchor===''||String(e.anchor.value)===anchor)&&(!query||e.search.includes(query)));$('rows').replaceChildren();let phrases=0;for(const entry of matches){const tr=document.createElement('tr'),th=document.createElement('th'),value=document.createElement('td'),cell=document.createElement('td'),list=document.createElement('ol');th.scope='row';th.textContent=entry.state.name;value.textContent=(entry.anchor.value>0?'+':'')+entry.anchor.value;value.className=entry.anchor.value>0?'positive':entry.anchor.value<0?'negative':'';for(const option of entry.anchor.options){phrases++;const li=document.createElement('li'),text=document.createElement('div'),id=document.createElement('span');text.textContent=option.text;id.textContent=option.id;id.className='option-id';li.append(text,id);list.append(li);}cell.append(list);tr.append(th,value,cell);$('rows').append(tr);}if(!matches.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=3;td.className='empty';td.textContent='No matching anchors. Clear the search or reset the filters.';tr.append(td);$('rows').append(tr);}$('status').textContent=`${matches.length} of ${entries.length} anchors · ${phrases} wording variants shown`;
const selected=data.dimensions.find(s=>s.id===state);$('definition').hidden=!selected;$('definition-content').replaceChildren();if(selected){for(const [label,key] of [['Definition','definition'],['Distinctions','distinctions'],['Interpretation guard','baseline_guard'],['Increase means','increase'],['Decrease means','decrease']]){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=selected[key]||'';$('definition-content').append(dt,dd);}}}
for(const id of ['search','dimension','anchor'])$(id).addEventListener('input',render);
$('reset').onclick=()=>{$('search').value='';$('dimension').value='';$('anchor').value='';render();};
$('random').onclick=()=>{const e=entries[Math.floor(Math.random()*entries.length)];$('search').value='';$('dimension').value=e.state.id;$('anchor').value=String(e.anchor.value);render();};
async function loadIndex(){
const controls=['search','dimension','anchor','random','reset','kind'];
controls.forEach(id=>$(id).disabled=true);$('status').className='';$('status').textContent='Loading index…';$('rows').replaceChildren();$('definition').hidden=true;
try{
const kind=$('kind').value,response=await fetch('/api/state-index?kind='+kind);
if(!response.ok)throw Error('Index unavailable. Select another index or reload after checking the local server.');
data=await response.json();entries=[];$('search').value='';
for(const [id,label] of [['dimension','All 23 states'],['anchor','All anchors']]){const o=document.createElement('option');o.value='';o.textContent=label;$(id).replaceChildren(o);}
for(const s of data.dimensions){const o=document.createElement('option');o.value=s.id;o.textContent=s.name;$('dimension').append(o);for(const a of s.anchors)entries.push({state:s,anchor:a,search:JSON.stringify({...s,anchors:undefined,anchor:a}).toLowerCase()});}
for(const v of data.anchor_values){const o=document.createElement('option');o.value=String(v);o.textContent=(v>0?'+':'')+v;$('anchor').append(o);}
$('version').textContent=`${kind==='state'?'Current state':'Relative change'} · Version ${data.version} · ${data.dimensions.length} states`;
$('index-notice').textContent='Read-only · exact stored wording · model-authored draft, not human-validated.';
$('scale-note').textContent=kind==='state'?'State coordinates: −10 to +10. Descriptions express current intensity without a past-state comparison. Intermediate values use neighboring anchors.':'Change categories: −4 to +4. These describe relative change, not the final state. Zero means no change; numeric adjustment remains a separate policy.';
controls.forEach(id=>$(id).disabled=false);render();
}catch(e){$('status').textContent=e.message;$('status').className='error';$('kind').disabled=false;}}
$('kind').onchange=loadIndex;loadIndex();
