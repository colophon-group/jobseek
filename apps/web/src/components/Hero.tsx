"use client";

import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { useAuth } from "@/components/AuthContext";
import { siteConfig, publicDomainAssets } from "@/content/config";
import { PublicDomainArt } from "@/components/PublicDomainArt";
import Container from "@mui/material/Container";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Button from "@mui/material/Button";
import Box from "@mui/material/Box";

export function Hero() {
  const { isLoggedIn } = useAuth();
  const { t } = useLingui();

  const primaryHref = isLoggedIn ? siteConfig.nav.dashboard.href : siteConfig.nav.login.href;
  const primaryLabel = isLoggedIn
    ? t({ id: "common.dashboard.goTo", comment: "CTA when logged in: go to dashboard", message: "Go to dashboard" })
    : t({ id: "home.hero.primaryCta", comment: "Hero primary call-to-action", message: "Get started" });

  const heroArt = publicDomainAssets[siteConfig.hero.art.assetKey];
  const heroArtFocus = siteConfig.hero.art.focus;

  return (
    <Container component="section" maxWidth="lg" sx={{ py: { xs: 8, md: 12 } }}>
      <Stack
        direction={{ xs: "column", md: "row" }}
        spacing={{ xs: 6, md: 10 }}
        alignItems="stretch"
      >
        <Stack maxWidth={640} spacing={3}>
          <Typography variant="overline" color="text.secondary" letterSpacing={1.5}>
            <Trans id="home.hero.eyebrow" comment="Hero eyebrow text above the title">Keep your hand on the job market pulse.</Trans>
          </Typography>
          <Typography variant="h2" component="h1">
            <Trans id="home.hero.title" comment="Main heading on the landing page">Find relevant roles faster.</Trans>
          </Typography>
          <Typography variant="body1" color="text.secondary">
            <Trans id="home.hero.description" comment="Hero description paragraph">
              Subscribe to updates from companies, track applications, and never miss new openings. Designed to keep you in control, not hand your decisions to a bot.
            </Trans>
          </Typography>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2} sx={{ pt: 2 }}>
            <Button href={primaryHref} variant="contained" size="large">
              {primaryLabel}
            </Button>
            <Button href={siteConfig.nav.features.href} variant="outlined" size="large">
              <Trans id="home.hero.secondaryCta" comment="Hero secondary call-to-action">Learn more</Trans>
            </Button>
          </Stack>
        </Stack>

        {heroArt && (
          <Box sx={{ flex: 1 }}>
            <PublicDomainArt
              asset={heroArt}
              focus={heroArtFocus}
              sx={{ minHeight: { xs: 240, sm: 300, lg: 380 }, width: "100%" }}
            />
          </Box>
        )}
      </Stack>
    </Container>
  );
}
