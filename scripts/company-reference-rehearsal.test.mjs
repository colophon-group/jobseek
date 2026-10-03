import { test } from 'node:test';
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, chmodSync, existsSync, rmSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
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

test('protected proof IO precedes the final owner/main authorization',()=>{
 const apply=readFileSync('.github/workflows/apply-web-routine-migration.yml','utf8').split('  apply:')[1];
 const steps=[...apply.matchAll(/^      - ([^\n]+)/gm)].map(match=>match[1]);
 const authorization=steps.indexOf('name: Reauthorize and refuse stale main immediately before DDL');
 assert.equal(steps[authorization+1],'name: Apply exactly one reviewed migration under the ledger lock');
 assert.ok(steps.indexOf('name: Require the exact protected archive rehearsal before company reference DDL')<authorization);
});

for (const rejected of [null,'actor','path','event','manifest']) test(`actual CLI ${rejected ?? 'accepts'} before evidence download`,()=>{
 const directory=mkdtempSync(join(tmpdir(),'jobseek-protected-proof-'));
 try {
  const f=fixture();
  if(rejected==='actor') f.run.actor.login='other';
  if(rejected==='path') f.run.path='other.yml';
  if(rejected==='event') f.run.event='pull_request';
  const bundle=join(directory,'deploy/backups/web-postgresql/company-reference-bundle');
  mkdirSync(bundle,{recursive:true});
  const manifest=JSON.stringify({sourceClean:true,sourceRevision:expected.revision,runtimeImage:expected.runtimeImage,migrations:[target]});
  writeFileSync(join(bundle,'manifest.json'),manifest);
  const hash=createHash('sha256').update(manifest).digest('hex');
  f.proof.manifestSha256=hash;
  const gh=join(directory,'gh');
  writeFileSync(gh,`#!/usr/bin/env node
const fs=require('node:fs');const path=require('node:path');const args=process.argv.slice(2);const f=JSON.parse(process.env.FIXTURE_PROOF);
if(args[0]==='api'){
 const endpoint=args[1];
 if(endpoint.endsWith('/git/ref/heads/main')) console.log(JSON.stringify({object:{sha:process.env.ROUTINE_MIGRATION_REVISION}}));
 else if(endpoint.includes('/artifacts?')) console.log(JSON.stringify({artifacts:[f.artifact]}));
 else console.log(JSON.stringify(f.run));
}else if(args[0]==='run' && args[1]==='download'){
 fs.writeFileSync(process.env.DOWNLOAD_MARKER,'downloaded');
 fs.writeFileSync(path.join(args[args.indexOf('--dir')+1],'proof.json'),JSON.stringify(f.proof));
}else process.exit(2);
`);
  chmodSync(gh,0o755);
  const marker=join(directory,'download-marker');
  const result=spawnSync('node',[resolve('.github/scripts/verify-company-reference-rehearsal.mjs')],{cwd:directory,encoding:'utf8',env:{...process.env,
   PATH:directory+':'+process.env.PATH,GH_TOKEN:'fixture',GITHUB_TOKEN:'fixture',GITHUB_REPOSITORY:'fixture/jobseek',REHEARSAL_RUN_ID:'1234',
   ROUTINE_MIGRATION_REVISION:expected.revision,ROUTINE_MIGRATION_TAG:target.tag,ROUTINE_MIGRATION_CREATED_AT:String(target.createdAt),
   ROUTINE_MIGRATION_HASH:target.hash,REHEARSAL_EXPECTED_MANIFEST_SHA256:rejected==='manifest'?'f'.repeat(64):hash,
   FIXTURE_PROOF:JSON.stringify(f),DOWNLOAD_MARKER:marker}});
  if(rejected){assert.equal(result.status,1);assert.equal(existsSync(marker),false);}
  else {assert.equal(result.status,0,result.stderr);assert.equal(existsSync(marker),true);}
 } finally {rmSync(directory,{recursive:true,force:true});}
});
