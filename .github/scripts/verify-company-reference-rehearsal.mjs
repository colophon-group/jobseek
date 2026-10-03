#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, lstatSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

function requireProof(condition) { if (!condition) throw new Error('Protected company reference rehearsal evidence differs'); }
export function validateRunIdentity(run, artifact, expected) {
  requireProof(run.status === 'completed' && run.conclusion === 'success' && run.event === 'workflow_dispatch'
    && run.head_branch === 'main' && run.head_sha === expected.revision
    && run.path === '.github/workflows/operate-web-postgresql-backup.yml'
    && Number.isFinite(Date.parse(run.updated_at)) && Date.now() - Date.parse(run.updated_at) >= 0
    && Date.now() - Date.parse(run.updated_at) <= 9 * 60 * 60 * 1000
    && run.actor?.login === 'viktor-shcherb' && run.triggering_actor?.login === 'viktor-shcherb');
  requireProof(artifact.name === `company-reference-rehearsal-${expected.runId}-${run.run_attempt}`
    && artifact.expired === false && artifact.size_in_bytes > 0 && artifact.size_in_bytes <= 131072);
}
export function validateEvidence(run, artifact, proof, expected) {
  validateRunIdentity(run, artifact, expected);
  requireProof(Object.keys(proof).sort().join(',') === ['runId','runAttempt','cleanup','packetVersion','contract','outcome','sourceRevision','manifestSha256','runtimeImage','target','archiveSha256','preflight','postflight','preserved','referenceRows','dependencies'].sort().join(','));
  requireProof(proof.runId === expected.runId && proof.runAttempt === run.run_attempt && proof.cleanup === 'passed'
    && proof.packetVersion === 3 && proof.contract === 'company_reference_archive_rehearsal'
    && proof.outcome === 'passed' && proof.sourceRevision === expected.revision
    && proof.manifestSha256 === expected.manifestSha256 && proof.runtimeImage === expected.runtimeImage
    && proof.target?.tag === expected.target.tag && proof.target?.createdAt === expected.target.createdAt && proof.target?.hash === expected.target.hash
    && /^[a-f0-9]{64}$/.test(proof.archiveSha256 ?? '')
    && proof.preflight === 'passed' && proof.postflight === 'passed');
  const retained = ['user','session','account','verification','user_preferences','industry','company','company_description','job_board',
    'saved_job','application_interview','followed_company','company_request','hiring_signal','outreach_draft','watchlist','watchlist_company'];
  if (expected.target.tag === '0101_company_reference_selection_contract') retained.push('company_reference');
  requireProof(JSON.stringify(Object.keys(proof.preserved ?? {}).sort()) === JSON.stringify(retained.sort()));
  for (const value of Object.values(proof.preserved)) requireProof(Object.keys(value).sort().join(',') === 'digest,rows'
    && Number.isSafeInteger(value.rows) && value.rows >= 0 && /^[a-f0-9]{64}$/.test(value.digest));
  requireProof(Number.isSafeInteger(proof.referenceRows) && proof.referenceRows >= 0
    && proof.dependencies?.foreignKeyCount === 6 && proof.dependencies?.snapshot === 'independent'
    && proof.dependencies?.phase === (expected.target.tag === '0100_company_references' ? 'bridge' : 'reference')
    && proof.referenceRows === proof.preserved[expected.target.tag === '0100_company_references' ? 'company' : 'company_reference'].rows);
}
function api(path) { return JSON.parse(execFileSync('gh', ['api', path], { encoding:'utf8', maxBuffer:1048576 })); }
export function main() {
  const { GITHUB_REPOSITORY: repository, REHEARSAL_RUN_ID: runId, ROUTINE_MIGRATION_REVISION: revision,
    ROUTINE_MIGRATION_TAG: tag, ROUTINE_MIGRATION_HASH: hash, ROUTINE_MIGRATION_CREATED_AT: createdAt } = process.env;
  requireProof(/^[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+$/.test(repository ?? '') && /^[1-9][0-9]*$/.test(runId ?? '')
    && /^[a-f0-9]{40}$/.test(revision ?? '') && /^[a-f0-9]{64}$/.test(hash ?? '') && /^[0-9]+$/.test(createdAt ?? ''));
  const manifestBytes = readFileSync('deploy/backups/web-postgresql/company-reference-bundle/manifest.json');
  const manifest = JSON.parse(manifestBytes);
  const manifestSha256 = process.env.REHEARSAL_EXPECTED_MANIFEST_SHA256;
  requireProof(manifest.sourceClean === true && manifest.sourceRevision === revision && /^[a-f0-9]{64}$/.test(manifestSha256 ?? '')
    && createHash('sha256').update(manifestBytes).digest('hex') === manifestSha256);
  const target = manifest.migrations.find(row => row.tag === tag);
  requireProof(target && target.hash === hash && String(target.createdAt) === createdAt);
  requireProof(api(`repos/${repository}/git/ref/heads/main`).object.sha === revision);
  const run = api(`repos/${repository}/actions/runs/${runId}`);
  const name = `company-reference-rehearsal-${runId}-${run.run_attempt}`;
  const artifacts = api(`repos/${repository}/actions/runs/${runId}/artifacts?per_page=100`).artifacts.filter(row => row.name === name);
  requireProof(artifacts.length === 1);
  const expected = { runId,revision,manifestSha256,runtimeImage:manifest.runtimeImage,target };
  // Reject untrusted/stale run metadata before downloading any archive.
  validateRunIdentity(run, artifacts[0], expected);
  const root = mkdtempSync(join(tmpdir(),'jobseek-rehearsal-evidence-'));
  try {
    execFileSync('gh',['run','download',runId,'--repo',repository,'--name',name,'--dir',root],{stdio:'pipe'});
    requireProof(readdirSync(root).join(',') === 'proof.json');
    const file = join(root,'proof.json'); const metadata=lstatSync(file);
    requireProof(metadata.isFile() && !metadata.isSymbolicLink() && metadata.size <= 65536);
    validateEvidence(run,artifacts[0],JSON.parse(readFileSync(file,'utf8')),expected);
    console.log('Protected company reference archive rehearsal verified');
  } finally { rmSync(root,{recursive:true,force:true}); }
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try { main(); } catch { console.error('Protected company reference rehearsal verification failed'); process.exitCode=1; }
}
