const {spawnSync}=require('node:child_process');const fs=require('node:fs');const path=require('node:path');
if(!process.env.PROXY_BENCH_CHILD){
 const observations=[];
 for(const scenario of ['redirect','obsolete-action','anonymous-legacy','unknown-company'])for(let sample=0;sample<8;sample++){
  const r=spawnSync(process.execPath,[__filename],{encoding:'utf8',env:{...process.env,PROXY_BENCH_CHILD:scenario}});
  if(r.status!==0)throw Error(r.stderr);
  observations.push({...JSON.parse(r.stdout.trim().split('\n').at(-1)),sample});
 }
 fs.writeFileSync(process.argv[2],JSON.stringify({node:process.version,observations},null,2));
 for(const scenario of ['redirect','obsolete-action','anonymous-legacy','unknown-company']){
  const rows=observations.filter(x=>x.scenario===scenario);const median=key=>rows.map(x=>x[key]).sort((a,b)=>a-b)[4];
  console.log(JSON.stringify({scenario,import_cpu_ms:median('import_cpu_ms'),first_request_cpu_ms:median('first_request_cpu_ms'),warm_request_cpu_ms:median('warm_request_cpu_ms')}));
 }
}else{(async()=>{
 const scenario=process.env.PROXY_BENCH_CHILD;
 const s=process.cpuUsage();const {handler}=require(path.resolve('.next/server/middleware.js'));const d=process.cpuUsage(s);
 const urls={'redirect':'/','obsolete-action':'/en/explore','anonymous-legacy':'/en/nonexistent-user/nonexistent-list','unknown-company':'/en/company/definitely-not-a-real-company'};
 const times=[],statuses=[];
 for(let n=0;n<6;n++){
  const req=new Request('https://jseek.co'+urls[scenario],{method:scenario==='obsolete-action'?'POST':'GET',headers:scenario==='obsolete-action'?{'next-action':'7ffac6a500b0410a78dcf5f6a75ea0d2253b635222'}:{accept:'text/html','accept-language':'en-US,en;q=0.9'}});
  const t=process.cpuUsage();const pending=[];const response=await handler(req,{waitUntil:p=>pending.push(p)});await Promise.all(pending);await response.text();const delta=process.cpuUsage(t);times.push((delta.user+delta.system)/1000);statuses.push(response.status);
 }
 console.log(JSON.stringify({scenario,import_cpu_ms:(d.user+d.system)/1000,first_request_cpu_ms:times[0],warm_request_cpu_ms:times.slice(1).sort((a,b)=>a-b)[2],request_cpu_ms:times,statuses}));
})().catch(e=>{console.error(e);process.exit(1)})}
