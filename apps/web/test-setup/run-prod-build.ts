import { execSync } from "node:child_process";
import { writeFileSync, mkdirSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Vitest globalSetup for the ISR build-output classifier slow lane.
 *
 * Runs `pnpm build` once before the test file and writes the captured
 * stdout to a known artifact path that the test reads. The test then
 * parses the per-route classification (`◐` / `○` / `ƒ`) from the build
 * output and asserts that each route in the must-stay-cacheable list
 * is NOT classified as `ƒ Dynamic`. See `apps/web/__tests__/build-output.test.ts`.
 *
 * Skipping the build: set `BUILD_OUTPUT_LOG` to point at an existing
 * captured-stdout file. CI splits this into a separate job that already
 * runs the build, so it sets the env var to skip the second build here.
 *
 * The build output is captured at `apps/web/.next/build-output.log` so
 * a developer running `pnpm test:isr` locally can re-read it after the
 * fact for diagnosis.
 */

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, "..");
const defaultLogPath = join(webRoot, ".next", "build-output.log");

type SetupContext = {
  provide: (key: "buildOutputLog", value: string) => void;
};

export default async function setup(context: SetupContext): Promise<void> {
  const explicit = process.env.BUILD_OUTPUT_LOG;
  if (explicit && existsSync(explicit)) {
    process.env.BUILD_OUTPUT_LOG = explicit;
    context.provide("buildOutputLog", explicit);
    return;
  }

  // Ensure .next exists so we can write the log inside it.
  mkdirSync(join(webRoot, ".next"), { recursive: true });

  // Run the production build, capture combined stdout+stderr for parsing.
  // We don't fail the setup on a non-zero exit code: if the build fails,
  // the test itself surfaces a clearer "no route summary found" error.
  let output = "";
  try {
    output = execSync("pnpm build", {
      cwd: webRoot,
      encoding: "utf8",
      env: {
        ...process.env,
        NODE_ENV: "production",
      },
      stdio: ["ignore", "pipe", "pipe"],
      maxBuffer: 1024 * 1024 * 64,
    });
  } catch (err) {
    const e = err as { stdout?: string | Buffer; stderr?: string | Buffer };
    const out = typeof e.stdout === "string" ? e.stdout : (e.stdout?.toString() ?? "");
    const errOut = typeof e.stderr === "string" ? e.stderr : (e.stderr?.toString() ?? "");
    output = `${out}\n${errOut}`;
  }

  writeFileSync(defaultLogPath, output, "utf8");
  process.env.BUILD_OUTPUT_LOG = defaultLogPath;
  context.provide("buildOutputLog", defaultLogPath);
}
