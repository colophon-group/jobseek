// hiring.cafe: 3-strategy hybrid (proxy → playwright → wayback)
import { Actor, log } from 'apify';
import { gotScraping } from 'got-scraping';
import { sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

const KV = 'company-discovery-portals';
const HC_KEY = 'hiring_cafe_job_counts';
const HC_API = 'https://hiring.cafe/api/search-jobs';
const HC_COMPANIES_API = 'https://hiring.cafe/api/search-companies';
const CDX = 'http://web.archive.org/cdx/search/cdx';
const WB = 'http://web.archive.org/web';

interface HCJob { v5_processed_company_data?: { name?: string }; source?: string }
interface HCCompany { name?: string; company_name?: string; slug?: string; totalActiveListings?: number; activeListings?: number; jobCount?: number }

function extractName(j: HCJob): string {
  return j.v5_processed_company_data?.name?.trim() ?? j.source?.trim() ?? '';
}

/** Try the /api/search-companies endpoint which returns companies directly (more efficient than extracting from jobs). */
async function fetchCompaniesEndpoint(proxyUrl: string, c: Map<string,number>): Promise<number> {
  let added=0;
  for(let p=0;p<20;p++){
    try{
      const r=await gotScraping({url:HC_COMPANIES_API,method:'POST',proxyUrl,headers:{'Content-Type':'application/json','Accept':'application/json','Origin':'https://hiring.cafe','Referer':'https://hiring.cafe/'},headerGeneratorOptions:{browsers:['chrome'],operatingSystems:['macos'],locales:['en-US']},body:JSON.stringify({searchQuery:'',filters:[],page:p,pageSize:50}),timeout:{request:30_000},followRedirect:true});
      if([403,429].includes(r.statusCode)||r.body.includes('cf-browser-verification'))break;
      if(r.statusCode!==200){await sleep(2000);continue;}
      const companies:HCCompany[]=JSON.parse(r.body)?.results??[];
      if(!companies.length)break;
      for(const co of companies){const n=(co.name??co.company_name??'').trim();if(n.length>1){const jobs=Math.max(1,co.totalActiveListings??co.activeListings??co.jobCount??3);c.set(n,(c.get(n)??0)+jobs);added++;}} // use actual job count if available
      log.info(`hiring.cafe/proxy companies: p${p}→${c.size}u`); await sleep(600);
    }catch(e){log.warning(`proxy companies p${p}: ${e}`);break;}
  }
  return added;
}

async function fetchLive(proxyUrl:string): Promise<Map<string,number>|null> {
  const c=new Map<string,number>(); let blocks=0;
  // First: try the dedicated companies endpoint (returns companies directly, not via job extraction)
  await fetchCompaniesEndpoint(proxyUrl, c);
  // Multi-sort passes: date + applications + views — each sort returns different top-N companies
  const sorts=['date','applications','views'];
  for(const sortBy of sorts){
    for(let p=0;p<25;p++){
      try{
        const r=await gotScraping({url:HC_API,method:'POST',proxyUrl,headers:{'Content-Type':'application/json','Accept':'application/json','Origin':'https://hiring.cafe','Referer':'https://hiring.cafe/'},headerGeneratorOptions:{browsers:['chrome'],operatingSystems:['macos'],locales:['en-US']},body:JSON.stringify({searchQuery:'',filters:[],page:p,pageSize:80,sortBy}),timeout:{request:30_000},followRedirect:true});
        if([403,429].includes(r.statusCode)||r.body.includes('cf-browser-verification')){if(++blocks>=3)return c.size>=50?c:null;await sleep(5000);continue;}
        if(r.statusCode!==200){await sleep(2000);continue;}
        blocks=0; const jobs:HCJob[]=JSON.parse(r.body)?.results??[]; if(!jobs.length)break;
        for(const j of jobs){const n=extractName(j);if(n.length>1)c.set(n,(c.get(n)??0)+1);}
        log.info(`hiring.cafe/proxy ${sortBy}: p${p}→${c.size}u`); await sleep(800);
      }catch(e){log.warning(`proxy ${sortBy} p${p}: ${e}`);await sleep(3000);}
    }
    blocks=0; // reset block counter between sort passes
  }
  return c.size>=10?c:null;
}

/** Strategy 2a: playwright-extra + stealth plugin — patches Playwright to bypass Cloudflare bot detection.
 *  Significantly harder to detect than standard Playwright: patches navigator, WebGL, chrome runtime,
 *  permissions, etc. Uses page.route() intercept to capture API responses in real time. */
async function fetchViaStealthPlaywright(proxyUrl?: string): Promise<Map<string,number>|null> {
  const c=new Map<string,number>();
  try {
    const {chromium: chromiumExtra}=await import('playwright-extra');
    const {default: StealthPlugin}=await import('puppeteer-extra-plugin-stealth');
    chromiumExtra.use(StealthPlugin());
    const launchOpts: Record<string,unknown>={headless:true,args:['--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled']};
    if(proxyUrl) launchOpts['proxy']={server:proxyUrl};
    const browser=await chromiumExtra.launch(launchOpts);
    try {
      type HCResult={results?:Array<{v5_processed_company_data?:{name?:string};source?:string}>};
      type HCCoResult={results?:Array<{name?:string;company_name?:string;totalActiveListings?:number;activeListings?:number;jobCount?:number}>};
      const urls=[
        {url:'https://hiring.cafe',sortBy:'date',mode:'jobs'},
        {url:'https://hiring.cafe/?sort=applications',sortBy:'applications',mode:'jobs'},
        {url:'https://hiring.cafe/?sort=views',sortBy:'views',mode:'jobs'},
        {url:'https://hiring.cafe/companies',sortBy:'',mode:'companies'},
      ];
      for(const {url,sortBy,mode} of urls){
        const ctx=await browser.newContext({userAgent:'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',locale:'en-US',timezoneId:'America/New_York',viewport:{width:1440,height:900}});
        const page=await ctx.newPage();
        // Intercept API responses passively as they stream in
        page.on('response',async resp=>{
          const u=resp.url();
          const isCo=u.includes('/api/search-companies');
          const isJobs=u.includes('/api/search-jobs');
          if(!isJobs&&!isCo)return;
          try{
            const body=await resp.text();
            if(!body.trimStart().startsWith('{'))return;
            if(isCo){
              const d=JSON.parse(body) as HCCoResult;
              for(const co of d.results??[]){const n=(co.name??co.company_name??'').trim();if(n.length>1){const jobs=Math.max(1,co.totalActiveListings??co.activeListings??co.jobCount??3);c.set(n,(c.get(n)??0)+jobs);}}
            }else{
              const d=JSON.parse(body) as HCResult;
              for(const j of d.results??[]){const n=(j.v5_processed_company_data?.name??j.source??'').trim();if(n.length>1)c.set(n,(c.get(n)??0)+1);}
            }
          }catch{/* ignore parse errors */}
        });
        try{
          await page.goto(url,{waitUntil:'networkidle',timeout:60_000});
          await page.waitForTimeout(3000);
          // Issue paginated requests from within browser context (session cookies intact)
          const pages=await page.evaluate(async(params:{sortBy:string;mode:string})=>{
            const out=[];
            if(params.mode==='companies'){
              for(let p=1;p<25;p++){
                try{const r=await fetch('/api/search-companies',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({searchQuery:'',filters:[],page:p,pageSize:50})});if(!r.ok)break;const d=await r.json();out.push({mode:'companies',data:d});if(!(d as {results?:unknown[]}).results?.length)break;await new Promise(res=>setTimeout(res,400));}catch{break;}
              }
            }else{
              for(let p=1;p<25;p++){
                try{const r=await fetch('/api/search-jobs',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({searchQuery:'',filters:[],page:p,pageSize:80,sortBy:params.sortBy})});if(!r.ok)break;const d=await r.json();out.push({mode:'jobs',data:d});if(!(d as {results?:unknown[]}).results?.length)break;await new Promise(res=>setTimeout(res,400));}catch{break;}
              }
            }
            return out;
          },{sortBy,mode});
          for(const {mode:m,data} of pages as {mode:string;data:unknown}[]){
            if(m==='companies'){const d=data as HCCoResult;for(const co of d.results??[]){const n=(co.name??co.company_name??'').trim();if(n.length>1){const jobs=Math.max(1,co.totalActiveListings??co.activeListings??co.jobCount??3);c.set(n,(c.get(n)??0)+jobs);}}}
            else{const d=data as HCResult;for(const j of d.results??[]){const n=(j.v5_processed_company_data?.name??j.source??'').trim();if(n.length>1)c.set(n,(c.get(n)??0)+1);}}
          }
          log.info(`hiring.cafe/stealth-pw ${mode}(${sortBy||'co'}): ${c.size} unique so far`);
        }catch(e){log.warning(`stealth-pw page ${url}: ${e}`);}
        await ctx.close();
        await sleep(1500);
      }
    }finally{await browser.close();}
  }catch(e){log.warning(`hiring.cafe/stealth-playwright: ${e}`);}
  return c.size>=20?c:null;
}

/** Strategy 2c: Crawlee PlaywrightCrawler — loads hiring.cafe to establish a real browser session.
 *  Uses page.on('response') passive streaming to capture ALL API responses as they arrive (including
 *  the initial page-load batch), then uses page.evaluate() for additional paginated passes.
 *  Three sort passes: date + applications + views — navigates to each sort URL for broadest coverage. */
async function fetchViaPlaywright(proxyUrl?: string): Promise<Map<string,number>|null> {
  const c=new Map<string,number>();
  try {
    const {PlaywrightCrawler}=await import('crawlee');
    // Use proxy if available for better Cloudflare bypass
    let proxyConfiguration;
    if(proxyUrl){
      try{
        const {ProxyConfiguration}=await import('crawlee');
        proxyConfiguration=new ProxyConfiguration({proxyUrls:[proxyUrl]});
      }catch{/* crawlee proxy setup failed, proceed without */}
    }
    await new PlaywrightCrawler({maxRequestsPerCrawl:4,headless:true,requestHandlerTimeoutSecs:240,
      ...(proxyConfiguration?{proxyConfiguration}:{}),
      launchContext:{launchOptions:{args:['--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage']}},
      async requestHandler({page,request}){
        type HCResult={results?:Array<{v5_processed_company_data?:{name?:string};source?:string}>};
        // Passive response streaming — captures ALL /api/search-jobs AND /api/search-companies responses
        page.on('response', async(resp)=>{
          const u=resp.url();
          const isCo=u.includes('/api/search-companies');
          const isJobs=u.includes('/api/search-jobs');
          if(!isJobs&&!isCo)return;
          try{
            const body=await resp.text();
            if(body.trimStart().startsWith('{')){
              const d=JSON.parse(body) as HCResult;
              for(const j of d.results??[]){
                let n:string;
                if(isCo){
                  // search-companies shape: {name?: string, company_name?: string, totalActiveListings?: number}
                  const co=j as unknown as HCCompany;
                  n=(co.name??co.company_name??'').trim();
                }else{
                  // search-jobs shape: {v5_processed_company_data?: {name?:string}, source?: string}
                  n=(j.v5_processed_company_data?.name??j.source??'').trim();
                }
                if(n.length>1)c.set(n,(c.get(n)??0)+1);
              }
            }
          }catch{/* ignore */}
        });
        // Navigate to sort-specific URL to trigger API calls for that sort order automatically
        const sortUrl=request.userData?.['sortUrl'] as string|undefined??'https://hiring.cafe';
        await page.goto(sortUrl,{waitUntil:'domcontentloaded',timeout:60_000});
        await page.waitForTimeout(4000); // let in-flight API calls land
        // Issue additional paginated API calls from within the browser context (cookies + auth intact)
        const isCompaniesPage = request.userData?.['mode'] === 'companies';
        type HCCoResult={results?:Array<{name?:string;company_name?:string;totalActiveListings?:number;activeListings?:number;jobCount?:number}>};
        const pages:HCResult[]=await page.evaluate(async(params:{sortBy:string;isCompaniesPage:boolean})=>{
          const out:HCResult[]=[];
          if(params.isCompaniesPage){
            // Companies page: paginate search-companies endpoint
            for(let p=0;p<20;p++){
              try{
                const r=await fetch('/api/search-companies',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({searchQuery:'',filters:[],page:p,pageSize:50})});
                if(!r.ok)break;
                const d=await r.json();
                out.push(d as HCResult);
                if(!(d as HCResult).results?.length)break;
                await new Promise(res=>setTimeout(res,300));
              }catch{break;}
            }
          }else{
            // Jobs page: paginate search-jobs endpoint
            for(let p=0;p<20;p++){
              try{
                const r=await fetch('/api/search-jobs',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({searchQuery:'',filters:[],page:p,pageSize:80,sortBy:params.sortBy})});
                if(!r.ok)break;
                const d=await r.json() as HCResult;
                out.push(d);
                if(!d.results?.length)break;
                await new Promise(res=>setTimeout(res,300));
              }catch{break;}
            }
          }
          return out;
        }, {sortBy:request.userData?.['sortBy'] as string ?? 'date', isCompaniesPage});
        if(isCompaniesPage){
          // search-companies results: {name:...} or {company_name:...}
          for(const page of pages)for(const j of (page as unknown as HCCoResult).results??[]){
            const n=(j.name??j.company_name??'').trim();
            if(n.length>1){const jobs=Math.max(1,j.totalActiveListings??j.activeListings??j.jobCount??3);c.set(n,(c.get(n)??0)+jobs);}
          }
        }else{
          for(const page of pages)for(const j of page.results??[]){
            const n=(j.v5_processed_company_data?.name??j.source??'').trim();
            if(n.length>1)c.set(n,(c.get(n)??0)+1);
          }
        }
        log.info(`hiring.cafe/pw ${isCompaniesPage?'companies':request.userData?.['sortBy']??'date'}: streamed ${c.size} unique so far`);
      },
    }).run([
      // Three sort passes on jobs + one companies page
      {url:'https://hiring.cafe',userData:{sortBy:'date',sortUrl:'https://hiring.cafe',mode:'jobs'}},
      {url:'https://hiring.cafe/?sort=applications',userData:{sortBy:'applications',sortUrl:'https://hiring.cafe/?sort=applications',mode:'jobs'}},
      {url:'https://hiring.cafe/?sort=views',userData:{sortBy:'views',sortUrl:'https://hiring.cafe/?sort=views',mode:'jobs'}},
      {url:'https://hiring.cafe/companies',userData:{mode:'companies',sortUrl:'https://hiring.cafe/companies'}},
    ]);
    log.info(`hiring.cafe/crawlee-stream+eval: ${c.size} unique`);
  }catch(e){log.warning(`hiring.cafe/crawlee: ${e}`);}
  return c.size>=20?c:null;
}

/** Extract a company name from a hiring.cafe company profile URL. */
function extractHCCompanyName(urlStr: string): string|null {
  try{
    const u=new URL(urlStr);
    const parts=u.pathname.split('/').filter(Boolean);
    // /companies/{slug} or /company/{slug}
    if(!['companies','company'].includes(parts[0]?.toLowerCase()||''))return null;
    const seg=parts[1];
    if(!seg||seg.length<2||['search','api','jobs','login','signup','about','blog','all'].includes(seg.toLowerCase()))return null;
    if(/^\d+$/.test(seg))return null; // skip pure numeric IDs
    const cleanSeg=decodeURIComponent(seg).replace(/-\d+$/, ''); // strip trailing numeric IDs
    const name=cleanSeg.replace(/-/g,' ').replace(/\b\w/g,ch=>ch.toUpperCase());
    return name.length>1?name:null;
  }catch{return null;}
}

/** Strategy 4b: Fetch archived hiring.cafe _next/data viewjob JSON files from Wayback Machine.
 *  These Next.js static data files contain full job data including v5_processed_company_data.name.
 *  Completely bypasses Cloudflare — pure CDX + Wayback Machine fetch, no live API calls needed.
 *  Typically yields ~200+ unique archived job pages from the most recent crawl batch. */
async function fetchViaNextData(maxFiles=300): Promise<Map<string,number>> {
  const c=new Map<string,number>();
  try {
    const cdxUrl=`${CDX}?url=hiring.cafe/_next/data/*&output=json&fl=original,timestamp&filter=statuscode:200&collapse=urlkey&limit=${maxFiles}`;
    const cdxResp=await gotScraping({url:cdxUrl,timeout:{request:30_000}});
    if(cdxResp.statusCode!==200)return c;
    const rows=(JSON.parse(cdxResp.body)as string[][]).slice(1).filter(r=>r[0].includes('/viewjob/'));
    log.info(`hiring.cafe/next-data: ${rows.length} archived viewjob JSON files in CDX`);
    let fetched=0;
    for(const [fileUrl,ts] of rows){
      try{
        const wbUrl=`${WB}/${ts}id_/${fileUrl}`;
        const resp=await gotScraping({url:wbUrl,timeout:{request:20_000},headers:{Accept:'application/json','Accept-Encoding':'gzip'}});
        if(resp.statusCode!==200)continue;
        // Response may be gzip-encoded even when headers don't say so
        let text=resp.body;
        if(text.charCodeAt(0)===0x1f&&text.charCodeAt(1)===0x8b){
          // gzip magic bytes detected — use Buffer approach
          try{const buf=Buffer.from(resp.rawBody??resp.body);const{gunzipSync}=await import('zlib');text=gunzipSync(buf).toString('utf-8');}catch{continue;}
        }
        if(!text.trimStart().startsWith('{'))continue;
        type HCNextData={pageProps?:{job?:{v5_processed_company_data?:{name?:string};source?:string}}};
        const d=JSON.parse(text)as HCNextData;
        const name=(d.pageProps?.job?.v5_processed_company_data?.name??'').trim();
        if(name.length>1){c.set(name,(c.get(name)??0)+1);fetched++;}
        await sleep(600);
      }catch{/* skip individual file errors */}
    }
    if(c.size>0)log.info(`hiring.cafe/next-data: ${c.size} unique companies from ${fetched} archived job pages`);
  }catch(e){log.warning(`hiring.cafe/next-data: ${e}`);}
  return c;
}

/** Strategy 4a: CDX company URL enumeration — extract company names directly from
 *  archived hiring.cafe company profile URLs (no API call needed, very reliable).
 *  Runs both CDX patterns in parallel for efficiency. */
async function fetchViaCompanyUrls(): Promise<Map<string,number>> {
  const cdxPatterns=[
    `${CDX}?url=hiring.cafe/companies/*&output=json&fl=original&filter=statuscode:200&collapse=urlkey&limit=8000`,
    `${CDX}?url=hiring.cafe/company/*&output=json&fl=original&filter=statuscode:200&collapse=urlkey&limit=3000`,
  ];
  const results=await Promise.all(cdxPatterns.map(async cdxUrl=>{
    const m=new Map<string,number>();
    try{
      const r=await gotScraping({url:cdxUrl,timeout:{request:45_000}});
      if(r.statusCode!==200)return m;
      const rows=(JSON.parse(r.body)as string[][]).slice(1);
      for(const row of rows){const n=extractHCCompanyName(row[0]);if(n)m.set(n,(m.get(n)??0)+1);}
    }catch{/* skip */}
    return m;
  }));
  const c=new Map(results[0]);
  for(const[k,v]of results[1])c.set(k,(c.get(k)??0)+v);
  if(c.size>0)log.info(`hiring.cafe/company-urls: ${c.size} companies from CDX URL enumeration`);
  return c;
}

async function fetchViaWayback(maxSnaps=30): Promise<Map<string,number>> {
  const c=new Map<string,number>();
  // Strategy 4a: company URL enumeration (CDX profile pages)
  const urlCounts=await fetchViaCompanyUrls();
  for(const[k,v]of urlCounts)c.set(k,v);
  // Strategy 4b: archived _next/data viewjob JSON files (gzip JSON with full company data)
  const nextDataCounts=await fetchViaNextData(300);
  for(const[k,v]of nextDataCounts)c.set(k,(c.get(k)??0)+v);
  try {
    // Look for snapshots of both the search-jobs endpoint and the search-companies endpoint
    const cdxUrls=[
      `${CDX}?url=hiring.cafe/api/search-jobs&output=json&fl=timestamp,length&filter=statuscode:200&limit=500`,
      `${CDX}?url=hiring.cafe/api/search-companies&output=json&fl=timestamp,length&filter=statuscode:200&limit=200`,
    ];
    const allSnaps:{ts:string;sz:number;endpoint:string}[]=[];
    for(const cdxUrl of cdxUrls){
      try{
        const r=await gotScraping({url:cdxUrl,timeout:{request:30_000}});
        if(r.statusCode!==200)continue;
        const endpoint=cdxUrl.includes('search-companies')?'search-companies':'search-jobs';
        const minSize=endpoint==='search-companies'?1*1024:5*1024;
        const rows=(JSON.parse(r.body)as string[][]).slice(1).map(row=>({ts:row[0],sz:parseInt(row[1],10),endpoint})).filter(s=>s.sz>=minSize);
        allSnaps.push(...rows);
      }catch{/* continue */}
    }
    allSnaps.sort((a,b)=>b.ts.localeCompare(a.ts));
    const n=Math.min(Math.ceil(maxSnaps/2),allSnaps.length); const older=allSnaps.slice(n); const step=Math.max(1,Math.floor(older.length/(maxSnaps-n)));
    const sel=[...allSnaps.slice(0,n),...older.filter((_,i)=>i%step===0).slice(0,maxSnaps-n)];
    log.info(`hiring.cafe/wayback: ${sel.length} snapshots (jobs+companies)`);
    for(const s of sel){
      try{
        const apiPath=s.endpoint==='search-companies'?'https://hiring.cafe/api/search-companies':'https://hiring.cafe/api/search-jobs';
        const resp=await gotScraping({url:`${WB}/${s.ts}id_/${apiPath}`,headers:{Accept:'application/json'},timeout:{request:30_000},followRedirect:true});
        if(resp.statusCode===200&&resp.body.trimStart().startsWith('{')){
          const data=JSON.parse(resp.body);
          const results=(data?.results??data)??[];
          let added=0;
          if(s.endpoint==='search-companies'){
            // search-companies: [{name: "...", totalActiveListings: N, ...}]
            type HCCo={name?:string;company_name?:string;totalActiveListings?:number;activeListings?:number;jobCount?:number};
            for(const co of results as HCCo[]){const nm=(co.name??co.company_name??'').trim();if(nm.length>1){const jobs=Math.max(1,co.totalActiveListings??co.activeListings??co.jobCount??1);c.set(nm,(c.get(nm)??0)+jobs);added++;}}
          }else{
            // search-jobs: [{v5_processed_company_data:{name:...}, source:...}]
            for(const j of results as HCJob[]){const nm=extractName(j);if(nm.length>1){c.set(nm,(c.get(nm)??0)+1);added++;}}
          }
          log.info(`wayback:${s.ts}(${s.endpoint})→${added}co ${c.size}u`); await sleep(1000);
        }else await sleep(400);
      }catch{await sleep(400);}
    }
  }catch(e){log.warning(`hiring.cafe/wayback CDX: ${e}`);}
  return c;
}

export async function discoverFromHiringCafe(maxSnapshots=50): Promise<CompanyDiscovery[]> {
  let counts:Map<string,number>|null=null; let strategy='wayback-cdx';
  let proxyUrl:string|undefined;
  try { const proxy=await Actor.createProxyConfiguration({groups:['RESIDENTIAL'],countryCode:'US'}); if(proxy){proxyUrl=await proxy.newUrl();if(proxyUrl){counts=await fetchLive(proxyUrl);if((counts?.size??0)>=50)strategy='live-proxy';else counts=null;}}} catch(e){log.info(`proxy n/a: ${e}`);}
  if(!counts){counts=await fetchViaStealthPlaywright(proxyUrl);if(counts)strategy='stealth-playwright';}
  if(!counts){counts=await fetchViaPlaywright(proxyUrl);if(counts)strategy='playwright';}
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
