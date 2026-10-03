import { writeFileSync } from "node:fs";
import dotenv from "dotenv";
import postgres from "postgres";
import { logExternalError } from "../src/lib/safe-external-error";
import { auditCompanyReferences, type CompanyReferenceMode } from "./company-reference-contract";

dotenv.config({ path: ".env.local", quiet: true });
async function main() {
  const [mode, outputPath] = process.argv.slice(2).filter(a => a !== "--");
  if (!(["preflight", "postflight", "contract-preflight", "contract-postflight", "drift"] as unknown[]).includes(mode)) throw new Error("Usage: verify-company-references.ts <preflight|postflight|contract-preflight|contract-postflight|drift> [output.json]");
  const runtimeRole = process.env.COMPANY_REFERENCE_RUNTIME_ROLE;
  if (mode === "drift" && !runtimeRole) throw new Error("COMPANY_REFERENCE_RUNTIME_ROLE is required for drift audit");
  const databaseUrl = process.env.DATABASE_URL_UNPOOLED;
  if (!databaseUrl || new URL(databaseUrl).port === "6543") throw new Error("Direct DATABASE_URL_UNPOOLED required");
  const sql = postgres(databaseUrl, { max: 1, prepare: false, connection: { application_name: `jobseek-company-reference-${mode}` } });
  try {
    const evidence = { checkedAt: new Date().toISOString(), ...(await auditCompanyReferences(sql, mode as CompanyReferenceMode, runtimeRole)) };
    const rendered = `${JSON.stringify(evidence, null, 2)}\n`;
    if (outputPath) writeFileSync(outputPath, rendered, { mode: 0o600 });
    process.stdout.write(rendered);
  } finally { await sql.end(); }
}
void main().catch(error => {
  logExternalError("error", { service: "database", operation: "verify_company_references" }, error);
  process.exitCode = 1;
});
