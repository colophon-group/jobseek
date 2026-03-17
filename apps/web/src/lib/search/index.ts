export type {
  PostingLocation,
  SearchProvider,
  SearchResponse,
  SearchResultCompany,
  SearchResultPosting,
  HistogramFilters,
  SalaryBucket,
  ExperienceBucket,
} from "./types";
export { PostgresSearchProvider } from "./postgres";

import type { SearchProvider } from "./types";
import { PostgresSearchProvider } from "./postgres";

let _provider: SearchProvider | undefined;

export function getSearchProvider(): SearchProvider {
  if (!_provider) {
    _provider = new PostgresSearchProvider();
  }
  return _provider;
}
