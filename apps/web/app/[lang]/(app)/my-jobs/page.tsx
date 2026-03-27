import { isLocale, defaultLocale } from "@/lib/i18n";
import { MyJobsLoader } from "./my-jobs-loader";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function MyJobsRoute({ params }: Props) {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  return <MyJobsLoader locale={locale} />;
}
