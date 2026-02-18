import { initI18nForPage } from "@/lib/i18n";
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
