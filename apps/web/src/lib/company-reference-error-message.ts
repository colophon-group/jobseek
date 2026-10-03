"use client";

import { useCallback } from "react";
import { useLingui } from "@lingui/react/macro";

export function useCompanyReferenceErrorMessage() {
  const { t } = useLingui();
  return useCallback((error: unknown): string => {
    const code = typeof error === "string" ? error :
      error && typeof error === "object" && "code" in error ? error.code : undefined;
    switch (code) {
      case "company_lookup_unavailable":
        return t({ id: "companies.selection.lookupUnavailable", comment: "Company selection save failed because the catalogue is temporarily unavailable", message: "Company lookup is temporarily unavailable. Please try again." });
      case "unknown_company":
      case "invalid_company":
      case "company_identity_conflict":
        return t({ id: "companies.selection.unavailable", comment: "Company selection save failed because a selected company cannot be verified", message: "A selected company is no longer available. Remove it or choose another company." });
      case "company_limit_reached":
        return t({ id: "companies.selection.limitReached", comment: "Company selection save failed because the watchlist company limit was exceeded", message: "Maximum of 250 companies per watchlist reached." });
      default:
        return t({ id: "watchlists.updateFailed", comment: "Error shown when an edit to an owned watchlist cannot be saved", message: "Could not save your changes." });
    }
  }, [t]);
}
