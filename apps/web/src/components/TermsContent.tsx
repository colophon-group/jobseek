"use client";

import { Trans } from "@lingui/react/macro";
import { siteConfig, publicDomainAssets } from "@/content/config";
import { PublicDomainArt } from "@/components/PublicDomainArt";
import Box from "@mui/material/Box";
import Container from "@mui/material/Container";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Paper from "@mui/material/Paper";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import Link from "@mui/material/Link";

export function TermsContent() {
  const contactEmail = siteConfig.indexing.contactEmail;
  const lastUpdated = siteConfig.terms.lastUpdated;
  const fullTermsLink = `${siteConfig.repoUrl}/blob/main/TERMS-OF-SERVICE`;

  const sectionLayoutSx = { width: "100%", maxWidth: 840, scrollMarginTop: { xs: 96, md: 128 } };

  const heroArt = publicDomainAssets[siteConfig.terms.hero.art.assetKey];
  const heroArtFocus = siteConfig.terms.hero.art.focus;
  const heroArtMaxWidth = 390;
  const cropInsets = heroArt?.crop;
  const effectiveW = heroArt ? heroArt.width - (cropInsets?.left ?? 0) - (cropInsets?.right ?? 0) : 0;
  const effectiveH = heroArt ? heroArt.height - (cropInsets?.top ?? 0) - (cropInsets?.bottom ?? 0) : 0;
  const heroArtWidth = heroArt ? Math.min(effectiveW, heroArtMaxWidth) : undefined;
  const heroArtAspect = effectiveH > 0 ? effectiveW / effectiveH : 1;

  return (
    <Box component="main" py={{ xs: 6, md: 10 }}>
      <Container maxWidth="md">
        <Stack spacing={{ xs: 6, md: 8 }}>
          {/* Hero */}
          <Box sx={sectionLayoutSx}>
            <Stack
              direction={{ xs: "column", md: "row" }}
              spacing={{ xs: 4, md: 6 }}
              alignItems={{ xs: "stretch", md: "flex-start" }}
              justifyContent="center"
            >
              <Stack spacing={2} sx={{ flex: 1 }}>
                <Typography variant="overline" color="text.secondary" letterSpacing={1.5}>
                  <Trans id="terms.hero.eyebrow" comment="Terms page eyebrow">Terms</Trans>
                </Typography>
                <Typography variant="h3" component="h1">
                  <Trans id="terms.hero.title" comment="Terms page title">Terms of Service</Trans>
                </Typography>
                <Typography color="text.secondary">
                  <Trans id="terms.hero.description" comment="Terms page description">
                    {"By using Job Seek you agree to these terms. Here\u2019s a plain-language overview."}
                  </Trans>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  <Trans id="terms.hero.lastUpdated" comment="Last updated date label">Last updated:</Trans>
                  {" "}{lastUpdated}
                </Typography>
              </Stack>
              {heroArt && heroArtWidth && (
                <Box
                  sx={{
                    flexBasis: { md: heroArtWidth },
                    flexShrink: 0,
                    width: "100%",
                    maxWidth: { xs: "100%", md: heroArtWidth },
                    order: 2,
                    display: "flex",
                    justifyContent: "center",
                    aspectRatio: heroArtAspect,
                    minHeight: { xs: 240, md: "auto" },
                    maxHeight: { xs: 320, md: "100%" },
                    mx: "auto",
                  }}
                >
                  <PublicDomainArt asset={heroArt} focus={heroArtFocus} credit sx={{ width: "100%", height: "100%" }} />
                </Box>
              )}
            </Stack>
          </Box>

          {/* The short version */}
          <Paper variant="outlined" sx={{ ...sectionLayoutSx, p: { xs: 3, md: 4 } }}>
            <Typography variant="h5">
              <Trans id="terms.short.title" comment="Short version section title">The short version</Trans>
            </Typography>
            <List sx={{ listStyleType: "disc", pl: 3, mt: 1 }}>
              <ListItem sx={{ display: "list-item", pl: 1 }}>
                <Trans id="terms.short.r1" comment="Age requirement">You must be at least 16 to use Job Seek.</Trans>
              </ListItem>
              <ListItem sx={{ display: "list-item", pl: 1 }}>
                <Trans id="terms.short.r2" comment="What the service does">We aggregate public job postings. We do not guarantee they are accurate or up to date.</Trans>
              </ListItem>
              <ListItem sx={{ display: "list-item", pl: 1 }}>
                <Trans id="terms.short.r3" comment="No scraping">{"Don\u2019t scrape the service, submit automated applications, or abuse the platform."}</Trans>
              </ListItem>
              <ListItem sx={{ display: "list-item", pl: 1 }}>
                <Trans id="terms.short.r4" comment="Provided as-is">{"The service is provided as-is \u2014 no warranties."}</Trans>
              </ListItem>
              <ListItem sx={{ display: "list-item", pl: 1 }}>
                <Trans id="terms.short.r5" comment="Account deletion">You can delete your account at any time.</Trans>
              </ListItem>
            </List>
          </Paper>

          {/* Contact + full terms link */}
          <Box sx={sectionLayoutSx}>
            <Typography color="text.secondary">
              <Trans id="terms.contact.description" comment="Terms contact call to action">
                Questions? Email us.
              </Trans>
              {" "}
              <Link href={`mailto:${contactEmail}`}>{contactEmail}</Link>
            </Typography>
            <Link href={fullTermsLink} target="_blank" rel="noreferrer" fontWeight={600} sx={{ mt: 1, display: "inline-block" }}>
              <Trans id="terms.fullTermsLink" comment="Link to full terms of service text">Read the full Terms of Service</Trans>
            </Link>
          </Box>
        </Stack>
      </Container>
    </Box>
  );
}
