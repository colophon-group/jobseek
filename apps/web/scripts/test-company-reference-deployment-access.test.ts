import { createServer, type Server } from "node:http";
import { once } from "node:events";
import type { AddressInfo } from "node:net";
import { chromium } from "playwright";
import { expect, test } from "vitest";
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
