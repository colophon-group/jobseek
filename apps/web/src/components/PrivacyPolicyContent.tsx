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

export function PrivacyPolicyContent() {
  const contactEmail = siteConfig.indexing.contactEmail;
  const lastUpdated = siteConfig.privacy.lastUpdated;
  const fullPolicyLink = `${siteConfig.repoUrl}/blob/main/PRIVACY-POLICY`;

  const sectionLayoutSx = { width: "100%", maxWidth: 840, scrollMarginTop: { xs: 96, md: 128 } };

  const heroArt = publicDomainAssets[siteConfig.privacy.hero.art.assetKey];
  const heroArtFocus = siteConfig.privacy.hero.art.focus;
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
                  <Trans id="privacy.hero.eyebrow" comment="Privacy policy page eyebrow">Privacy</Trans>
                </Typography>
                <Typography variant="h3" component="h1">
                  <Trans id="privacy.hero.title" comment="Privacy policy page title">Privacy at Job Seek</Trans>
                </Typography>
                <Typography color="text.secondary">
                  <Trans id="privacy.hero.description" comment="Privacy policy page description">
                    {"We collect only what we need, we don\u2019t sell your data, and you can delete everything at any time."}
                  </Trans>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  <Trans id="privacy.hero.lastUpdated" comment="Last updated date label">Last updated:</Trans>
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
              <Trans id="privacy.short.title" comment="Short version section title">The short version</Trans>
            </Typography>
            <List sx={{ listStyleType: "disc", pl: 3, mt: 1 }}>
              <ListItem sx={{ display: "list-item", pl: 1 }}>
                <Trans id="privacy.short.r1" comment="What we store">We store your name, email, and profile picture from your OAuth sign-in, plus the data you create while using the app.</Trans>
              </ListItem>
              <ListItem sx={{ display: "list-item", pl: 1 }}>
                <Trans id="privacy.short.r2" comment="No selling">{"We don\u2019t sell, rent, or share your data for marketing."}</Trans>
              </ListItem>
              <ListItem sx={{ display: "list-item", pl: 1 }}>
                <Trans id="privacy.short.r3" comment="Third parties">{"We use a handful of third-party services for sign-in, hosting, and storage \u2014 nothing else."}</Trans>
              </ListItem>
              <ListItem sx={{ display: "list-item", pl: 1 }}>
                <Trans id="privacy.short.r4" comment="Cookies">{"Cookies are session-only \u2014 no ads, no tracking."}</Trans>
              </ListItem>
              <ListItem sx={{ display: "list-item", pl: 1 }}>
                <Trans id="privacy.short.r5" comment="Encryption">All data is encrypted in transit and at rest.</Trans>
              </ListItem>
            </List>
          </Paper>

          {/* Your rights */}
          <Paper variant="outlined" sx={{ ...sectionLayoutSx, p: { xs: 3, md: 4 } }}>
            <Typography variant="h5">
              <Trans id="privacy.rights.title" comment="Your rights section title">Your rights</Trans>
            </Typography>
            <Typography color="text.secondary" sx={{ mt: 1 }}>
              <Trans id="privacy.rights.intro" comment="Your rights intro">
                {"Under GDPR you can ask for a copy of your data, have it corrected or deleted, export it, or object to processing. Delete your account and everything is wiped within 30 days."}
              </Trans>
            </Typography>
          </Paper>

          {/* Contact + full policy link */}
          <Box sx={sectionLayoutSx}>
            <Typography color="text.secondary">
              <Trans id="privacy.contact.description" comment="Privacy contact call to action">
                Questions? Email us.
              </Trans>
              {" "}
              <Link href={`mailto:${contactEmail}`}>{contactEmail}</Link>
            </Typography>
            <Link href={fullPolicyLink} target="_blank" rel="noreferrer" fontWeight={600} sx={{ mt: 1, display: "inline-block" }}>
              <Trans id="privacy.extras.fullPolicyLink" comment="Link to full privacy policy text">Read the full Privacy Policy</Trans>
            </Link>
          </Box>
        </Stack>
      </Container>
    </Box>
  );
}
