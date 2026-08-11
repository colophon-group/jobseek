import type { CompanySuggestion } from "@/lib/services/company";
import type { LocationSuggestion } from "@/lib/services/locations";
import type { TaxonomySuggestion } from "@/lib/services/taxonomy";
import type { TypeaheadBoostFilters } from "./typeahead-boost";

export interface SearchBarTypeaheadParams {
  query: string;
  locale: string;
  userLat?: number;
  userLng?: number;
  includeCompanies: boolean;
  locationFilters?: TypeaheadBoostFilters;
  occupationFilters?: TypeaheadBoostFilters;
  seniorityFilters?: TypeaheadBoostFilters;
  technologyFilters?: TypeaheadBoostFilters;
}

export interface SearchBarTypeaheadResults {
  locations: LocationSuggestion[];
  companies: CompanySuggestion[];
  occupations: TaxonomySuggestion[];
  seniorities: TaxonomySuggestion[];
  technologies: TaxonomySuggestion[];
}
