"use client";

import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { siteConfig, publicDomainAssets } from "@/content/config";
import { PublicDomainArt } from "@/components/PublicDomainArt";
import { TableOfContents } from "@/components/TableOfContents";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Paper from "@mui/material/Paper";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import Alert from "@mui/material/Alert";
import Link from "@mui/material/Link";
import Divider from "@mui/material/Divider";
import Container from "@mui/material/Container";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";

export function HowWeIndexContent() {
  const { t } = useLingui();
  const cfg = siteConfig.indexing;
  const anchors = cfg.anchors;
  const sectionScrollMargin = { xs: 96, md: 128 } as const;
  const sectionLayoutSx = { width: "100%", maxWidth: 840, scrollMarginTop: sectionScrollMargin };

  const monkArt = publicDomainAssets.the_monk;
  const monkMaxWidth = 320;
  const monkEffectiveWidth = monkArt ? Math.min(monkArt.width, monkMaxWidth) : undefined;
  const monkAspectRatio = monkArt ? monkArt.width / monkArt.height : 1;

  const tocTitle = t({ id: "indexing.toc.title", comment: "Table of contents heading", message: "Contents" });
  const tocAriaLabel = t({ id: "indexing.toc.ariaLabel", comment: "Table of contents aria label", message: "Page contents" });

  const tocItems = [
    { label: t({ id: "indexing.toc.overview", comment: "TOC: Overview", message: "Overview" }), href: `#${anchors.overview}` },
    { label: t({ id: "indexing.toc.assurances", comment: "TOC: Crawling assurances", message: "Crawling assurances" }), href: `#${anchors.assurances}` },
    { label: t({ id: "indexing.toc.ingestion", comment: "TOC: How postings enter the index", message: "How postings enter the index" }), href: `#${anchors.ingestion}` },
    { label: t({ id: "indexing.toc.optOut", comment: "TOC: Opt-out or questions", message: "Opt-out or questions" }), href: `#${anchors.optOut}` },
    { label: t({ id: "indexing.toc.automation", comment: "TOC: Our stance on automation", message: "Our stance on automation" }), href: `#${anchors.automation}` },
    { label: t({ id: "indexing.toc.oss", comment: "TOC: Open-source crawlers", message: "Open-source crawlers" }), href: `#${anchors.oss}` },
    { label: t({ id: "indexing.toc.outreach", comment: "TOC: Need to reach us?", message: "Need to reach us?" }), href: `#${anchors.outreach}` },
  ];

  return (
    <Box component="main" py={{ xs: 6, md: 10 }}>
      <Container maxWidth="lg">
        <Stack direction={{ xs: "column", lg: "row" }} spacing={{ xs: 6, lg: 10 }} alignItems="flex-start">
          <Stack spacing={{ xs: 6, md: 8 }} sx={{ flex: 1 }}>
            {/* Overview */}
            <Stack spacing={2} sx={sectionLayoutSx} id={anchors.overview}>
              <Typography variant="overline" color="text.secondary" letterSpacing={1.5}>
                <Trans id="indexing.hero.eyebrow" comment="Indexing page eyebrow">Indexing policy</Trans>
              </Typography>
              <Typography variant="h3" component="h1">
                <Trans id="indexing.hero.title" comment="Indexing page title">How we find and process job postings</Trans>
              </Typography>
              <Typography color="text.secondary">
                <Trans id="indexing.hero.description" comment="Indexing page description">
                  Job Seek is a search engine for both active candidates and professionals who passively track the market—it surfaces fresh roles while staying respectful of employer infrastructure. This page documents exactly how our crawler behaves, the controls that keep it polite, and how jobs ultimately land in the index.
                </Trans>
              </Typography>
            </Stack>

            {/* Assurances */}
            <Box sx={sectionLayoutSx} id={anchors.assurances}>
              <Stack
                direction={{ xs: "column", md: "row" }}
                spacing={{ xs: 4, md: 6 }}
                alignItems={{ xs: "stretch", md: "flex-start" }}
                justifyContent="center"
              >
                <Paper variant="outlined" sx={{ flex: 1, p: { xs: 3, md: 4 }, order: 1 }}>
                  <Typography variant="h5">
                    <Trans id="indexing.assurances.title" comment="Assurances section title">Crawling assurances</Trans>
                  </Typography>
                  <List>
                    <ListItem alignItems="flex-start" disablePadding sx={{ mt: 2 }}>
                      <ListItemText
                        primary={<Trans id="indexing.assurances.i1.title" comment="Assurance 1 title">Respectful pacing.</Trans>}
                        secondary={<Trans id="indexing.assurances.i1.body" comment="Assurance 1 body">Every retry window uses exponential backoff so we never hammer an origin, and we bail if a host keeps timing out.</Trans>}
                        slotProps={{ primary: { sx: { fontWeight: 600 } } }}
                      />
                    </ListItem>
                    <ListItem alignItems="flex-start" disablePadding sx={{ mt: 2 }}>
                      <ListItemText
                        primary={<Trans id="indexing.assurances.i2.title" comment="Assurance 2 title">Robots, attribution, and TDM reservation.</Trans>}
                        secondary={
                          <Trans id="indexing.assurances.i2.body" comment="Assurance 2 body">
                            Our crawler reads <code>robots.txt</code>, honours disallow rules, identifies itself via <code>User-Agent</code>, and respects the W3C <code>TDM-Reservation</code> header{"\u2014"}if a page signals reservation, we skip it.
                          </Trans>
                        }
                        slotProps={{ primary: { sx: { fontWeight: 600 } } }}
                      />
                    </ListItem>
                    <ListItem alignItems="flex-start" disablePadding sx={{ mt: 2 }}>
                      <ListItemText
                        primary={<Trans id="indexing.assurances.i3.title" comment="Assurance 3 title">One page per minute.</Trans>}
                        secondary={<Trans id="indexing.assurances.i3.body" comment="Assurance 3 body">Even after discovery we retrieve job detail pages at a strict limit of one request per site per minute.</Trans>}
                        slotProps={{ primary: { sx: { fontWeight: 600 } } }}
                      />
                    </ListItem>
                  </List>
                </Paper>

                {monkArt && monkEffectiveWidth && (
                  <Box
                    sx={{
                      flexBasis: { md: monkEffectiveWidth },
                      flexShrink: 0,
                      width: "100%",
                      maxWidth: { xs: "100%", md: monkEffectiveWidth },
                      order: 2,
                      display: "flex",
                      justifyContent: "center",
                      aspectRatio: monkAspectRatio,
                      minHeight: { xs: 240, md: "auto" },
                      maxHeight: { xs: 320, md: "none" },
                      mx: "auto",
                    }}
                  >
                    <PublicDomainArt asset={monkArt} credit sx={{ width: "100%", height: "100%", mx: "auto" }} />
                  </Box>
                )}
              </Stack>
            </Box>

            {/* Ingestion */}
            <Box sx={sectionLayoutSx} id={anchors.ingestion}>
              <Typography variant="h5">
                <Trans id="indexing.ingestion.title" comment="Ingestion section title">How postings enter the index</Trans>
              </Typography>
              <Typography color="text.secondary" sx={{ mt: 1.5 }}>
                <Trans id="indexing.ingestion.description" comment="Ingestion section description">
                  We look for structured feeds before scraping raw HTML. First we check for sitemaps, then client-side JSON APIs, and only parse full pages when neither exists.
                </Trans>
              </Typography>
              <List component="ol" sx={{ listStyleType: "decimal", pl: 3, mt: 2 }}>
                <ListItem component="li" sx={{ display: "list-item", pl: 1, mb: 1.5 }}>
                  <Typography fontWeight={600} component="span">
                    <Trans id="indexing.ingestion.s1.title" comment="Step 1 title">Sitemap first.</Trans>
                  </Typography>{" "}
                  <Typography component="span" color="text.secondary">
                    <Trans id="indexing.ingestion.s1.body" comment="Step 1 body">
                      We look for a sitemap that already lists every careers or job detail page{"\u2014"}ideally linked from <code>robots.txt</code>{"\u2014"}and rely on it whenever possible.
                    </Trans>
                  </Typography>
                </ListItem>
                <ListItem component="li" sx={{ display: "list-item", pl: 1, mb: 1.5 }}>
                  <Typography fontWeight={600} component="span">
                    <Trans id="indexing.ingestion.s2.title" comment="Step 2 title">Client APIs second.</Trans>
                  </Typography>{" "}
                  <Typography component="span" color="text.secondary">
                    <Trans id="indexing.ingestion.s2.body" comment="Step 2 body">
                      If no sitemap exists we inspect the client application for JSON APIs it calls; when found we hit those endpoints directly to enumerate posting URLs without scraping the DOM.
                    </Trans>
                  </Typography>
                </ListItem>
                <ListItem component="li" sx={{ display: "list-item", pl: 1, mb: 1.5 }}>
                  <Typography fontWeight={600} component="span">
                    <Trans id="indexing.ingestion.s3.title" comment="Step 3 title">Graceful page parsing.</Trans>
                  </Typography>{" "}
                  <Typography component="span" color="text.secondary">
                    <Trans id="indexing.ingestion.s3.body" comment="Step 3 body">
                      As a last resort we parse the careers pages themselves, preferring newest-first sorts and stopping once previously indexed roles reappear instead of crawling every page.
                    </Trans>
                  </Typography>
                </ListItem>
                <ListItem component="li" sx={{ display: "list-item", pl: 1, mb: 1.5 }}>
                  <Typography fontWeight={600} component="span">
                    <Trans id="indexing.ingestion.s4.title" comment="Step 4 title">Selective storage.</Trans>
                  </Typography>{" "}
                  <Typography component="span" color="text.secondary">
                    <Trans id="indexing.ingestion.s4.body" comment="Step 4 body">
                      Once we fetch an individual posting we store only the job-specific metadata (title, role description, location, compensation notes, posting URL, and timestamps) plus extracted structured fields. We do not archive unrelated site content.
                    </Trans>
                  </Typography>
                </ListItem>
              </List>
            </Box>

            {/* Sitemap note */}
            <Alert
              severity="info"
              icon={<InfoOutlinedIcon fontSize="small" />}
              variant="outlined"
              sx={{
                ...sectionLayoutSx,
                mt: { xs: 2, md: 3 },
                borderColor: "var(--info-border)",
                backgroundColor: "var(--info-bg)",
                color: "var(--info-color)",
                "& .MuiAlert-icon": { color: "var(--info-color)" },
              }}
            >
              <Trans id="indexing.sitemapNote" comment="Info box about sitemaps">
                We strongly encourage publishing an easily discoverable sitemap for your careers section. Without it, we periodically mint lightweight <code>HEAD</code> requests against previously discovered job URLs to confirm they are still live, which introduces unnecessary traffic.
              </Trans>
            </Alert>

            {/* Bottom sections */}
            <Stack spacing={4} divider={<Divider sx={{ borderColor: "divider" }} />} sx={{ width: "100%", maxWidth: 840 }}>
              <Box id={anchors.optOut} sx={{ scrollMarginTop: sectionScrollMargin }}>
                <Typography variant="h5">
                  <Trans id="indexing.optOut.title" comment="Opt-out section title">Opt-out or questions</Trans>
                </Typography>
                <Typography color="text.secondary" sx={{ mt: 1.5 }}>
                  <Trans id="indexing.optOut.body" comment="Opt-out section body">
                    If you notice unexpected activity from our crawler or prefer that your careers site not be indexed, please email us and we will respond promptly.
                  </Trans>
                  {" "}
                  <Link href={`mailto:${cfg.contactEmail}`}>{cfg.contactEmail}</Link>.
                </Typography>
              </Box>

              <Box id={anchors.automation} sx={{ scrollMarginTop: sectionScrollMargin }}>
                <Typography variant="h5">
                  <Trans id="indexing.automation.title" comment="Automation stance title">Our stance on automation</Trans>
                </Typography>
                <Typography color="text.secondary" sx={{ mt: 1.5 }}>
                  <Trans id="indexing.automation.body" comment="Automation stance body">
                    We oppose handing hiring or job-search decisions over to black-box automation {"\u2014"} whether on the employer or applicant side. Every outbound link we share includes <code>utm_source=jobseek</code> so recruiters recognise the traffic, and we continuously review usage patterns plus enforce friction to deter scripted applications.
                  </Trans>
                </Typography>
              </Box>

              <Box id={anchors.oss} sx={{ scrollMarginTop: sectionScrollMargin }}>
                <Typography variant="h5">
                  <Trans id="indexing.oss.title" comment="Open-source section title">Open-source crawlers</Trans>
                </Typography>
                <Typography color="text.secondary" sx={{ mt: 1.5 }}>
                  <Trans id="indexing.oss.body" comment="Open-source section body">
                    Transparency matters, so the code for our job link collection service and extraction pipeline is open source.
                  </Trans>
                  {" "}
                  <Trans id="indexing.oss.browseRepo" comment="Link text for browsing the repo">Browse the repository at</Trans>
                  {" "}
                  <Link href={cfg.ossRepoUrl} target="_blank" rel="noreferrer">
                    {cfg.ossRepoUrl.replace("https://", "")}
                  </Link>.
                </Typography>
              </Box>

              <Box id={anchors.outreach} sx={{ scrollMarginTop: sectionScrollMargin }}>
                <Typography variant="h5">
                  <Trans id="indexing.outreach.title" comment="Outreach section title">Need to reach us?</Trans>
                </Typography>
                <Typography color="text.secondary" sx={{ mt: 1.5 }}>
                  <Trans id="indexing.outreach.body" comment="Outreach section body">
                    If you notice unusual crawler behaviour, prefer that we do not index your content, or have suggestions on how to improve our safeguards, please reach out.
                  </Trans>
                  {" "}
                  <Link href={`mailto:${cfg.contactEmail}`}>{cfg.contactEmail}</Link>.
                </Typography>
              </Box>
            </Stack>
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
