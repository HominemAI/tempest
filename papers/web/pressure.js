'use strict';
// Draft simulation policy. Pure functions shared by UI and regression tests.
(function(root){
const clamp=(v,lo=-10,hi=10)=>Math.max(lo,Math.min(hi,v));
const specs=[
 ['sensitivity','Pressure sensitivity',1,0,4,.05,'Higher values lower the effective activation threshold. Zero prevents new shifts.'],
 ['shift','Mid-term shift rate',.1,0,1,.01,'Fraction of distance toward the pressure target moved each event.'],
 ['returnRate','Mid-term recovery',.05,0,1,.01,'Fraction of temporary displacement released each turn.'],
 ['release','Pressure release',.15,0,1,.01,'Fraction of the pressure bucket released per turn.'],
 ['longRate','Long-term adaptation',.005,0,.1,.001,'Fraction moved toward the temporary baseline after sustained pressure; only when enabled.']
];
function defaults(){return Object.fromEntries(specs.map(([key,,v])=>[key,Array(23).fill(v)]));}
function create(original){return {original:[...original],adapted:[...original],temporary:[...original],pressure:Array(23).fill(0),duration:Array(23).fill(0),intensity:Array(23).fill(0)};}
function advance(old,current,requested,cfg,enabled,longEnabled,links=[]){
 const next=JSON.parse(JSON.stringify(old));
 const recovered=[...old.temporary],direct=Array(23).fill(0),sourceShift=Array(23).fill(0),active=Array(23).fill(false);
 for(let i=0;i<23;i++){
  if(!enabled){next.temporary[i]=old.adapted[i];next.pressure[i]=0;next.duration[i]=0;next.intensity[i]=0;continue;}
  const d=requested[i],direction=Math.sign(d),displacement=current[i]-old.adapted[i];
  const retained=old.pressure[i]*(1-cfg.release[i]);
  // Same-direction displacement increases incoming pressure, including at caps.
  const intensity=Math.abs(d)*(1+Math.max(0,direction*displacement)/10);
  const pressure=clamp(retained+direction*intensity,-100,100);
  const activation=Math.max(0,Math.abs(pressure)*cfg.sensitivity[i]-1);
  const target=clamp(old.adapted[i]+Math.sign(pressure)*10*Math.tanh(activation/10));
  const recovering=old.temporary[i]+(old.adapted[i]-old.temporary[i])*cfg.returnRate[i];
  recovered[i]=recovering;
  next.temporary[i]=clamp(recovering+(target-recovering)*cfg.shift[i]);
  direct[i]=next.temporary[i]-recovering;active[i]=activation>0;
  // Recovery/inward settling is never a source for relational propagation.
  if(activation>0&&Math.sign(direct[i])===Math.sign(pressure))sourceShift[i]=direct[i];
  next.pressure[i]=pressure;next.intensity[i]=intensity;
 }
 const proposals=[direct];next.relatedProposals=[];
 if(enabled)for(const link of links){
  const {source,target,direction,multiplier}=link;
  if(!Number.isInteger(source)||!Number.isInteger(target)||source<0||source>=23||target<0||target>=23||source===target||![1,-1].includes(direction)||!Number.isFinite(multiplier))throw new Error('Invalid relationship configuration');
  if(Math.sign(sourceShift[source])!==direction||cfg.sensitivity[target]===0)continue;
  const value=sourceShift[source]*multiplier,vector=Array(23).fill(0);vector[target]=value;proposals.push(vector);next.relatedProposals.push({source,target,value});
 }
 const merged=unionShifts(proposals);next.directShift=direct;next.unionShift=merged.net;next.relatedAdjustment=Array(23).fill(0);
 if(enabled)for(let i=0;i<23;i++){
  const final=clamp(recovered[i]+merged.net[i]);next.relatedAdjustment[i]=final-next.temporary[i];next.temporary[i]=final;
 }
 for(let i=0;i<23;i++){
  if(!enabled)continue;
  const pressure=next.pressure[i];
  const offset=next.temporary[i]-old.adapted[i];
  const sustained=active[i]&&Math.abs(offset)>=1&&Math.sign(offset)===Math.sign(pressure);
  next.duration[i]=sustained?(Math.sign(old.pressure[i])===Math.sign(pressure)?old.duration[i]+1:1):0;
  // Ten sustained event turns before adaptation. Not a biological threshold.
  if(longEnabled&&next.duration[i]>=10)next.adapted[i]=clamp(old.adapted[i]+offset*cfg.longRate[i]);
 }
 return next;
}
// Collect a whole cycle before resolving. Repeated sources cannot stack.
function unionShifts(proposals){
 const positive=Array(23).fill(0),negative=Array(23).fill(0);
 for(const vector of proposals){
  if(!Array.isArray(vector)||vector.length!==23||!vector.every(Number.isFinite))throw new Error('Each shift proposal must contain 23 finite numbers.');
  vector.forEach((value,i)=>{positive[i]=Math.max(positive[i],value);negative[i]=Math.min(negative[i],value);});
 }
 return {positive,negative,net:positive.map((value,i)=>value+negative[i])};
}
const api={specs,defaults,create,advance,unionShifts};if(typeof module!=='undefined')module.exports=api;else root.TempestPressure=api;
})(globalThis);
