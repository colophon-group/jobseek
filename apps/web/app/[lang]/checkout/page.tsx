import { redirect } from "next/navigation";
import type { Metadata } from "next";
export const metadata: Metadata = { robots: { index: false, follow: false } };
export default async function CheckoutPage({ params }: { params: Promise<{ lang: string }> }) {
  const { lang } = await params;
  redirect(`/${["en", "de", "fr", "it"].includes(lang) ? lang : "en"}/settings/billing`);
}
