import { createHash } from 'crypto';

/**
 * Generates a 16-char hex SHA-256 hash used as Signal.id for deduplication.
 *
 * Convention: signalId(company, signal_type, date) or signalId(company, signal_type, subtype, date)
 */
export function signalId(...parts: string[]): string {
  return createHash('sha256').update(parts.join(':')).digest('hex').slice(0, 16);
}
