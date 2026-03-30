import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface BJob { id:string|number; jobOpeningName?:string; title?:string; employmentType?:string; department?:{name?:string}|string; location?:{city?:string;state?:string;country?:string;remote?:boolean}; city?:string; state?:string; country?:string; isRemote?:boolean }

export function extractBambooHRSlug(url: URL): string | null {
  if (!url.hostname.endsWith('.bamboohr.com')) return null;
  const s = url.hostname.replace('.bamboohr.com',''); return s&&s!=='www'?s:null;
}
export async function extractFromBambooHR(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractBambooHRSlug(url);
  if (!slug) return { jobs:[], method:'bamboohr-api' };
  // Try multiple BambooHR endpoints (API changed across versions)
  let rawData: BJob[] | null = null;
  for (const apiUrl of [
    `https://${slug}.bamboohr.com/careers/list`,
    `https://${slug}.bamboohr.com/api/gateway.php/${slug}/v1/applicant_tracking/jobs`,
    `https://${slug}.bamboohr.com/jobs/list.php`,
  ]) {
    const d = await fetchArchivedJson<BJob[] | { jobs?: BJob[]; data?: BJob[] }>(ts, apiUrl);
    if (!d) continue;
    rawData = Array.isArray(d) ? d : (d as { jobs?: BJob[]; data?: BJob[] }).jobs ?? (d as { jobs?: BJob[]; data?: BJob[] }).data ?? [];
    if (rawData.length > 0) break;
  }
  if (!rawData?.length) return { jobs:[], method:'bamboohr-api' };
  const data = rawData;
  const jobs: JobPosting[] = data.map(j => {
    const title=j.jobOpeningName??j.title??'';
    let location: string|undefined;
    if (j.location) location=(j.isRemote??j.location.remote)?'Remote':[j.location.city,j.location.state,j.location.country].filter(Boolean).join(', ')||undefined;
    else if (j.city||j.state||j.country) location=[j.city,j.state,j.country].filter(Boolean).join(', ');
    const dept=typeof j.department==='object'?j.department?.name:typeof j.department==='string'?j.department:undefined;
    const id=String(j.id);
    return {title,location,department:dept,url:`https://${slug}.bamboohr.com/careers/${id}`,id,employmentType:j.employmentType};
  }).filter(j=>j.title.length>0);
  log.info(`BambooHR: ${jobs.length} jobs`); return {jobs,method:'bamboohr-api'};
}

export function extractICIMSSlug(url: URL): string | null {
  if (!url.hostname.endsWith('.icims.com')) return null;
  const s=url.hostname.replace('.icims.com','').toLowerCase().replace(/^(careers?[-_.]|jobs[-_.]|apply[-_.])/,'');
  return (!s||s.length<2||['www','api','app','jobs','login','support','portal','sso'].includes(s))?null:s;
}
export async function extractFromICIMS(url: URL, ts: string): Promise<ExtractionResult> {
  if (!extractICIMSSlug(url)) return {jobs:[],method:'icims-api'};
  type IJob={id?:string|number;jobtitle?:string;title?:string;category?:string;location?:string|{city?:string;state?:string;country?:string};employmenttype?:string};
  type IResp={searchResults?:IJob[];totalCount?:number};
  // iCIMS paginates with startrow — fetch up to 5 pages (500 jobs)
  const allRaw:IJob[]=[];
  for(let page=0;page<5;page++){
    const startrow=page*100;
    const data=await fetchArchivedJson<IResp>(ts,`https://${url.hostname}/jobs/search?ss=1&in_iframe=1&sortby=postdate&ipp=100&startrow=${startrow}`);
    const rows=data?.searchResults??[];
    if(!rows.length)break;
    allRaw.push(...rows);
    if(rows.length<100)break; // last page
  }
  if(!allRaw.length){
    // fallback: try default endpoint without pagination params
    const data=await fetchArchivedJson<IResp>(ts,`https://${url.hostname}/jobs/search?ss=1&in_iframe=1`);
    allRaw.push(...(data?.searchResults??[]));
  }
  if(!allRaw.length) return {jobs:[],method:'icims-api'};
  const jobs:JobPosting[]=allRaw.map(j=>({title:j.jobtitle??j.title??'',location:typeof j.location==='object'&&j.location?[j.location.city,j.location.state,j.location.country].filter(Boolean).join(', ')||undefined:j.location as string|undefined,department:j.category,id:j.id?String(j.id):undefined,url:j.id?`https://${url.hostname}/jobs/${j.id}/job`:undefined,employmentType:j.employmenttype})).filter(j=>j.title.length>0);
  log.info(`iCIMS: ${jobs.length} jobs`); return {jobs,method:'icims-api'};
}
