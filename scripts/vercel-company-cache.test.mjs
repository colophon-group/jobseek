import assert from "node:assert/strict";
import test from "node:test";
import { verifyStaticCompanyTrace } from "../.github/scripts/verify-vercel-company-cache.mjs";

const route = "/en/company/aircall";
const deploymentId = "dpl_expected";
const fixture = () => ({ spans: [
  { name: `GET ${route}`, attributes: {
    "vercel.deployment_id": deploymentId,
    "http.response.header.x-vercel-cache": "HIT",
  } },
  { name: "Get Static PPR Content" },
  { name: "Stream Static PPR Content" },
] });

test("accepts complete cached delivery from the intended deployment", () => {
  assert.equal(verifyStaticCompanyTrace(fixture(), route, deploymentId).functionInvocations, 0);
});

test("rejects the observed production HIT with a Function resume", () => {
  const trace = fixture();
  trace.spans.push({ name: "Invoke Function" }, { name: "Stream Dynamic PPR Content" });
  assert.throws(() => verifyStaticCompanyTrace(trace, route, deploymentId), /dynamic work/);
});

test("rejects incomplete traces, error spans, and uncached responses", () => {
  assert.throws(() => verifyStaticCompanyTrace({ spans: [] }, route, deploymentId), /no spans/);
  const incomplete = fixture();
  incomplete.spans.pop();
  assert.throws(() => verifyStaticCompanyTrace(incomplete, route, deploymentId), /complete static/);
  const failed = fixture();
  failed.spans[1].status = { code: 2 };
  assert.throws(() => verifyStaticCompanyTrace(failed, route, deploymentId), /errors/);
  const miss = fixture();
  miss.spans[0].attributes["http.response.header.x-vercel-cache"] = "MISS";
  assert.throws(() => verifyStaticCompanyTrace(miss, route, deploymentId), /cache HIT/);
});

test("rejects a successful trace from a different deployment or route", () => {
  assert.throws(() => verifyStaticCompanyTrace(fixture(), route, "dpl_old"), /staged route/);
  assert.throws(() => verifyStaticCompanyTrace(fixture(), "/fr/company/hellofresh", deploymentId), /staged route/);
});
