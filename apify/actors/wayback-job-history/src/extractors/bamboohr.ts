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
  const data = await fetchArchivedJson<BJob[]>(ts, `https://${slug}.bamboohr.com/careers/list`);
  if (!Array.isArray(data)||!data.length) return { jobs:[], method:'bamboohr-api' };
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
  const data=await fetchArchivedJson<{searchResults?:{id?:string|number;jobtitle?:string;title?:string;category?:string;location?:string|{city?:string;state?:string;country?:string};employmenttype?:string}[]}>( ts,`https://${url.hostname}/jobs/search?ss=1&in_iframe=1`);
  const raw=data?.searchResults??[];
  if (!raw.length) return {jobs:[],method:'icims-api'};
  const jobs:JobPosting[]=raw.map(j=>({title:j.jobtitle??j.title??'',location:typeof j.location==='object'&&j.location?[j.location.city,j.location.state,j.location.country].filter(Boolean).join(', ')||undefined:j.location as string|undefined,department:j.category,id:j.id?String(j.id):undefined,url:j.id?`https://${url.hostname}/jobs/${j.id}/job`:undefined,employmentType:j.employmenttype})).filter(j=>j.title.length>0);
  log.info(`iCIMS: ${jobs.length} jobs`); return {jobs,method:'icims-api'};
}
