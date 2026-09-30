import type { Locale } from "@/lib/i18n";

// Increment when the scope or wording changes. Store the actual localized text
// with each affirmative choice so later catalog edits cannot rewrite evidence.
export const PRODUCT_NEWS_CONSENT_VERSION = "product-news-v1";
export const PRODUCT_NEWS_CONSENT_TEXT: Record<Locale, string> = {
  en: "Email me occasional Job Seek product updates and offers. Unsubscribe anytime.",
  de: "Ich möchte gelegentlich E-Mails mit Produktneuigkeiten und Angeboten von Job Seek erhalten. Jederzeit abmeldbar.",
  fr: "Je souhaite recevoir occasionnellement par e-mail les nouveautés et offres de Job Seek. Désinscription à tout moment.",
  it: "Desidero ricevere occasionalmente via email novità e offerte di Job Seek. Posso annullare l’iscrizione in qualsiasi momento.",
};

export type ProductNewsSource = "signup" | "settings" | "unsubscribe";

export function productNewsSignupChoice(path: string | undefined, body: Record<string, unknown> | undefined): boolean {
  return path === "/sign-up/email" && body?.productNews === true &&
    body.productNewsConsentVersion === PRODUCT_NEWS_CONSENT_VERSION;
}

export function normalizeProductNewsEmail(email: string): string {
  return email.trim().toLowerCase();
}
