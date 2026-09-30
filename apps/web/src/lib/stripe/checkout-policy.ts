import "server-only";
import { msg } from "@lingui/core/macro";
import { loadCatalog, type Locale } from "@/lib/i18n";

export async function checkoutPolicyMessage(locale: Locale) {
  const { i18n } = await loadCatalog(locale);
  return i18n._({ ...msg({
    id: "checkout.policies.acceptance",
    comment: "Stripe hosted Checkout required consent; preserve Markdown links and URL placeholders",
    message: "I agree to the [Terms]({termsUrl}) and acknowledge the [Privacy Policy]({privacyUrl}). [Refund policy]({refundUrl}).",
  }), values: {
    termsUrl: `https://jseek.co/${locale}/terms`,
    privacyUrl: `https://jseek.co/${locale}/privacy-policy`,
    refundUrl: `https://jseek.co/${locale}/terms#refund-policy`,
  } });
}
