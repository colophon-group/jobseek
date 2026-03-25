/**
 * @actor sec-edgar-actor
 *
 * Parses SEC 10-K and 10-Q filings for hiring/growth language via EDGAR full-text search.
 * Signal type: `sec_filing`
 */

import { runSignalActor } from '../../../shared/signalActor';
import { parseEdgarFilings } from './parser';

interface SecEdgarActorInput {
  companies?: string[];
  lookbackDays?: number;
}

runSignalActor<SecEdgarActorInput>(async (input) => {
  const { companies = [], lookbackDays = 30 } = input;
  console.log(`sec-edgar-actor: companies=${companies.length > 0 ? companies.join(', ') : 'all'}, lookbackDays=${lookbackDays}`);
  return parseEdgarFilings(companies, lookbackDays);
});
