export type {
  PostingLocation,
  SearchProvider,
  SearchResponse,
  SearchResultCompany,
  SearchResultPosting,
  HistogramFilters,
  SalaryBucket,
  ExperienceBucket,
  WorkMode,
} from "./types";
export { WORK_MODE_VALUES, isWorkMode } from "./types";
export { TypesenseSearchProvider } from "./typesense";

import type { SearchProvider } from "./types";
import { TypesenseSearchProvider } from "./typesense";

let _provider: SearchProvider | undefined;

export function getSearchProvider(): SearchProvider {
  if (!_provider) {
    _provider = new TypesenseSearchProvider();
  }
  return _provider;
}
