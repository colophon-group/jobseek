"use client";

import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { siteConfig, publicDomainAssets } from "@/content/config";
import { PublicDomainArt } from "@/components/PublicDomainArt";
import { TableOfContents } from "@/components/TableOfContents";
import Box from "@mui/material/Box";
import Container from "@mui/material/Container";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Paper from "@mui/material/Paper";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import Link from "@mui/material/Link";

export function LicenseContent() {
  const { t } = useLingui();
  const anchors = siteConfig.license.anchors;
  const codeLink = `${siteConfig.repoUrl}/blob/main/LICENSE`;
  const dataLink = `${siteConfig.repoUrl}/blob/main/LICENSE-JOB-DATA`;
  const contactEmail = siteConfig.indexing.contactEmail;

  const sectionLayoutSx = { width: "100%", maxWidth: 840, scrollMarginTop: { xs: 96, md: 128 } };

  const heroArt = publicDomainAssets[siteConfig.license.hero.art.assetKey];
  const heroArtFocus = siteConfig.license.hero.art.focus;
  const heroArtMaxWidth = 300;
  const cropInsets = heroArt?.crop;
  const effectiveW = heroArt ? heroArt.width - (cropInsets?.left ?? 0) - (cropInsets?.right ?? 0) : 0;
  const effectiveH = heroArt ? heroArt.height - (cropInsets?.top ?? 0) - (cropInsets?.bottom ?? 0) : 0;
  const heroArtWidth = heroArt ? Math.min(effectiveW, heroArtMaxWidth) : undefined;
  const heroArtAspect = effectiveH > 0 ? effectiveW / effectiveH : 1;

  const tocTitle = t({ id: "license.toc.title", comment: "Table of contents heading", message: "Contents" });
  const tocAriaLabel = t({ id: "license.toc.ariaLabel", comment: "Table of contents aria label", message: "Page contents" });

  const tocItems = [
    { label: t({ id: "license.toc.overview", comment: "TOC: Overview", message: "Overview" }), href: `#${anchors.overview}` },
    { label: t({ id: "license.toc.code", comment: "TOC: Application code (MIT)", message: "Application code (MIT)" }), href: `#${anchors.code}` },
    { label: t({ id: "license.toc.data", comment: "TOC: Job data (CC BY-NC 4.0)", message: "Job data (CC BY-NC 4.0)" }), href: `#${anchors.data}` },
    { label: t({ id: "license.toc.contact", comment: "TOC: Contact", message: "Contact" }), href: `#${anchors.contact}` },
  ];

  return (
    <Box component="main" py={{ xs: 6, md: 10 }}>
      <Container maxWidth="lg">
        <Stack direction={{ xs: "column", lg: "row" }} spacing={{ xs: 6, lg: 10 }} alignItems="flex-start">
          <Stack spacing={{ xs: 6, md: 8 }} sx={{ flex: 1 }}>
            {/* Overview */}
            <Box sx={sectionLayoutSx} id={anchors.overview}>
              <Stack
                direction={{ xs: "column", md: "row" }}
                spacing={{ xs: 4, md: 6 }}
                alignItems={{ xs: "stretch", md: "flex-start" }}
                justifyContent="center"
              >
                <Stack spacing={2} sx={{ flex: 1 }}>
                  <Typography variant="overline" color="text.secondary" letterSpacing={1.5}>
                    <Trans id="license.hero.eyebrow" comment="License page eyebrow">Licensing</Trans>
                  </Typography>
                  <Typography variant="h3" component="h1">
                    <Trans id="license.hero.title" comment="License page title">License of Job Seek</Trans>
                  </Typography>
                  <Typography color="text.secondary">
                    <Trans id="license.hero.description" comment="License page description">
                      {"Job Seek's codebase is open source under MIT. The job data we collect and enrich is Creative Commons BY-NC 4.0. Below is the plain-language summary \u2014 please read the full licenses for exact terms."}
                    </Trans>
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

            {/* Code license */}
            <Paper variant="outlined" sx={{ ...sectionLayoutSx, p: { xs: 3, md: 4 } }} id={anchors.code}>
              <Typography variant="h5">
                <Trans id="license.code.title" comment="MIT license section title">Application code (MIT License)</Trans>
              </Typography>
              <Typography color="text.secondary" sx={{ mt: 1 }}>
                <Trans id="license.code.summary" comment="MIT license summary">
                  You can use, modify, and redistribute the code in personal or commercial products as long as you include the copyright and license notice.
                </Trans>
              </Typography>
              <List sx={{ listStyleType: "disc", pl: 3 }}>
                <ListItem sx={{ display: "list-item", pl: 1 }}>
                  <Trans id="license.code.r1" comment="MIT right 1">Copy and modify the code for any purpose, including commercial products.</Trans>
                </ListItem>
                <ListItem sx={{ display: "list-item", pl: 1 }}>
                  <Trans id="license.code.r2" comment="MIT right 2">Redistribute your changes, as long as you include the MIT notice.</Trans>
                </ListItem>
                <ListItem sx={{ display: "list-item", pl: 1 }}>
                  <Trans id="license.code.r3" comment="MIT right 3">{"No warranty \u2014 use at your own risk."}</Trans>
                </ListItem>
              </List>
              <Link href={codeLink} target="_blank" rel="noreferrer" fontWeight={600}>
                <Trans id="license.code.linkLabel" comment="Link to full MIT license">Read the full MIT License</Trans>
              </Link>
            </Paper>

            {/* Data license */}
            <Paper variant="outlined" sx={{ ...sectionLayoutSx, p: { xs: 3, md: 4 } }} id={anchors.data}>
              <Typography variant="h5">
                <Trans id="license.data.title" comment="CC license section title">Collection of job postings (CC BY-NC 4.0)</Trans>
              </Typography>
              <Typography color="text.secondary" sx={{ mt: 1 }}>
                <Trans id="license.data.summary" comment="CC license summary">
                  You may reuse the job dataset with attribution for non-commercial purposes. Commercial usage requires prior written consent.
                </Trans>
              </Typography>
              <List sx={{ listStyleType: "disc", pl: 3 }}>
                <ListItem sx={{ display: "list-item", pl: 1 }}>
                  <Trans id="license.data.r1" comment="CC right 1">
                    {"\u201CViktor Shcherbakov, Collection of Job Postings\u201D with a link to the source."}
                  </Trans>
                </ListItem>
                <ListItem sx={{ display: "list-item", pl: 1 }}>
                  <Trans id="license.data.r2" comment="CC right 2">No commercial redistribution or resale without permission.</Trans>
                </ListItem>
                <ListItem sx={{ display: "list-item", pl: 1 }}>
                  <Trans id="license.data.r3" comment="CC right 3">You can remix/transform the data for research or personal dashboards.</Trans>
                </ListItem>
              </List>
              <Link href={dataLink} target="_blank" rel="noreferrer" fontWeight={600}>
                <Trans id="license.data.linkLabel" comment="Link to full CC license">Read the CC BY-NC 4.0 License</Trans>
              </Link>
            </Paper>

            {/* Contact */}
            <Box sx={sectionLayoutSx} id={anchors.contact}>
              <Typography variant="h5" sx={{ mb: 1.5 }}>
                <Trans id="license.contact.title" comment="Contact section title">Contact</Trans>
              </Typography>
              <Typography color="text.secondary">
                <Trans id="license.contactCta" comment="Contact call to action">
                  Questions about licensing or commercial use? Email us.
                </Trans>
                {" "}
                <Link href={`mailto:${contactEmail}`}>{contactEmail}</Link>
              </Typography>
            </Box>
          </Stack>

          <TableOfContents
            title={tocTitle}
            ariaLabel={tocAriaLabel}
            items={tocItems}
            sx={{ maxWidth: 260, display: { xs: "none", md: "block" } }}
          />
        </Stack>
      </Container>
    </Box>
  );
}
