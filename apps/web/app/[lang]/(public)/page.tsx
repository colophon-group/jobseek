import type { Metadata } from "next";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { Hero } from "@/components/Hero";
import { Features } from "@/components/Features";
import { Pricing } from "@/components/Pricing";
import { PublicDomainArt } from "@/components/PublicDomainArt";
import { siteConfig, publicDomainAssets } from "@/content/config";
import Box from "@mui/material/Box";
import Container from "@mui/material/Container";

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n.t({ id: "home.meta.title", message: "Find Relevant Roles Faster" });
  const description = i18n.t({
    id: "home.meta.description",
    message: "Subscribe to updates from companies, track applications, and never miss new openings.",
  });

  return {
    title,
    description,
    openGraph: { title, description, url: `/${locale}` },
  };
}

export default async function HomePage({ params }: Props) {
  await initI18nForPage(params);

  const afterPricingArt = publicDomainAssets[siteConfig.homepageArt.assetKey];

  return (
    <>
      <Hero />
      <Features />
      <Pricing />
      {afterPricingArt && (
        <Box component="section" sx={{ py: 10 }}>
          <Container maxWidth="lg">
            <Box sx={{ mx: "auto", width: "100%", maxWidth: 768 }}>
              <PublicDomainArt
                asset={afterPricingArt}
                focus={siteConfig.homepageArt.focus}
                sx={{ minHeight: { xs: 360, sm: 460, lg: 560 } }}
              />
            </Box>
          </Container>
        </Box>
      )}
    </>
  );
}
