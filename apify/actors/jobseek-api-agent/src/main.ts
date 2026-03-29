/**
 * @actor jobseek-api-agent
 *
 * Demonstrates the Job Seek Agentic API crypto paywall end-to-end:
 *
 *  1. Call /api/jobs with no token           → 401
 *  2. Call /api/jobs with unknown token      → 402 + payment instructions
 *  3. Send 0.001 ETH to API wallet (Sepolia) → tx confirmed
 *  4. POST /api/pay { txHash }               → 200, credit token issued
 *  5. Make `numCalls` calls to /api/ping     → all 200 ✓
 *  6. Call /api/ping one more time           → 402 Credit Exhausted
 *     (only if numCalls == 1000, otherwise credits remain)
 *
 * Secrets (set in Apify actor environment variables):
 *   AGENT_PRIVATE_KEY — Sepolia wallet private key (0x...)
 *
 * Input:
 *   numCalls    — number of credits to consume (default 100)
 *   apiBaseUrl  — base URL of the agentic API
 *   sepoliaRpcUrl — Sepolia JSON-RPC endpoint
 */

import { Actor } from 'apify';
import { ethers } from 'ethers';

interface Input {
  numCalls?: number;
  apiBaseUrl?: string;
  sepoliaRpcUrl?: string;
}

interface StepResult {
  step: number;
  label: string;
  status: 'ok' | 'error';
  httpStatus?: number;
  detail: string;
}

await Actor.init();

const input = (await Actor.getInput<Input>()) ?? {};
const numCalls    = input.numCalls    ?? 100;
const apiBaseUrl  = (input.apiBaseUrl  ?? 'https://jseek.co/agentic/api').replace(/\/$/, '');
const rpcUrl      = input.sepoliaRpcUrl ?? 'https://ethereum-sepolia-rpc.publicnode.com';

const privateKey = process.env.AGENT_PRIVATE_KEY;
if (!privateKey) {
  await Actor.fail('AGENT_PRIVATE_KEY env var is not set. Add it as an actor secret.');
}

const provider = new ethers.JsonRpcProvider(rpcUrl);
const wallet   = new ethers.Wallet(privateKey!, provider);

const results: StepResult[] = [];
const log = (step: number, label: string, msg: string) => {
  console.log(`[STEP ${step}] ${label}: ${msg}`);
};

async function callApi(path: string, token?: string): Promise<{ status: number; body: Record<string, unknown> }> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`${apiBaseUrl}${path}`, { headers });
  const body = await res.json().catch(() => ({})) as Record<string, unknown>;
  return { status: res.status, body };
}

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

// ── Step 1: No token → 401 ────────────────────────────────────────────────
log(1, 'No-token call', 'GET /api/jobs');
const s1 = await callApi('/jobs?q=typescript');
results.push({
  step: 1, label: 'No-token call → 401', status: s1.status === 401 ? 'ok' : 'error',
  httpStatus: s1.status, detail: JSON.stringify(s1.body),
});

// ── Step 2: Bad token → 402 + payment info ────────────────────────────────
log(2, 'Bad-token call', 'GET /api/jobs (expect 402 with payTo)');
const s2 = await callApi('/jobs?q=typescript', 'not-a-real-token');
const apiWallet = s2.body.payTo as string;
const priceWei  = BigInt((s2.body.priceWei as string) ?? '1000000000000000');
results.push({
  step: 2, label: 'Unknown-token → 402 with payment info', status: apiWallet ? 'ok' : 'error',
  httpStatus: s2.status, detail: `payTo=${apiWallet}, priceWei=${priceWei}`,
});

if (!apiWallet) {
  await Actor.fail('No payTo address returned in 402 response');
}

// ── Step 3: Send ETH ──────────────────────────────────────────────────────
const balance = await provider.getBalance(wallet.address);
log(3, 'Send ETH', `wallet=${wallet.address} balance=${ethers.formatEther(balance)} ETH`);

if (balance < priceWei) {
  await Actor.fail(`Insufficient balance: have ${ethers.formatEther(balance)} ETH, need ${ethers.formatEther(priceWei)} ETH`);
}

const tx = await wallet.sendTransaction({ to: apiWallet, value: priceWei });
log(3, 'Tx sent', tx.hash);
const receipt = await tx.wait(1);
log(3, 'Confirmed', `block ${receipt!.blockNumber}`);
results.push({
  step: 3, label: 'Send 0.001 ETH on Sepolia', status: receipt!.status === 1 ? 'ok' : 'error',
  detail: `txHash=${tx.hash}, block=${receipt!.blockNumber}`,
});

// ── Step 4: Submit txHash → credit token ─────────────────────────────────
log(4, 'POST /api/pay', tx.hash);
const payRes = await fetch(`${apiBaseUrl}/pay`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ txHash: tx.hash, walletAddress: wallet.address }),
});
const payBody = await payRes.json() as Record<string, unknown>;
const creditToken   = payBody.token as string;
const creditsGranted = payBody.creditsGranted as number;

results.push({
  step: 4, label: 'POST /api/pay → credit token', status: creditToken ? 'ok' : 'error',
  httpStatus: payRes.status, detail: `token=${creditToken}, creditsGranted=${creditsGranted}`,
});

if (!creditToken) {
  await Actor.fail('No token returned from /api/pay: ' + JSON.stringify(payBody));
}

// ── Step 5: Make numCalls calls ───────────────────────────────────────────
log(5, 'Burn credits', `${numCalls} calls via /api/ping`);
let successCount = 0;
for (let i = 1; i <= numCalls; i++) {
  let r: { status: number; body: Record<string, unknown> } | undefined;
  for (let attempt = 0; attempt < 3; attempt++) {
    r = await callApi('/ping', creditToken);
    if (r.status !== 500 && r.status !== 502 && r.status !== 503) break;
    await sleep(300);
  }
  if (r!.status !== 200) {
    results.push({
      step: 5, label: `Call ${i} failed`, status: 'error',
      httpStatus: r!.status, detail: JSON.stringify(r!.body),
    });
    await Actor.fail(`Unexpected ${r!.status} on call ${i}`);
  }
  successCount++;
  if (i % 10 === 0) log(5, 'Progress', `${i}/${numCalls} (${r!.body.creditsRemaining} credits left)`);
  await sleep(10);
}

results.push({
  step: 5, label: `${numCalls} paywall-gated calls`, status: 'ok',
  detail: `All ${successCount} calls returned 200`,
});

// ── Step 6: One more call — check if exhausted or still has credits ────────
log(6, 'Post-quota call', 'GET /api/ping');
const s6 = await callApi('/ping', creditToken);
const exhausted = s6.status === 402;
const stillActive = s6.status === 200;

results.push({
  step: 6,
  label: numCalls === 1000 ? 'Call 1001 → 402 exhausted' : `Call ${numCalls + 1} (credits remain if < 1000)`,
  status: exhausted || stillActive ? 'ok' : 'error',
  httpStatus: s6.status,
  detail: exhausted
    ? `Credit exhausted as expected: ${JSON.stringify(s6.body)}`
    : `Credits still remaining: ${s6.body.creditsRemaining}`,
});

// ── Push results to dataset ───────────────────────────────────────────────
await Actor.pushData({
  runAt: new Date().toISOString(),
  agentWallet: wallet.address,
  apiWallet,
  txHash: tx.hash,
  creditToken,
  creditsGranted,
  numCallsRequested: numCalls,
  numCallsSucceeded: successCount,
  finalCallStatus: s6.status,
  steps: results,
  summary: results.every(r => r.status === 'ok')
    ? `✓ All ${results.length} steps passed`
    : `✗ Some steps failed`,
});

console.log('\n=== SUMMARY ===');
results.forEach(r => console.log(`  Step ${r.step} [${r.status.toUpperCase()}] ${r.label}`));
console.log(`\nCalls succeeded: ${successCount}/${numCalls}  |  Final call HTTP ${s6.status}`);

await Actor.exit();
