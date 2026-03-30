import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';
interface JJob { id:string; title:string; city?:string; state?:string; country?:string; employment_type?:string; department?:string }
export function extractJazzHRSlug(url: URL): string | null {
  if (!url.hostname.endsWith('.applytojob.com')) return null;
  const s = url.hostname.replace('.applytojob.com',''); return s&&s!=='www'?s:null;
}
export async function extractFromJazzHR(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractJazzHRSlug(url); if (!slug) return {jobs:[],method:'jazzhr-api'};
  const data = await fetchArchivedJson<{jobs?:JJob[]}|JJob[]>(ts,`https://${slug}.applytojob.com/apply?format=json`);
  const raw: JJob[] = Array.isArray(data)?data:(data as {jobs?:JJob[]})?.jobs??[];
  if (!raw.length) return {jobs:[],method:'jazzhr-api'};
  const jobs: JobPosting[] = raw.map(j=>({title:j.title,location:[j.city,j.state,j.country].filter(Boolean).join(', ')||undefined,department:j.department,url:`https://${slug}.applytojob.com/apply/${j.id}`,id:j.id,employmentType:j.employment_type})).filter(j=>j.title.length>0);
  log.info(`JazzHR: ${jobs.length} jobs`); return {jobs,method:'jazzhr-api'};
}
interface TaleoJob { requisitionId?:string; jobTitle?:string; title?:string; location?:string; jobFamily?:string; contractType?:string }
export function extractTaleoSlug(url: URL): string | null {
  if (!url.hostname.endsWith('.taleo.net')) return null;
  const s = url.hostname.replace('.taleo.net','').toLowerCase();
  return (!s||s.length<2||['www','api','app','preview','cdn','mail','secure'].includes(s))?null:s;
}
export async function extractFromTaleo(url: URL, ts: string): Promise<ExtractionResult> {
  if (!extractTaleoSlug(url)) return {jobs:[],method:'taleo-api'};
  type TResp={requisitions?:TaleoJob[];results?:TaleoJob[];totalCount?:number};
  const allRaw:TaleoJob[]=[];
  for(let page=0;page<5;page++){
    const pageParam=page===0?'':`&startrow=${page*100}`;
    const data=await fetchArchivedJson<TResp>(ts,`https://${url.hostname}/careersection/rest/jobboard/requisitionList?lang=en&rows=100${pageParam}`);
    const rows=data?.requisitions??data?.results??[];
    if(!rows.length)break;
    allRaw.push(...rows);
    if(rows.length<100)break;
  }
  if(!allRaw.length) return {jobs:[],method:'taleo-api'};
  const jobs: JobPosting[] = allRaw.map(j=>({title:j.jobTitle??j.title??'',location:j.location,department:j.jobFamily,id:j.requisitionId,url:j.requisitionId?`https://${url.hostname}/careersection/2/jobdetail.ftl?job=${j.requisitionId}`:undefined,employmentType:j.contractType})).filter(j=>j.title.length>0);
  log.info(`Taleo: ${jobs.length} jobs`); return {jobs,method:'taleo-api'};
}
interface JVJob { id?:string; title?:string; jobTitle?:string; location?:string; department?:string; jobType?:string }
export function extractJobviteSlug(url: URL): string | null {
  if (!url.hostname.endsWith('.jobvite.com')) return null;
  const s = url.hostname.replace('.jobvite.com','').toLowerCase();
  return (!s||s.length<2||['www','api','app','jobs','login','support','careers','hire','web'].includes(s))?null:s;
}
export async function extractFromJobvite(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractJobviteSlug(url); if (!slug) return {jobs:[],method:'jobvite-api'};
  let raw: JVJob[] = [];
  for (const apiUrl of [
    `https://${slug}.jobvite.com/api/v4/job/list`,
    `https://${slug}.jobvite.com/api/v3/job/list`,
    `https://${slug}.jobvite.com/api/v1/job/list`,
  ]) {
    const data = await fetchArchivedJson<{jobs?:JVJob[]}|JVJob[]>(ts, apiUrl);
    raw = Array.isArray(data)?data:(data as {jobs?:JVJob[]})?.jobs??[];
    if (raw.length > 0) break;
  }
  if (!raw.length) return {jobs:[],method:'jobvite-api'};
  const jobs: JobPosting[] = raw.map(j=>({title:j.jobTitle??j.title??'',location:typeof j.location==='string'?j.location||undefined:undefined,department:j.department,id:j.id,url:j.id?`https://${slug}.jobvite.com/careers?c=${j.id}`:undefined,employmentType:j.jobType})).filter(j=>j.title.length>0);
  log.info(`Jobvite: ${jobs.length} jobs`); return {jobs,method:'jobvite-api'};
}
