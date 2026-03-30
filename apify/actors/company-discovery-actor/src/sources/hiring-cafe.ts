// hiring.cafe: 3-strategy hybrid (proxy → playwright → wayback)
import { Actor, log } from 'apify';
import { gotScraping } from 'got-scraping';
import { sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

const KV = 'company-discovery-portals';
const HC_KEY = 'hiring_cafe_job_counts';
const HC_API = 'https://hiring.cafe/api/search-jobs';
const CDX = 'http://web.archive.org/cdx/search/cdx';
const WB = 'http://web.archive.org/web';

interface HCJob { v5_processed_company_data?: { name?: string }; source?: string }

function extractName(j: HCJob): string {
  return j.v5_processed_company_data?.name?.trim() ?? j.source?.trim() ?? '';
}

async function fetchLive(proxyUrl:string): Promise<Map<string,number>|null> {
  const c=new Map<string,number>(); let blocks=0;
  for(let p=0;p<25;p++){
    try{
      const r=await gotScraping({url:HC_API,method:'POST',proxyUrl,headers:{'Content-Type':'application/json','Accept':'application/json','Origin':'https://hiring.cafe','Referer':'https://hiring.cafe/'},headerGeneratorOptions:{browsers:['chrome'],operatingSystems:['macos'],locales:['en-US']},body:JSON.stringify({searchQuery:'',filters:[],page:p,pageSize:80,sortBy:'date'}),timeout:{request:30_000},followRedirect:true});
      if([403,429].includes(r.statusCode)||r.body.includes('cf-browser-verification')){if(++blocks>=3)return c.size>=50?c:null;await sleep(5000);continue;}
      if(r.statusCode!==200){await sleep(2000);continue;}
      blocks=0; const jobs:HCJob[]=JSON.parse(r.body)?.results??[]; if(!jobs.length)break;
      for(const j of jobs){const n=extractName(j);if(n.length>1)c.set(n,(c.get(n)??0)+1);}
      log.info(`hiring.cafe/proxy: p${p}→${c.size}u`); await sleep(800);
    }catch(e){log.warning(`proxy p${p}: ${e}`);await sleep(3000);}
  }
  return c.size>=10?c:null;
}

/** Strategy 2: Crawlee PlaywrightCrawler — loads hiring.cafe to establish a real browser session,
 *  then uses page.evaluate() to issue direct API pagination (30 pages × 80 jobs) from within
 *  the browser context. This is faster than DOM scrolling and captures more data. */
async function fetchViaPlaywright(): Promise<Map<string,number>|null> {
  const c=new Map<string,number>();
  try {
    const {PlaywrightCrawler}=await import('crawlee');
    await new PlaywrightCrawler({maxRequestsPerCrawl:1,headless:true,requestHandlerTimeoutSecs:120,
      launchContext:{launchOptions:{args:['--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage']}},
      async requestHandler({page}){
        // Load the page once to seed cookies/fingerprint and pass CF challenge
        await page.goto('https://hiring.cafe',{waitUntil:'networkidle',timeout:60_000});
        await page.waitForTimeout(2000);
        // Issue explicit API pagination directly from browser context (avoids DOM scroll timing)
        type HCResult={results?:Array<{v5_processed_company_data?:{name?:string};source?:string}>};
        const pages:HCResult[]=await page.evaluate(async()=>{
          const out:HCResult[]=[];
          for(let p=0;p<30;p++){
            try{
              const r=await fetch('/api/search-jobs',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({searchQuery:'',filters:[],page:p,pageSize:80,sortBy:'date'})});
              if(!r.ok)break;
              const d=await r.json() as HCResult;
              out.push(d);
              if(!d.results?.length)break;
              await new Promise(res=>setTimeout(res,300));
            }catch{break;}
          }
          return out;
        });
        for(const page of pages)for(const j of page.results??[]){
          const n=(j.v5_processed_company_data?.name??j.source??'').trim();
          if(n.length>1)c.set(n,(c.get(n)??0)+1);
        }
      },
    }).run([{url:'https://hiring.cafe'}]);
    log.info(`hiring.cafe/crawlee-eval: ${c.size} unique`);
  }catch(e){log.warning(`hiring.cafe/crawlee: ${e}`);}
  return c.size>=20?c:null;
}

async function fetchViaWayback(maxSnaps=30): Promise<Map<string,number>> {
  const c=new Map<string,number>();
  try {
    const r=await gotScraping({url:`${CDX}?url=hiring.cafe/api/search-jobs&output=json&fl=timestamp,length&filter=statuscode:200&limit=500`,timeout:{request:30_000}});
    if(r.statusCode!==200)return c;
    const raw=(JSON.parse(r.body)as string[][]).slice(1).map(row=>({ts:row[0],sz:parseInt(row[1],10)})).filter(s=>s.sz>=50*1024).sort((a,b)=>b.ts.localeCompare(a.ts));
    const n=Math.min(Math.ceil(maxSnaps/2),raw.length); const older=raw.slice(n); const step=Math.max(1,Math.floor(older.length/(maxSnaps-n)));
    const sel=[...raw.slice(0,n),...older.filter((_,i)=>i%step===0).slice(0,maxSnaps-n)];
    log.info(`hiring.cafe/wayback: ${sel.length} snapshots`);
    for(const s of sel){
      try{
        const resp=await gotScraping({url:`${WB}/${s.ts}if_/https://hiring.cafe/api/search-jobs`,headers:{Accept:'application/json'},timeout:{request:30_000},followRedirect:true});
        if(resp.statusCode===200&&resp.body.trimStart().startsWith('{')){
          const jobs:HCJob[]=JSON.parse(resp.body)?.results??[];
          for(const j of jobs){const nm=extractName(j);if(nm.length>1)c.set(nm,(c.get(nm)??0)+1);}
          log.info(`wayback:${s.ts}→${jobs.length}j ${c.size}u`); await sleep(1200);
        }else await sleep(500);
      }catch{await sleep(500);}
    }
  }catch(e){log.warning(`hiring.cafe/wayback CDX: ${e}`);}
  return c;
}

export async function discoverFromHiringCafe(maxSnapshots=30): Promise<CompanyDiscovery[]> {
  let counts:Map<string,number>|null=null; let strategy='wayback-cdx';
  try { const proxy=await Actor.createProxyConfiguration({groups:['RESIDENTIAL'],countryCode:'US'}); if(proxy){const pu=await proxy.newUrl();if(pu){counts=await fetchLive(pu);if((counts?.size??0)>=50)strategy='live-proxy';else counts=null;}} } catch(e){log.info(`proxy n/a: ${e}`);}
  if(!counts){counts=await fetchViaPlaywright();if(counts)strategy='playwright';}
  if(!counts){counts=await fetchViaWayback(maxSnapshots);}
  if(!counts?.size)return [];
  log.info(`hiring.cafe: strategy=${strategy} companies=${counts.size}`);
  const store=await Actor.openKeyValueStore(KV);
  const prev:Record<string,number>=(await store.getValue<Record<string,number>>(HC_KEY))??{};
  const now=new Date().toISOString(); const results:CompanyDiscovery[]=[]; const nc:Record<string,number>={};
  for(const[name,cnt]of counts){nc[name]=cnt;const p=prev[name]??null;results.push({company_name:name,job_board_url:`https://hiring.cafe/?q=${encodeURIComponent(name)}`,estimated_jobs:cnt,source:'hiring-cafe',discovered_at:now,prev_jobs:p,jobs_delta:p!==null?cnt-p:null}as CompanyDiscovery);}
  await store.setValue(HC_KEY,nc);
  results.sort((a,b)=>b.estimated_jobs-a.estimated_jobs);
  return results;
}
