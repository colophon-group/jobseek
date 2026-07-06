export type {
  PostingLocation,
  SearchProvider,
  SearchResponse,
  SearchResultCompany,
  SearchResultPosting,
  HistogramFilters,
  SalaryBucket,
  ExperienceBucket,
  EmploymentType,
  WorkMode,
} from "./types";
export { EMPLOYMENT_TYPE_VALUES, WORK_MODE_VALUES, isEmploymentType, isWorkMode } from "./types";
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
