// Execute the exact pressure/union implementation used by the browser.
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const context={module:{exports:{}}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../papers/web/pressure.js'),'utf8'),context);
const engine=context.module.exports;
let input='';process.stdin.on('data',chunk=>input+=chunk);process.stdin.on('end',()=>{
 try{const p=JSON.parse(input);const value=p.operation==='advance'?engine.advance(p.old,p.current,p.delta,p.config,p.enabled,p.longEnabled,p.links):engine.unionShifts(p.proposals);process.stdout.write(JSON.stringify(value));}
 catch(e){console.error(e.message);process.exitCode=1;}
});
