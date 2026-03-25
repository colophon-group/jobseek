import { Actor } from 'apify';
import type { Signal } from './types';
import { DATASETS } from './constants';
import { pushDataWithFallback } from './storage';

/**
 * Wraps the common lifecycle of a signal-producing actor:
 *   init → getInput → discover → dedupe → push → exit
 *
 * The `discover` callback receives the parsed actor input and returns
 * an array of Signal objects. Deduplication by signal.id and dataset
 * push are handled automatically.
 */
export async function runSignalActor<T = Record<string, unknown>>(
  discover: (input: T) => Promise<Signal[]>,
): Promise<void> {
  await Actor.init();
  const input = ((await Actor.getInput<T>()) ?? {}) as T;

  const signals = await discover(input);

  // Deduplicate by signal.id
  const seen = new Set<string>();
  const unique = signals.filter((s) => {
    if (seen.has(s.id)) return false;
    seen.add(s.id);
    return true;
  });

  console.log(`Emitting ${unique.length} signals (${signals.length - unique.length} duplicates removed)`);
  await pushDataWithFallback(unique, DATASETS.SIGNALS);
  await Actor.exit();
}
