import { Suspense } from "react";
import Image from "next/image";
import { Building2 } from "lucide-react";
import { getI18n } from "@lingui/react/server";
import { BackLink } from "@/components/BackLink";
import { StarButton } from "@/components/search/star-button";
import {
  JsonLd,
  buildOrganizationJsonLd,
  buildBreadcrumbJsonLd,
  formatEmployeeCount,
} from "@/lib/seo";
import { withUtmSource } from "@/lib/utm";
import type { CompanyDetail } from "@/lib/actions/company";
import type { Locale } from "@/lib/i18n";
import { CompanyBackLink } from "./company-back-link";

type Props = {
  company: CompanyDetail;
  locale: Locale;
};

export function CompanyHead({ company, locale }: Props) {
  const i18n = getI18n()!;

  const metaParts: string[] = [];
  if (company.industryName) metaParts.push(company.industryName);
  const employees = formatEmployeeCount(company.employeeCountRange);
  if (employees) {
    metaParts.push(
      i18n._({
        id: "company.head.employees",
        comment: "Employee count range shown on company page header",
        message: "{range} employees",
        values: { range: employees },
      }),
    );
  }
  if (company.foundedYear) {
    metaParts.push(
      i18n._({
        id: "company.head.founded",
        comment: "Founded year shown on company page header",
        message: "Founded {year}",
        values: { year: company.foundedYear },
      }),
    );
  }

  const homeName = i18n._({
    id: "breadcrumb.home",
    comment: "Breadcrumb label for the site root",
    message: "Home",
  });
  const exploreName = i18n._({
    id: "breadcrumb.explore",
    comment: "Breadcrumb label for the Explore page",
    message: "Explore",
  });
  const backLabel = i18n._({
    id: "company.head.backToSearch",
    comment: "Back-to-search link on company page header",
    message: "Search results",
  });

  return (
    <div className="space-y-4">
      <Suspense
        fallback={<BackLink href={`/${locale}/explore`}>{backLabel}</BackLink>}
      >
        <CompanyBackLink locale={locale} label={backLabel} />
      </Suspense>

      <div className="flex items-center gap-3">
        {company.icon ? (
          <Image
            src={company.icon}
            alt=""
            width={32}
            height={32}
            sizes="32px"
            className="size-8 shrink-0 rounded"
          />
        ) : (
          <div
            aria-hidden="true"
            className="flex size-8 shrink-0 items-center justify-center rounded bg-border-soft text-muted"
          >
            <Building2 size={18} />
          </div>
        )}
        <h1 className="m-0 text-lg font-semibold">
          {company.website ? (
            <a
              href={withUtmSource(company.website)}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:underline"
            >
              {company.name}
            </a>
          ) : (
            company.name
          )}
        </h1>
        <StarButton companyId={company.id} />
      </div>

      {company.description && (
        <p className="text-sm text-muted">{company.description}</p>
      )}

      {metaParts.length > 0 && (
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
          {metaParts.map((part, i) => (
            <span key={i}>{part}</span>
          ))}
        </div>
      )}

      <JsonLd data={buildOrganizationJsonLd(company, locale)} />
      <JsonLd
        data={buildBreadcrumbJsonLd(
          [
            { name: homeName, path: "" },
            { name: exploreName, path: "/explore" },
            { name: company.name, path: `/company/${company.slug}` },
          ],
          locale,
        )}
      />
    </div>
  );
}

