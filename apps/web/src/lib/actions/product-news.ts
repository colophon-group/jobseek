"use server";

import { getSessionUserId } from "@/lib/sessionCache";
import { isLocale } from "@/lib/i18n";
import { PRODUCT_NEWS_CONSENT_VERSION } from "@/lib/product-news/policy";
import { getProductNewsPreference, setProductNewsPreference } from "@/lib/services/product-news";

export async function getProductNewsSettings() {
  const userId = await getSessionUserId();
  return userId ? getProductNewsPreference(userId) : null;
}

export async function setProductNewsSettings(enabled: boolean, locale: string, consentVersion: string) {
  if (typeof enabled !== "boolean" || !isLocale(locale) || (enabled && consentVersion !== PRODUCT_NEWS_CONSENT_VERSION)) {
    return { error: "invalid_request" as const };
  }
  const userId = await getSessionUserId();
  if (!userId) return { error: "not_authenticated" as const };
  return setProductNewsPreference(userId, enabled, locale, "settings");
}
