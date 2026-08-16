import { pathToFileURL } from "node:url";

type CountValue = string | number | { reason: string; result: string };

interface VercelRequestLog {
  cache?: string;
  cacheReason?: string;
  level?: string;
  message?: string;
  requestMethod?: string;
  requestPath?: string;
  responseStatusCode?: number;
  source?: string;
  timestamp?: number;
}

interface AnalyzeOptions {
  paths?: string[];
}

interface Count<T extends CountValue = CountValue> {
  count: number;
  value: T;
}

const SERVER_ACTION_ID = /Server Action ["']?([0-9a-f]{32,64})/i;

function countBy<T extends CountValue>(values: T[]): Count<T>[] {
  const counts = new Map<string, Count<T>>();

  for (const value of values) {
    const key = JSON.stringify(value);
    const existing = counts.get(key);
    if (existing) {
      existing.count += 1;
    } else {
      counts.set(key, { value, count: 1 });
    }
  }

  return [...counts.values()].sort(
    (left, right) =>
      right.count - left.count ||
      JSON.stringify(left.value).localeCompare(JSON.stringify(right.value)),
  );
}

function textOrUnknown(value: string | undefined): string {
  return value && value.length > 0 ? value : "(none)";
}

export function analyzeVercelLogs(
  input: VercelRequestLog[],
  options: AnalyzeOptions = {},
) {
  const paths = [...new Set(options.paths ?? [])];
  const pathFilter = new Set(paths);
  const entries =
    pathFilter.size === 0
      ? input
      : input.filter(
          (entry) =>
            entry.requestPath !== undefined &&
            pathFilter.has(entry.requestPath),
        );
  const timestamps = entries
    .map((entry) => entry.timestamp)
    .filter(
      (timestamp): timestamp is number =>
        timestamp !== undefined && Number.isFinite(timestamp),
    );
  const oldest = timestamps.length > 0 ? Math.min(...timestamps) : null;
  const newest = timestamps.length > 0 ? Math.max(...timestamps) : null;
  const spanSeconds =
    oldest !== null && newest !== null ? (newest - oldest) / 1000 : null;
  const actionIds = entries.flatMap((entry) => {
    const match = entry.message?.match(SERVER_ACTION_ID);
    return match?.[1] ? [match[1]] : [];
  });

  return {
    input_entries: input.length,
    matched_entries: entries.length,
    filtered_out_entries: input.length - entries.length,
    path_filter: paths,
    window: {
      first_at: oldest === null ? null : new Date(oldest).toISOString(),
      last_at: newest === null ? null : new Date(newest).toISOString(),
      span_seconds: spanSeconds,
    },
    log_entries_per_minute:
      spanSeconds !== null && spanSeconds > 0
        ? entries.length / (spanSeconds / 60)
        : null,
    concentrations: {
      request_method: countBy(
        entries.map((entry) => textOrUnknown(entry.requestMethod)),
      ),
      response_status: countBy(
        entries.map((entry) => entry.responseStatusCode ?? "(none)"),
      ),
      cache: countBy(
        entries.map((entry) => ({
          result: textOrUnknown(entry.cache),
          reason: textOrUnknown(entry.cacheReason),
        })),
      ),
      source: countBy(entries.map((entry) => textOrUnknown(entry.source))),
      request_path: countBy(
        entries.map((entry) => textOrUnknown(entry.requestPath)),
      ),
      level: countBy(entries.map((entry) => textOrUnknown(entry.level))),
      server_action_id: countBy(actionIds),
    },
  };
}

function parseArgs(args: string[]): AnalyzeOptions & { help: boolean } {
  const paths: string[] = [];
  let help = false;

  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (argument === "--") continue;
    if (argument === "--help" || argument === "-h") {
      help = true;
      continue;
    }
    if (argument === "--path") {
      const path = args[index + 1];
      if (!path || path.startsWith("--")) {
        throw new Error("--path requires an exact request path");
      }
      paths.push(path);
      index += 1;
      continue;
    }
    throw new Error(`Unknown argument: ${argument}`);
  }

  return { help, paths };
}

async function readJsonLines(): Promise<VercelRequestLog[]> {
  process.stdin.setEncoding("utf8");
  let input = "";
  for await (const chunk of process.stdin) input += chunk;

  return input
    .split(/\r?\n/u)
    .map((line, index) => ({ line, index }))
    .filter(({ line }) => line.trim().length > 0)
    .map(({ line, index }) => {
      try {
        return JSON.parse(line) as VercelRequestLog;
      } catch {
        throw new Error(`Invalid JSON on input line ${index + 1}`);
      }
    });
}

async function main(): Promise<void> {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    process.stdout.write(
      "Usage: analyze-vercel-logs [--path /exact/path]... < logs.jsonl\n",
    );
    return;
  }

  const entries = await readJsonLines();
  process.stdout.write(
    `${JSON.stringify(analyzeVercelLogs(entries, options), null, 2)}\n`,
  );
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  main().catch((error: unknown) => {
    const message = error instanceof Error ? error.message : "Unknown error";
    process.stderr.write(`${message}\n`);
    process.exitCode = 1;
  });
}
