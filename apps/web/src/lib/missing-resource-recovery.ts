import type { Locale } from "@/lib/i18n";

export type MissingResourceKind = "company" | "watchlist";

type RecoveryCopy = {
  company: {
    title: string;
    message: string;
    exploreLabel: string;
    requestLabel: string;
  };
  watchlist: {
    title: string;
    message: string;
    browseLabel: string;
  };
};

/**
 * Static recovery responses cannot run the Lingui/App Router runtime because
 * they deliberately prohibit every script. Keep this copy aligned with the
 * matching Lingui message ids used by the ordinary route-level not-found UI.
 */
const RECOVERY_COPY: Record<Locale, RecoveryCopy> = {
  en: {
    company: {
      title: "Company not found",
      message:
        "The company you are looking for does not exist or has been removed.",
      exploreLabel: "Explore companies",
      requestLabel: "Request this company",
    },
    watchlist: {
      title: "Watchlist not found",
      message: "This watchlist does not exist or is not public.",
      browseLabel: "Browse watchlists",
    },
  },
  de: {
    company: {
      title: "Unternehmen nicht gefunden",
      message:
        "Das gesuchte Unternehmen existiert nicht oder wurde entfernt.",
      exploreLabel: "Unternehmen entdecken",
      requestLabel: "Dieses Unternehmen anfragen",
    },
    watchlist: {
      title: "Beobachtungsliste nicht gefunden",
      message:
        "Diese Beobachtungsliste existiert nicht oder ist nicht öffentlich.",
      browseLabel: "Watchlists entdecken",
    },
  },
  fr: {
    company: {
      title: "Entreprise introuvable",
      message:
        "L'entreprise que vous recherchez n'existe pas ou a été supprimée.",
      exploreLabel: "Découvrir les entreprises",
      requestLabel: "Demander cette entreprise",
    },
    watchlist: {
      title: "Liste de surveillance introuvable",
      message:
        "Cette liste de surveillance n'existe pas ou n'est pas publique.",
      browseLabel: "Parcourir les watchlists",
    },
  },
  it: {
    company: {
      title: "Azienda non trovata",
      message: "L'azienda che stai cercando non esiste o è stata rimossa.",
      exploreLabel: "Scopri le aziende",
      requestLabel: "Richiedi questa azienda",
    },
    watchlist: {
      title: "Watchlist non trovata",
      message: "Questa watchlist non esiste o non è pubblica.",
      browseLabel: "Esplora le watchlist",
    },
  },
};

const DOCUMENT_STYLE = `
  :root { color-scheme: light dark; font-family: ui-sans-serif, system-ui, sans-serif; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #fff; color: #171717; }
  main { min-height: 100vh; display: grid; place-content: center; padding: 3rem 1.5rem; text-align: center; }
  h1 { margin: 0; font-size: 1.5rem; line-height: 2rem; }
  p { max-width: 42rem; margin: .5rem auto 0; color: #666; line-height: 1.5; }
  nav { display: flex; flex-wrap: wrap; justify-content: center; gap: .75rem; margin-top: 1.5rem; }
  a { display: inline-flex; min-height: 2.5rem; align-items: center; justify-content: center; border: 1px solid #171717; border-radius: .5rem; padding: .5rem 1rem; color: inherit; text-decoration: none; }
  a.primary { background: #171717; color: #fff; }
  a:focus-visible { outline: 3px solid #2563eb; outline-offset: 3px; }
  @media (prefers-color-scheme: dark) {
    body { background: #0a0a0a; color: #ededed; }
    p { color: #a3a3a3; }
    a { border-color: #ededed; }
    a.primary { background: #ededed; color: #0a0a0a; }
  }
`;

function companyActions(locale: Locale, slug: string, copy: RecoveryCopy): string {
  // URLSearchParams percent-encodes attribute-breaking characters in the
  // request slug. The result is used only as a URL query, never as HTML text.
  const query = new URLSearchParams({ name: slug.replaceAll("-", " ") });
  return `<a class="primary" href="/${locale}/explore">${copy.company.exploreLabel}</a><a href="/${locale}/companies/request?${query.toString()}">${copy.company.requestLabel}</a>`;
}

/**
 * Build a complete recovery document from fixed copy and context-safe URL
 * components. No upstream HTML is accepted, parsed, or filtered here.
 */
export function staticMissingResourceDocument(
  kind: MissingResourceKind,
  locale: Locale,
  slug = "",
): string {
  const copy = RECOVERY_COPY[locale];
  const content = kind === "company"
    ? {
        title: copy.company.title,
        message: copy.company.message,
        actions: companyActions(locale, slug, copy),
      }
    : {
        title: copy.watchlist.title,
        message: copy.watchlist.message,
        actions: `<a class="primary" href="/${locale}/watchlists">${copy.watchlist.browseLabel}</a>`,
      };

  return `<!doctype html><html lang="${locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex, follow"><title>${content.title}</title><style>${DOCUMENT_STYLE}</style></head><body><main><h1>${content.title}</h1><p>${content.message}</p><nav aria-label="Recovery">${content.actions}</nav></main></body></html>`;
}
