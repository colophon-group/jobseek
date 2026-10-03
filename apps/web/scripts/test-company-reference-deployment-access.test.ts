import { createServer, type Server } from "node:http";
import { once } from "node:events";
import type { AddressInfo } from "node:net";
import { chromium } from "playwright";
import { expect, test } from "vitest";
import { navigateCanary, waitForCanaryOwnerShell } from "./company-reference/canary-navigation";
import { signInCanaryAccount } from "./company-reference/canary-lifecycle";
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
