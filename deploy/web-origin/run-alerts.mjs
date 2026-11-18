import { readFileSync } from "node:fs";

const config = JSON.parse(readFileSync("/etc/jobseek-web/production.json", "utf8"));
const secret = config?.env?.CRON_SECRET;
if (typeof secret !== "string" || !secret) throw new Error("job alerts cron secret unavailable");

const response = await fetch("http://127.0.0.1:3000/api/internal/job-alerts", {
  headers: { Authorization: `Bearer ${secret}`, Host: "jseek.co", "X-Forwarded-Proto": "https" },
  signal: AbortSignal.timeout(330_000),
});
if (!response.ok) throw new Error(`job alerts returned HTTP ${response.status}`);
console.log("job_alerts_run_succeeded");
