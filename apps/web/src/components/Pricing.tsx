"use client";

import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { useAuth } from "@/components/AuthContext";
import { siteConfig } from "@/content/config";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import Container from "@mui/material/Container";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import CardActions from "@mui/material/CardActions";
import Button from "@mui/material/Button";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Box from "@mui/material/Box";

function FreeTier({ isLoggedIn }: { isLoggedIn: boolean }) {
  const { t } = useLingui();
  const cfg = siteConfig.pricing.free;
  const ctaHref = isLoggedIn ? siteConfig.nav.dashboard.href : cfg.href;
  const ctaLabel = isLoggedIn
    ? t({ id: "common.dashboard.open", comment: "CTA when logged in: open dashboard", message: "Open dashboard" })
    : t({ id: "home.pricing.free.cta", comment: "Free tier CTA", message: "Start for free" });

  return (
    <Box sx={{ flex: { xs: "0 1 auto", md: "1 1 320px" }, maxWidth: { xs: 500, md: 360 }, width: "100%", display: "flex", mx: { xs: "auto", md: 0 } }}>
      <Card
        variant="outlined"
        sx={(theme) => ({
          display: "flex",
          flexDirection: "column",
          borderWidth: 1,
          borderColor: theme.palette.mode === "light" ? theme.palette.grey[300] : theme.palette.divider,
          boxShadow: "none",
          width: "100%",
        })}
      >
        <CardContent sx={{ flexGrow: 1, display: "flex", flexDirection: "column" }}>
          <Typography variant="subtitle1" color="text.secondary">
            <Trans id="home.pricing.free.name" comment="Free tier name">Free</Trans>
          </Typography>
          <Stack direction="row" spacing={1} alignItems="baseline" sx={{ mt: 1 }}>
            <Typography variant="h3">$0</Typography>
            <Typography color="text.secondary">
              <Trans id="home.pricing.free.period" comment="Free tier period">Forever</Trans>
            </Typography>
          </Stack>
          <Typography color="text.secondary" sx={{ mt: 1 }}>
            <Trans id="home.pricing.free.description" comment="Free tier description">Test Job Seek with enough headroom to be up to date with your dream companies.</Trans>
          </Typography>
          <List sx={{ mt: 2, flexGrow: 1 }}>
            <ListItem disableGutters>
              <ListItemIcon sx={{ minWidth: 32, color: "primary.main" }}><CheckCircleIcon fontSize="small" /></ListItemIcon>
              <ListItemText primary={<Trans id="home.pricing.free.f1" comment="Free feature: subscribe to companies">Subscribe to up to 5 companies</Trans>} />
            </ListItem>
            <ListItem disableGutters>
              <ListItemIcon sx={{ minWidth: 32, color: "primary.main" }}><CheckCircleIcon fontSize="small" /></ListItemIcon>
              <ListItemText primary={<Trans id="home.pricing.free.f2" comment="Free feature: application tracker">Application tracker</Trans>} />
            </ListItem>
            <ListItem disableGutters>
              <ListItemIcon sx={{ minWidth: 32, color: "primary.main" }}><CheckCircleIcon fontSize="small" /></ListItemIcon>
              <ListItemText primary={<Trans id="home.pricing.free.f3" comment="Free feature: saved searches">Saved searches</Trans>} />
            </ListItem>
          </List>
        </CardContent>
        <CardActions sx={{ px: 3, pb: 3, pt: 0 }}>
          <Button href={ctaHref} fullWidth variant="outlined" size="large">{ctaLabel}</Button>
        </CardActions>
      </Card>
    </Box>
  );
}

function ProTier({ isLoggedIn }: { isLoggedIn: boolean }) {
  const { t } = useLingui();
  const cfg = siteConfig.pricing.pro;
  const ctaHref = isLoggedIn ? siteConfig.nav.dashboard.href : cfg.href;
  const ctaLabel = isLoggedIn
    ? t({ id: "common.dashboard.open", comment: "CTA when logged in: open dashboard", message: "Open dashboard" })
    : t({ id: "home.pricing.pro.cta", comment: "Pro tier CTA", message: "Upgrade to Pro" });

  return (
    <Box sx={{ flex: { xs: "0 1 auto", md: "1 1 320px" }, maxWidth: { xs: 500, md: 360 }, width: "100%", display: "flex", mx: { xs: "auto", md: 0 } }}>
      <Card
        variant="outlined"
        sx={(theme) => ({
          display: "flex",
          flexDirection: "column",
          borderWidth: 2,
          borderColor: theme.palette.primary.main,
          boxShadow: theme.shadows[2],
          width: "100%",
        })}
      >
        <CardContent sx={{ flexGrow: 1, display: "flex", flexDirection: "column" }}>
          <Typography variant="subtitle1" color="text.secondary">
            <Trans id="home.pricing.pro.name" comment="Pro tier name">Pro</Trans>
          </Typography>
          <Stack direction="row" spacing={1} alignItems="baseline" sx={{ mt: 1 }}>
            <Typography variant="h3">$10</Typography>
            <Typography color="text.secondary">
              <Trans id="home.pricing.pro.period" comment="Pro tier period">per month</Trans>
            </Typography>
          </Stack>
          <Typography color="text.secondary" sx={{ mt: 1 }}>
            <Trans id="home.pricing.pro.description" comment="Pro tier description">For active job seekers who need unlimited reach and faster insight.</Trans>
          </Typography>
          <List sx={{ mt: 2, flexGrow: 1 }}>
            <ListItem disableGutters>
              <ListItemIcon sx={{ minWidth: 32, color: "primary.main" }}><CheckCircleIcon fontSize="small" /></ListItemIcon>
              <ListItemText primary={<Trans id="home.pricing.pro.f1" comment="Pro feature: unlimited subscriptions">Unlimited company subscriptions</Trans>} />
            </ListItem>
            <ListItem disableGutters>
              <ListItemIcon sx={{ minWidth: 32, color: "primary.main" }}><CheckCircleIcon fontSize="small" /></ListItemIcon>
              <ListItemText primary={<Trans id="home.pricing.pro.f2" comment="Pro feature: application tracker">Application tracker</Trans>} />
            </ListItem>
            <ListItem disableGutters>
              <ListItemIcon sx={{ minWidth: 32, color: "primary.main" }}><CheckCircleIcon fontSize="small" /></ListItemIcon>
              <ListItemText primary={<Trans id="home.pricing.pro.f3" comment="Pro feature: saved searches">Saved searches</Trans>} />
            </ListItem>
            <ListItem disableGutters>
              <ListItemIcon sx={{ minWidth: 32, color: "primary.main" }}><CheckCircleIcon fontSize="small" /></ListItemIcon>
              <ListItemText primary={<Trans id="home.pricing.pro.f4" comment="Pro feature: email alerts">Email alerts & updates</Trans>} />
            </ListItem>
          </List>
        </CardContent>
        <CardActions sx={{ px: 3, pb: 3, pt: 0 }}>
          <Button href={ctaHref} fullWidth variant="contained" size="large">{ctaLabel}</Button>
        </CardActions>
      </Card>
    </Box>
  );
}

export function Pricing() {
  const { isLoggedIn } = useAuth();

  return (
    <Container id={siteConfig.pricing.anchorId} component="section" maxWidth="lg" sx={{ py: { xs: 6, md: 10 } }}>
      <Stack spacing={2} textAlign="center" maxWidth={640} mx="auto">
        <Typography variant="overline" color="text.secondary" letterSpacing={1.5}>
          <Trans id="home.pricing.eyebrow" comment="Pricing section eyebrow">Pricing</Trans>
        </Typography>
        <Typography variant="h3" component="h2">
          <Trans id="home.pricing.title" comment="Pricing section heading">Choose the right plan for you</Trans>
        </Typography>
        <Typography color="text.secondary">
          <Trans id="home.pricing.description" comment="Pricing section description">Simple, transparent pricing. Start for free and upgrade when you get serious about your job search.</Trans>
        </Typography>
      </Stack>

      <Stack
        direction={{ xs: "column", md: "row" }}
        spacing={3}
        justifyContent="center"
        alignItems={{ xs: "center", md: "stretch" }}
        sx={{ mt: { xs: 4, md: 6 }, flexWrap: "wrap" }}
      >
        <FreeTier isLoggedIn={isLoggedIn} />
        <ProTier isLoggedIn={isLoggedIn} />
      </Stack>
    </Container>
  );
}
