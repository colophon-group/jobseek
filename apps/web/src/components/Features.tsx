"use client";

import type { ElementType } from "react";
import { Trans } from "@lingui/react/macro";
import { siteConfig } from "@/content/config";
import { ThemedImage } from "@/components/ThemedImage";
import NotificationsIcon from "@mui/icons-material/Notifications";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import BookmarkIcon from "@mui/icons-material/Bookmark";
import BugReportIcon from "@mui/icons-material/BugReport";
import TravelExploreIcon from "@mui/icons-material/TravelExplore";
import CampaignIcon from "@mui/icons-material/Campaign";
import Container from "@mui/material/Container";
import Stack from "@mui/material/Stack";
import type { StackProps } from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Box from "@mui/material/Box";
import type { Theme } from "@mui/material/styles";

const iconMap: Record<string, ElementType> = {
  notifications: NotificationsIcon,
  check_circle: CheckCircleIcon,
  bookmark: BookmarkIcon,
  bug: BugReportIcon,
  travel_explore: TravelExploreIcon,
  campaign: CampaignIcon,
};

const TEXT_COLUMN_WIDTH = 520;
const IMAGE_BORDER_RADIUS = 24;
const EXTRA_WIDE_BREAKPOINT = 2448;
const MEDIA_SHADOW = "0px 12px 32px rgba(15, 23, 42, 0.18)";

const heroStartOffset = (theme: Theme) =>
  `calc((100vw - ${theme.breakpoints.values.lg}px) / 2 + ${theme.spacing(3)})`;

const extraWideInset = (theme: Theme, mediaWidth: number) => {
  const columnGap = parseFloat(theme.spacing(10));
  const heroGutter = parseFloat(theme.spacing(3));
  const halfLg = theme.breakpoints.values.lg / 2;
  const requiredWidth = TEXT_COLUMN_WIDTH + mediaWidth + columnGap;
  const constant = halfLg - heroGutter - requiredWidth;
  return `max(0px, calc(50vw + ${constant}px))`;
};

const featureRowBaseStyles = (mediaWidth: number, align: "left" | "right" = "left") => (theme: Theme) => {
  const smKey = theme.breakpoints.up("sm");
  const lgKey = theme.breakpoints.up("lg");
  const extraWideKey = `@media (min-width: ${EXTRA_WIDE_BREAKPOINT}px)`;

  if (align === "left") {
    return {
      width: "100%",
      paddingLeft: theme.spacing(2),
      paddingRight: 0,
      [smKey]: { paddingLeft: theme.spacing(3), paddingRight: 0 },
      [lgKey]: { paddingLeft: heroStartOffset(theme), paddingRight: 0 },
      [extraWideKey]: { paddingRight: extraWideInset(theme, mediaWidth) },
    };
  }

  return {
    width: "100%",
    paddingLeft: theme.spacing(2),
    paddingRight: 0,
    [smKey]: { paddingLeft: theme.spacing(3), paddingRight: 0 },
    [lgKey]: { paddingLeft: 0, paddingRight: heroStartOffset(theme) },
    [extraWideKey]: { paddingLeft: extraWideInset(theme, mediaWidth) },
  };
};

const buildImageWrapperSx = (mediaWidth: number, inverted: boolean) => ({
  width: "100%",
  maxWidth: mediaWidth,
  borderTopLeftRadius: inverted ? { xs: IMAGE_BORDER_RADIUS, sm: 0 } : IMAGE_BORDER_RADIUS,
  borderBottomLeftRadius: inverted ? { xs: IMAGE_BORDER_RADIUS, sm: 0 } : IMAGE_BORDER_RADIUS,
  borderTopRightRadius: inverted ? { xs: 0, sm: IMAGE_BORDER_RADIUS } : 0,
  borderBottomRightRadius: inverted ? { xs: 0, sm: IMAGE_BORDER_RADIUS } : 0,
  overflow: "hidden",
  boxShadow: MEDIA_SHADOW,
  backgroundColor: "background.paper",
  display: "flex",
  justifyContent: "flex-start",
  "& img": { width: mediaWidth, maxWidth: "none", height: "auto", display: "block" },
  [`@media (min-width: ${EXTRA_WIDE_BREAKPOINT}px)`]: {
    borderTopLeftRadius: IMAGE_BORDER_RADIUS,
    borderBottomLeftRadius: IMAGE_BORDER_RADIUS,
    borderTopRightRadius: IMAGE_BORDER_RADIUS,
    borderBottomRightRadius: IMAGE_BORDER_RADIUS,
  },
});

type PointBlockProps = {
  icon: string;
  title: React.ReactNode;
  description: React.ReactNode;
};

function PointBlock({ icon, title, description }: PointBlockProps) {
  const IconComponent = iconMap[icon] ?? NotificationsIcon;
  return (
    <Stack direction="row" spacing={2} component="div" alignItems="flex-start">
      <IconComponent fontSize="small" />
      <Box>
        <Typography variant="subtitle1" component="dt">{title}</Typography>
        <Typography component="dd" color="text.secondary" sx={{ mt: 0.5 }}>{description}</Typography>
      </Box>
    </Stack>
  );
}

function FeatureSection1() {
  const cfg = siteConfig.features.sections[0];
  const mediaWidth = cfg.screenshot.width;

  return (
    <Stack direction={{ xs: "column", sm: "row" }} spacing={{ xs: 6, md: 8, lg: 10 }} alignItems="stretch" sx={featureRowBaseStyles(mediaWidth, "left")}>
      <Box sx={{ flexBasis: { sm: 400, md: 460, lg: TEXT_COLUMN_WIDTH }, flexShrink: 0, width: "100%", maxWidth: { xs: "100%", sm: TEXT_COLUMN_WIDTH }, px: 0 }}>
        <Stack spacing={2}>
          <Box>
            <Typography variant="overline" color="text.secondary" letterSpacing={1.5}>
              <Trans id="home.features.s1.eyebrow" comment="Feature section 1 eyebrow text">Everything you need to stay ahead</Trans>
            </Typography>
            <Typography variant="h3" component="h2" sx={{ mt: 1 }}>
              <Trans id="home.features.s1.title" comment="Feature section 1 heading">Built for active job seekers</Trans>
            </Typography>
            <Typography color="text.secondary" sx={{ mt: 2 }}>
              <Trans id="home.features.s1.description" comment="Feature section 1 description">Track roles, get notified when companies post something new, and keep your pipeline clean.</Trans>
            </Typography>
          </Box>
          <Stack component="dl" spacing={3} sx={{ mt: 4 }}>
            <PointBlock
              icon={cfg.pointIcons[0]}
              title={<Trans id="home.features.s1.p1.title" comment="Feature: company alerts title">Company alerts</Trans>}
              description={<Trans id="home.features.s1.p1.description" comment="Feature: company alerts description">Follow target employers and get notified when they add new roles.</Trans>}
            />
            <PointBlock
              icon={cfg.pointIcons[1]}
              title={<Trans id="home.features.s1.p2.title" comment="Feature: application tracker title">Application tracker</Trans>}
              description={<Trans id="home.features.s1.p2.description" comment="Feature: application tracker description">Log where you applied, status, contacts, and next steps.</Trans>}
            />
            <PointBlock
              icon={cfg.pointIcons[2]}
              title={<Trans id="home.features.s1.p3.title" comment="Feature: saved searches title">Saved searches</Trans>}
              description={<Trans id="home.features.s1.p3.description" comment="Feature: saved searches description">Save your filters for quick scans without retyping everything.</Trans>}
            />
          </Stack>
        </Stack>
      </Box>
      <Box sx={{ flex: 1, display: "flex", justifyContent: { xs: "flex-start", sm: "flex-end" }, minHeight: { xs: 320, sm: 360, md: 400, lg: 460 }, pr: 0 }}>
        <Box sx={buildImageWrapperSx(mediaWidth, false)}>
          <ThemedImage darkSrc={cfg.screenshot.dark} lightSrc={cfg.screenshot.light} alt="Job Seek dashboard showing tracked applications and company alerts" width={cfg.screenshot.width} height={cfg.screenshot.height} />
        </Box>
      </Box>
    </Stack>
  );
}

function FeatureSection2() {
  const cfg = siteConfig.features.sections[1];
  const mediaWidth = cfg.screenshot.width;

  return (
    <Stack direction={{ xs: "column", sm: "row-reverse" }} spacing={{ xs: 6, md: 8, lg: 10 }} alignItems="stretch" sx={featureRowBaseStyles(mediaWidth, "right")}>
      <Box sx={{ flexBasis: { sm: 400, md: 460, lg: TEXT_COLUMN_WIDTH }, flexShrink: 0, width: "100%", maxWidth: { xs: "100%", sm: TEXT_COLUMN_WIDTH }, px: 0, pl: { xs: 2, sm: 0 } }}>
        <Stack spacing={2}>
          <Box>
            <Typography variant="overline" color="text.secondary" letterSpacing={1.5}>
              <Trans id="home.features.s2.eyebrow" comment="Feature section 2 eyebrow text">Stay in control</Trans>
            </Typography>
            <Typography variant="h3" component="h2" sx={{ mt: 1 }}>
              <Trans id="home.features.s2.title" comment="Feature section 2 heading">The first job aggregator that puts you behind the wheel</Trans>
            </Typography>
            <Typography color="text.secondary" sx={{ mt: 2 }}>
              <Trans id="home.features.s2.description" comment="Feature section 2 description">{"Don't see your favourite company in the feed? Paste its careers link and we'll start scraping it for you\u2014no scripts, no spreadsheets, no waiting in support queues."}</Trans>
            </Typography>
          </Box>
          <Stack component="dl" spacing={3} sx={{ mt: 4 }}>
            <PointBlock
              icon={cfg.pointIcons[0]}
              title={<Trans id="home.features.s2.p1.title" comment="Feature: paste a link title">Paste a link, kick off a crawl</Trans>}
              description={<Trans id="home.features.s2.p1.description" comment="Feature: paste a link description">Point us at any careers page or Notion job board and Job Seek mirrors it in your workspace within minutes.</Trans>}
            />
            <PointBlock
              icon={cfg.pointIcons[1]}
              title={<Trans id="home.features.s2.p2.title" comment="Feature: kill tab routine title">Kill the 50-tab routine</Trans>}
              description={<Trans id="home.features.s2.p2.description" comment="Feature: kill tab routine description">Park every interesting startup in one dashboard instead of juggling Chrome windows and bookmarks.</Trans>}
            />
            <PointBlock
              icon={cfg.pointIcons[2]}
              title={<Trans id="home.features.s2.p3.title" comment="Feature: alerts you drive title">Alerts you actually drive</Trans>}
              description={<Trans id="home.features.s2.p3.description" comment="Feature: alerts you drive description">Set the cadence per company so you hear about fresh openings without doom-scrolling job sites all day.</Trans>}
            />
          </Stack>
        </Stack>
      </Box>
      <Box sx={{ flex: 1, display: "flex", justifyContent: { xs: "flex-start", sm: "flex-start" }, minHeight: { xs: 320, sm: 360, md: 400, lg: 460 }, pr: 0 }}>
        <Box sx={buildImageWrapperSx(mediaWidth, true)}>
          <ThemedImage darkSrc={cfg.screenshot.dark} lightSrc={cfg.screenshot.light} alt="Job Seek dashboard showing tracked applications and company alerts" width={cfg.screenshot.width} height={cfg.screenshot.height} />
        </Box>
      </Box>
    </Stack>
  );
}

export function Features() {
  return (
    <Container
      id={siteConfig.features.anchorId}
      component="section"
      maxWidth={false}
      disableGutters
      sx={{
        py: { xs: 8, md: 12 },
        overflowX: "hidden",
        overflowY: "visible",
        position: "relative",
        zIndex: 1,
        pb: { xs: 4, md: 6 },
      }}
    >
      <Stack spacing={{ xs: 12, md: 16 }}>
        <FeatureSection1 />
        <FeatureSection2 />
      </Stack>
    </Container>
  );
}
