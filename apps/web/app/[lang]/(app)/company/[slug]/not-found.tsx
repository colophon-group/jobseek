"use client";

import { Trans } from "@lingui/react/macro";
import { useParams } from "next/navigation";
import { CompanyNotFoundState } from "./company-not-found";

/** Localized recovery UI for the real 404 emitted by the company route. */
export default function CompanyNotFound() {
  const { lang, slug } = useParams<{ lang: string; slug: string }>();

  return (
    <CompanyNotFoundState
      locale={lang}
      slug={slug}
      title={
        <Trans
          id="company.notFound.title"
          comment="Heading shown when the company URL slug doesn't resolve to a known company"
        >
          Company not found
        </Trans>
      }
      message={
        <Trans
          id="company.notFound.body"
          comment="Body text for the company-not-found page; explains the company is either gone or never existed"
        >
          The company you are looking for does not exist or has been removed.
        </Trans>
      }
      exploreLabel={
        <Trans
          id="company.notFound.explore"
          comment="Primary recovery action on the company-not-found page"
        >
          Explore companies
        </Trans>
      }
      requestLabel={
        <Trans
          id="company.notFound.request"
          comment="Secondary action on the company-not-found page to request that company"
        >
          Request this company
        </Trans>
      }
    />
  );
}
