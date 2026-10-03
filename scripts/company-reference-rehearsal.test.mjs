import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateEvidence } from '../.github/scripts/verify-company-reference-rehearsal.mjs';
const target={tag:'0100_company_references',createdAt:1790985600000,hash:'b'.repeat(64)};
const expected={runId:'1234',revision:'a'.repeat(40),manifestSha256:'c'.repeat(64),runtimeImage:'node:24-alpine@sha256:'+ 'd'.repeat(64),target};
function fixture(){
 const run={status:'completed',conclusion:'success',event:'workflow_dispatch',head_branch:'main',head_sha:expected.revision,
 path:'.github/workflows/operate-web-postgresql-backup.yml',run_attempt:1,updated_at:new Date().toISOString(),actor:{login:'viktor-shcherb'},triggering_actor:{login:'viktor-shcherb'}};
 const artifact={name:'company-reference-rehearsal-1234-1',expired:false,size_in_bytes:1000};
 const preserved=Object.fromEntries(['user','session','account','verification','user_preferences','industry','company','company_description','job_board','saved_job','application_interview','followed_company','company_request','hiring_signal','outreach_draft','watchlist','watchlist_company'].map(table=>[table,{rows:1,digest:'e'.repeat(64)}]));
 const proof={runId:'1234',runAttempt:1,cleanup:'passed',packetVersion:3,contract:'company_reference_archive_rehearsal',outcome:'passed',
 sourceRevision:expected.revision,manifestSha256:expected.manifestSha256,runtimeImage:expected.runtimeImage,target:{hash:target.hash,tag:target.tag,createdAt:target.createdAt},
 archiveSha256:'f'.repeat(64),preflight:'passed',postflight:'passed',preserved,referenceRows:1,dependencies:{phase:'bridge',foreignKeyCount:6,lifecycleOwners:4,optionalForeignKeyCount:0,snapshot:'independent'}};
 return {run,artifact,proof};
}
test('accepts only complete protected identity and preservation proof',()=>{const {run,artifact,proof}=fixture();validateEvidence(run,artifact,proof,expected)});
for(const [name,mutate] of Object.entries({
 'failed run':f=>f.run.conclusion='failure','unprotected path':f=>f.run.path='other.yml','foreign actor':f=>f.run.actor.login='other',
 'stale main':f=>f.run.head_sha='b'.repeat(40),'PR event':f=>f.run.event='pull_request','expired artifact':f=>f.artifact.expired=true,
 'wrong attempt':f=>f.proof.runAttempt=2,'oversized archive':f=>f.artifact.size_in_bytes=200000,'wrong archive':f=>f.proof.archiveSha256='unknown',
 'wrong target':f=>f.proof.target.hash='a'.repeat(64),'incomplete cleanup':f=>f.proof.cleanup='unknown','historic v2':f=>f.proof.packetVersion=2,
 'missing description':f=>delete f.proof.preserved.company_description,'changed runtime':f=>f.proof.runtimeImage='node:latest',
 'changed bundle':f=>f.proof.manifestSha256='a'.repeat(64),'negative row count':f=>f.proof.preserved.saved_job.rows=-1,
 'extra private field':f=>f.proof.rawRows=[],'incomplete FK coverage':f=>f.proof.dependencies.foreignKeyCount=5,
 'stale rehearsal':f=>f.run.updated_at=new Date(Date.now()-10*60*60*1000).toISOString()
})) test(`rejects ${name}`,()=>{const f=fixture();mutate(f);assert.throws(()=>validateEvidence(f.run,f.artifact,f.proof,expected))});
test('final rehearsal must preserve existing references too',()=>{const f=fixture();const final={...expected,target:{...target,tag:'0101_company_reference_selection_contract'}};f.proof.target=final.target;assert.throws(()=>validateEvidence(f.run,f.artifact,f.proof,final));f.proof.preserved.company_reference={rows:3,digest:'e'.repeat(64)};validateEvidence(f.run,f.artifact,f.proof,final)});
