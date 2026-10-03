import { createServer, type Server } from "node:http";
import { once } from "node:events";
import type { AddressInfo } from "node:net";
import { chromium, type Page } from "playwright";
import type { Sql } from "postgres";
import { expect, test } from "vitest";
import { navigateCanary, waitForCanaryOwnerShell, diagnoseCanaryOwnerReload, canaryBrowserError, type CanaryOwnerEvidence } from "./company-reference/canary-navigation";
import { signInCanaryAccount, requireCanaryCleanup, readCanaryReadinessState } from "./company-reference/canary-lifecycle";
import { selectCanaryCompany, canaryCompanyRow } from "./company-reference/canary-picker";
import { bootstrapDeploymentAccess } from "./company-reference/deployment-access";

async function listen(server: Server) {
  server.listen(0); await once(server, "listening");
  return (server.address() as AddressInfo).port;
}
async function close(server: Server) { await new Promise<void>(resolve => server.close(() => resolve())); }

test("secret is sent only to the bootstrap origin and host cookie stays there across API and browser redirects", async () => {
  const foreignRequests: { cookie?: string; bypass?: string }[] = [];
  const foreign = createServer((req, res) => {
    foreignRequests.push({ cookie: req.headers.cookie, bypass: req.headers["x-vercel-protection-bypass"] as string | undefined });
    res.end("foreign");
  });
  const foreignPort = await listen(foreign); const target = `http://localhost:${foreignPort}/`;
  let bootstrapCount = 0; let cookieRequests = 0;
  const first = createServer((req, res) => {
    if (req.headers["x-vercel-protection-bypass"]) {
      bootstrapCount++; expect(req.headers["x-vercel-protection-bypass"]).toBe("local-fixture-secret");
      expect(req.headers["x-vercel-set-bypass-cookie"]).toBe("true");
      res.writeHead(307, { location: "/", "set-cookie": "fixture_bypass=host-token; Path=/; HttpOnly; SameSite=Lax" }); res.end(); return;
    }
    expect(req.headers.cookie).toContain("fixture_bypass=host-token"); cookieRequests++;
    res.writeHead(307, { location: target }); res.end();
  });
  const firstPort = await listen(first); const base = new URL(`http://127.0.0.1:${firstPort}/`);
  const browser = await chromium.launch({ headless: true }); const context = await browser.newContext();
  try {
    await bootstrapDeploymentAccess(context, base, "local-fixture-secret");
    expect(foreignRequests).toHaveLength(0); // bootstrap redirect was not followed
    await context.request.get(base.origin);
    const page = await context.newPage(); await page.goto(base.origin);
    expect(bootstrapCount).toBe(1); expect(cookieRequests).toBe(2);
    expect(foreignRequests).toHaveLength(2);
    for (const request of foreignRequests) { expect(request.bypass).toBeUndefined(); expect(request.cookie).toBeUndefined(); }
  } finally { await context.close(); await browser.close(); await close(first); await close(foreign); }
});

test("bootstrap fails closed when a protection response returns no scoped cookie", async () => {
  const first = createServer((_, res) => res.end("unprotected")); const port = await listen(first);
  const browser = await chromium.launch({ headless: true }); const context = await browser.newContext();
  try { await expect(bootstrapDeploymentAccess(context, new URL(`http://127.0.0.1:${port}/`), "fixture")).rejects.toMatchObject({ code: "BYPASS_COOKIE_MISSING" }); }
  finally { await context.close(); await browser.close(); await close(first); }
});

test("bootstrap rejects a redirect to another origin without requesting it", async () => {
  let foreignRequests = 0;
  const foreign = createServer((_, res) => { foreignRequests++; res.end(); }); const foreignPort = await listen(foreign);
  const first = createServer((_, res) => {
    res.writeHead(307, { location: `http://localhost:${foreignPort}/`, "set-cookie": "fixture=token; Path=/; HttpOnly" }); res.end();
  }); const port = await listen(first);
  const browser = await chromium.launch({ headless: true }); const context = await browser.newContext();
  try {
    await expect(bootstrapDeploymentAccess(context, new URL(`http://127.0.0.1:${port}/`), "fixture")).rejects.toMatchObject({ code: "BYPASS_BOOTSTRAP_FOREIGN_REDIRECT" });
    expect(foreignRequests).toBe(0);
  } finally { await context.close(); await browser.close(); await close(first); await close(foreign); }
});

test("bootstrap rejects a cookie that cannot protect all application routes", async () => {
  const first = createServer((_, res) => {
    res.writeHead(307, { location: "/", "set-cookie": "fixture=token; Path=/narrow; HttpOnly" }); res.end();
  }); const port = await listen(first);
  const browser = await chromium.launch({ headless: true }); const context = await browser.newContext();
  try { await expect(bootstrapDeploymentAccess(context, new URL(`http://127.0.0.1:${port}/`), "fixture")).rejects.toMatchObject({ code: "BYPASS_COOKIE_SCOPE_INVALID" }); }
  finally { await context.close(); await browser.close(); await close(first); }
});

// This runs real Chromium in the already browser-enabled required contract job.
test.each([true, false])("exact picker row and idempotent scope work with initially broad scope=%s", async (broad) => {
  const browser = await chromium.launch({ headless: true }); const page = await browser.newPage();
  const name = "Fixture (Research)+";
  try {
    await page.setContent(`<button>${name}</button><button id="scope">Any company</button>
      <button id="company" ${broad ? "disabled" : ""}>Company</button>
      <div role="dialog" hidden><input placeholder="Search companies...">
      <button id="exact"><span>${name}</span><span>42 jobs</span></button>
      <button id="prefix"><span>${name} extended</span><span>9 jobs</span></button>
      <button id="close">Close</button></div>
      <script>
        document.querySelector('#scope').onclick=()=>{document.querySelector('#company').disabled=!document.querySelector('#company').disabled;};
        document.querySelector('#company').onclick=()=>{document.querySelector('[role=dialog]').hidden=false;};
        document.querySelector('#exact').onclick=()=>{document.body.dataset.selected='exact';};
        document.querySelector('#prefix').onclick=()=>{document.body.dataset.selected='wrong';};
        document.querySelector('#close').onclick=()=>{document.querySelector('[role=dialog]').hidden=true;};
      </script>`);
    const phases: string[] = [];
    await selectCanaryCompany(page, name, phase => phases.push(phase));
    expect(await page.locator('body').getAttribute('data-selected')).toBe('exact');
    expect(await page.locator('#company').isEnabled()).toBe(true);
    expect(phases).toEqual(['picker_scope_toggle','picker_modal_open','picker_search_input','picker_result_locate','picker_result_click','picker_modal_close']);
    await page.locator('#company').click();
    expect(await canaryCompanyRow(page, name).count()).toBe(1);
    const dialog = page.getByRole('dialog');
    expect(await dialog.getByRole('button').filter({has: dialog.getByText(name, {exact:true})}).count()).toBe(0);
  } finally { await browser.close(); }
});

test("sign-in waits for one visible complete form without filling hidden or transitioning duplicate forms", async () => {
  const browser = await chromium.launch({ headless: true }); const page = await browser.newPage();
  try {
    const form = (id: string) => `<form id="${id}" onsubmit="event.preventDefault(); document.body.dataset.submitted=this.id">
      <label>Email or username<input></label><label>Password<input type="password"></label><button>Sign in</button></form>`;
    await page.setContent(`${form('active')}${form('retiring')}`);
    const phases: string[] = [];
    const signingIn = signInCanaryAccount(page, 'fixture@example.invalid', 'fixture-only', phase => phases.push(phase));
    await page.waitForTimeout(200);
    expect(await page.locator('input').evaluateAll(inputs => inputs.every(input => !(input as HTMLInputElement).value))).toBe(true);
    await page.locator('#retiring').evaluate(form => { (form as HTMLElement).hidden = true; });
    await signingIn;
    expect(await page.locator('body').getAttribute('data-submitted')).toBe('active');
    expect(await page.locator('#retiring input').evaluateAll(inputs => inputs.every(input => !(input as HTMLInputElement).value))).toBe(true);
    expect(phases).toEqual(['canary_clone_sign_in_form','canary_clone_sign_in_credentials','canary_clone_sign_in_submit']);
  } finally { await browser.close(); }
});

test("explicit429 navigation retries exactly one GET after its bounded Retry-After", async () => {
  const methods: string[] = []; const waits: number[] = [];
  const server = createServer((req, res) => {
    if (req.url !== '/case') { res.end(); return; }
    methods.push(req.method!);
    if (methods.length === 1) { res.writeHead(429, {'retry-after': '65'}); res.end('limited'); }
    else { res.end('<h1>Owner fixture</h1>'); }
  });
  const port = await listen(server); const browser = await chromium.launch({headless:true}); const page = await browser.newPage();
  try {
    await navigateCanary(page, `http://127.0.0.1:${port}/case`, async ms => {waits.push(ms);});
    expect(methods).toEqual(['GET','GET']); expect(waits).toEqual([65000]);
    expect(await page.getByRole('heading').textContent()).toBe('Owner fixture');
  } finally {await browser.close(); await close(server);}
});

test.each([
  ['1', 429, 'CANARY_NAVIGATION_RATE_LIMIT_EXHAUSTED', 2],
  ['0', 429, 'CANARY_NAVIGATION_RETRY_AFTER_INVALID', 1],
  ['invalid-private-text', 429, 'CANARY_NAVIGATION_RETRY_AFTER_INVALID', 1],
  ['66', 429, 'CANARY_NAVIGATION_RETRY_AFTER_EXCESSIVE', 1],
  [undefined, 429, 'CANARY_NAVIGATION_RETRY_AFTER_INVALID', 1],
  ['1', 503, 'CANARY_NAVIGATION_HTTP_FAILED', 1],
] as const)("navigation refuses exhausted/invalid/non429 contract %s/%s", async (header, status, code, expectedCalls) => {
  const methods: string[] = []; const waits: number[] = [];
  const server = createServer((req, res) => {
    if (req.url !== '/case') {res.end(); return;}
    methods.push(req.method!); res.writeHead(status, header ? {'retry-after':header} : {}); res.end('fixture');
  });
  const port = await listen(server); const browser = await chromium.launch({headless:true}); const page = await browser.newPage();
  try {
    await expect(navigateCanary(page, `http://127.0.0.1:${port}/case`, async ms => {waits.push(ms);})).rejects.toMatchObject({code});
    expect(methods).toHaveLength(expectedCalls); expect(methods.every(method => method==='GET')).toBe(true);
    expect(waits).toHaveLength(expectedCalls===2 ? 1 : 0);
  } finally {await browser.close(); await close(server);}
});

test("removed-company absence waits until authenticated editable owner shell renders", async () => {
  const browser = await chromium.launch({headless:true}); const page = await browser.newPage();
  try {
    await page.setContent('<button>Account menu</button><div role="status">Loading watchlist</div>');
    let ready = false;
    const waiting = waitForCanaryOwnerShell(page, 'Owned fixture').then(() => {ready=true;});
    await page.waitForTimeout(100); expect(ready).toBe(false);
    await page.locator('body').evaluate(body => {body.insertAdjacentHTML('beforeend','<h1><button>Owned fixture</button></h1>');});
    await waiting; expect(ready).toBe(true);
    // A shared/read-only title does not certify owner readiness.
    await page.setContent('<button>Account menu</button><h1>Owned fixture</h1>'); page.setDefaultTimeout(100);
    await expect(waitForCanaryOwnerShell(page, 'Owned fixture')).rejects.toMatchObject({name:'TimeoutError'});
  } finally {await browser.close();}
});


const diagnosticPath = "/en/watchlists/11111111-1111-4111-8111-111111111111";
const privateTitle = "PRIVATE_TITLE_SENTINEL";
const readinessState = { persistedTitleMatches: true, companyMembershipCount: 0, anyCompany: false, sessionStatus: 200, sessionIdentityMatches: true };

test.each(["header", "title"] as const)("owner diagnostics distinguish missing %s without exposing fixture content", async missing => {
  const browser = await chromium.launch({ headless: true }); const page = await browser.newPage();
  const evidence: CanaryOwnerEvidence[] = []; const phases: string[] = [];
  try {
    page.setDefaultTimeout(150);
    await page.setContent(`${missing === "header" ? '<a href="/en/sign-in">Log in</a>' : '<button>Account menu</button>'}<h1>${missing === "title" ? "<button>PRIVATE_OTHER_TITLE</button>" : `<button>${privateTitle}</button>`}</h1><div role="status" aria-label="Loading watchlist"></div><div role="dialog">PRIVATE_DIALOG_TEXT</div>`);
    await expect(waitForCanaryOwnerShell(page, privateTitle, { stage: "removal_reload", expectedPath: diagnosticPath, expectedOrigin: "http://fixture.invalid", referenceCoverage: "existing_reference",
      onPhase: value => phases.push(value), onEvidence: value => evidence.push(value), readState: async () => ({ ...readinessState, privateToken: "PRIVATE_STATE_SENTINEL" }) })).rejects.toMatchObject({ name: "TimeoutError" });
    expect(evidence).toHaveLength(1);
    expect(evidence[0].readiness).toBe(missing === "header" ? "header_not_ready" : "title_not_ready");
    expect(evidence[0].visibility.account).toBe(missing === "header" ? 0 : 1);
    expect(evidence[0].visibility.expectedTitle).toBe(missing === "title" ? 0 : 1);
    expect(evidence[0].visibility.dialog).toBe(1);
    expect(evidence[0].state).toEqual(readinessState);
    expect(evidence[0].firstUse).toBe(false);
    expect(phases).toEqual(missing === "header" ? ["canary_removal_reload_owner_header"] : ["canary_removal_reload_owner_header", "canary_removal_reload_owner_title"]);
    expect(JSON.stringify(evidence)).not.toMatch(/PRIVATE_|11111111|fixture.invalid|http:\/\//);
  } finally { await browser.close(); }
});

test("modal-hidden owner DOM is distinguished from absent owner controls", async () => {
  const browser = await chromium.launch({headless:true}); const page = await browser.newPage(); const evidence: CanaryOwnerEvidence[] = [];
  try {
    page.setDefaultTimeout(150);
    await page.setContent(`<div aria-hidden="true"><button>Account menu</button><h1><button>${privateTitle}</button></h1></div><div role="dialog">Overlay</div>`);
    await expect(waitForCanaryOwnerShell(page, privateTitle, { stage: "removal_reload", expectedPath: diagnosticPath, expectedOrigin: "http://fixture.invalid", onEvidence: value => evidence.push(value) })).rejects.toMatchObject({name:"TimeoutError"});
    expect(evidence[0].visibility).toMatchObject({account:0,accountDom:1,expectedTitle:0,expectedTitleDom:1,dialog:1});
  } finally { await browser.close(); }
});

test("read-only existing-reference reload captures bounded metadata and cannot qualify as first use", async () => {
  const methods: string[] = [];
  const server = createServer((req,res) => {
    if(req.url === '/api/auth/get-session') { res.writeHead(401); res.end('{"privateToken":"PRIVATE_RESPONSE_SENTINEL"}'); return; }
    if(req.method === 'POST') { res.writeHead(503); res.end('PRIVATE_ACTION_RESPONSE'); return; }
    if(req.url === diagnosticPath) { methods.push(req.method!); res.setHeader('content-type','text/html'); res.end(`<button>Account menu</button><h1><button>${privateTitle}</button></h1><script>
      fetch('/api/auth/get-session'); fetch(location.pathname,{method:'POST',headers:{'next-action':'PRIVATE_ACTION_SENTINEL'},body:'PRIVATE_REQUEST_BODY'});
      setTimeout(()=>{throw new TypeError('PRIVATE_ERROR_MESSAGE');},0);
    </script>`); return; }
    res.end();
  });
  const port = await listen(server); const origin = `http://127.0.0.1:${port}`; const browser = await chromium.launch({headless:true}); const page = await browser.newPage();
  const evidence: CanaryOwnerEvidence[] = [];
  try {
    const result = await diagnoseCanaryOwnerReload(page,privateTitle,{expectedPath:diagnosticPath,expectedOrigin:origin,onEvidence:value=>evidence.push(value),readState:async()=>{await page.waitForTimeout(100);return readinessState;}});
    expect(result).toEqual({firstUse:false,referenceCoverage:'existing_reference',readOnlyReload:true});
    expect(methods).toEqual(['GET']);
    expect(evidence[0]).toMatchObject({readiness:'ready',route:'owned_watchlist',expectedRouteMatches:true,expectedOriginMatches:true,firstUse:false,navigation:{httpStatus:200},state:readinessState});
    expect(evidence[0].requests).toEqual(expect.arrayContaining([expect.objectContaining({kind:'session_get',httpStatus:401}),expect.objectContaining({kind:'server_action_candidate',httpStatus:503})]));
    expect(evidence[0].browserErrors).toContainEqual({category:'type_error',nextDigest:null});
    expect(JSON.stringify(evidence)).not.toMatch(/PRIVATE_|11111111|127.0.0.1|http:\/\//);
    expect(()=>requireCanaryCleanup({recovered:true,residual:1,starRestored:true,sessionClosed:true})).toThrow('CANARY_CLEANUP_INCOMPLETE');
    expect(()=>requireCanaryCleanup({recovered:true,residual:0,starRestored:true,sessionClosed:false})).toThrow('CANARY_CLEANUP_INCOMPLETE');
    expect(()=>requireCanaryCleanup({recovered:true,residual:0,starRestored:true,sessionClosed:true})).not.toThrow();
  } finally {await browser.close();await close(server);}
});

test("diagnostic callback/state failures cannot replace the original readiness timeout", async () => {
  const browser = await chromium.launch({headless:true}); const page = await browser.newPage();
  try { page.setDefaultTimeout(100); await page.setContent('<div>Unavailable</div>');
    await expect(waitForCanaryOwnerShell(page,privateTitle,{stage:'cleanup',expectedPath:diagnosticPath,expectedOrigin:'http://fixture.invalid',readState:async()=>{throw new Error('PRIVATE_PROBE_ERROR');},onEvidence:()=>{throw new Error('PRIVATE_CONSUMER_ERROR');}})).rejects.toMatchObject({name:'TimeoutError'});
  } finally {await browser.close();}
});

test("browser categories/digests are fixed and never inspect messages or stacks", () => {
  const error = {name:'TypeError',digest:'123456789',get message(){throw new Error('must_not_read');},get stack(){throw new Error('must_not_read');}};
  expect(canaryBrowserError(error)).toEqual({category:'type_error',nextDigest:'123456789'});
  expect(canaryBrowserError({name:'constructor',digest:'PRIVATE_DIGEST'})).toEqual({category:'unknown',nextDigest:null});
  expect(canaryBrowserError(new Proxy({}, {get(){throw new Error('PRIVATE_GETTER');}}))).toEqual({category:'unknown',nextDigest:null});
});


test("passive request evidence is capped and flags truncation without changing readiness", async () => {
  const server = createServer((req,res)=> {
    if(req.method==='POST'){res.end('PRIVATE_RESPONSE');return;}
    res.setHeader('content-type','text/html');res.end(`<button>Account menu</button><h1><button>${privateTitle}</button></h1><script>for(let i=0;i<12;i++)fetch(location.pathname,{method:'POST',headers:{'next-action':'PRIVATE_ACTION'},body:'PRIVATE_BODY'});</script>`);
  });
  const port=await listen(server);const origin=`http://127.0.0.1:${port}`;const browser=await chromium.launch({headless:true});const page=await browser.newPage();const evidence:CanaryOwnerEvidence[]=[];
  try{
    await diagnoseCanaryOwnerReload(page,privateTitle,{expectedPath:diagnosticPath,expectedOrigin:origin,onEvidence:value=>evidence.push(value),readState:async()=>{await page.waitForTimeout(100);return readinessState;}});
    expect(evidence[0].requests).toHaveLength(8);expect(evidence[0].evidenceTruncated).toBe(true);expect(evidence[0].readiness).toBe('ready');
    expect(JSON.stringify(evidence)).not.toMatch(/PRIVATE_|11111111|127.0.0.1/);
  }finally{await browser.close();await close(server);}
});


test.each([false, true])("early session failure cancels pending SELECT and preserves the original error (cancel throws=%s)", async cancelThrows => {
  let outstanding = true; let cancelled = 0; let rejectSelect!: (error: Error) => void;
  const query = Object.assign(new Promise<never>((_, reject) => { rejectSelect = reject; }), {
    cancel: () => { cancelled++; outstanding = false; rejectSelect(new Error("select_cancelled")); if (cancelThrows) throw new Error("cancel_transport_failure"); },
  });
  const sql = (() => query) as unknown as Sql;
  const sessionFailure = new Error("session_transport_failed");
  const page = { context: () => ({ request: { get: async () => { throw sessionFailure; } } }) } as unknown as Page;
  await expect(readCanaryReadinessState(page, sql, "private-user", "private-watchlist", privateTitle, true)).rejects.toBe(sessionFailure);
  expect(cancelled).toBe(1);
  expect(outstanding).toBe(false);
});


test.each([true, false])("owner diagnostics request session only on failed readiness (ready=%s)", async ready => {
  let sessionGets = 0; let sqlReads = 0;
  const server = createServer((req, res) => {
    if (req.url === "/api/auth/get-session") {
      sessionGets++; res.setHeader("content-type", "application/json"); res.end('{"user":{"id":"PRIVATE_USER"}}'); return;
    }
    res.setHeader("content-type", "text/html");
    res.end(`${ready ? "<button>Account menu</button>" : '<a href="/en/sign-in">Log in</a>'}<h1><button>${privateTitle}</button></h1>`);
  });
  const port = await listen(server); const origin = `http://127.0.0.1:${port}`;
  const browser = await chromium.launch({headless:true}); const context = await browser.newContext({baseURL:origin}); const page = await context.newPage();
  const evidence: CanaryOwnerEvidence[] = [];
  const sql = (() => { sqlReads++; return Object.assign(Promise.resolve([{title_matches:true,membership_count:0,any_company:"false"}]), {cancel:()=>{}}); }) as unknown as Sql;
  try {
    page.setDefaultTimeout(200);
    const reload = diagnoseCanaryOwnerReload(page, privateTitle, {expectedPath:diagnosticPath,expectedOrigin:origin,onEvidence:value=>evidence.push(value),
      readState: readiness => readCanaryReadinessState(page,sql,"PRIVATE_USER","PRIVATE_WATCHLIST",privateTitle,readiness !== "ready")});
    if (ready) await expect(reload).resolves.toMatchObject({firstUse:false});
    else await expect(reload).rejects.toMatchObject({name:"TimeoutError"});
    expect(sqlReads).toBe(1); expect(sessionGets).toBe(ready ? 0 : 1);
    expect(evidence[0].state).toEqual({...readinessState,sessionStatus:ready ? null : 200,sessionIdentityMatches:ready ? null : true});
    expect(evidence[0].readiness).toBe(ready ? "ready" : "header_not_ready");
    expect(JSON.stringify(evidence)).not.toMatch(/PRIVATE_|11111111|127.0.0.1/);
  } finally { await browser.close(); await close(server); }
});
