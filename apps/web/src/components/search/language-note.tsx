"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { getLanguage } from "@/lib/job-languages";

interface LanguageNoteProps {
  /** Raw job-language preference: [] = default, ["*"] = all, ["en","de"] = specific */
  jobLanguages: string[];
  locale: string;
}

export function LanguageNote({ jobLanguages, locale }: LanguageNoteProps) {
  const params = useParams();
  const lang = (params.lang as string) ?? locale;
  const settingsHref = `/${lang}/app/settings`;

  const isAll = jobLanguages.includes("*");
  const isDefault = jobLanguages.length === 0;

  // Resolve effective display codes
  const displayCodes = isAll ? [] : isDefault ? [locale] : jobLanguages;

  // Build display names from CLDR autonyms
  const MAX_INLINE = 2;
  const allNames = displayCodes.map((code) => getLanguage(code)?.label ?? code);
  const shownNames = allNames.slice(0, MAX_INLINE).join(", ");
  const remaining = displayCodes.length - MAX_INLINE;

  const changeLink = (
    <Link href={settingsHref} className="text-primary hover:underline">
      <Trans id="search.languageNote.change" comment="Link to change language settings">
        change
      </Trans>
    </Link>
  );

  if (isAll) {
    return (
      <p className="shrink-0 text-xs text-muted">
        <Trans id="search.languageNote.all" comment="Note showing jobs in all languages">
          Showing jobs in all languages
        </Trans>
        <span className="mx-1">&middot;</span>
        {changeLink}
      </p>
    );
  }

  return (
    <p className="shrink-0 text-xs text-muted">
      {remaining > 0 ? (
        <Trans
          id="search.languageNote.withMore"
          comment="Note showing jobs in specific languages with overflow count"
        >
          Showing jobs in {shownNames} and {remaining} more
        </Trans>
      ) : (
        <Trans
          id="search.languageNote.specific"
          comment="Note showing jobs in specific languages"
        >
          Showing jobs in {shownNames}
        </Trans>
      )}
      <span className="mx-1">&middot;</span>
      {changeLink}
    </p>
  );
}
