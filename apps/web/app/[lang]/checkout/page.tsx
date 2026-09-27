import { Suspense } from "react";
import type { Metadata } from "next";
import { initI18nForPage } from "@/lib/i18n";
import { PaddlePaymentLink } from "@/components/PaddlePaymentLink";

export const metadata: Metadata = { robots: { index: false, follow: false } };

export default async function CheckoutPage({ params }: { params: Promise<{ lang: string }> }) {
  await initI18nForPage(params);
  return <Suspense><PaddlePaymentLink /></Suspense>;
}
